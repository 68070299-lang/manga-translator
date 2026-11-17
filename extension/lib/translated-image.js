/**
 * Translated image source helpers.
 *
 * Provides `getTranslatedSrc` and `revokeTranslatedSrc` for converting translated
 * base64 image data into blob URLs that the DOM can render. The blob URL is
 * cached on the store element's dataset so repeated calls with the same
 * base64 reuse the existing URL instead of re-encoding.
 *
 * This file is loaded directly in the content script (assigns to
 * `globalThis.MangaTranslatorTranslatedImage`) and can also be required from
 * Jest tests.
 */
(function(root) {
  'use strict';

  function decodeBase64ToBlob(base64, mimeType) {
    if (typeof root?.base64ToBlob === 'function') {
      return root.base64ToBlob(base64, mimeType);
    }

    const base64Data = base64.replace(/^data:[^,]*;base64,/, '');
    if (!base64Data) {
      throw new Error('Invalid base64 input: empty after prefix removal');
    }

    const byteCharacters = atob(base64Data);
    const byteNumbers = new Array(byteCharacters.length);
    for (let i = 0; i < byteCharacters.length; i++) {
      byteNumbers[i] = byteCharacters.charCodeAt(i);
    }
    return new Blob([new Uint8Array(byteNumbers)], { type: mimeType });
  }

  function getTranslatedSrc(storeElement, base64, mimeType) {
    if (!base64) return null;

    if (base64.startsWith('data:') || base64.startsWith('blob:')) {
      return base64;
    }

    const dataset = storeElement && storeElement.dataset;
    const cachedBase64 = dataset && dataset.mangaTranslatedBase64;
    const cachedUrl = dataset && dataset.mangaTranslatedUrl;

    if (cachedBase64 === base64 && cachedUrl && cachedUrl.startsWith('blob:')) {
      return cachedUrl;
    }

    if (cachedUrl && cachedUrl.startsWith('blob:')) {
      URL.revokeObjectURL(cachedUrl);
    }

    const blob = decodeBase64ToBlob(base64, mimeType || 'image/png');
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
  function revokeTranslatedSrc(storeElement) {
    if (!storeElement) return;
    const dataset = storeElement.dataset;
    if (!dataset) return;

    const url = dataset.mangaTranslatedUrl;
    if (url && url.startsWith('blob:')) {
      URL.revokeObjectURL(url);
    }
    delete dataset.mangaTranslatedUrl;
    delete dataset.mangaTranslatedBase64;
  }

  const exported = {
    getTranslatedSrc,
    revokeTranslatedSrc
  };

  if (root) {
    root.MangaTranslatorTranslatedImage = exported;
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = exported;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
