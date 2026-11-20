import { describe, expect, it, vi } from 'vitest';
import {
  fetchLocalUrlAsBase64,
  getBackgroundImageUrls,
  getImageSourceCandidates,
  getSrcsetUrls,
  normalizeSourceUrl,
  shouldFetchInContentScript,
  sourceUrlsMatch,
} from '../../extension/content/helpers';

describe('content source helpers', () => {
  it('normalizes relative URLs against the page base URI', () => {
    expect(normalizeSourceUrl('/image.png', 'https://reader.example.com/chapter/page.html')).toBe(
      'https://reader.example.com/image.png',
    );
    expect(normalizeSourceUrl('panel.jpg', 'https://reader.example.com/chapter/page.html')).toBe(
      'https://reader.example.com/chapter/panel.jpg',
    );
    expect(normalizeSourceUrl(' data:image/png;base64,abc ')).toBe('data:image/png;base64,abc');
    expect(normalizeSourceUrl('')).toBe('');
    expect(normalizeSourceUrl(null)).toBe('');
  });

  it('compares source URLs after normalization', () => {
    const baseUri = 'https://reader.example.com/chapter/page.html';

    expect(sourceUrlsMatch('/image.png', 'https://reader.example.com/image.png', baseUri)).toBe(
      true,
    );
    expect(sourceUrlsMatch('panel-a.png', 'panel-b.png', baseUri)).toBe(false);
    expect(sourceUrlsMatch('', 'panel-b.png', baseUri)).toBe(false);
  });

  it('extracts URLs from srcset descriptors', () => {
    expect(getSrcsetUrls('small.jpg 1x, large.jpg 2x, /wide.jpg 1000w')).toEqual([
      'small.jpg',
      'large.jpg',
      '/wide.jpg',
    ]);
    expect(getSrcsetUrls('')).toEqual([]);
    expect(getSrcsetUrls(null)).toEqual([]);
  });

  it('collects normalized image source candidates from reader lazy-load attributes', () => {
    const baseUri = 'https://reader.example.com/chapter/page.html';
    const img = document.createElement('img');
    img.setAttribute('src', 'panel-a.jpg');
    img.setAttribute('data-src', '/lazy/panel-b.jpg');
    img.setAttribute('data-original', 'https://cdn.example.com/panel-c.jpg');
    img.setAttribute('data-original-src', 'panel-a.jpg');
    img.setAttribute('data-lazy-src', 'data:image/png;base64,abc');
    img.setAttribute('data-url', 'blob:https://reader.example.com/id');
    img.setAttribute('data-image', '');
    img.setAttribute('srcset', 'small.jpg 1x, /large.jpg 2x');
    img.setAttribute('data-srcset', 'small.jpg 1x, https://cdn.example.com/wide.jpg 1000w');

    Object.defineProperty(img, 'currentSrc', {
      configurable: true,
      value: 'https://reader.example.com/current.jpg',
    });
    Object.defineProperty(img, 'src', {
      configurable: true,
      value: 'https://reader.example.com/chapter/panel-a.jpg',
    });

    expect(getImageSourceCandidates(img, baseUri)).toEqual([
      'https://reader.example.com/current.jpg',
      'https://reader.example.com/chapter/panel-a.jpg',
      'https://reader.example.com/lazy/panel-b.jpg',
      'https://cdn.example.com/panel-c.jpg',
      'data:image/png;base64,abc',
      'blob:https://reader.example.com/id',
      'https://reader.example.com/chapter/small.jpg',
      'https://reader.example.com/large.jpg',
      'https://cdn.example.com/wide.jpg',
    ]);
  });

  it('extracts normalized background image URLs from CSS values', () => {
    const baseUri = 'https://reader.example.com/chapter/page.html';
    const styleValue =
      'linear-gradient(red, blue), url("panel-a.jpg"), url(/panel-b.png), url(data:image/png;base64,abc)';

    expect(getBackgroundImageUrls(styleValue, baseUri)).toEqual([
      'https://reader.example.com/chapter/panel-a.jpg',
      'https://reader.example.com/panel-b.png',
      'data:image/png;base64,abc',
    ]);
    expect(getBackgroundImageUrls('none', baseUri)).toEqual([]);
  });

  it('keeps browser-local schemes in the content script', () => {
    expect(shouldFetchInContentScript('data:image/png;base64,abc')).toBe(true);
    expect(shouldFetchInContentScript('blob:https://example.com/id')).toBe(true);
    expect(shouldFetchInContentScript('file:///C:/tmp/page.png')).toBe(true);
    expect(shouldFetchInContentScript('chrome-extension://id/page.png')).toBe(true);
    expect(shouldFetchInContentScript('https://example.com/page.png')).toBe(false);
  });

  it('extracts data URL base64 without background messaging', async () => {
    const base64 = 'aGVs bG8%3D';

    await expect(fetchLocalUrlAsBase64(`data:text/plain;base64,${base64}`)).resolves.toBe(
      'aGVsbG8=',
    );
  });

  it('fetches browser-local URLs with no-store and converts non-empty blobs', async () => {
    const blob = new Blob(['local-image'], { type: 'image/png' });
    const fetchMock = vi.fn(async () => ({
      ok: true,
      blob: async () => blob,
    })) as unknown as typeof fetch;
    const blobToBase64 = vi.fn(async (receivedBlob: Blob) => {
      expect(receivedBlob).toBe(blob);
      return 'bG9jYWwtaW1hZ2U=';
    });

    await expect(
      fetchLocalUrlAsBase64('blob:https://example.com/id', {
        fetch: fetchMock,
        blobToBase64,
      }),
    ).resolves.toBe('bG9jYWwtaW1hZ2U=');

    expect(fetchMock).toHaveBeenCalledWith('blob:https://example.com/id', {
      method: 'GET',
      cache: 'no-store',
    });
  });

  it('rejects failed and empty local fetches', async () => {
    await expect(
      fetchLocalUrlAsBase64('blob:https://example.com/missing', {
        fetch: vi.fn(async () => ({ ok: false, status: 404 })) as unknown as typeof fetch,
        blobToBase64: vi.fn(),
      }),
    ).rejects.toThrow('HTTP 404');

    await expect(
      fetchLocalUrlAsBase64('blob:https://example.com/empty', {
        fetch: vi.fn(async () => ({
          ok: true,
          blob: async () => new Blob([]),
        })) as unknown as typeof fetch,
        blobToBase64: vi.fn(),
      }),
    ).rejects.toThrow('empty response body');
  });
});
