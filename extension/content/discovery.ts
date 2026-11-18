/**
 * Finding translatable things on the page: images, canvases, CSS backgrounds
 * and composite (sliced) readers, in same-origin iframes too, plus the checks
 * that decide whether an element is already translated or worth translating.
 */

import { getActiveBookWalkerCanvases } from './bookwalker';
import { clearCanvasTranslationIfChanged } from './canvas-memory';
import { isBookWalkerViewer } from './canvas-probe';
import { clearCompositeTranslation, clearImgTranslation } from './clear';
import {
  MIN_IMAGE_HEIGHT,
  MIN_IMAGE_WIDTH,
  NON_MANGA_PATH_PATTERN,
  PROCESSING_ATTR,
  TRANSLATION_ATTR,
} from './constants';
import { getElementBackgroundImageUrls } from './dom';
import { getImageSourceCandidates, normalizeSourceUrl, sourceUrlsMatch } from './helpers';
import { isImageRetryBlocked } from './retry';
import type { ImageTarget } from './types';
import { debugLog } from './utils';
import { isCanvasVisible, isPotentialMangaCanvas, isRenderedWithAncestors } from './visibility';

/** SPA readers can swap src on a reused element without changing the URL. */
function isImageSrcStale(img: HTMLImageElement): boolean {
  const originalSrc = img.dataset?.mangaOriginalSrc;
  const currentSrc = img.src || img.dataset?.src || '';
  // Blob and data URLs are unique per creation, so comparing them says nothing.
  const isEphemeral = (url: string) => url.startsWith('blob:') || url.startsWith('data:');
  return (
    Boolean(originalSrc) &&
    originalSrc !== currentSrc &&
    !isEphemeral(originalSrc as string) &&
    !isEphemeral(currentSrc)
  );
}

/**
 * Everything translatable on the page, in-viewport items first and then by
 * distance from the viewport center.
 */
