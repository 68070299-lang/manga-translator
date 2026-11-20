/**
 * Read-only inspection of canvas elements: sampled signatures, stored
 * identity (translation key, dimensions) and the BookWalker viewer check.
 */
import { CANVAS_SOURCE_ATTR, CANVAS_TRANSLATION_KEY_ATTR, PROCESSING_ATTR } from './constants';
import type { CanvasProbe } from './types';
import { hashString } from './utils';

/** True on BookWalker's viewer, which renders pages into canvases. */
export function isBookWalkerViewer(): boolean {
  return (
    /(^|\.)viewer(?:-trial)?\.bookwalker\.jp$/i.test(window.location.hostname) ||
    Boolean(
      document.querySelector('#renderer .currentScreen canvas, #renderer [id^="viewport"] canvas'),
    )
  );
}

/**
 * Sample a canvas down to 64x64 and hash it, so changed page content can be
 * told apart without reading the full-size pixels.
 */
export function probeCanvas(canvas: HTMLCanvasElement): CanvasProbe {
  const width = canvas.width || canvas.clientWidth;
  const height = canvas.height || canvas.clientHeight;
  if (!width || !height) {
    return { signature: '', blank: true };
  }

  const sampleSize = 64;
  const sample = document.createElement('canvas');
  sample.width = sampleSize;
  sample.height = sampleSize;

  try {
    const ctx = sample.getContext('2d', { willReadFrequently: true });
    if (!ctx) throw new Error('2D canvas context unavailable');
    ctx.drawImage(canvas, 0, 0, sampleSize, sampleSize);
    const data = ctx.getImageData(0, 0, sampleSize, sampleSize).data;
    let hash = 2166136261;
    let opaque = 0;
    let min = 255;
    let max = 0;

    for (let i = 0; i < data.length; i += 4) {
      const r = data[i] ?? 0;
      const g = data[i + 1] ?? 0;
      const b = data[i + 2] ?? 0;
      const lum = Math.round(r * 0.299 + g * 0.587 + b * 0.114);
      min = Math.min(min, lum);
      max = Math.max(max, lum);
      if ((data[i + 3] ?? 0) > 0) opaque++;

      for (let channel = 0; channel < 4; channel++) {
        hash ^= data[i + channel] ?? 0;
        hash = Math.imul(hash, 16777619);
      }
    }

    const signature = [width, height, (hash >>> 0).toString(36), opaque, min, max].join(':');
    const blank = opaque === 0 || max - min < 3;

    return { signature, blank };
  } catch (error) {
    return {
      signature: [width, height, 'unreadable'].join(':'),
      blank: false,
      unreadable: true,
      error,
    };
  }
}

/** The signature recorded when the canvas was captured, or a fresh probe of it. */
export function getStoredCanvasSignature(canvas: HTMLCanvasElement): string {
  return canvas.getAttribute(CANVAS_SOURCE_ATTR) || probeCanvas(canvas).signature;
}

/** Cache identity for a canvas capture: size plus a hash of the captured bytes. */
export async function createCanvasCacheSignature(
  canvas: HTMLCanvasElement,
  base64: string,
): Promise<string> {
  const hash = await hashString(base64);
  return `canvas:${canvas.width || canvas.clientWidth || 0}:${canvas.height || canvas.clientHeight || 0}:${hash}`;
}

/** Where a canvas's overlay is mounted: its reader screen, parent, or itself. */
export function getCanvasOverlayHost(canvas: HTMLCanvasElement): HTMLElement {
  return (
    canvas.closest?.<HTMLElement>('.currentScreen, [id^="viewport"]') ||
    canvas.parentElement ||
    canvas
  );
}

/**
 * Identity of a canvas page. BookWalker ids are reusable display slots, so the
 * stored translation key and the page signature are tracked separately.
 */
export function getCanvasTranslationKey(canvas: HTMLCanvasElement | null | undefined): string {
  if (!canvas) return '';

  if (isBookWalkerViewer()) {
    const id = canvas.id?.trim() || getCanvasOverlayHost(canvas).id?.trim();
    if (id) return `bookwalker:${id}`;
  }

  return getStoredCanvasSignature(canvas);
}

export function storeCanvasTranslationKey(
  canvas: HTMLCanvasElement,
  key: string = getCanvasTranslationKey(canvas),
): void {
  canvas.dataset.mangaCanvasTranslationKey = key;
  canvas.setAttribute(CANVAS_TRANSLATION_KEY_ATTR, key);
}

export interface CanvasDimensions {
  width: number;
  height: number;
}

export function getCanvasDimensions(canvas: HTMLCanvasElement): CanvasDimensions {
  return {
    width: Math.round(canvas.width || canvas.clientWidth || 0),
    height: Math.round(canvas.height || canvas.clientHeight || 0),
  };
}

export function storeCanvasDimensions(
  canvas: HTMLCanvasElement,
  { width, height }: CanvasDimensions,
): void {
  canvas.dataset.mangaCanvasWidth = String(width);
  canvas.dataset.mangaCanvasHeight = String(height);
}

export function canvasDimensionsChanged(canvas: HTMLCanvasElement): boolean {
  const { width, height } = getCanvasDimensions(canvas);
  return (
    width !== Number.parseInt(canvas.dataset?.mangaCanvasWidth || '0', 10) ||
    height !== Number.parseInt(canvas.dataset?.mangaCanvasHeight || '0', 10)
  );
}

/** True while a canvas still shows the page that was captured for its translation. */
export function canvasMatchesStoredSource(canvas: HTMLCanvasElement): boolean {
  if (isBookWalkerViewer()) {
    const previousKey = canvas.dataset?.mangaCanvasTranslationKey;
    if (previousKey) {
      if (getCanvasTranslationKey(canvas) !== previousKey) return false;
      if (canvasDimensionsChanged(canvas)) return true;
    }
  }

  const previousSignature = canvas.getAttribute(CANVAS_SOURCE_ATTR);
  if (!previousSignature) return true;

  const currentSignature = probeCanvas(canvas).signature;
  return Boolean(currentSignature && currentSignature === previousSignature);
}

/** Id used in debug logs for a canvas. */
export function getCanvasLogId(canvas: HTMLCanvasElement | null | undefined): string {
  if (!canvas) return 'img-unknown';
  return (
    canvas.dataset?.mangaCanvasLogId ||
    canvas.getAttribute(PROCESSING_ATTR) ||
    canvas.id ||
    canvas.parentElement?.id ||
    canvas.closest?.('[id]')?.id ||
    'img-unknown'
  );
}
