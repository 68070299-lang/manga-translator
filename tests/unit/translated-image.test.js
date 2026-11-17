/**
 * @jest-environment jsdom
 */
const { base64ToBlob } = require('../../extension/lib/utils');
globalThis.base64ToBlob = base64ToBlob;

const { getTranslatedSrc, revokeTranslatedSrc } = require('../../extension/lib/translated-image');

const TINY_PNG_BASE64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=';
const TINY_PNG_BASE64_ALT = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkZAAAAAYAAjCB0C8AAAAASUVORK5CYII=';

if (typeof URL.createObjectURL !== 'function') {
  let counter = 0;
  URL.createObjectURL = jest.fn(() => `blob:mock/${++counter}`);
  URL.revokeObjectURL = jest.fn();
}

describe('translated image source cache', () => {
  afterEach(() => {
    URL.createObjectURL.mockClear && URL.createObjectURL.mockClear();
    URL.revokeObjectURL.mockClear && URL.revokeObjectURL.mockClear();
  });

  it('returns null for empty base64', () => {
    expect(getTranslatedSrc(null, '', 'image/png')).toBeNull();
  });

  it('returns a data URL as-is without decoding', () => {
    const dataUrl = 'data:image/png;base64,abc';
    expect(getTranslatedSrc(null, dataUrl, 'image/png')).toBe(dataUrl);
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it('returns a blob URL as-is without decoding', () => {
    const blobUrl = 'blob:http://example.com/abc';
    expect(getTranslatedSrc(null, blobUrl, 'image/png')).toBe(blobUrl);
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it('creates a new blob URL on first call and stores it on the element dataset', () => {
    const store = document.createElement('canvas');
    const url = getTranslatedSrc(store, TINY_PNG_BASE64, 'image/png');
    expect(url).toMatch(/^blob:/);
    expect(store.dataset.mangaTranslatedUrl).toBe(url);
    expect(store.dataset.mangaTranslatedBase64).toBe(TINY_PNG_BASE64);
  });

  it('reuses the cached blob URL when called again with the same base64', () => {
    const store = document.createElement('canvas');
    const firstUrl = getTranslatedSrc(store, TINY_PNG_BASE64, 'image/png');

    const secondUrl = getTranslatedSrc(store, TINY_PNG_BASE64, 'image/png');

    expect(secondUrl).toBe(firstUrl);
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
  });

  it('revokes the previous URL and creates a new one when the base64 changes', () => {
    const store = document.createElement('canvas');
    const firstUrl = getTranslatedSrc(store, TINY_PNG_BASE64, 'image/png');

    const secondUrl = getTranslatedSrc(store, TINY_PNG_BASE64_ALT, 'image/png');

    expect(secondUrl).toMatch(/^blob:/);
    expect(secondUrl).not.toBe(firstUrl);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith(firstUrl);
    expect(store.dataset.mangaTranslatedBase64).toBe(TINY_PNG_BASE64_ALT);
  });

  it('decodes the base64 into a real Blob on first call', () => {
    const store = document.createElement('canvas');
    getTranslatedSrc(store, TINY_PNG_BASE64, 'image/png');

    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    const [blob] = URL.createObjectURL.mock.calls[0];
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe('image/png');
    expect(blob.size).toBeGreaterThan(0);
  });
});

describe('revokeTranslatedSrc', () => {
  afterEach(() => {
    URL.createObjectURL.mockClear && URL.createObjectURL.mockClear();
    URL.revokeObjectURL.mockClear && URL.revokeObjectURL.mockClear();
  });

  it('revokes the blob URL and clears dataset keys', () => {
    const store = document.createElement('canvas');
    const url = getTranslatedSrc(store, TINY_PNG_BASE64, 'image/png');
    expect(url).toMatch(/^blob:/);

    revokeTranslatedSrc(store);

    expect(URL.revokeObjectURL).toHaveBeenCalledWith(url);
    expect(store.dataset.mangaTranslatedUrl).toBeUndefined();
    expect(store.dataset.mangaTranslatedBase64).toBeUndefined();
  });

  it('does nothing for a null element', () => {
    expect(() => revokeTranslatedSrc(null)).not.toThrow();
  });

  it('does nothing when no blob URL is cached', () => {
    const store = document.createElement('canvas');
    revokeTranslatedSrc(store);
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
  });

  it('does not revoke data URLs', () => {
    const store = document.createElement('canvas');
    store.dataset.mangaTranslatedUrl = 'data:image/png;base64,abc';
    revokeTranslatedSrc(store);
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    expect(store.dataset.mangaTranslatedUrl).toBeUndefined();
  });
});
