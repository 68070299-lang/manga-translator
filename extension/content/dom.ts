/** DOM helpers for mutating page elements reversibly. */

import { revokeTranslatedSrc } from '../lib/translated-image';
import { PROCESSING_ATTR, TRANSLATION_ATTR } from './constants';
import { getBackgroundImageUrls } from './helpers';

const ORIGINAL_STYLE_PREFIX = 'mangaOriginalStyle';

type StyleRecord = Record<string, string>;

function computedStyleRecord(element: Element): StyleRecord {
  return getComputedStyle(element) as unknown as StyleRecord;
}

function styleRecord(element: HTMLElement): StyleRecord {
  return element.style as unknown as StyleRecord;
}

/** Make `element` a containing block for absolutely positioned overlays. */
export function ensurePositioned(element: HTMLElement): void {
  if (getComputedStyle(element).position === 'static') {
    saveOriginalStyle(element, 'position');
    element.style.position = 'relative';
  }
}

/**
 * Save original computed style values before mutation, so they can be
 * restored on clear.
 */
export function saveOriginalStyle(element: HTMLElement | null | undefined, ...props: string[]) {
  if (!element?.dataset) return;
  for (const prop of props) {
    const dataKey = `${ORIGINAL_STYLE_PREFIX}${prop.charAt(0).toUpperCase() + prop.slice(1)}`;
    if (element.dataset[dataKey] === undefined) {
      const computed = computedStyleRecord(element)[prop];
      element.dataset[dataKey] = computed || '';
    }
  }
}

/** Restore original style values that were saved before mutation. */
export function restoreOriginalStyles(element: HTMLElement | null | undefined): void {
  if (!element?.dataset) return;
  const keys = Object.keys(element.dataset).filter((key) => key.startsWith(ORIGINAL_STYLE_PREFIX));
  for (const dataKey of keys) {
    const prop = dataKey.replace(ORIGINAL_STYLE_PREFIX, '');
    const cssProp = prop.charAt(0).toLowerCase() + prop.slice(1);
    styleRecord(element)[cssProp] = element.dataset[dataKey] ?? '';
    delete element.dataset[dataKey];
  }
}

/** Undo the marks a translation leaves on an element (styles, attributes, blob URL). */
export function resetTranslationState(element: HTMLElement): void {
  restoreOriginalStyles(element);
  element.removeAttribute(TRANSLATION_ATTR);
  element.removeAttribute(PROCESSING_ATTR);
  delete element.dataset.mangaTranslationHidden;
  revokeTranslatedSrc(element);
}

/** Take an element back out of the `.manga-translate-wrapper` that SVG overlays add. */
export function unwrapFromTranslateWrapper(element: HTMLElement): void {
  const wrapper = element.parentElement;
  if (!wrapper?.classList.contains('manga-translate-wrapper')) return;
  for (const item of wrapper.querySelectorAll(
    '.manga-translate-overlay, .manga-translated-img-overlay, .manga-composite-overlay',
  )) {
    item.remove();
  }
  wrapper.parentElement?.insertBefore(element, wrapper);
  if (wrapper.children.length === 0) {
    wrapper.remove();
  }
}

/** Normalized URLs in an element's computed background-image. */
export function getElementBackgroundImageUrls(element: Element): string[] {
  return getBackgroundImageUrls(getComputedStyle(element).backgroundImage);
}

/**
 * Quick check if an image element is mostly black (indicates backend
 * rendering failure or corrupted blob). Draws a small thumbnail sample.
 */
export function isImageMostlyBlack(img: CanvasImageSource, threshold = 0.95): boolean {
  try {
    const canvas = document.createElement('canvas');
    canvas.width = 32;
    canvas.height = 32;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    if (!ctx) return false;
    ctx.drawImage(img, 0, 0, 32, 32);
    const data = ctx.getImageData(0, 0, 32, 32).data;
    let darkPixels = 0;
    for (let i = 0; i < data.length; i += 4) {
      const brightness = ((data[i] ?? 0) + (data[i + 1] ?? 0) + (data[i + 2] ?? 0)) / 3;
      if (brightness < 10) darkPixels++;
    }
    return darkPixels / (data.length / 4) > threshold;
  } catch {
    return false;
  }
}

/** Id of a page container (cmoa.jp style): its own, or its parent's. */
export function getPageId(container: Element): string | undefined {
  return container.id || container.parentElement?.id || undefined;
}
