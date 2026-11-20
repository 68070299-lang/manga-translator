/**
 * Translated image source helpers.
 *
 * Provides `getTranslatedSrc` and `revokeTranslatedSrc` for converting translated
 * base64 image data into blob URLs that the DOM can render. The blob URL is
 * cached on the store element's dataset so repeated calls with the same
 * base64 reuse the existing URL instead of re-encoding.
 */
import { base64ToBlob } from './utils';

/** Source of a translated image for the DOM, or `null` for empty input. */
export function getTranslatedSrc(
  storeElement: HTMLElement | null | undefined,
  base64: string | null | undefined,
  mimeType?: string,
): string | null {
  if (!base64) return null;

  if (base64.startsWith('data:') || base64.startsWith('blob:')) {
    return base64;
  }

  const dataset = storeElement?.dataset;
  const cachedBase64 = dataset?.mangaTranslatedBase64;
  const cachedUrl = dataset?.mangaTranslatedUrl;

  if (cachedBase64 === base64 && cachedUrl?.startsWith('blob:')) {
    return cachedUrl;
  }

  if (cachedUrl?.startsWith('blob:')) {
    URL.revokeObjectURL(cachedUrl);
  }

  const blob = base64ToBlob(base64, mimeType || 'image/png');
  const url = URL.createObjectURL(blob);

  if (dataset) {
    dataset.mangaTranslatedUrl = url;
    dataset.mangaTranslatedBase64 = base64;
  }

  return url;
}

/**
 * Revoke the blob URL cached on a store element and clean up associated
 * dataset keys. Call this when the translated image is no longer needed
 * (e.g. when clearing a canvas translation overlay).
 */
export function revokeTranslatedSrc(storeElement: HTMLElement | null | undefined): void {
  const dataset = storeElement?.dataset;
  if (!dataset) return;

  const url = dataset.mangaTranslatedUrl;
  if (url?.startsWith('blob:')) {
    URL.revokeObjectURL(url);
  }
  delete dataset.mangaTranslatedUrl;
  delete dataset.mangaTranslatedBase64;
}
