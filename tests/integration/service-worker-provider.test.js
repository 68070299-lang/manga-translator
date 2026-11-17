const { TranslationProvider, LocalBackendProvider } = require('../../extension/lib/api');
const { TextEncoder } = require('util');

describe('service worker to cloud provider boundary', () => {
  let serviceWorker;

  beforeEach(() => {
    jest.resetModules();
    global.TextEncoder = TextEncoder;
    global.importScripts = jest.fn();
    global.self = {
      addEventListener: jest.fn(),
      clients: { claim: jest.fn(async () => undefined) }
    };
    global.MangaTranslatorSettings = require('../../extension/lib/settings');
    const { base64ToBlob, blobToBase64 } = require('../../extension/lib/utils');
    global.base64ToBlob = base64ToBlob;
    global.blobToBase64 = blobToBase64;
    global.generateCacheKey = jest.fn(() => 'integration-cache-key');
    global.getFromCache = jest.fn(async () => null);
    global.saveToCache = jest.fn(async () => undefined);
    global.TranslationProvider = TranslationProvider;
    global.LocalBackendProvider = LocalBackendProvider;
    chrome.tabs._addMessageListener(7, (_message, _sender, sendResponse) => {
      sendResponse({ success: true });
      return false;
    });
    global.fetch = jest.fn(async () => ({
      ok: true,
      json: async () => ({
        candidates: [{
          content: {
            parts: [{
              text: JSON.stringify([{
                bbox: [1, 2, 30, 40],
                text: '?',
                translated: 'cat'
              }])
            }]
          }
        }]
      })
    }));

    serviceWorker = require('../../extension/background/service-worker');
  });

  afterEach(() => {
    delete global.importScripts;
    delete global.self;
    delete global.MangaTranslatorSettings;
    delete global.base64ToBlob;
    delete global.blobToBase64;
    delete global.generateCacheKey;
    delete global.getFromCache;
    delete global.TextEncoder;
    delete global.saveToCache;
    delete global.TranslationProvider;
    delete global.LocalBackendProvider;
  });

  it('routes a cloud translation through the real provider and caches the result', async () => {
    const response = await serviceWorker.handleTranslateImage({
      imageId: 'integration-image',
      base64: 'data:image/png;base64,aW1hZ2U=',
      settings: {
        pipelineMode: 'cloud',
        provider: 'gemini',
        apiKey: 'test-key',
        model: 'gemini-2.0-flash',
        targetLang: 'English'
      }
    }, { tab: { id: 7 } });

    expect(response).toEqual(expect.objectContaining({ success: true }));
    expect(global.fetch).toHaveBeenCalledWith(
      'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ 'x-goog-api-key': 'test-key' })
      })
    );
    expect(response).toEqual({
      success: true,
      result: {
        mode: 'cloud',
        blocks: [{
          bbox: [1, 2, 30, 40],
          translated: 'cat'
        }]
      }
    });
    expect(global.saveToCache).toHaveBeenCalledWith('integration-cache-key', response.result);
  });
});
