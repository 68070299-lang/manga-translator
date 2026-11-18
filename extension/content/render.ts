/**
 * Replace a page element with a translated image: an overlay over `img`
 * elements, canvases and composite containers, or the CSS background of
 * other elements.
 */
import { createImageOverlay } from '../lib/overlay-dom';
import { getTranslatedSrc } from '../lib/translated-image';
import { applyCanvasTranslatedOverlay } from './canvas-reader';
import { BLACK_IMAGE_MESSAGE } from './constants';
import { ensurePositioned, isImageMostlyBlack, saveOriginalStyle } from './dom';
import { showImageError } from './ui';
import { debugLog } from './utils';

/**
 * Uses a sibling overlay because moving an `img` in the DOM can reload its src
 * and fail with ERR_FILE_NOT_FOUND on blob URLs.
 */
function applyImgTranslatedOverlay(img: HTMLImageElement, translatedSrc: string): void {
  if (!img.dataset.mangaOriginalSrc) {
    img.dataset.mangaOriginalSrc = img.src || '';
  }

  // Keep the layout space of the original image.
  saveOriginalStyle(img, 'width', 'height');
  img.style.width = img.style.width || '100%';
  img.style.height = img.style.height || '100%';

  const existingOverlay = img.nextElementSibling;
  if (existingOverlay?.classList.contains('manga-translated-img-overlay')) {
    existingOverlay.remove();
  }

  if (img.parentElement) ensurePositioned(img.parentElement);

  const { host: overlay, image: overlayImg } = createImageOverlay({
    className: 'manga-translated-img-overlay',
    alt: img.alt || '',
    objectFit: getComputedStyle(img).objectFit || 'fill',
    styleText: `
        position: absolute;
        z-index: 9999;
        pointer-events: none;
        display: none;
      `,
  });
  img.after(overlay);

  const reveal = () => {
    if (isImageMostlyBlack(overlayImg)) {
      overlay.style.display = 'none';
      showImageError(img, BLACK_IMAGE_MESSAGE);
      return;
    }
    overlay.style.top = `${img.offsetTop}px`;
    overlay.style.left = `${img.offsetLeft}px`;
    overlay.style.width = `${img.offsetWidth}px`;
    overlay.style.height = `${img.offsetHeight}px`;
    overlay.style.display = 'block';
    img.style.visibility = 'hidden';
  };

  overlayImg.onload = reveal;
  overlayImg.onerror = () => {
    img.style.visibility = '';
    overlay.style.display = 'none';
    showImageError(img, 'Failed to load translated image');
  };

  overlayImg.src = translatedSrc;

  // A cached or blob image may already be loaded.
  if (overlayImg.complete && overlayImg.naturalWidth > 0) {
    reveal();
  }
}

/** Composite containers like cmoa.jp sliced readers get an overlay without wrapping. */
function applyCompositeTranslatedOverlay(container: HTMLElement, translatedSrc: string): void {
  ensurePositioned(container);
  container.querySelector('.manga-composite-overlay')?.remove();

  const { host: overlay, image: overlayImg } = createImageOverlay({
    className: 'manga-composite-overlay',
    styleText: `
          position: absolute;
          top: 0;
          left: 0;
          width: 100%;
          height: 100%;
          z-index: 9999;
          pointer-events: none;
        `,
  });
  container.appendChild(overlay);
  overlayImg.src = translatedSrc;

  debugLog('Composite overlay applied to', container.id || container.className);
}

/**
 * Show a translated image (base64 or URL) in place of `element`'s picture.
 * False when the image could not be used; the element then shows an error chip.
 */
export function replaceWithTranslatedImage(
  element: HTMLElement,
  base64: string,
  mimeType?: string,
  imageId = '',
): boolean {
  let translatedSrc: string | null = null;
  try {
    translatedSrc = getTranslatedSrc(element, base64, mimeType);
  } catch (error) {
    showImageError(
      element,
      `Invalid translated image: ${(error as { message?: string } | null)?.message}`,
    );
    return false;
  }

  if (!translatedSrc) {
    showImageError(element, 'Empty translated image');
    return false;
  }

  if (element.tagName === 'DIV') {
    applyCompositeTranslatedOverlay(element, translatedSrc);
    return true;
  }

  saveOriginalStyle(element, 'cursor');
  element.style.cursor = 'default';

  if (element.tagName === 'IMG') {
    applyImgTranslatedOverlay(element as HTMLImageElement, translatedSrc);
    return true;
  }

  if (element.tagName === 'CANVAS') {
    return applyCanvasTranslatedOverlay(element as HTMLCanvasElement, translatedSrc, imageId);
  }

  saveOriginalStyle(element, 'backgroundSize', 'backgroundRepeat', 'backgroundPosition');
  if (!element.dataset.mangaOriginalBg) {
    element.dataset.mangaOriginalBg = getComputedStyle(element).backgroundImage || '';
  }
  element.style.backgroundImage = `url("${translatedSrc}")`;
  element.dataset.mangaTranslatedBg = element.style.backgroundImage;
  element.style.backgroundSize = 'cover';
  element.style.backgroundRepeat = 'no-repeat';
  element.style.backgroundPosition = 'center';
  return true;
}
