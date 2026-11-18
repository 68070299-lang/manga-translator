/**
 * Page memory of canvas translations. A reader that redraws a page it already
 * translated (BookWalker revisiting a page, a resize) gets the stored result
 * back without another round trip; changed content clears the stale overlay.
 */

import {
  canvasDimensionsChanged,
  getCanvasDimensions,
  getCanvasTranslationKey,
  getStoredCanvasSignature,
  isBookWalkerViewer,
  probeCanvas,
  storeCanvasDimensions,
  storeCanvasTranslationKey,
} from './canvas-probe';
import { clearCanvasTranslation } from './canvas-reader';
import {
  CANVAS_SOURCE_ATTR,
  CANVAS_TRANSLATION_MEMORY_MAX,
  PROCESSING_ATTR,
  TRANSLATION_ATTR,
} from './constants';
import { replaceWithTranslatedImage } from './render';
import { state } from './state';
import { renderTranslationOverlay } from './svg-overlay';
import type { CanvasMemoryBody, CanvasMemoryRecord, ResultInput } from './types';
import { hideImageProgress } from './ui';
import { debugLog, lruSet } from './utils';

function canvasTranslationMemorySet(key: string, record: CanvasMemoryRecord | null): void {
  if (key && record)
    lruSet(state.canvasTranslationMemory, key, record, CANVAS_TRANSLATION_MEMORY_MAX);
}

function normalizeCanvasTranslationResult(
  result: ResultInput | null | undefined,
): CanvasMemoryBody | null {
  if (!Array.isArray(result) && result?.translatedImage) {
    return {
      type: 'image',
      translatedImage: result.translatedImage,
      mimeType: result.mimeType || 'image/png',
    };
  }

  const blocks = Array.isArray(result) ? result : result?.blocks;
  return Array.isArray(blocks) && blocks.length > 0 ? { type: 'blocks', blocks } : null;
}

/** Keep a canvas's translation so a redraw of the same page can restore it. */
export function rememberCanvasTranslation(
  canvas: HTMLCanvasElement,
  result: ResultInput | null | undefined,
): void {
  const body = normalizeCanvasTranslationResult(result);
  if (!body) return;

  const sourceSignature = getStoredCanvasSignature(canvas);
  const { width, height } = getCanvasDimensions(canvas);
  const record: CanvasMemoryRecord = {
    ...body,
    sourceSignature,
    canvasWidth: width,
    canvasHeight: height,
  };

  canvasTranslationMemorySet(
    canvas.dataset?.mangaCanvasTranslationKey || getCanvasTranslationKey(canvas),
    record,
  );
  canvasTranslationMemorySet(sourceSignature, record);
}

/** Redraw a remembered translation on a canvas; false when there is none or it no longer fits. */
export function restoreCanvasTranslationFromMemory(canvas: HTMLCanvasElement): boolean {
  const translationKey =
    canvas.dataset?.mangaCanvasTranslationKey || getCanvasTranslationKey(canvas);
  const sourceSignature = probeCanvas(canvas).signature;
  const dimensions = getCanvasDimensions(canvas);
  const { width, height } = dimensions;
  const sourceRecord = state.canvasTranslationMemory.get(sourceSignature);
  const keyRecord = state.canvasTranslationMemory.get(translationKey);
  // A BookWalker canvas/viewport id is a reusable display slot, not a page
  // identity. Prefer the exact captured source so translating a later page in
  // the same slot does not hide an older page's still-valid memory record.
  const record =
    sourceRecord?.sourceSignature === sourceSignature ? sourceRecord : keyRecord || sourceRecord;
  if (!record) return false;

  if (
    record.sourceSignature &&
    sourceSignature &&
    record.sourceSignature !== sourceSignature &&
    record.canvasWidth === width &&
    record.canvasHeight === height
  ) {
    return false;
  }

  storeCanvasTranslationKey(canvas, translationKey);
  canvas.setAttribute(CANVAS_SOURCE_ATTR, sourceSignature);
  storeCanvasDimensions(canvas, dimensions);

  let restored = true;
  if (record.type === 'image') {
    restored = replaceWithTranslatedImage(canvas, record.translatedImage, record.mimeType);
  } else {
    renderTranslationOverlay(canvas, record.blocks);
  }

  if (!restored) {
    clearCanvasTranslation(canvas);
    return false;
  }

  canvas.setAttribute(TRANSLATION_ATTR, 'true');
  Object.assign(record, { sourceSignature, canvasWidth: width, canvasHeight: height });
  canvasTranslationMemorySet(sourceSignature, record);
  canvasTranslationMemorySet(translationKey, record);
  debugLog('Restored canvas translation from page memory');
  return true;
}

/**
 * Clear a canvas's translation when the page it shows changed. Returns true
 * when it cleared; BookWalker resizes of the same page are kept (and re-keyed).
 */
export function clearCanvasTranslationIfChanged(canvas: HTMLCanvasElement): boolean {
  if (
    !canvas.hasAttribute(TRANSLATION_ATTR) &&
    !canvas.hasAttribute(PROCESSING_ATTR) &&
    !canvas.dataset?.mangaCanvasOverlayId
  ) {
    return false;
  }

  const previousSignature = canvas.getAttribute(CANVAS_SOURCE_ATTR);
  if (!previousSignature) return false;

  const current = probeCanvas(canvas).signature;
  if (!current || current === previousSignature) return false;

  if (isBookWalkerViewer()) {
    const previousKey = canvas.dataset?.mangaCanvasTranslationKey || '';
    if (
      previousKey &&
      previousKey === getCanvasTranslationKey(canvas) &&
      canvasDimensionsChanged(canvas)
    ) {
      const dimensions = getCanvasDimensions(canvas);
      canvas.setAttribute(CANVAS_SOURCE_ATTR, current);
      storeCanvasDimensions(canvas, dimensions);

      const record = state.canvasTranslationMemory.get(previousKey);
      if (record) {
        canvasTranslationMemorySet(previousKey, {
          ...record,
          sourceSignature: current,
          canvasWidth: dimensions.width,
          canvasHeight: dimensions.height,
        });
      }

      return false;
    }
  }

  clearCanvasTranslation(canvas);
  hideImageProgress(canvas);
  return true;
}
