const {
  normalizeSelection,
  isSelectionLargeEnough,
  calibrateCaptureRegion
} = require('../../extension/lib/region-capture');

describe('region capture geometry', () => {
  const viewport = { width: 1000, height: 500 };

  it('normalizes a drag in any direction and clamps it to the viewport', () => {
    expect(normalizeSelection({ x: 300, y: 200 }, { x: 100, y: 50 }, viewport))
      .toEqual({ left: 100, top: 50, width: 200, height: 150 });
    expect(normalizeSelection({ x: -50, y: 400 }, { x: 80, y: 900 }, viewport))
      .toEqual({ left: 0, top: 400, width: 80, height: 100 });
  });

  it('rejects selections smaller than the minimum on either axis', () => {
    expect(isSelectionLargeEnough({ width: 16, height: 16 })).toBe(true);
    expect(isSelectionLargeEnough({ width: 15, height: 200 })).toBe(false);
    expect(isSelectionLargeEnough({ width: 200, height: 15 })).toBe(false);
  });

  it('scales from the screenshot size, so any device pixel ratio or zoom works', () => {
    const rect = { left: 100, top: 50, width: 200, height: 100 };
    expect(calibrateCaptureRegion(rect, viewport, { width: 1000, height: 500 }))
      .toEqual({ x: 100, y: 50, width: 200, height: 100 });
    expect(calibrateCaptureRegion(rect, viewport, { width: 2000, height: 1000 }))
      .toEqual({ x: 200, y: 100, width: 400, height: 200 });
    expect(calibrateCaptureRegion(rect, viewport, { width: 1250, height: 625 }))
      .toEqual({ x: 125, y: 63, width: 250, height: 125 });
  });

  it('keeps the crop inside the screenshot', () => {
    const rect = { left: 900, top: 400, width: 500, height: 500 };
    expect(calibrateCaptureRegion(rect, viewport, { width: 1000, height: 500 }))
      .toEqual({ x: 900, y: 400, width: 100, height: 100 });
  });

  it('throws on invalid or empty regions', () => {
    const capture = { width: 1000, height: 500 };
    expect(() => calibrateCaptureRegion(undefined, viewport, capture)).toThrow('Invalid region selection');
    expect(() => calibrateCaptureRegion({ left: 0, top: 0, width: 10, height: NaN }, viewport, capture))
      .toThrow('Invalid region selection');
    expect(() => calibrateCaptureRegion({ left: 0, top: 0, width: 10, height: 10 }, { width: 0, height: 0 }, capture))
      .toThrow('Invalid region selection');
    expect(() => calibrateCaptureRegion({ left: 2000, top: 0, width: 10, height: 10 }, viewport, capture))
      .toThrow('outside the visible tab');
  });
});
