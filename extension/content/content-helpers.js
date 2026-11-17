/**
 * Testable helpers for content-script source URL handling.
 */

(function(root) {
  'use strict';

function getBaseUri(explicitBaseUri) {
  return explicitBaseUri || root.document?.baseURI || 'http://localhost/';
}

function normalizeSourceUrl(value, baseUri) {
  if (!value || typeof value !== 'string') return '';

  const trimmed = value.trim();
  if (!trimmed) return '';
  if (/^(data|blob|file|chrome-extension):/i.test(trimmed)) {
    return trimmed;
  }

  try {
    return new URL(trimmed, getBaseUri(baseUri)).href;
  } catch (_error) {
    return trimmed;
  }
}

function sourceUrlsMatch(a, b, baseUri) {
  const left = normalizeSourceUrl(a, baseUri);
  const right = normalizeSourceUrl(b, baseUri);
  return Boolean(left && right && left === right);
}

function getSrcsetUrls(srcset) {
  if (!srcset || typeof srcset !== 'string') return [];

  return srcset
    .split(',')
    .map(candidate => candidate.trim().split(/\s+/)[0])
    .filter(Boolean);
}

function getImageSourceCandidates(img, baseUri) {
  if (!img) return [];

  const candidates = [
    img.currentSrc,
    img.src,
    img.getAttribute?.('src'),
    img.getAttribute?.('data-src'),
    img.getAttribute?.('data-original'),
    img.getAttribute?.('data-original-src'),
    img.getAttribute?.('data-lazy-src'),
    img.getAttribute?.('data-url'),
    img.getAttribute?.('data-image')
  ];

  candidates.push(...getSrcsetUrls(img.getAttribute?.('srcset')));
  candidates.push(...getSrcsetUrls(img.getAttribute?.('data-srcset')));

  return [...new Set(candidates.map(candidate => normalizeSourceUrl(candidate, baseUri)).filter(Boolean))];
}

function getBackgroundImageUrls(styleValue, baseUri) {
  const urls = [];
  const regex = /url\(["']?([^"')]+)["']?\)/g;
  let match;

  while ((match = regex.exec(styleValue || '')) !== null) {
    urls.push(match[1]);
  }

  return urls.map(url => normalizeSourceUrl(url, baseUri)).filter(Boolean);
}

function shouldFetchInContentScript(url) {
  return /^(data|blob|file|chrome-extension):/i.test(url || '');
}

async function fetchLocalUrlAsBase64(url, dependencies = {}) {
  if (/^data:/i.test(url)) {
    const match = /^data:[^,]*;base64,(.*)$/i.exec(url);
    if (match) {
      return decodeURIComponent(match[1]).replace(/\s+/g, '');
    }
  }

  const fetchImpl = dependencies.fetch || root.fetch;
  const blobToBase64 = dependencies.blobToBase64 || root.blobToBase64;

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

const exported = {
  normalizeSourceUrl,
  sourceUrlsMatch,
  getSrcsetUrls,
  getImageSourceCandidates,
  getBackgroundImageUrls,
  shouldFetchInContentScript,
  fetchLocalUrlAsBase64
};

if (root) {
  root.MangaTranslatorContentHelpers = exported;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = exported;
}

})(typeof globalThis !== 'undefined' ? globalThis : this);