export function discoverImages(): ImageTarget[] {
  const images: ImageTarget[] = [];
  const processedElements = new Set<Element>();
  const isBookWalker = isBookWalkerViewer();
  const activeBookWalkerCanvases = isBookWalker ? new Set(getActiveBookWalkerCanvases()) : null;

  function scan(doc: Document | null | undefined, offsetTop = 0, offsetLeft = 0): void {
    if (!doc) return;

    const addImage = (
      element: HTMLElement,
      type: ImageTarget['type'],
      src: string | null = null,
      selector: string | null = null,
    ) => {
      images.push({ element, selector, type, src, offsetTop, offsetLeft });
    };

    for (const img of doc.querySelectorAll<HTMLImageElement>(`img[${TRANSLATION_ATTR}]`)) {
      if (isImageSrcStale(img)) {
        debugLog('Img src changed; clearing stale translation');
        clearImgTranslation(img);
      }
    }

    for (const container of doc.querySelectorAll<HTMLElement>(`.pt-img[${TRANSLATION_ATTR}]`)) {
      if (Array.from(container.querySelectorAll('img')).some(isImageSrcStale)) {
        debugLog('Composite container src changed; clearing stale translation');
        clearCompositeTranslation(container);
      }
    }

    // Sliced readers like cmoa.jp: content-p* > pt-img > div[inset] > img (slices).
    // Hidden containers count too, because cmoa.jp hides off-screen pages.
    for (const container of doc.querySelectorAll<HTMLElement>('.pt-img')) {
      const childImages = container.querySelectorAll('img');
      for (const img of childImages) processedElements.add(img);

      if (
        container.hasAttribute(TRANSLATION_ATTR) ||
        container.hasAttribute(PROCESSING_ATTR) ||
        container.querySelector('.manga-composite-overlay') ||
        isImageRetryBlocked(container, 'composite')
      ) {
        continue;
      }

      const containerId = container.id || container.parentElement?.id || null;
      addImage(container, 'composite', null, containerId ? `[id="${containerId}"]` : null);
      processedElements.add(container);
      debugLog('Found composite:', containerId || 'unknown', `(${childImages.length} slices)`);
    }

    // A content-p* page without .pt-img yet is likely still lazy loading.
    for (const pageContainer of doc.querySelectorAll<HTMLElement>('[id^="content-p"]')) {
      if (
        processedElements.has(pageContainer) ||
        isImageRetryBlocked(pageContainer, 'composite') ||
        pageContainer.querySelector('.pt-img')
      ) {
        continue;
      }
      addImage(pageContainer, 'composite', null, `[id="${pageContainer.id}"]`);
      processedElements.add(pageContainer);
      debugLog('Found potential page container:', pageContainer.id);
    }

    for (const img of doc.querySelectorAll<HTMLImageElement>('img')) {
      if (!processedElements.has(img) && isTranslatableImage(img)) {
        addImage(img, 'img', img.src || img.dataset.src || null);
      }
    }

    for (const canvas of doc.querySelectorAll<HTMLCanvasElement>('canvas')) {
      if (processedElements.has(canvas) || !isPotentialMangaCanvas(canvas)) continue;
      if (isBookWalker && !activeBookWalkerCanvases?.has(canvas)) continue;
      clearCanvasTranslationIfChanged(canvas);
      if (!isCanvasVisible(canvas)) continue;
      if (isBookWalker && isImageRetryBlocked(canvas, 'canvas')) continue;
      addImage(canvas, 'canvas');
    }

    for (const el of doc.querySelectorAll<HTMLElement>('[style*="background-image"]')) {
      if (processedElements.has(el)) continue;
      const urlMatch = getComputedStyle(el).backgroundImage.match(/url\(["']?([^"')]+)["']?\)/);
      if (
        urlMatch?.[1] &&
        (el.offsetWidth >= MIN_IMAGE_WIDTH || el.offsetHeight >= MIN_IMAGE_HEIGHT) &&
        !isImageRetryBlocked(el, 'background', urlMatch[1])
      ) {
        addImage(el, 'background', urlMatch[1]);
      }
    }

    // Same-origin iframes only.
    for (const iframe of doc.querySelectorAll('iframe')) {
      try {
        const iframeSrc = iframe.src || iframe.getAttribute('src') || '';
        if (iframeSrc && !iframeSrc.startsWith('about:') && !iframeSrc.startsWith('javascript:')) {
          const iframeUrl = new URL(iframeSrc, window.location.href);
          if (iframeUrl.origin !== window.location.origin) continue;
        }
        const frameDoc = iframe.contentDocument || iframe.contentWindow?.document;
        if (!frameDoc) continue;
        const rect = iframe.getBoundingClientRect();
        scan(frameDoc, offsetTop + rect.top, offsetLeft + rect.left);
      } catch {
        // Ignore cross-origin iframes
      }
    }
  }

  scan(document);

  // Images in the viewport first, then by distance from its center.
  const viewportHeight = window.innerHeight;
  const viewportPlacement = ({ element, offsetTop = 0 }: ImageTarget) => {
    const rect = element.getBoundingClientRect();
    const top = offsetTop + rect.top;
    return {
      inViewport: top < viewportHeight && offsetTop + rect.bottom > 0,
      distance: Math.abs(top + rect.height / 2 - viewportHeight / 2),
    };
  };
  images.sort((a, b) => {
    const placementA = viewportPlacement(a);
    const placementB = viewportPlacement(b);
    return (
      Number(placementB.inViewport) - Number(placementA.inViewport) ||
      placementA.distance - placementB.distance
    );
  });

  return images;
}

/** The composite container (cmoa.jp style) an image belongs to, if any. */
export function getCompositeContainerForImage(img: HTMLImageElement): HTMLElement | null {
  const ptImg = img.closest?.<HTMLElement>('.pt-img');
  if (ptImg) return ptImg;

  const pageContainer = img.closest?.<HTMLElement>('[id^="content-p"]');
  if (pageContainer && pageContainer.querySelectorAll('img').length > 1) {
    return pageContainer.querySelector<HTMLElement>('.pt-img') || pageContainer;
  }

  return null;
}

