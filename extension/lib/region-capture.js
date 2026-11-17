/**
 * Geometry for translating a dragged region of the visible tab.
 *
 * The selection is in CSS pixels and the screenshot is in device pixels. The
 * scale is measured from the screenshot itself because devicePixelRatio does
 * not always describe it, for example under browser zoom.
 */
(function(root) {
  'use strict';

  const MIN_SELECTION_CSS_PIXELS = 16;

  function normalizeSelection(start, end, viewport) {
    const clampX = value => Math.max(0, Math.min(viewport.width, value));
    const clampY = value => Math.max(0, Math.min(viewport.height, value));
    const left = Math.min(clampX(start.x), clampX(end.x));
    const top = Math.min(clampY(start.y), clampY(end.y));
    return {
      left,
      top,
      width: Math.abs(clampX(end.x) - clampX(start.x)),
      height: Math.abs(clampY(end.y) - clampY(start.y))
    };
  }

  function isSelectionLargeEnough(rect) {
    return rect.width >= MIN_SELECTION_CSS_PIXELS && rect.height >= MIN_SELECTION_CSS_PIXELS;
  }

  /**
   * Map a CSS-pixel selection onto whole screenshot pixels.
   * @returns {{x: number, y: number, width: number, height: number}}
   */
  function calibrateCaptureRegion(rect, viewport, capture) {
    const values = [
      rect?.left, rect?.top, rect?.width, rect?.height,
      viewport?.width, viewport?.height, capture?.width, capture?.height
    ];
    if (!values.every(Number.isFinite) || viewport.width <= 0 || viewport.height <= 0) {
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

  const exported = {
    MIN_SELECTION_CSS_PIXELS,
    normalizeSelection,
    isSelectionLargeEnough,
    calibrateCaptureRegion
  };

  if (root) {
    root.MangaTranslatorRegionCapture = exported;
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = exported;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
