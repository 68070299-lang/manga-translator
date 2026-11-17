/**
 * Page cache for sliced readers like cmoa.jp: nearby pages are captured up
 * front because scrolling can revoke the blob URLs of their slices.
 */

import { captureCompositeImage, waitForSlicesToLoad } from './capture';
import {
  LOG_PREFIX,
  MIN_IMAGE_DATA_LENGTH,
  PAGE_IMAGE_CACHE_MAX,
  TRANSLATION_ATTR,
} from './constants';
import { getPageId } from './dom';
import { state } from './state';
import { debugLog, lruSet } from './utils';

/** The pre-cached capture of a page container, if any. */
export function getCachedPageImage(container: Element): string | null {
  const pageId = getPageId(container);
  return (pageId && state.pageImageCache.get(pageId)) || null;
}

/** Caches visible and nearby pages up front, because scrolling can revoke their blob URLs. */
export async function preCacheVisiblePages(): Promise<void> {
  if (!state.siteEnabled) return;
  const pageContainers = document.querySelectorAll<HTMLElement>('[id^="content-p"], .pt-img');
  if (pageContainers.length === 0) return;

  const viewportHeight = window.innerHeight;
  const viewportCenter = window.scrollY + viewportHeight / 2;

  const pages = Array.from(pageContainers).map((container) => {
    const rect = container.getBoundingClientRect();
    const pageCenter = window.scrollY + rect.top + rect.height / 2;
    return {
      container,
      rect,
      distance: Math.abs(pageCenter - viewportCenter),
      isVisible: rect.bottom > 0 && rect.top < viewportHeight,
    };
  });
  pages.sort((a, b) => Number(b.isVisible) - Number(a.isVisible) || a.distance - b.distance);

  // Four pages cover two double-page spreads.
  for (const { container, rect } of pages.slice(0, 4)) {
    const pageId = getPageId(container);
    if (
      !pageId ||
      state.pageImageCache.has(pageId) ||
      state.pageCacheStatus.get(pageId) === 'pending'
    ) {
      continue;
    }
    if (
      container.hasAttribute(TRANSLATION_ATTR) ||
      container.querySelector('.manga-composite-overlay')
    ) {
      continue;
    }
    if (rect.width === 0) continue;

    const slices = container.querySelectorAll('img');
    if (slices.length === 0) continue;

    state.pageCacheStatus.set(pageId, 'pending');
    try {
      await waitForSlicesToLoad(slices, 3000);
      const base64 = await captureCompositeImage(container);

      if (base64 && base64.length > MIN_IMAGE_DATA_LENGTH) {
        lruSet(state.pageImageCache, pageId, base64, PAGE_IMAGE_CACHE_MAX, (key) =>
          state.pageCacheStatus.delete(key),
        );
        state.pageCacheStatus.set(pageId, 'cached');
        debugLog(`Pre-cached page: ${pageId}`);
      } else {
        state.pageCacheStatus.set(pageId, 'failed');
      }
    } catch (e) {
      console.warn(
        `${LOG_PREFIX} Failed to pre-cache page ${pageId}:`,
        (e as { message?: string } | null)?.message,
      );
      state.pageCacheStatus.set(pageId, 'failed');
    }
  }
}
