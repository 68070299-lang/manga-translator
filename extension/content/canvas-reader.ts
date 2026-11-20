/**
 * Canvas overlays: translated pages for canvas-based readers (BookWalker and
 * similar) are drawn as sibling overlays positioned over the canvas, since a
 * canvas's pixels cannot be replaced from the page.
 */
import { createImageOverlay } from '../lib/overlay-dom';
import { clearBookWalkerQueueState } from './bookwalker';
import { getCanvasLogId, getCanvasOverlayHost } from './canvas-probe';
import {
  BLACK_IMAGE_MESSAGE,
  CANVAS_OVERLAY_CLASS,
  CANVAS_OVERLAY_STYLE,
  CANVAS_SOURCE_ATTR,
  CANVAS_TRANSLATION_KEY_ATTR,
} from './constants';
import {
  ensurePositioned,
  isImageMostlyBlack,
  resetTranslationState,
  saveOriginalStyle,
} from './dom';
import { clearImageRetryBlock } from './retry';
import { state } from './state';
import { hideImageProgress, showImageError } from './ui';
import { debugLog, generateImageId } from './utils';

export function canvasOverlaySelector(overlayId: string): string {
  return `.${CANVAS_OVERLAY_CLASS}[data-manga-canvas-overlay-id="${overlayId}"]`;
}

export function removeCanvasOverlays(overlayId: string): void {
  for (const overlay of document.querySelectorAll(canvasOverlaySelector(overlayId))) {
    overlay.remove();
  }
}

export function ensureCanvasOverlayId(canvas: HTMLElement): string {
  if (!canvas.dataset.mangaCanvasOverlayId) {
    canvas.dataset.mangaCanvasOverlayId = generateImageId();
  }
  return canvas.dataset.mangaCanvasOverlayId;
}

/** Remove a canvas's translation, overlay, queue state, and every mark it left. */
export function clearCanvasTranslation(canvas: HTMLCanvasElement): void {
  resetTranslationState(canvas);
  clearBookWalkerQueueState(canvas);
  canvas.removeAttribute(CANVAS_SOURCE_ATTR);
  canvas.removeAttribute(CANVAS_TRANSLATION_KEY_ATTR);
  hideImageProgress(canvas);
  delete canvas.dataset.mangaCanvasTranslationKey;
  delete canvas.dataset.mangaCanvasWidth;
  delete canvas.dataset.mangaCanvasHeight;
  delete canvas.dataset.mangaCanvasLogId;
  delete canvas.dataset.mangaTranslatedSrc;
  delete canvas.dataset.mangaLastRestoreTime;
  clearImageRetryBlock(canvas, 'canvas');
  state.canvasElementRetryBlocks.delete(canvas);

  const overlayId = canvas.dataset?.mangaCanvasOverlayId;
  if (overlayId) {
    removeCanvasOverlays(overlayId);
    delete canvas.dataset.mangaCanvasOverlayId;
  }
}

/** Line an overlay up with its canvas, re-parenting it into the canvas's overlay host if needed. */
export function positionCanvasOverlay(
  canvas: HTMLCanvasElement | null | undefined,
  overlay: HTMLElement | null | undefined,
): void {
  if (!canvas || !overlay) return;

  const preferredHost = getCanvasOverlayHost(canvas);
  if (!preferredHost) return;
  if (!overlay.parentElement && !overlay.isConnected) return;

  ensurePositioned(preferredHost);
  if (overlay.parentElement && overlay.parentElement !== preferredHost) {
    preferredHost.appendChild(overlay);
  }

  const host = overlay.parentElement || preferredHost;
  const canvasRect = canvas.getBoundingClientRect();
  const hostRect = host.getBoundingClientRect();

  overlay.style.left = `${canvasRect.left - hostRect.left + host.scrollLeft}px`;
  overlay.style.top = `${canvasRect.top - hostRect.top + host.scrollTop}px`;
  overlay.style.width = `${canvasRect.width}px`;
  overlay.style.height = `${canvasRect.height}px`;
}

