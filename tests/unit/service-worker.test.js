describe('service worker context menu routing', () => {
  let serviceWorker;
  let translateImageMock;

  beforeEach(() => {
    jest.resetModules();
    global.importScripts = jest.fn();
    // Make utils.js functions available globally (normally loaded via importScripts)
    const { base64ToBlob, blobToBase64 } = require('../../extension/lib/utils');
    global.base64ToBlob = base64ToBlob;
    global.blobToBase64 = blobToBase64;
    global.self = {
      addEventListener: jest.fn(),
      clients: {
        claim: jest.fn(() => Promise.resolve())
      }
    };
    global.MangaTranslatorSettings = require('../../extension/lib/settings.js');
    global.generateCacheKey = jest.fn(() => 'manga_cache_force_key');
    global.getFromCache = jest.fn();
    global.saveToCache = jest.fn();
    translateImageMock = jest.fn(async () => [{ text: 'fresh', translated: 'Fresh', bbox: [1, 2, 3, 4] }]);
    global.normalizeTranslationBlocks = require('../../extension/lib/api.js').normalizeTranslationBlocks;
    global.TranslationProvider = jest.fn(() => ({
      translateImage: translateImageMock
    }));
    global.LocalBackendProvider = jest.fn(() => ({
      requestTimeoutMs: 600000,
      translateImage: jest.fn(async () => new Blob(['translated-image'], { type: 'image/png' }))
    }));

    serviceWorker = require('../../extension/background/service-worker.js');
  });

  afterEach(() => {
    delete global.importScripts;
    delete global.self;
    delete global.MangaTranslatorSettings;
    delete global.generateCacheKey;
    delete global.getFromCache;
    delete global.saveToCache;
    delete global.normalizeTranslationBlocks;
    delete global.TranslationProvider;
    delete global.LocalBackendProvider;
    delete global.base64ToBlob;
    delete global.blobToBase64;
    delete global.pruneExpiredCache;
    delete chrome.declarativeNetRequest;
  });

  describe('region capture', () => {
    const sender = { frameId: 0, tab: { id: 7, windowId: 3, active: true } };
    const message = {
      rect: { left: 10, top: 20, width: 100, height: 50 },
      viewport: { width: 500, height: 250 }
    };
    let bitmap;
    let drawImage;

    beforeEach(() => {
      bitmap = { width: 1000, height: 500, close: jest.fn() };
      drawImage = jest.fn();
      global.createImageBitmap = jest.fn(async () => bitmap);
      global.OffscreenCanvas = jest.fn((width, height) => ({
        width,
        height,
        getContext: () => ({ drawImage }),
        convertToBlob: async () => new Blob(['crop'], { type: 'image/png' })
      }));
      chrome.tabs.captureVisibleTab = jest.fn(async () => 'data:image/png;base64,AAAA');
    });

    afterEach(() => {
      delete global.createImageBitmap;
      delete global.OffscreenCanvas;
    });

    it('crops the selection out of the screenshot at the capture scale', async () => {
      const result = await serviceWorker.handleCaptureRegion(message, sender);

      expect(chrome.tabs.captureVisibleTab).toHaveBeenCalledWith(3, { format: 'png' });
      expect(global.OffscreenCanvas).toHaveBeenCalledWith(200, 100);
      expect(drawImage).toHaveBeenCalledWith(bitmap, 20, 40, 200, 100, 0, 0, 200, 100);
      expect(result).toEqual({ success: true, base64: btoa('crop'), width: 200, height: 100 });
      expect(bitmap.close).toHaveBeenCalledTimes(1);
    });

    it('refuses subframes, background tabs and senders without a tab', async () => {
      const refused = [
        { frameId: 2, tab: sender.tab },
        { frameId: 0, tab: { ...sender.tab, active: false } },
        { frameId: 0 }
      ];
      for (const candidate of refused) {
        const result = await serviceWorker.handleCaptureRegion(message, candidate);
        expect(result.success).toBe(false);
      }
      expect(chrome.tabs.captureVisibleTab).not.toHaveBeenCalled();
    });

    it('reports capture failures and releases the bitmap', async () => {
      const failedCapture = await (async () => {
        chrome.tabs.captureVisibleTab.mockRejectedValueOnce(new Error('capture denied'));
        return serviceWorker.handleCaptureRegion(message, sender);
      })();
      expect(failedCapture).toEqual({ success: false, error: 'capture denied' });

      const invalid = await serviceWorker.handleCaptureRegion({ ...message, rect: null }, sender);
      expect(invalid).toEqual({ success: false, error: 'Invalid region selection' });
      expect(bitmap.close).toHaveBeenCalledTimes(1);
    });
  });

  it('only serves fonts listed in the bundled registry', async () => {
    await expect(serviceWorker.handleGetBundledFont('../manifest.json'))
      .resolves.toEqual({ success: false, error: 'Unknown bundled font' });
  });

  it('registers page and image context menu actions', () => {
    serviceWorker.registerContextMenus();

    const ids = chrome.contextMenus.create.mock.calls.map(([item]) => item.id);
    expect(ids).toEqual([
      'translate-image',
      'translate-visible-page',
      'translate-page',
      'translate-region',
      'stop-translation',
      'clear-page-translations'
    ]);
  });

  it('updates enabled and disabled toolbar state per tab', async () => {
    await expect(serviceWorker.updateActionState(321, 'https://example.com/reader')).resolves.toBe(true);
    expect(chrome.action.setIcon).toHaveBeenCalledWith(expect.objectContaining({ tabId: 321 }));
    expect(chrome.action.setTitle).toHaveBeenCalledWith({
      tabId: 321,
      title: 'Manga Translator enabled on this site'
    });
    expect(chrome.action.setBadgeText).toHaveBeenCalledWith({ tabId: 321, text: '' });

    await chrome.storage.local.set({ disabledUrls: ['example.com'] });
    await expect(serviceWorker.updateActionState(321, 'https://example.com/reader')).resolves.toBe(false);
    expect(chrome.action.setTitle).toHaveBeenLastCalledWith({
      tabId: 321,
      title: 'Manga Translator disabled on this site'
    });
    expect(chrome.action.setBadgeText).toHaveBeenLastCalledWith({ tabId: 321, text: 'OFF' });
    await expect(serviceWorker.updateAllActionStates()).resolves.toBeUndefined();
  });

  it('refreshes toolbar state on worker startup and extension installation', async () => {
    const startupHandler = chrome.runtime.onStartup.addListener.mock.calls.at(-1)?.[0];
    const installHandler = chrome.runtime.onInstalled.addListener.mock.calls.at(-1)?.[0];

    expect(startupHandler).toEqual(expect.any(Function));
    expect(installHandler).toEqual(expect.any(Function));

    await startupHandler();
    expect(chrome.tabs.query).toHaveBeenCalledWith({});

    await installHandler();
    expect(chrome.contextMenus.create).toHaveBeenCalled();
  });

  it('migrates legacy sync settings without touching private local storage', async () => {
    chrome.storage.sync._setData({
      settings: {
        provider: 'openrouter',
        localBackend: { inpainter: 'lama_large', detector: 'animetext-yolo' }
      }
    });
    chrome.storage.sync.set.mockClear();
    chrome.storage.local.set.mockClear();

    await expect(serviceWorker.migrateStoredSettingsSafely()).resolves.toBe(true);

    const stored = await chrome.storage.sync.get('settings');
    expect(stored.settings).toEqual({
      settingsVersion: global.MangaTranslatorSettings.SETTINGS_VERSION,
      provider: 'openrouter',
      fontSize: 16,
      localBackend: {
        inpainter: 'flux2_klein_lanpaint',
        detector: 'animetext-yolo'
      }
    });
    expect(chrome.storage.local.set).not.toHaveBeenCalled();

    chrome.storage.sync.set.mockClear();
    await expect(serviceWorker.migrateStoredSettingsSafely()).resolves.toBe(false);
    expect(chrome.storage.sync.set).not.toHaveBeenCalled();
  });

  it('keeps translation available when settings migration storage fails', async () => {
    const warning = jest.spyOn(console, 'warn').mockImplementation(() => {});
    chrome.storage.sync.get.mockRejectedValueOnce(new Error('storage unavailable'));

    await expect(serviceWorker.migrateStoredSettingsSafely()).resolves.toBe(false);
    expect(warning).toHaveBeenCalledWith(
      '[Service Worker] Failed to migrate settings:',
      'storage unavailable'
    );
    warning.mockRestore();
  });

  it('routes context menu clicks to content-script workflow messages', () => {
    const tab = { id: 123 };

    serviceWorker.handleContextMenuClick({
      menuItemId: 'translate-image',
      srcUrl: 'https://example.test/page.png'
    }, tab);
    serviceWorker.handleContextMenuClick({ menuItemId: 'translate-visible-page' }, tab);
    serviceWorker.handleContextMenuClick({ menuItemId: 'translate-page' }, tab);
    serviceWorker.handleContextMenuClick({ menuItemId: 'translate-region' }, tab);
    serviceWorker.handleContextMenuClick({ menuItemId: 'stop-translation' }, tab);
    serviceWorker.handleContextMenuClick({ menuItemId: 'clear-page-translations' }, tab);

    expect(chrome.tabs.sendMessage.mock.calls).toEqual([
      [123, { type: 'TRANSLATE_IMAGE_BY_SRC', src: 'https://example.test/page.png' }],
      [123, { type: 'TRANSLATE_VISIBLE_IMAGES' }],
      [123, { type: 'TRANSLATE_PAGE' }],
      [123, { type: 'START_REGION_SELECTION' }],
      [123, { type: 'STOP_TRANSLATION' }],
      [123, { type: 'CLEAR_PAGE_TRANSLATIONS' }]
    ]);
  });

  it('ignores unknown context menu ids and missing tabs', () => {
    serviceWorker.handleContextMenuClick({ menuItemId: 'unknown' }, { id: 123 });
    serviceWorker.handleContextMenuClick({ menuItemId: 'translate-page' }, {});

    expect(chrome.tabs.sendMessage).not.toHaveBeenCalled();
  });

  it('uses cached translations for normal image requests', async () => {
    const cached = {
      mode: 'cloud',
      blocks: [{ text: 'cached', translated: 'Cached' }]
    };
    global.getFromCache.mockResolvedValue(cached);

    const result = await serviceWorker.handleTranslateImage({
      imageId: 'image-1',
      base64: 'data:image/png;base64,abcd',
      canvasSignature: 'reader:page:1',
      settings: {
        pipelineMode: 'cloud',
        provider: 'gemini',
        apiKey: 'test-key'
      }
    }, { tab: { id: 123 } });

    expect(result).toEqual({ success: true, result: cached, fromCache: true });
    expect(global.getFromCache).toHaveBeenCalledWith('manga_cache_force_key');
    expect(global.TranslationProvider).not.toHaveBeenCalled();
    expect(global.saveToCache).not.toHaveBeenCalled();
    expect(chrome.storage.local.remove).not.toHaveBeenCalled();
  });

  it('normalizes cloud provider blocks before caching', async () => {
    global.getFromCache.mockResolvedValue(null);
    translateImageMock.mockResolvedValue([
      { translated_text: 'Fresh', bbox: [1, 2, 3, 4] },
      { text: 'original only', bbox: [1, 2, 3, 4] },
      { translated: 'reversed', bbox: [4, 4, 2, 1] }
    ]);

    const result = await serviceWorker.handleTranslateImage({
      imageId: 'image-cloud',
      base64: 'data:image/png;base64,abcd',
      canvasSignature: 'cloud:page:1',
      settings: {
        pipelineMode: 'cloud',
        provider: 'gemini',
        apiKey: 'test-key',
        targetLang: 'English'
      }
    }, { tab: { id: 123 } });

    expect(result).toEqual({
      success: true,
      result: {
        mode: 'cloud',
        blocks: [{ translated: 'Fresh', bbox: [1, 2, 3, 4] }]
      }
    });
    expect(global.saveToCache).toHaveBeenCalled();
  });

  it('shares one in-flight provider job across tabs', async () => {
    global.getFromCache.mockResolvedValue(null);
    let resolveProvider;
    translateImageMock.mockImplementationOnce(() => new Promise(resolve => {
      resolveProvider = resolve;
    }));

    const request = {
      imageId: 'duplicate-image',
      base64: 'data:image/png;base64,abcd',
      canvasSignature: 'shared:page:1',
      settings: {
        pipelineMode: 'cloud',
        provider: 'gemini',
        apiKey: 'test-key'
      }
    };
    const first = serviceWorker.handleTranslateImage(request, { tab: { id: 101 } });
    const second = serviceWorker.handleTranslateImage({ ...request, imageId: 'duplicate-image-copy' }, { tab: { id: 202 } });

    for (let turn = 0; turn < 8 && !resolveProvider; turn += 1) await Promise.resolve();
    expect(global.TranslationProvider).toHaveBeenCalledTimes(1);

    resolveProvider([{ translated: 'Shared', bbox: [1, 2, 3, 4] }]);
    await expect(Promise.all([first, second])).resolves.toEqual([
      { success: true, result: { mode: 'cloud', blocks: [{ translated: 'Shared', bbox: [1, 2, 3, 4] }] } },
      { success: true, result: { mode: 'cloud', blocks: [{ translated: 'Shared', bbox: [1, 2, 3, 4] }] } }
    ]);
    expect(global.saveToCache).toHaveBeenCalledTimes(1);
  });

  it('uses transient fixed referrer rules for known hotlink-protected CDNs', async () => {
    const updateSessionRules = jest.fn(async () => undefined);
    chrome.declarativeNetRequest = { updateSessionRules };
    global.fetch.mockResolvedValue({
      ok: true,
      blob: async () => new Blob(['image'], { type: 'image/png' })
    });

    const response = await serviceWorker.handleFetchImage('https://i.hamreus.com/books/page.png');

    expect(response.success).toBe(true);
    expect(updateSessionRules).toHaveBeenCalledTimes(2);
    expect(updateSessionRules.mock.calls[0][0].addRules[0]).toEqual(expect.objectContaining({
      action: expect.objectContaining({
        requestHeaders: [expect.objectContaining({
          header: 'Referer',
          value: 'https://www.manhuagui.com/'
        })]
      }),
      condition: {
        requestDomains: ['i.hamreus.com'],
        urlFilter: '||i.hamreus.com/',
        resourceTypes: ['xmlhttprequest']
      }
    }));
    expect(updateSessionRules.mock.calls[1][0]).toEqual({
      removeRuleIds: [updateSessionRules.mock.calls[0][0].addRules[0].id]
    });
    expect(global.fetch.mock.calls[0][1]).toEqual(expect.objectContaining({
      referrer: 'https://www.manhuagui.com/'
    }));
  });

  it('reconciles only owned stale referrer rules and preserves unrelated rules', async () => {
    const staleLegacyRule = {
      id: 1007,
      action: {
        type: 'modifyHeaders',
        requestHeaders: [{
          header: 'Referer',
          operation: 'set',
          value: 'https://www.pixiv.net/'
        }]
      },
      condition: {
        urlFilter: 'pximg.net',
        resourceTypes: ['xmlhttprequest']
      }
    };
    const unrelatedRule = {
      id: 1008,
      action: { type: 'block' },
      condition: { urlFilter: 'pximg.net', resourceTypes: ['xmlhttprequest'] }
    };
    const getSessionRules = jest.fn(async () => [staleLegacyRule, unrelatedRule]);
    const updateSessionRules = jest.fn(async () => undefined);
    chrome.declarativeNetRequest = { getSessionRules, updateSessionRules };

    await serviceWorker.reconcileDnrSessionRules();

    expect(updateSessionRules).toHaveBeenCalledWith({ removeRuleIds: [1007] });
    expect(updateSessionRules).not.toHaveBeenCalledWith({ removeRuleIds: [1008] });
  });

  it('falls back to ordinary fetching when a reserved DNR id is unrelated', async () => {
    const getSessionRules = jest.fn(async () => [{
      id: 1001,
      action: { type: 'block' },
      condition: { urlFilter: 'i.hamreus.com', resourceTypes: ['xmlhttprequest'] }
    }]);
    const updateSessionRules = jest.fn(async () => undefined);
    chrome.declarativeNetRequest = { getSessionRules, updateSessionRules };
    global.fetch.mockResolvedValue({
      ok: true,
      blob: async () => new Blob(['image'], { type: 'image/png' })
    });

    const response = await serviceWorker.handleFetchImage('https://i.hamreus.com/books/page.png');

    expect(response.success).toBe(true);
    expect(updateSessionRules).not.toHaveBeenCalled();
    expect(global.fetch.mock.calls[0][1]).not.toHaveProperty('referrer');
  });

  it('falls back to ordinary fetching when DNR rule installation fails', async () => {
    const updateSessionRules = jest.fn(async options => {
      if (options.addRules) throw new Error('rule collision');
    });
    chrome.declarativeNetRequest = { updateSessionRules };
    global.fetch.mockResolvedValue({
      ok: true,
      blob: async () => new Blob(['image'], { type: 'image/png' })
    });

    const response = await serviceWorker.handleFetchImage('https://hitomi.la/page.png');

    expect(response.success).toBe(true);
    expect(updateSessionRules).toHaveBeenCalledTimes(1);
    expect(global.fetch.mock.calls[0][1]).not.toHaveProperty('referrer');
  });

  it('shares one DNR lease across concurrent fetches for the same CDN', async () => {
    const updateSessionRules = jest.fn(async () => undefined);
    chrome.declarativeNetRequest = { updateSessionRules };
    const fetchResolvers = [];
    global.fetch.mockImplementation(() => new Promise(resolve => fetchResolvers.push(resolve)));

    const first = serviceWorker.handleFetchImage('https://i.hamreus.com/books/one.png');
    const second = serviceWorker.handleFetchImage('https://i.hamreus.com/books/two.png');
    for (let turn = 0; turn < 12 && fetchResolvers.length < 2; turn += 1) await Promise.resolve();

    expect(fetchResolvers).toHaveLength(2);
    expect(updateSessionRules).toHaveBeenCalledTimes(1);
    fetchResolvers.forEach(resolve => resolve({
      ok: true,
      blob: async () => new Blob(['image'], { type: 'image/png' })
    }));

    await expect(Promise.all([first, second])).resolves.toEqual([
      expect.objectContaining({ success: true }),
      expect.objectContaining({ success: true })
    ]);
    expect(updateSessionRules).toHaveBeenCalledTimes(2);
    expect(updateSessionRules.mock.calls[1][0]).toEqual({ removeRuleIds: [1001] });
  });

  it('removes transient referrer rules when both fetch attempts fail', async () => {
    const updateSessionRules = jest.fn(async () => undefined);
    chrome.declarativeNetRequest = { updateSessionRules };
    global.fetch.mockRejectedValue(new Error('blocked'));

    const response = await serviceWorker.handleFetchImage('https://klimv1.xyz/page.png');

    expect(response.success).toBe(false);
    expect(updateSessionRules).toHaveBeenCalledTimes(2);
    expect(updateSessionRules.mock.calls[1][0].removeRuleIds).toHaveLength(1);
  });

  it('does not install a referrer rule for unknown hosts', async () => {
    const updateSessionRules = jest.fn(async () => undefined);
    chrome.declarativeNetRequest = { updateSessionRules };
    global.fetch.mockResolvedValue({
      ok: true,
      blob: async () => new Blob(['image'], { type: 'image/png' })
    });

    const response = await serviceWorker.handleFetchImage('https://images.example.com/page.png');

    expect(response.success).toBe(true);
    expect(updateSessionRules).not.toHaveBeenCalled();
    expect(global.fetch.mock.calls[0][1]).not.toHaveProperty('referrer');
  });

  it('rechecks the cache inside a queued job before calling the provider', async () => {
    let cacheReads = 0;
    const cached = {
      mode: 'cloud',
      blocks: [{ translated: 'Recovered', bbox: [1, 2, 3, 4] }]
    };
    global.getFromCache.mockImplementation(async () => {
      cacheReads += 1;
      return cacheReads === 1 ? null : cached;
    });

    const result = await serviceWorker.handleTranslateImage({
      imageId: 'cache-race',
      base64: 'data:image/png;base64,abcd',
      canvasSignature: 'cache-race:page:1',
      settings: {
        pipelineMode: 'cloud',
        provider: 'gemini',
        apiKey: 'test-key'
      }
    }, { tab: { id: 123 } });

    expect(result).toEqual({ success: true, result: cached, fromCache: true });
    expect(cacheReads).toBe(2);
    expect(global.TranslationProvider).not.toHaveBeenCalled();
    expect(global.saveToCache).not.toHaveBeenCalled();
  });

  it('returns a local Q4 fallback without caching it as Distilled output', async () => {
    global.getFromCache.mockResolvedValue(null);
    global.blobToBase64 = jest.fn(async () => `data:image/png;base64,${'a'.repeat(160)}`);
    global.LocalBackendProvider.mockImplementationOnce(() => ({
      requestTimeoutMs: 600000,
      translateImage: jest.fn(async () => {
        const blob = new Blob(['fallback-image'], { type: 'image/png' });
        blob.inpainterUsed = 'flux2_klein_q4';
        blob.inpainterFallback = true;
        return blob;
      })
    }));

    const response = await serviceWorker.handleTranslateImage({
      imageId: 'fallback-image',
      base64: 'data:image/png;base64,abcd',
      canvasSignature: 'fallback-image:page:1',
      settings: {
        pipelineMode: 'local',
        localBackend: {
          ...global.MangaTranslatorSettings.DEFAULT_SETTINGS.localBackend,
          inpainter: 'flux2_klein_lanpaint'
        }
      }
    }, { tab: { id: 123 } });

    expect(response.success).toBe(true);
    expect(response.result.inpainterFallback).toBe(true);
    expect(response.result.inpainterUsed).toBe('flux2_klein_q4');
    expect(global.saveToCache).not.toHaveBeenCalled();
  });

  it('classifies worker network errors from the local backend path', async () => {
    global.getFromCache.mockResolvedValue(null);
    global.LocalBackendProvider.mockImplementationOnce(() => ({
      requestTimeoutMs: 600000,
      translateImage: jest.fn(async () => {
        throw new Error('network error in worker');
      })
    }));

    const response = await serviceWorker.handleTranslateImage({
      type: 'TRANSLATE_IMAGE',
      imageId: 'img-test',
      base64: 'data:image/png;base64,' + Buffer.from('not-an-empty-image').toString('base64'),
      canvasSignature: 'bookwalker:img-test',
      settings: {
        pipelineMode: 'local',
        localBackend: {
          url: 'http://127.0.0.1:8001'
        }
      }
    }, { tab: { id: 123 } });

    expect(response).toEqual({
      success: false,
      error: 'Cannot connect to local backend at http://127.0.0.1:8001. Worker fetch failed: network error in worker'
    });
  });

  it('preserves local backend request id in surfaced errors', async () => {
    global.getFromCache.mockResolvedValue(null);
    global.LocalBackendProvider.mockImplementationOnce(() => ({
      requestTimeoutMs: 600000,
      lastRequestId: 'req-worker-1',
      translateImage: jest.fn(async () => {
        const error = new Error('Local backend error: 500 - pipeline failed');
        error.requestId = 'req-worker-1';
        throw error;
      })
    }));

    const response = await serviceWorker.handleTranslateImage({
      type: 'TRANSLATE_IMAGE',
      imageId: 'img-test',
      base64: 'data:image/png;base64,' + Buffer.from('not-an-empty-image').toString('base64'),
      canvasSignature: 'bookwalker:img-test',
      settings: {
        pipelineMode: 'local',
        localBackend: {
          url: 'http://127.0.0.1:8001'
        }
      }
    }, { tab: { id: 123 } });

    expect(response.success).toBe(false);
    expect(response.error).toContain('req-worker-1');
  });

  it('keeps a backend 504 provider timeout detail instead of blaming the local request', async () => {
    global.getFromCache.mockResolvedValue(null);
    const detail = 'Local backend error: 504 - {"detail":"Translation provider request timed out"}';
    global.LocalBackendProvider.mockImplementationOnce(() => ({
      requestTimeoutMs: 600000,
      translateImage: jest.fn(async () => {
        const error = new Error(detail);
        error.backendStatus = 504;
        throw error;
      })
    }));

    const response = await serviceWorker.handleTranslateImage({
      type: 'TRANSLATE_IMAGE',
      imageId: 'img-504',
      base64: 'data:image/png;base64,' + Buffer.from('not-an-empty-image').toString('base64'),
      canvasSignature: 'bookwalker:img-504',
      settings: { pipelineMode: 'local', localBackend: { url: 'http://127.0.0.1:8001' } }
    }, { tab: { id: 123 } });

    expect(response).toEqual({ success: false, error: detail });
    expect(response.error).not.toContain('Local backend error: Local backend error');
    expect(response.error).not.toContain('after 600s');
  });

  it('reports why the backend health check failed', async () => {
    global.LocalBackendProvider.mockImplementationOnce(() => ({
      checkHealth: jest.fn(async function checkHealth() {
        this.lastHealthReason = 'unauthorized';
        return false;
      })
    }));

    await expect(serviceWorker.handleCheckBackend({ pipelineMode: 'local' }))
      .resolves.toEqual({ success: false, reason: 'unauthorized' });
  });

  it('does not warn when progress cannot reach a tab without a receiver', async () => {
    global.getFromCache.mockResolvedValue(null);
    const warning = jest.spyOn(console, 'warn').mockImplementation(() => {});
    chrome.tabs.sendMessage.mockRejectedValue(
      new Error('Could not establish connection. Receiving end does not exist.')
    );

    try {
      await serviceWorker.handleTranslateImage({
        imageId: 'no-receiver',
        base64: 'data:image/png;base64,abcd',
        canvasSignature: 'no-receiver:page:1',
        settings: { pipelineMode: 'cloud', provider: 'gemini', apiKey: 'test-key' }
      }, { tab: { id: 123 } });
      await new Promise(resolve => setTimeout(resolve, 0));

      expect(chrome.tabs.sendMessage).toHaveBeenCalled();
      expect(warning).not.toHaveBeenCalledWith(
        expect.stringContaining('notifyProgress failed'),
        expect.anything()
      );
    } finally {
      warning.mockRestore();
      chrome.tabs.sendMessage.mockReset();
    }
  });

  it('bypasses and replaces cached translations for force refresh requests', async () => {
    const freshBlocks = [{ text: 'fresh', translated: 'Fresh', bbox: [1, 2, 3, 4] }];
    translateImageMock.mockResolvedValue(freshBlocks);

    const result = await serviceWorker.handleTranslateImage({
      imageId: 'image-1',
      base64: 'data:image/png;base64,abcd',
      canvasSignature: 'reader:page:1',
      forceRefresh: true,
      settings: {
        pipelineMode: 'cloud',
        provider: 'gemini',
        apiKey: 'test-key'
      }
    }, { tab: { id: 123 } });

    expect(global.getFromCache).not.toHaveBeenCalled();
    expect(chrome.storage.local.remove).toHaveBeenCalledWith('manga_cache_force_key');
    expect(global.saveToCache).toHaveBeenCalledWith('manga_cache_force_key', {
      mode: 'cloud',
      blocks: [{ translated: 'Fresh', bbox: [1, 2, 3, 4] }]
    });
    expect(result).toEqual({
      success: true,
      result: {
        mode: 'cloud',
        blocks: [{ translated: 'Fresh', bbox: [1, 2, 3, 4] }]
      }
    });
  });

  it('rejects oversized decoded payloads before hashing or provider calls', async () => {
    const oversized = `data:image/png;base64,${'A'.repeat(28_000_000)}`;
    const response = await serviceWorker.handleTranslateImage({
      imageId: 'oversized',
      base64: oversized,
      settings: { pipelineMode: 'cloud', provider: 'gemini', apiKey: 'test-key' }
    }, { tab: { id: 123 } });

    expect(response).toEqual({ success: false, error: 'Image exceeds 20 MB decoded image limit' });
    expect(global.generateCacheKey).not.toHaveBeenCalled();
    expect(global.TranslationProvider).not.toHaveBeenCalled();
  });

  it('rejects decoded images over the pixel limit when bitmap decoding is available', async () => {
    const originalCreateImageBitmap = global.createImageBitmap;
    global.createImageBitmap = jest.fn(async () => ({ width: 5001, height: 5000, close: jest.fn() }));
    try {
      await expect(serviceWorker.validateImagePayload('data:image/png;base64,abcd'))
        .rejects.toThrow('Image exceeds 25 MP pixel limit');
    } finally {
      global.createImageBitmap = originalCreateImageBitmap;
    }
  });

  describe('message listener', () => {
    const getListener = () => chrome.runtime.onMessage.addListener.mock.calls.at(-1)[0];

    it('does not handle the removed CHECK_CACHE message or touch arbitrary storage keys', async () => {
      const apiKeys = { apiKey: 'secret-key', googleOcrApiKey: '', localLlmApiKey: '' };
      chrome.storage.local._setData({ apiKeys });
      chrome.storage.local.remove.mockClear();
      const sendResponse = jest.fn();

      expect(getListener()({ type: 'CHECK_CACHE', cacheKey: 'apiKeys' }, {}, sendResponse)).toBe(false);
      await Promise.resolve();

      expect(sendResponse).not.toHaveBeenCalled();
      expect(global.getFromCache).not.toHaveBeenCalled();
      expect(chrome.storage.local.remove).not.toHaveBeenCalled();
      expect(chrome.storage.local._getData().apiKeys).toEqual(apiKeys);
    });

    it('ignores null, typeless and non-string-typed messages without throwing', () => {
      const sendResponse = jest.fn();

      expect(getListener()(null, {}, sendResponse)).toBe(false);
      expect(getListener()(undefined, {}, sendResponse)).toBe(false);
      expect(getListener()({}, {}, sendResponse)).toBe(false);
      expect(getListener()({ type: 42 }, {}, sendResponse)).toBe(false);
      expect(sendResponse).not.toHaveBeenCalled();
    });

    it('still answers supported async message types', async () => {
      const response = await new Promise(resolve => {
        expect(getListener()({ type: 'FETCH_IMAGE', url: 'ftp://example.test/a.png' }, {}, resolve)).toBe(true);
      });

      expect(response).toEqual({ success: false, error: 'Unsupported URL scheme: ftp' });
    });
  });

  describe('FETCH_IMAGE validation', () => {
    it('rejects blobs larger than the decoded image limit', async () => {
      global.blobToBase64 = jest.fn(async () => 'data:image/png;base64,abcd');
      global.fetch.mockResolvedValue({
        ok: true,
        blob: async () => ({ size: 20 * 1024 * 1024 + 1, type: 'image/png' })
      });

      const response = await serviceWorker.handleFetchImage('https://images.example.com/huge.png');

      expect(response).toEqual({ success: false, error: 'Image exceeds 20 MB decoded image limit' });
      expect(global.blobToBase64).not.toHaveBeenCalled();
    });

    it('rejects responses with a non-image content type', async () => {
      global.blobToBase64 = jest.fn(async () => 'data:text/html;base64,abcd');
      global.fetch.mockResolvedValue({
        ok: true,
        blob: async () => new Blob(['<html></html>'], { type: 'text/html' })
      });

      const response = await serviceWorker.handleFetchImage('https://images.example.com/page.html');

      expect(response.success).toBe(false);
      expect(response.error).toContain('text/html');
      expect(global.blobToBase64).not.toHaveBeenCalled();
    });

    it('accepts images with an empty or octet-stream content type', async () => {
      global.blobToBase64 = jest.fn(async () => 'data:image/png;base64,abcd');
      global.fetch.mockResolvedValue({
        ok: true,
        blob: async () => new Blob(['image'])
      });
      await expect(serviceWorker.handleFetchImage('https://images.example.com/a')).resolves.toEqual(
        expect.objectContaining({ success: true })
      );

      global.fetch.mockResolvedValue({
        ok: true,
        blob: async () => new Blob(['image'], { type: 'application/octet-stream' })
      });
      await expect(serviceWorker.handleFetchImage('https://images.example.com/b')).resolves.toEqual(
        expect.objectContaining({ success: true })
      );
    });

    it('surfaces the real HTTP failure without a no-cors retry', async () => {
      global.fetch.mockResolvedValue({ ok: false, status: 403 });

      const response = await serviceWorker.handleFetchImage('https://images.example.com/denied.png');

      expect(response).toEqual({ success: false, error: 'HTTP 403 fetching image' });
      expect(global.fetch).toHaveBeenCalledTimes(1);
      expect(global.fetch.mock.calls[0][1].mode).toBe('cors');
    });

    it('surfaces the network error message', async () => {
      global.fetch.mockRejectedValue(new Error('Failed to fetch'));

      const response = await serviceWorker.handleFetchImage('https://images.example.com/down.png');

      expect(response).toEqual({ success: false, error: 'Failed to fetch' });
      expect(global.fetch).toHaveBeenCalledTimes(1);
    });
  });

  describe('cache pruning', () => {
    it('prunes expired cache entries on startup and install without blocking them', async () => {
      global.pruneExpiredCache = jest.fn(() => new Promise(() => {}));
      const startupHandler = chrome.runtime.onStartup.addListener.mock.calls.at(-1)[0];
      const installHandler = chrome.runtime.onInstalled.addListener.mock.calls.at(-1)[0];

      await startupHandler();
      expect(global.pruneExpiredCache).toHaveBeenCalledTimes(1);

      await installHandler();
      expect(global.pruneExpiredCache).toHaveBeenCalledTimes(2);
    });

    it('prunes once when the worker boots', () => {
      global.pruneExpiredCache = jest.fn(async () => 0);
      jest.resetModules();

      require('../../extension/background/service-worker.js');

      expect(global.pruneExpiredCache).toHaveBeenCalledTimes(1);
    });

    it('returns the pruned count and tolerates a missing pruner', async () => {
      await expect(serviceWorker.pruneCacheSafely()).resolves.toBe(0);

      global.pruneExpiredCache = jest.fn(async () => 4);
      await expect(serviceWorker.pruneCacheSafely()).resolves.toBe(4);
    });

    it('does not throw when pruning fails', async () => {
      const warning = jest.spyOn(console, 'warn').mockImplementation(() => {});
      global.pruneExpiredCache = jest.fn(async () => {
        throw new Error('storage unavailable');
      });
      const startupHandler = chrome.runtime.onStartup.addListener.mock.calls.at(-1)[0];

      await expect(serviceWorker.pruneCacheSafely()).resolves.toBe(0);
      await expect(startupHandler()).resolves.toBeUndefined();
      expect(warning).toHaveBeenCalledWith('[Service Worker] Failed to prune cache:', 'storage unavailable');
      warning.mockRestore();
    });
  });

  describe('provider failures', () => {
    it('does not cache a job whose provider rejected', async () => {
      const errorLog = jest.spyOn(console, 'error').mockImplementation(() => {});
      global.getFromCache.mockResolvedValue(null);
      translateImageMock.mockRejectedValue(new Error('Failed to parse translation response'));

      const response = await serviceWorker.handleTranslateImage({
        imageId: 'unparseable',
        base64: 'data:image/png;base64,abcd',
        canvasSignature: 'unparseable:page:1',
        settings: { pipelineMode: 'cloud', provider: 'gemini', apiKey: 'test-key' }
      }, { tab: { id: 123 } });

      expect(response).toEqual({ success: false, error: 'Failed to parse translation response' });
      expect(global.saveToCache).not.toHaveBeenCalled();
      errorLog.mockRestore();
    });
  });

  describe('DNR referrer rule leases', () => {
    // blobToBase64 settles on a macrotask, so microtask-only loops are not enough here.
    const waitUntil = async predicate => {
      for (let attempt = 0; attempt < 100 && !predicate(); attempt += 1) {
        await new Promise(resolve => setTimeout(resolve, 0));
      }
    };

    it('reinstalls the rule when a fetch starts while the previous lease is being removed', async () => {
      let rules = [];
      let finishRemove = null;
      const updateSessionRules = jest.fn(options => {
        const apply = () => {
          rules = rules.filter(rule => !(options.removeRuleIds || []).includes(rule.id));
          rules.push(...(options.addRules || []));
        };
        if (options.removeRuleIds && !options.addRules && !finishRemove) {
          return new Promise(resolve => {
            finishRemove = () => {
              apply();
              resolve();
            };
          });
        }
        apply();
        return Promise.resolve();
      });
      chrome.declarativeNetRequest = { updateSessionRules };
      const fetchResolvers = [];
      global.fetch
        .mockResolvedValueOnce({ ok: true, blob: async () => new Blob(['image'], { type: 'image/png' }) })
        .mockImplementation(() => new Promise(resolve => fetchResolvers.push(resolve)));

      const first = serviceWorker.handleFetchImage('https://i.hamreus.com/books/one.png');
      await waitUntil(() => finishRemove);
      expect(finishRemove).toEqual(expect.any(Function));
      expect(updateSessionRules).toHaveBeenCalledTimes(2);

      const second = serviceWorker.handleFetchImage('https://i.hamreus.com/books/two.png');
      await new Promise(resolve => setTimeout(resolve, 20));
      // The install must wait for the pending remove instead of trusting the doomed rule.
      expect(updateSessionRules).toHaveBeenCalledTimes(2);
      expect(fetchResolvers).toHaveLength(0);

      finishRemove();
      await waitUntil(() => fetchResolvers.length > 0);

      expect(fetchResolvers).toHaveLength(1);
      expect(updateSessionRules).toHaveBeenCalledTimes(3);
      expect(updateSessionRules.mock.calls[1][0]).toEqual({ removeRuleIds: [1001] });
      expect(updateSessionRules.mock.calls[2][0].addRules).toHaveLength(1);
      expect(rules.map(rule => rule.id)).toEqual([1001]);

      fetchResolvers[0]({ ok: true, blob: async () => new Blob(['image'], { type: 'image/png' }) });
      await expect(Promise.all([first, second])).resolves.toEqual([
        expect.objectContaining({ success: true }),
        expect.objectContaining({ success: true })
      ]);
      expect(rules).toEqual([]);
    });

    it('keeps the rule tracked when its removal fails so the next acquire does not double-install', async () => {
      const warning = jest.spyOn(console, 'warn').mockImplementation(() => {});
      const updateSessionRules = jest.fn(async options => {
        if (options.removeRuleIds && !options.addRules) throw new Error('remove failed');
      });
      chrome.declarativeNetRequest = { updateSessionRules };
      global.fetch.mockResolvedValue({ ok: true, blob: async () => new Blob(['image'], { type: 'image/png' }) });

      await serviceWorker.handleFetchImage('https://i.hamreus.com/books/one.png');
      await serviceWorker.handleFetchImage('https://i.hamreus.com/books/two.png');

      // add, failed remove, then the second fetch reuses the still-installed rule.
      expect(updateSessionRules.mock.calls.map(([options]) => Object.keys(options))).toEqual([
        ['addRules'],
        ['removeRuleIds'],
        ['removeRuleIds']
      ]);
      warning.mockRestore();
    });
  });

  describe('legacy synced API keys', () => {
    const syncedSettings = (overrides = {}) => ({
      settingsVersion: 2,
      provider: 'openrouter',
      ...overrides
    });

    it('moves legacy synced API keys to local-only storage', async () => {
      chrome.storage.sync._setData({
        settings: syncedSettings({
          apiKey: 'legacy-key',
          googleOcr: { apiKey: 'google-key', languageHint: 'ja' },
          localLlm: { apiKey: 'llm-key', model: 'qwen' },
          localBackend: { authToken: 'backend-token' }
        })
      });

      await expect(serviceWorker.migrateStoredSettingsSafely()).resolves.toBe(true);

      const local = await chrome.storage.local.get('apiKeys');
      expect(local.apiKeys).toEqual({
        apiKey: 'legacy-key',
        googleOcrApiKey: 'google-key',
        localLlmApiKey: 'llm-key',
        backendAuthToken: 'backend-token'
      });
      const { settings } = await chrome.storage.sync.get('settings');
      expect(settings.provider).toBe('openrouter');
      expect(settings.googleOcr.languageHint).toBe('ja');
      expect(settings.localLlm.model).toBe('qwen');
      expect(settings).not.toHaveProperty('apiKey');
      expect(settings.googleOcr).not.toHaveProperty('apiKey');
      expect(settings.localLlm).not.toHaveProperty('apiKey');
      expect(settings.localBackend).not.toHaveProperty('authToken');
      expect(JSON.stringify(settings)).not.toMatch(/legacy-key|google-key|llm-key|backend-token/);
    });

    it('moves legacy keys even when no version migration is pending and does not clobber local keys', async () => {
      chrome.storage.local._setData({
        apiKeys: { apiKey: 'local-key', googleOcrApiKey: '', localLlmApiKey: 'local-llm' }
      });
      chrome.storage.sync._setData({
        settings: syncedSettings({
          apiKey: 'legacy-key',
          googleOcr: { apiKey: 'google-key' },
          localLlm: { apiKey: 'legacy-llm' }
        })
      });

      await expect(serviceWorker.migrateStoredSettingsSafely()).resolves.toBe(true);

      const local = await chrome.storage.local.get('apiKeys');
      expect(local.apiKeys).toEqual({
        apiKey: 'local-key',
        googleOcrApiKey: 'google-key',
        localLlmApiKey: 'local-llm'
      });
      const { settings } = await chrome.storage.sync.get('settings');
      expect(settings).not.toHaveProperty('apiKey');
      expect(settings.googleOcr).not.toHaveProperty('apiKey');
      expect(settings.localLlm).not.toHaveProperty('apiKey');
    });

    it('writes nothing when no legacy keys are synced', async () => {
      chrome.storage.sync._setData({ settings: syncedSettings({ apiKey: '', googleOcr: { apiKey: '  ' } }) });
      chrome.storage.sync.set.mockClear();
      chrome.storage.local.set.mockClear();

      await expect(serviceWorker.migrateStoredSettingsSafely()).resolves.toBe(false);

      expect(chrome.storage.sync.set).not.toHaveBeenCalled();
      expect(chrome.storage.local.set).not.toHaveBeenCalled();
    });

    it('leaves sync untouched when the local key write fails', async () => {
      const warning = jest.spyOn(console, 'warn').mockImplementation(() => {});
      chrome.storage.sync._setData({ settings: syncedSettings({ apiKey: 'legacy-key' }) });
      chrome.storage.sync.set.mockClear();
      chrome.storage.local.set.mockRejectedValueOnce(new Error('quota'));

      await expect(serviceWorker.migrateStoredSettingsSafely()).resolves.toBe(false);

      expect(chrome.storage.sync.set).not.toHaveBeenCalled();
      expect((await chrome.storage.sync.get('settings')).settings.apiKey).toBe('legacy-key');
      warning.mockRestore();
    });
  });

  describe('chrome API promise handling', () => {
    const rejectedWith = message => {
      const promise = Promise.reject(new Error(message));
      const then = jest.spyOn(promise, 'then');
      return { promise, then };
    };

    it('attaches a rejection handler to context menu messages and ignores a missing receiver', () => {
      const warning = jest.spyOn(console, 'warn').mockImplementation(() => {});
      const { promise, then } = rejectedWith('Could not establish connection. Receiving end does not exist.');
      chrome.tabs.sendMessage.mockReturnValueOnce(promise);

      serviceWorker.handleContextMenuClick({ menuItemId: 'translate-page' }, { id: 7 });

      expect(then).toHaveBeenCalledWith(undefined, expect.any(Function));
      return promise.then(undefined, () => {}).then(() => {
        expect(warning).not.toHaveBeenCalled();
        warning.mockRestore();
      });
    });

    it('handles rejected chrome.action setters for closed tabs', async () => {
      const warning = jest.spyOn(console, 'warn').mockImplementation(() => {});
      const setters = ['setIcon', 'setTitle', 'setBadgeText', 'setBadgeBackgroundColor'];
      const spies = setters.map(name => {
        const { promise, then } = rejectedWith('No tab with id: 55.');
        chrome.action[name].mockReturnValueOnce(promise);
        return then;
      });

      serviceWorker.setActionState(55, false);
      await new Promise(resolve => setTimeout(resolve, 0));

      spies.forEach(then => expect(then).toHaveBeenCalledWith(undefined, expect.any(Function)));
      expect(warning).not.toHaveBeenCalled();
      warning.mockRestore();
    });

    it('warns about unexpected rejections and tolerates non-promise results', async () => {
      const warning = jest.spyOn(console, 'warn').mockImplementation(() => {});

      expect(serviceWorker.safeChromeCall(undefined)).toBeUndefined();
      expect(serviceWorker.safeChromeCall('ok')).toBe('ok');
      serviceWorker.safeChromeCall(Promise.reject(new Error('Extension context invalidated')));
      await new Promise(resolve => setTimeout(resolve, 0));

      expect(warning).toHaveBeenCalledWith(
        '[Service Worker] Chrome API call failed:',
        'Extension context invalidated'
      );
      warning.mockRestore();
    });
  });
});
