/**
 * Retry blocks: after a non-retryable failure (timeout, size limit) an image
 * is skipped for a while. The block is kept both on the element (dataset) and
 * in a bounded page-level memory keyed by a source signature, so a re-created
 * element for the same image stays blocked.
 */

import { getStoredCanvasSignature } from './canvas-probe';
import { IMAGE_RETRY_BLOCK_MEMORY_MAX, NON_RETRYABLE_IMAGE_BACKOFF_MS } from './constants';
import { getElementBackgroundImageUrls } from './dom';
import { getImageSourceCandidates, normalizeSourceUrl } from './helpers';
import { state } from './state';
import type { ImageType, RetryBlockRecord } from './types';
import { fnv1aHash, getErrorMessage, lruSet } from './utils';

function compactRetrySignaturePart(value: unknown): string {
  const normalized = normalizeSourceUrl(value);
  if (!normalized) return '';
  if (normalized.length <= 160) return normalized;
  return `${normalized.slice(0, 80)}#${fnv1aHash(normalized).toString(36)}:${normalized.length}`;
}

function compactSourceList(sources: readonly unknown[], limit: number): string {
  return sources
    .map((source) => compactRetrySignaturePart(source))
    .filter(Boolean)
    .slice(0, limit)
    .join(',');
}

type MediaElement = HTMLElement &
  Partial<Pick<HTMLImageElement, 'naturalWidth' | 'naturalHeight' | 'width' | 'height'>>;

/** Identity of an image for retry blocking, stable across re-created elements. */
export function getImageRetryBlockSignature(
  element: HTMLElement | null | undefined,
  type: ImageType = 'img',
  src: string | null = null,
): string {
  if (!element) return '';

  if (type === 'canvas' || element.tagName === 'CANVAS') {
    return getStoredCanvasSignature(element as HTMLCanvasElement);
  }

  const media = element as MediaElement;
  const rect = element.getBoundingClientRect?.() || { width: 0, height: 0 };
  const dimensions = [
    media.naturalWidth || media.width || Math.round(rect.width) || 0,
    media.naturalHeight || media.height || Math.round(rect.height) || 0,
    Math.round(rect.width) || 0,
    Math.round(rect.height) || 0,
  ].join('x');

  if (type === 'composite') {
    const containerId = element.id || element.parentElement?.id || '';
    if (containerId) return `composite|${containerId}`;
    const childImages = Array.from(element.querySelectorAll?.('img') || []);
    const childSources = compactSourceList(
      childImages.flatMap((img) => getImageSourceCandidates(img)),
      12,
    );
    return childSources
      ? `composite|${childImages.length}|${childSources}`
      : `composite|${dimensions}`;
  }

  if (type === 'background') {
    return `background|${compactSourceList([src, ...getElementBackgroundImageUrls(element)], Number.POSITIVE_INFINITY) || dimensions}`;
  }

  return `img|${compactSourceList([src, ...getImageSourceCandidates(element as HTMLImageElement)], 8) || dimensions}`;
}

export function clearImageRetryBlock(
  element: HTMLElement | null | undefined,
  type: ImageType | null = null,
  src: string | null = null,
): void {
  const blockedSignature = element?.dataset?.mangaRetryBlockedSignature;
  if (blockedSignature) {
    state.imageRetryBlockMemory.delete(blockedSignature);
  }
  if (element && type) {
    state.imageRetryBlockMemory.delete(getImageRetryBlockSignature(element, type, src));
  }

  if (!element?.dataset) return;
  delete element.dataset.mangaRetryBlockedSignature;
  delete element.dataset.mangaRetryBlockedUntil;
  delete element.dataset.mangaRetryBlockedReason;
}

function storeRetryBlock(element: HTMLElement, signature: string, record: RetryBlockRecord): void {
  element.dataset.mangaRetryBlockedSignature = signature;
  element.dataset.mangaRetryBlockedUntil = String(record.blockedUntil);
  element.dataset.mangaRetryBlockedReason = record.reason;
}

export function markImageRetryBlocked(
  element: HTMLElement | null | undefined,
  type: ImageType,
  error: unknown,
  src: string | null = null,
): void {
  if (!element?.dataset) return;

  const signature = getImageRetryBlockSignature(element, type, src);
  if (!signature) return;

  const record: RetryBlockRecord = {
    blockedUntil: Date.now() + NON_RETRYABLE_IMAGE_BACKOFF_MS,
    reason: getErrorMessage(error),
  };
  lruSet(state.imageRetryBlockMemory, signature, record, IMAGE_RETRY_BLOCK_MEMORY_MAX);
  storeRetryBlock(element, signature, record);
}

export function isImageRetryBlocked(
  element: HTMLElement | null | undefined,
  type: ImageType = 'img',
  src: string | null = null,
): boolean {
  if (!element?.dataset) return false;

  const blockedUntil = Number.parseInt(element.dataset.mangaRetryBlockedUntil || '0', 10);
  const now = Date.now();
  const blockedSignature = element.dataset.mangaRetryBlockedSignature || '';
  const currentSignature = getImageRetryBlockSignature(element, type, src);

  if (blockedUntil) {
    if (now >= blockedUntil) {
      clearImageRetryBlock(element, type, src);
    } else if (blockedSignature && currentSignature && blockedSignature !== currentSignature) {
      clearImageRetryBlock(element);
    } else {
      return true;
    }
  }

  if (!currentSignature) return false;

  const memoryRecord = state.imageRetryBlockMemory.get(currentSignature);
  if (!memoryRecord) return false;

  if (now >= memoryRecord.blockedUntil) {
    state.imageRetryBlockMemory.delete(currentSignature);
    return false;
  }

  storeRetryBlock(element, currentSignature, memoryRecord);
  return true;
}

/** Short-lived block on one canvas element whose source changed mid-translation. */
export function isCanvasElementRetryBlocked(canvas: HTMLCanvasElement): boolean {
  const record = state.canvasElementRetryBlocks.get(canvas);
  if (!record) return false;
  if (Date.now() >= record.blockedUntil) {
    state.canvasElementRetryBlocks.delete(canvas);
    return false;
  }
  return true;
}

export function getRetryBlockedError(element: HTMLElement | null, type: ImageType = 'img'): string {
  const label = type === 'canvas' ? 'Canvas' : 'Image';
  return (
    element?.dataset?.mangaRetryBlockedReason ||
    `${label} translation is paused after a recent timeout`
  );
}