function rejectCanvasTranslatedOverlay(
  canvas: HTMLCanvasElement,
  overlay: HTMLElement | null,
  message: string,
): void {
  if (overlay) {
    overlay.style.display = 'none';
  }
  clearCanvasTranslation(canvas);
  showImageError(canvas, message);
}

interface MountOptions {
  restoring?: boolean;
  loadErrorMessage: string;
  logArgs: unknown[];
}

/** Returns the mounted overlay, or null when the image was already loaded and rejected. */
function mountCanvasOverlay(
  canvas: HTMLCanvasElement,
  translatedSrc: string,
  overlayId: string,
  { restoring = false, loadErrorMessage, logArgs }: MountOptions,
): HTMLElement | null {
  const host = getCanvasOverlayHost(canvas);
  if (getComputedStyle(host).position === 'static') {
    if (!restoring) saveOriginalStyle(host, 'position');
    host.style.position = 'relative';
  }

  const { host: overlay, image: overlayImg } = createImageOverlay({
    className: `${CANVAS_OVERLAY_CLASS} manga-translated-img-overlay`,
    styleText: CANVAS_OVERLAY_STYLE,
  });
  overlay.dataset.mangaCanvasOverlayId = overlayId;

  positionCanvasOverlay(canvas, overlay);

  const reveal = (...logSuffix: unknown[]): boolean => {
    if (isImageMostlyBlack(overlayImg)) {
      rejectCanvasTranslatedOverlay(canvas, overlay, BLACK_IMAGE_MESSAGE);
      return false;
    }
    positionCanvasOverlay(canvas, overlay);
    overlay.style.display = 'block';
    debugLog(...logArgs, ...logSuffix);
    return true;
  };

  overlayImg.onload = () => reveal();
  overlayImg.onerror = () => {
    rejectCanvasTranslatedOverlay(canvas, overlay, loadErrorMessage);
  };

  state.isRestoringOverlay = restoring;
  host.appendChild(overlay);
  overlayImg.src = translatedSrc;
  state.isRestoringOverlay = false;

  // If image is already loaded (cached/blob URL), trigger display immediately
  // so the user sees the translated image right after it is applied.
  if (overlayImg.complete && overlayImg.naturalWidth > 0 && !reveal('(cached)')) {
    return null;
  }
  return overlay;
}

/** Put a canvas's overlay back from the stored translated source (rate limited to once per 3 s). */
export function restoreCanvasOverlay(canvas: HTMLCanvasElement): boolean {
  const translatedSrc = canvas.dataset.mangaTranslatedSrc;
  const overlayId = canvas.dataset.mangaCanvasOverlayId;
  if (!translatedSrc || !overlayId) return false;

  const lastRestore = Number.parseInt(canvas.dataset.mangaLastRestoreTime || '0', 10);
  const now = Date.now();
  if (now - lastRestore < 3000) {
    return true;
  }
  canvas.dataset.mangaLastRestoreTime = now.toString();

  return (
    mountCanvasOverlay(canvas, translatedSrc, overlayId, {
      restoring: true,
      loadErrorMessage: 'Failed to restore translated canvas image',
      logArgs: ['Canvas overlay restored'],
    }) !== null
  );
}

/** Show a translated image over a canvas. False when the image was rejected. */
export function applyCanvasTranslatedOverlay(
  canvas: HTMLCanvasElement,
  translatedSrc: string,
  imageId = '',
): boolean {
  const overlayId = ensureCanvasOverlayId(canvas);
  canvas.dataset.mangaTranslatedSrc = translatedSrc;
  removeCanvasOverlays(overlayId);

  const overlay = mountCanvasOverlay(canvas, translatedSrc, overlayId, {
    loadErrorMessage: 'Failed to load translated canvas image',
    logArgs: ['Canvas translated overlay applied', imageId || getCanvasLogId(canvas)],
  });
  if (!overlay) return false;

  requestAnimationFrame(() => {
    positionCanvasOverlay(canvas, overlay);
    setTimeout(() => positionCanvasOverlay(canvas, overlay), 100);
  });
  return true;
}
