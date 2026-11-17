/**
 * Testable helpers for content-script source URL handling.
 */
import { blobToBase64 as defaultBlobToBase64 } from '../lib/utils';

function getBaseUri(explicitBaseUri?: string): string {
  return explicitBaseUri || document?.baseURI || 'http://localhost/';
}

/** Absolute form of a source URL; browser-local schemes are kept as written. */
export function normalizeSourceUrl(value: unknown, baseUri?: string): string {
  if (!value || typeof value !== 'string') return '';

  const trimmed = value.trim();
  if (!trimmed) return '';
  if (/^(data|blob|file|chrome-extension):/i.test(trimmed)) {
    return trimmed;
  }

  try {
    return new URL(trimmed, getBaseUri(baseUri)).href;
  } catch {
    return trimmed;
  }
}

export function sourceUrlsMatch(a: unknown, b: unknown, baseUri?: string): boolean {
  const left = normalizeSourceUrl(a, baseUri);
  const right = normalizeSourceUrl(b, baseUri);
  return Boolean(left && right && left === right);
}

/** The URL part of every candidate in a `srcset` attribute value. */
export function getSrcsetUrls(srcset: unknown): string[] {
  if (!srcset || typeof srcset !== 'string') return [];

  return srcset
    .split(',')
    .map((candidate) => candidate.trim().split(/\s+/)[0])
    .filter((url): url is string => Boolean(url));
}

/** Every distinct, normalized URL an `img` might be loading its picture from. */
export function getImageSourceCandidates(
  img: HTMLImageElement | null | undefined,
  baseUri?: string,
): string[] {
  if (!img) return [];

  const candidates: unknown[] = [
    img.currentSrc,
    img.src,
    img.getAttribute?.('src'),
    img.getAttribute?.('data-src'),
    img.getAttribute?.('data-original'),
    img.getAttribute?.('data-original-src'),
    img.getAttribute?.('data-lazy-src'),
    img.getAttribute?.('data-url'),
    img.getAttribute?.('data-image'),
  ];

  candidates.push(...getSrcsetUrls(img.getAttribute?.('srcset')));
  candidates.push(...getSrcsetUrls(img.getAttribute?.('data-srcset')));

  return [
    ...new Set(
      candidates.map((candidate) => normalizeSourceUrl(candidate, baseUri)).filter(Boolean),
    ),
  ];
}

/** Normalized `url(...)` targets of a CSS `background-image` value. */
export function getBackgroundImageUrls(styleValue: string | null | undefined, baseUri?: string) {
  const urls: string[] = [];
  const regex = /url\(["']?([^"')]+)["']?\)/g;
  let match = regex.exec(styleValue || '');

  while (match !== null) {
    if (match[1]) urls.push(match[1]);
    match = regex.exec(styleValue || '');
  }

  return urls.map((url) => normalizeSourceUrl(url, baseUri)).filter(Boolean);
}

/** True for URLs only the page can reach (the service worker cannot fetch them). */
export function shouldFetchInContentScript(url: string | null | undefined): boolean {
  return /^(data|blob|file|chrome-extension):/i.test(url || '');
}

export interface LocalFetchDependencies {
  fetch?: (url: string, init?: RequestInit) => Promise<Response>;
  blobToBase64?: (blob: Blob) => Promise<string>;
}

/** Base64 of a page-local (data/blob/file) URL, fetched here instead of in the worker. */
export async function fetchLocalUrlAsBase64(
  url: string,
  dependencies: LocalFetchDependencies = {},
): Promise<string> {
  if (/^data:/i.test(url)) {
    const match = /^data:[^,]*;base64,(.*)$/i.exec(url);
    if (match?.[1] !== undefined) {
      return decodeURIComponent(match[1]).replace(/\s+/g, '');
    }
  }

  const fetchImpl = dependencies.fetch ?? globalThis.fetch;
  const blobToBase64 = dependencies.blobToBase64 ?? defaultBlobToBase64;

  if (typeof fetchImpl !== 'function') {
    throw new Error('Failed to fetch local image: fetch is not available');
  }
  if (typeof blobToBase64 !== 'function') {
    throw new Error('Failed to fetch local image: blobToBase64 is not available');
  }

  const response = await fetchImpl(url, { method: 'GET', cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`Failed to fetch local image: HTTP ${response.status || 'error'}`);
  }

  const blob = await response.blob();
  if (!blob || blob.size <= 0) {
    throw new Error('Failed to fetch local image: empty response body');
  }

  return await blobToBase64(blob);
}
