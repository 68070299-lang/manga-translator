import { beforeEach, describe, expect, it, vi } from 'vitest';
import { handleTranslateImage } from '../../extension/background/translate';
import { stubWorkerBrowserApis } from '../helpers/browser-stubs';
import { type FetchMock, mockFetch } from '../helpers/fetch';

const mocks = vi.hoisted(() => ({
  generateCacheKey: vi.fn(),
  getFromCache: vi.fn(),
  saveToCache: vi.fn(),
}));

vi.mock('../../extension/lib/cache', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../extension/lib/cache')>()),
  generateCacheKey: mocks.generateCacheKey,
  getFromCache: mocks.getFromCache,
  saveToCache: mocks.saveToCache,
}));

describe('service worker to cloud provider boundary', () => {
  let fetchMock: FetchMock;

  beforeEach(() => {
    mocks.generateCacheKey.mockReset().mockReturnValue('integration-cache-key');
    mocks.getFromCache.mockReset().mockResolvedValue(null);
    mocks.saveToCache.mockReset().mockResolvedValue(undefined);
    stubWorkerBrowserApis();
    vi.stubGlobal('createImageBitmap', undefined);
    fetchMock = mockFetch();
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        candidates: [
          {
            content: {
              parts: [
                {
                  text: JSON.stringify([{ bbox: [1, 2, 30, 40], text: '?', translated: 'cat' }]),
                },
              ],
            },
          },
        ],
      }),
    });
  });

  it('routes a cloud translation through the real provider and caches the result', async () => {
    const response = await handleTranslateImage(
      {
        type: 'TRANSLATE_IMAGE',
        imageId: 'integration-image',
        base64: 'data:image/png;base64,aW1hZ2U=',
        settings: {
          pipelineMode: 'cloud',
          provider: 'gemini',
          apiKey: 'test-key',
          model: 'gemini-2.0-flash',
          targetLang: 'English',
        },
      },
      { tab: { id: 7 } },
    );

    expect(fetchMock).toHaveBeenCalledWith(
      'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ 'x-goog-api-key': 'test-key' }),
      }),
    );
    expect(response).toEqual({
      success: true,
      result: { mode: 'cloud', blocks: [{ bbox: [1, 2, 30, 40], translated: 'cat' }] },
    });
    expect(mocks.saveToCache).toHaveBeenCalledWith(
      'integration-cache-key',
      (response as { result: unknown }).result,
    );
  });
});
