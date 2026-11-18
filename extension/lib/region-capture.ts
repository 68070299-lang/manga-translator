/**
 * Geometry for translating a dragged region of the visible tab.
 *
 * The selection is in CSS pixels and the screenshot is in device pixels. The
 * scale is measured from the screenshot itself because devicePixelRatio does
 * not always describe it, for example under browser zoom.
 */
import type { PixelRegion, Point, Size, ViewportRect } from '../shared/types';

export const MIN_SELECTION_CSS_PIXELS = 16;

/** Turn a drag from `start` to `end` into a rectangle clamped to the viewport. */
export function normalizeSelection(start: Point, end: Point, viewport: Size): ViewportRect {
  const clampX = (value: number) => Math.max(0, Math.min(viewport.width, value));
  const clampY = (value: number) => Math.max(0, Math.min(viewport.height, value));
  const left = Math.min(clampX(start.x), clampX(end.x));
  const top = Math.min(clampY(start.y), clampY(end.y));
  return {
    left,
    top,
    width: Math.abs(clampX(end.x) - clampX(start.x)),
    height: Math.abs(clampY(end.y) - clampY(start.y)),
  };
}

export function isSelectionLargeEnough(rect: Size): boolean {
  return rect.width >= MIN_SELECTION_CSS_PIXELS && rect.height >= MIN_SELECTION_CSS_PIXELS;
}

/**
 * Map a CSS-pixel selection onto whole screenshot pixels. Throws on invalid
 * input or a selection outside the capture.
 */
export function calibrateCaptureRegion(
  rect: ViewportRect | null | undefined,
  viewport: Size | null | undefined,
  capture: Size | null | undefined,
): PixelRegion {
  const values = [
    rect?.left,
    rect?.top,
    rect?.width,
    rect?.height,
    viewport?.width,
    viewport?.height,
    capture?.width,
    capture?.height,
  ];
  if (!rect || !viewport || !capture || !values.every(Number.isFinite)) {
    throw new Error('Invalid region selection');
  }
  if (viewport.width <= 0 || viewport.height <= 0) {
    throw new Error('Invalid region selection');
  }

  const scaleX = capture.width / viewport.width;
  const scaleY = capture.height / viewport.height;
  const x = Math.max(0, Math.round(rect.left * scaleX));
  const y = Math.max(0, Math.round(rect.top * scaleY));
  const right = Math.min(capture.width, Math.round((rect.left + rect.width) * scaleX));
  const bottom = Math.min(capture.height, Math.round((rect.top + rect.height) * scaleY));

  if (right <= x || bottom <= y) {
    throw new Error('Region selection is outside the visible tab');
  }
  return { x, y, width: right - x, height: bottom - y };
}
