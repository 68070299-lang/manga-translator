import { beforeEach, describe, expect, it, type MockInstance, vi } from 'vitest';
import { getTranslatedSrc, revokeTranslatedSrc } from '../../extension/lib/translated-image';

const TINY_PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=';
const TINY_PNG_BASE64_ALT =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkZAAAAAYAAjCB0C8AAAAASUVORK5CYII=';

let createObjectURL: MockInstance<(obj: Blob | MediaSource) => string>;
let revokeObjectURL: MockInstance<(url: string) => void>;

beforeEach(() => {
  // happy-dom may not implement object URLs; make sure the statics exist, then spy on them.
  if (typeof URL.createObjectURL !== 'function') {
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: () => '',
    });
  }
  if (typeof URL.revokeObjectURL !== 'function') {
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      writable: true,
      value: () => {},
    });
  }
  let counter = 0;
  createObjectURL = vi
    .spyOn(URL, 'createObjectURL')
    .mockImplementation(() => `blob:mock/${++counter}`);
  revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
});

describe('translated image source cache', () => {
  it('returns null for empty base64', () => {
    expect(getTranslatedSrc(null, '', 'image/png')).toBeNull();
  });

  it('returns a data URL as-is without decoding', () => {
    const dataUrl = 'data:image/png;base64,abc';
    expect(getTranslatedSrc(null, dataUrl, 'image/png')).toBe(dataUrl);
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it('returns a blob URL as-is without decoding', () => {
    const blobUrl = 'blob:http://example.com/abc';
    expect(getTranslatedSrc(null, blobUrl, 'image/png')).toBe(blobUrl);
    expect(createObjectURL).not.toHaveBeenCalled();
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
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).not.toHaveBeenCalled();
  });

  it('revokes the previous URL and creates a new one when the base64 changes', () => {
    const store = document.createElement('canvas');
    const firstUrl = getTranslatedSrc(store, TINY_PNG_BASE64, 'image/png');

    const secondUrl = getTranslatedSrc(store, TINY_PNG_BASE64_ALT, 'image/png');

    expect(secondUrl).toMatch(/^blob:/);
    expect(secondUrl).not.toBe(firstUrl);
    expect(revokeObjectURL).toHaveBeenCalledWith(firstUrl);
    expect(store.dataset.mangaTranslatedBase64).toBe(TINY_PNG_BASE64_ALT);
  });

  it('decodes the base64 into a real Blob on first call', () => {
    const store = document.createElement('canvas');
    getTranslatedSrc(store, TINY_PNG_BASE64, 'image/png');

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    const [blob] = createObjectURL.mock.calls[0] as [Blob];
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe('image/png');
    expect(blob.size).toBeGreaterThan(0);
  });
});

describe('revokeTranslatedSrc', () => {
  it('revokes the blob URL and clears dataset keys', () => {
    const store = document.createElement('canvas');
    const url = getTranslatedSrc(store, TINY_PNG_BASE64, 'image/png');
    expect(url).toMatch(/^blob:/);

    revokeTranslatedSrc(store);

    expect(revokeObjectURL).toHaveBeenCalledWith(url);
    expect(store.dataset.mangaTranslatedUrl).toBeUndefined();
    expect(store.dataset.mangaTranslatedBase64).toBeUndefined();
  });

  it('does nothing for a null element', () => {
    expect(() => revokeTranslatedSrc(null)).not.toThrow();
  });

  it('does nothing when no blob URL is cached', () => {
    const store = document.createElement('canvas');
    revokeTranslatedSrc(store);
    expect(revokeObjectURL).not.toHaveBeenCalled();
  });

  it('does not revoke data URLs', () => {
    const store = document.createElement('canvas');
    store.dataset.mangaTranslatedUrl = 'data:image/png;base64,abc';
    revokeTranslatedSrc(store);
    expect(revokeObjectURL).not.toHaveBeenCalled();
    expect(store.dataset.mangaTranslatedUrl).toBeUndefined();
  });
});
