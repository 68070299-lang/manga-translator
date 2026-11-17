/**
 * Reconciling canvas readers with the page: overlays follow their canvases,
 * stale translations are cleared, lost overlays are restored, and (with
 * auto-translate) visible canvases are translated or queued.
 */

import { getActiveBookWalkerCanvases, renderBookWalkerQueueState } from './bookwalker';
import { enqueueBookWalkerCanvas } from './bookwalker-queue';
import {
  clearCanvasTranslationIfChanged,
  restoreCanvasTranslationFromMemory,
} from './canvas-memory';
import { isBookWalkerViewer } from './canvas-probe';
import {
  canvasOverlaySelector,
  clearCanvasTranslation,
  positionCanvasOverlay,
  restoreCanvasOverlay,
} from './canvas-reader';
import {
  BOOKWALKER_QUEUE_ATTR,
  CANVAS_OVERLAY_CLASS,
  CANVAS_READER_SELECTOR,
  PROCESSING_ATTR,
  TRANSLATION_ATTR,
} from './constants';
import { isCanvasElementRetryBlocked, isImageRetryBlocked } from './retry';
import { state } from './state';
import { processImage } from './translate';
import { hideImageProgress } from './ui';
import { debugLog } from './utils';
import { isCanvasVisible, isPotentialMangaCanvas } from './visibility';

/** Auto-translate is on, the site is enabled, and no run is in progress. */
export function canAutoProcessImages(): boolean {
  return state.siteEnabled && Boolean(state.settings?.autoTranslate) && !state.isProcessing;
}

function isCanvasReaderNode(node: Node | null | undefined): boolean {
  if (!node || node.nodeType !== Node.ELEMENT_NODE) return false;
  const element = node as Element;
  return Boolean(
    element.matches?.(CANVAS_READER_SELECTOR) || element.querySelector?.(CANVAS_READER_SELECTOR),
  );
}

/** True when a DOM mutation touches a canvas reader (so overlays may need reconciling). */
export function mutationTouchesCanvasReader(mutation: MutationRecord): boolean {
  return (
    isCanvasReaderNode(mutation.target) ||
    Array.from(mutation.addedNodes || []).some((node) => isCanvasReaderNode(node))
  );
}

/** Keep canvas overlays attached and positioned; BookWalker also gets lost overlays restored. */
export function syncCanvasOverlays(): void {
  for (const overlay of document.querySelectorAll<HTMLElement>(`.${CANVAS_OVERLAY_CLASS}`)) {
    const overlayId = overlay.dataset.mangaCanvasOverlayId;
    const canvas = overlayId
      ? document.querySelector<HTMLCanvasElement>(
          `canvas[data-manga-canvas-overlay-id="${overlayId}"]`,
        )
      : null;

    if (canvas?.isConnected) {
      positionCanvasOverlay(canvas, overlay);
    } else {
      overlay.remove();
    }
  }

  // BookWalker's viewer sometimes removes foreign DOM elements, so translated
  // canvases that lost their overlay get it back from what was stored.
  if (isBookWalkerViewer()) {
    for (const canvas of document.querySelectorAll<HTMLCanvasElement>(
      `canvas[${TRANSLATION_ATTR}]`,
    )) {
      const overlayId = canvas.dataset?.mangaCanvasOverlayId;
      if (!overlayId) {
        debugLog('Canvas translated but has no overlay id; trying to restore from memory');
        if (!restoreCanvasTranslationFromMemory(canvas)) {
          clearCanvasTranslation(canvas);
        }
      } else if (!document.querySelector(canvasOverlaySelector(overlayId))) {
        debugLog('Canvas overlay missing; restoring from stored translation');
        if (!restoreCanvasOverlay(canvas)) {
          clearCanvasTranslation(canvas);
        }
      }
    }
  }
}

/** Debounced `reconcileVisibleCanvases`. */
export function scheduleCanvasReconcile(autoProcess = false, delayMs = 120): void {
  if (!state.siteEnabled) return;
  if (state.canvasReconcileDebounce !== null) clearTimeout(state.canvasReconcileDebounce);
  state.canvasReconcileDebounce = setTimeout(() => {
    reconcileVisibleCanvases(autoProcess);
  }, delayMs);
}

export function reconcileVisibleCanvases(autoProcess = false): void {
  if (!state.siteEnabled) return;
  syncCanvasOverlays();

  const isBookWalker = isBookWalkerViewer();
  const activeBookWalkerCanvases = isBookWalker ? new Set(getActiveBookWalkerCanvases()) : null;
  const allowAutoProcess =
    autoProcess && (isBookWalker ? Boolean(state.settings?.autoTranslate) : canAutoProcessImages());
  for (const canvas of document.querySelectorAll<HTMLCanvasElement>('canvas')) {
    if (!isPotentialMangaCanvas(canvas)) continue;

    if (isBookWalker && !activeBookWalkerCanvases?.has(canvas)) {
      if (canvas.hasAttribute(PROCESSING_ATTR) || canvas.hasAttribute(BOOKWALKER_QUEUE_ATTR)) {
        renderBookWalkerQueueState(canvas);
        continue;
      }
      hideImageProgress(canvas);
      if (canvas.hasAttribute(TRANSLATION_ATTR) || canvas.dataset?.mangaCanvasOverlayId) {
        clearCanvasTranslation(canvas);
      }
      continue;
    }

    if (clearCanvasTranslationIfChanged(canvas)) {
      debugLog('Canvas content changed; cleared stale translation overlay');
    }

    if (isBookWalker) {
      renderBookWalkerQueueState(canvas);
    }

    if (
      (isBookWalker && isImageRetryBlocked(canvas, 'canvas')) ||
      isCanvasElementRetryBlocked(canvas)
    ) {
      continue;
    }

    if (
      canvas.hasAttribute(TRANSLATION_ATTR) ||
      canvas.hasAttribute(PROCESSING_ATTR) ||
      !isCanvasVisible(canvas)
    ) {
      continue;
    }

    if (!isBookWalker) {
      if (allowAutoProcess) void processImage(canvas, 'canvas', null, null, { skipScroll: true });
    } else if (
      !restoreCanvasTranslationFromMemory(canvas) &&
      allowAutoProcess &&
      !canvas.hasAttribute(BOOKWALKER_QUEUE_ATTR)
    ) {
      enqueueBookWalkerCanvas(canvas, 'reconcile-visible');
    }
  }
}