/** The overlay an element already has: a sibling overlay or one in its translate wrapper. */
function getAssociatedImageOverlay(element: HTMLElement | null | undefined): Element | null {
  if (!element) return null;
  const nextOverlay = element.nextElementSibling;
  if (nextOverlay?.classList?.contains('manga-translated-img-overlay')) {
    const overlayId = element.dataset?.mangaCanvasOverlayId;
    if (
      element.tagName !== 'CANVAS' ||
      (nextOverlay as HTMLElement).dataset?.mangaCanvasOverlayId === overlayId
    ) {
      return nextOverlay;
    }
  }
  const wrapper = element.parentElement?.classList?.contains('manga-translate-wrapper')
    ? element.parentElement
    : null;
  return wrapper?.querySelector?.('.manga-translated-img-overlay') || null;
}

/** True when an element is translated, being translated, or already has an overlay. */
export function isAlreadyTranslatedElement(element: HTMLElement | null | undefined): boolean {
  return Boolean(
    element?.hasAttribute?.(TRANSLATION_ATTR) ||
      element?.hasAttribute?.(PROCESSING_ATTR) ||
      element?.querySelector?.('.manga-composite-overlay') ||
      element?.querySelector?.('.manga-translated-img-overlay') ||
      getAssociatedImageOverlay(element),
  );
}

/** Find the page image that a context-menu `src` refers to. */
export function resolveImageDataBySource(src: string | null | undefined): ImageTarget | null {
  const target = normalizeSourceUrl(src);
  if (!target) return null;

  for (const img of document.querySelectorAll<HTMLImageElement>('img')) {
    const matchesTarget = getImageSourceCandidates(img).some((candidate) =>
      sourceUrlsMatch(candidate, target),
    );
    if (!matchesTarget) continue;

    const composite = getCompositeContainerForImage(img);
    if (composite) {
      return {
        element: composite,
        selector: composite.id ? `[id="${composite.id}"]` : null,
        type: 'composite',
        src: null,
      };
    }

    return {
      element: img,
      type: 'img',
      src: img.currentSrc || img.src || img.getAttribute('src') || null,
    };
  }

  for (const element of document.querySelectorAll<HTMLElement>('[style*="background-image"]')) {
    if (
      getElementBackgroundImageUrls(element).some((candidate) => sourceUrlsMatch(candidate, target))
    ) {
      return { element, type: 'background', src: target };
    }
  }

  return null;
}

/** Whether an `img` is worth translating (size, source, and reader-specific rules). */
export function isTranslatableImage(img: HTMLImageElement): boolean {
  if (img.hasAttribute(TRANSLATION_ATTR) || img.hasAttribute(PROCESSING_ATTR)) {
    return false;
  }

  if (
    isImageRetryBlocked(img, 'img', img.currentSrc || img.src || img.getAttribute('src') || null)
  ) {
    return false;
  }

  if (
    img.parentElement?.classList.contains('manga-translate-wrapper') ||
    img.dataset.mangaOriginalSrc
  ) {
    return false;
  }

  if (isBookWalkerViewer()) {
    const rect = img.getBoundingClientRect();
    if (
      !img.closest?.('.pt-img, [id^="content-p"]') ||
      rect.width < 1 ||
      rect.height < 1 ||
      !isRenderedWithAncestors(img)
    ) {
      return false;
    }
  }

  const src = (img.src || '').toLowerCase();

  // In some readers a blob or data image is very likely a manga page.
  if (src.startsWith('blob:') || src.startsWith('data:image/')) {
    return true;
  }

  // Loaded but displayed at 0x0: loading spinners, tracking pixels, etc.
  if (img.complete && img.clientWidth === 0 && img.clientHeight === 0) {
    const rect = img.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) {
      return false;
    }
  }

  // Skip tiny images (icons, etc.) but be lenient if they might be loading
  const width = img.naturalWidth || img.width || img.clientWidth || (img.complete ? 0 : 999);
  const height = img.naturalHeight || img.height || img.clientHeight || (img.complete ? 0 : 999);

  if (width < MIN_IMAGE_WIDTH && height < MIN_IMAGE_HEIGHT && img.complete) {
    return false;
  }

  // Skip common non-manga images. Match whole path tokens only, so hosts and
  // words such as "uploads", "download" or "mangadex" are not mistaken for ads.
  if (src) {
    let path = src;
    try {
      path = new URL(src, document.baseURI).pathname;
    } catch {
      // Fall back to the raw string when the URL cannot be parsed.
    }
    if (NON_MANGA_PATH_PATTERN.test(path)) {
      return false;
    }
  }

  return true;
}
