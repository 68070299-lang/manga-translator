import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { browser } from 'wxt/browser';
import { registerBackground } from '../../extension/background';
import {
  setActionState,
  updateActionState,
  updateAllActionStates,
} from '../../extension/background/action-state';
import { handleGetBundledFont } from '../../extension/background/bundled-font';
import { handleCaptureRegion } from '../../extension/background/capture-region';
import {
  getContextMenuMessage,
  handleContextMenuClick,
  registerContextMenus,
} from '../../extension/background/context-menus';
import {
  isOwnedDnrRule,
  reconcileDnrSessionRules,
  resetDnrReconciliation,
  withDnrReferrerRule,
} from '../../extension/background/dnr-rules';
import { handleFetchImage } from '../../extension/background/fetch-image';
import {
  estimateDecodedBase64Bytes,
  validateImagePayload,
} from '../../extension/background/image-payload';
import {
  handleExtensionInstalled,
  handleExtensionStartup,
  pruneCacheSafely,
} from '../../extension/background/lifecycle';
import { handleRuntimeMessage } from '../../extension/background/message-router';
import { handleCheckBackend } from '../../extension/background/providers';
import { migrateStoredSettingsSafely } from '../../extension/background/settings-migration';
import { safeBrowserCall } from '../../extension/background/tab-messaging';
import { getTranslationQueues, handleTranslateImage } from '../../extension/background/translate';
import { CDN_REFERRER_RULES } from '../../extension/lib/referrer-rules';
import { DEFAULT_SETTINGS, SETTINGS_VERSION } from '../../extension/lib/settings';
import type { TranslateImageRequest } from '../../extension/shared/messages';
import {
  constructing,
  setDeclarativeNetRequest,
  stubWorkerBrowserApis,
} from '../helpers/browser-stubs';
import { type FetchMock, mockFetch } from '../helpers/fetch';

const mocks = vi.hoisted(() => ({
  translateImage: vi.fn(),
  TranslationProvider: vi.fn(),
  LocalBackendProvider: vi.fn(),
  generateCacheKey: vi.fn(),
  getFromCache: vi.fn(),
  saveToCache: vi.fn(),
  pruneExpiredCache: vi.fn(),
  blobToBase64: vi.fn(),
}));

vi.mock('../../extension/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../extension/lib/api')>()),
  TranslationProvider: mocks.TranslationProvider,
  LocalBackendProvider: mocks.LocalBackendProvider,
}));

vi.mock('../../extension/lib/cache', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../extension/lib/cache')>()),
  generateCacheKey: mocks.generateCacheKey,
  getFromCache: mocks.getFromCache,
  saveToCache: mocks.saveToCache,
  pruneExpiredCache: mocks.pruneExpiredCache,
}));

vi.mock('../../extension/lib/utils', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../extension/lib/utils')>()),
  blobToBase64: mocks.blobToBase64,
}));

type Stubs = ReturnType<typeof stubWorkerBrowserApis>;

interface StoredSettings {
  provider?: string;
  fontSize?: number;
  apiKey?: string;
  localLlm?: { apiKey?: string; model?: string };
  localBackend?: { authToken?: string };
}

const storedSettings = async (): Promise<StoredSettings> =>
  (await browser.storage.sync.get('settings')).settings as StoredSettings;

/** `{ type: 'TRANSLATE_IMAGE', ... }` without repeating the type in every test. */
const translateRequest = (
  fields: Omit<TranslateImageRequest, 'type'> & { type?: 'TRANSLATE_IMAGE' },
): TranslateImageRequest => ({ type: 'TRANSLATE_IMAGE', ...fields });

const imageResponse = () => ({
  ok: true,
  blob: async () => new Blob(['image'], { type: 'image/png' }),
});

describe('service worker', () => {
  let stubs: Stubs;
  let fetchMock: FetchMock;

  beforeEach(async () => {
    const { blobToBase64 } = await vi.importActual<typeof import('../../extension/lib/utils')>(
      '../../extension/lib/utils',
    );
    stubs = stubWorkerBrowserApis();
    // The worker skips the pixel check where createImageBitmap is unavailable.
    vi.stubGlobal('createImageBitmap', undefined);
    fetchMock = mockFetch();
    resetDnrReconciliation();

    mocks.generateCacheKey.mockReset().mockReturnValue('manga_cache_force_key');
    mocks.getFromCache.mockReset();
    mocks.saveToCache.mockReset();
    mocks.pruneExpiredCache.mockReset().mockResolvedValue(0);
    mocks.blobToBase64.mockReset().mockImplementation(blobToBase64);
    mocks.translateImage
      .mockReset()
      .mockImplementation(async () => [{ text: 'fresh', translated: 'Fresh', bbox: [1, 2, 3, 4] }]);
    mocks.TranslationProvider.mockReset().mockImplementation(
      constructing(() => ({ translateImage: mocks.translateImage })),
    );
    mocks.LocalBackendProvider.mockReset().mockImplementation(
      constructing(() => ({
        requestTimeoutMs: 600000,
        translateImage: vi.fn(async () => new Blob(['translated-image'], { type: 'image/png' })),
      })),
    );
  });

  afterEach(() => {
    setDeclarativeNetRequest(undefined);
  });

  describe('region capture', () => {
    const sender = { frameId: 0, tab: { id: 7, windowId: 3, active: true } };
    const message = {
      rect: { left: 10, top: 20, width: 100, height: 50 },
      viewport: { width: 500, height: 250 },
    };
    let bitmap: { width: number; height: number; close: ReturnType<typeof vi.fn> };
    let drawImage: ReturnType<typeof vi.fn>;
    let offscreenCanvas: ReturnType<typeof vi.fn>;

    beforeEach(() => {
      bitmap = { width: 1000, height: 500, close: vi.fn() };
      drawImage = vi.fn();
      vi.stubGlobal(
        'createImageBitmap',
        vi.fn(async () => bitmap),
      );
      offscreenCanvas = vi.fn(
        constructing(() => ({
          getContext: () => ({ drawImage }),
          convertToBlob: async () => new Blob(['crop'], { type: 'image/png' }),
        })),
      );
      vi.stubGlobal('OffscreenCanvas', offscreenCanvas);
      stubs.captureVisibleTab.mockResolvedValue('data:image/png;base64,AAAA');
    });

    it('crops the selection out of the screenshot at the capture scale', async () => {
      const result = await handleCaptureRegion(message, sender);

      expect(stubs.captureVisibleTab).toHaveBeenCalledWith(3, { format: 'png' });
      expect(offscreenCanvas).toHaveBeenCalledWith(200, 100);
      expect(drawImage).toHaveBeenCalledWith(bitmap, 20, 40, 200, 100, 0, 0, 200, 100);
      expect(result).toEqual({ success: true, base64: btoa('crop'), width: 200, height: 100 });
      expect(bitmap.close).toHaveBeenCalledTimes(1);
    });

    it('refuses subframes, background tabs and senders without a tab', async () => {
      const refused = [
        { frameId: 2, tab: sender.tab },
        { frameId: 0, tab: { ...sender.tab, active: false } },
        { frameId: 0 },
      ];
      for (const candidate of refused) {
        const result = await handleCaptureRegion(message, candidate);
        expect(result.success).toBe(false);
      }
      expect(stubs.captureVisibleTab).not.toHaveBeenCalled();
    });

    it('reports capture failures and releases the bitmap', async () => {
      vi.spyOn(console, 'error').mockImplementation(() => {});
      stubs.captureVisibleTab.mockRejectedValueOnce(new Error('capture denied'));
      const failedCapture = await handleCaptureRegion(message, sender);
      expect(failedCapture).toEqual({ success: false, error: 'capture denied' });

      const invalid = await handleCaptureRegion(
        { ...message, rect: null as unknown as typeof message.rect },
        sender,
      );
      expect(invalid).toEqual({ success: false, error: 'Invalid region selection' });
      expect(bitmap.close).toHaveBeenCalledTimes(1);
    });
  });

  it('only serves fonts listed in the bundled registry', async () => {
    await expect(handleGetBundledFont('../manifest.json')).resolves.toEqual({
      success: false,
      error: 'Unknown bundled font',
    });
  });

  it('serves a registered bundled font as base64', async () => {
    fetchMock.mockResolvedValue({ ok: true, blob: async () => new Blob(['font-bytes']) });

    await expect(handleGetBundledFont('cc-wild-words')).resolves.toEqual({
      success: true,
      base64: btoa('font-bytes'),
    });
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('fonts/CCWildWords.ttf');
  });

  it('registers page and image context menu actions', () => {
    registerContextMenus();

    const ids = stubs.contextMenusCreate.mock.calls.map(([item]) => item.id);
    expect(ids).toEqual([
      'translate-image',
      'translate-visible-page',
      'translate-page',
      'translate-region',
      'stop-translation',
      'clear-page-translations',
    ]);
  });

  it('updates enabled and disabled toolbar state per tab', async () => {
    await expect(updateActionState(321, 'https://example.com/reader')).resolves.toBe(true);
    expect(stubs.action.setIcon).toHaveBeenCalledWith(expect.objectContaining({ tabId: 321 }));
    expect(stubs.action.setTitle).toHaveBeenCalledWith({
      tabId: 321,
      title: 'Manga Translator enabled on this site',
    });
    expect(stubs.action.setBadgeText).toHaveBeenCalledWith({ tabId: 321, text: '' });

    await browser.storage.local.set({ disabledUrls: ['example.com'] });
    await expect(updateActionState(321, 'https://example.com/reader')).resolves.toBe(false);
    expect(stubs.action.setTitle).toHaveBeenLastCalledWith({
      tabId: 321,
      title: 'Manga Translator disabled on this site',
    });
    expect(stubs.action.setBadgeText).toHaveBeenLastCalledWith({ tabId: 321, text: 'OFF' });
    await expect(updateAllActionStates()).resolves.toBeUndefined();
  });

  describe('worker wiring', () => {
    function register() {
      const onStartup = vi.spyOn(browser.runtime.onStartup, 'addListener');
      const onInstalled = vi.spyOn(browser.runtime.onInstalled, 'addListener');
      const onMessage = vi.spyOn(browser.runtime.onMessage, 'addListener');
      registerBackground();
      return { onStartup, onInstalled, onMessage };
    }

    it('registers every listener synchronously', () => {
      const { onStartup, onInstalled, onMessage } = register();

      expect(onMessage).toHaveBeenCalledWith(handleRuntimeMessage);
      expect(onInstalled).toHaveBeenCalledWith(handleExtensionInstalled);
      expect(onStartup).toHaveBeenCalledWith(handleExtensionStartup);
      expect(stubs.contextMenusOnClicked.addListener).toHaveBeenCalledWith(handleContextMenuClick);
    });

    it('refreshes toolbar state on worker startup and extension installation', async () => {
      const querySpy = vi.spyOn(browser.tabs, 'query');
      const { onStartup, onInstalled } = register();
      const startupHandler = onStartup.mock.calls.at(-1)?.[0] as () => Promise<void>;
      const installHandler = onInstalled.mock.calls.at(-1)?.[0] as () => Promise<void>;

      expect(startupHandler).toEqual(expect.any(Function));
      expect(installHandler).toEqual(expect.any(Function));

      querySpy.mockClear();
      await startupHandler();
      expect(querySpy).toHaveBeenCalledWith({});

      await installHandler();
      expect(stubs.contextMenusCreate).toHaveBeenCalled();
    });

    it('prunes once when the worker boots', () => {
      register();

      expect(mocks.pruneExpiredCache).toHaveBeenCalledTimes(1);
    });

    it('migrates stored settings when the worker boots', async () => {
      await browser.storage.sync.set({ settings: { provider: 'openrouter', fontSize: 14 } });

      register();
      await vi.waitFor(async () => {
        const settings = await storedSettings();
        expect(settings.fontSize).toBe(16);
      });
    });
  });

  describe('settings migration', () => {
    it('migrates legacy sync settings without touching private local storage', async () => {
      await browser.storage.sync.set({
        settings: {
          provider: 'openrouter',
          localBackend: { inpainter: 'lama_large', detector: 'animetext-yolo' },
        },
      });
      const localSet = vi.spyOn(browser.storage.local, 'set');
      const syncSet = vi.spyOn(browser.storage.sync, 'set');

      await expect(migrateStoredSettingsSafely()).resolves.toBe(true);

      const stored = { settings: await storedSettings() };
      expect(stored.settings).toEqual({
        settingsVersion: SETTINGS_VERSION,
        provider: 'openrouter',
        fontSize: 16,
        localBackend: {
          inpainter: 'flux2_klein_lanpaint',
          detector: 'animetext-yolo',
        },
      });
      expect(localSet).not.toHaveBeenCalled();

      syncSet.mockClear();
      await expect(migrateStoredSettingsSafely()).resolves.toBe(false);
      expect(syncSet).not.toHaveBeenCalled();
    });

    it('keeps translation available when settings migration storage fails', async () => {
      const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});
      vi.spyOn(browser.storage.sync, 'get').mockRejectedValueOnce(new Error('storage unavailable'));

      await expect(migrateStoredSettingsSafely()).resolves.toBe(false);
      expect(warning).toHaveBeenCalledWith(
        '[Service Worker] Failed to migrate settings:',
        'storage unavailable',
      );
    });

    it('does nothing when no settings have been stored yet', async () => {
      const syncSet = vi.spyOn(browser.storage.sync, 'set');

      await expect(migrateStoredSettingsSafely()).resolves.toBe(false);
      expect(syncSet).not.toHaveBeenCalled();
    });
  });

  describe('context menu clicks', () => {
    it('routes context menu clicks to content-script workflow messages', () => {
      const tab = { id: 123 };

      handleContextMenuClick(
        { menuItemId: 'translate-image', srcUrl: 'https://example.test/page.png' },
        tab,
      );
      handleContextMenuClick({ menuItemId: 'translate-visible-page' }, tab);
      handleContextMenuClick({ menuItemId: 'translate-page' }, tab);
      handleContextMenuClick({ menuItemId: 'translate-region' }, tab);
      handleContextMenuClick({ menuItemId: 'stop-translation' }, tab);
      handleContextMenuClick({ menuItemId: 'clear-page-translations' }, tab);

      expect(stubs.tabsSendMessage.mock.calls).toEqual([
        [123, { type: 'TRANSLATE_IMAGE_BY_SRC', src: 'https://example.test/page.png' }],
        [123, { type: 'TRANSLATE_VISIBLE_IMAGES' }],
        [123, { type: 'TRANSLATE_PAGE' }],
        [123, { type: 'START_REGION_SELECTION' }],
        [123, { type: 'STOP_TRANSLATION' }],
        [123, { type: 'CLEAR_PAGE_TRANSLATIONS' }],
      ]);
    });

    it('ignores unknown context menu ids and missing tabs', () => {
      handleContextMenuClick({ menuItemId: 'unknown' }, { id: 123 });
      handleContextMenuClick({ menuItemId: 'translate-page' }, {});

      expect(stubs.tabsSendMessage).not.toHaveBeenCalled();
      expect(getContextMenuMessage({ menuItemId: 'unknown' })).toBeNull();
    });
  });

  describe('TRANSLATE_IMAGE', () => {
    it('uses cached translations for normal image requests', async () => {
      const cached = {
        mode: 'cloud',
        blocks: [{ text: 'cached', translated: 'Cached' }],
      };
      mocks.getFromCache.mockResolvedValue(cached);
      const removeSpy = vi.spyOn(browser.storage.local, 'remove');

      const result = await handleTranslateImage(
        translateRequest({
          imageId: 'image-1',
          base64: 'data:image/png;base64,abcd',
          canvasSignature: 'reader:page:1',
          settings: { pipelineMode: 'cloud', provider: 'gemini', apiKey: 'test-key' },
        }),
        { tab: { id: 123 } },
      );

      expect(result).toEqual({ success: true, result: cached, fromCache: true });
      expect(mocks.getFromCache).toHaveBeenCalledWith('manga_cache_force_key');
      expect(mocks.TranslationProvider).not.toHaveBeenCalled();
      expect(mocks.saveToCache).not.toHaveBeenCalled();
      expect(removeSpy).not.toHaveBeenCalled();
    });

    it('derives the cache identity from the canvas signature or the image hash', async () => {
      mocks.getFromCache.mockResolvedValue(null);
      const settings = { pipelineMode: 'cloud', provider: 'gemini', apiKey: 'test-key' } as const;

      await handleTranslateImage(
        translateRequest({
          imageId: 'canvas',
          base64: 'data:image/png;base64,abcd',
          canvasSignature: 'reader:page/1',
          settings,
        }),
        { tab: { id: 1 } },
      );
      await handleTranslateImage(
        translateRequest({ imageId: 'plain', base64: 'data:image/png;base64,abcd', settings }),
        { tab: { id: 1 } },
      );

      expect(mocks.generateCacheKey.mock.calls[0]?.[0]).toBe('canvas_reader_page_1');
      // SHA-256 of the base64 payload without its data: prefix ("abcd").
      expect(mocks.generateCacheKey.mock.calls[1]?.[0]).toBe(
        '88d4266fd4e6338d13b845fcf289579d209c897823b9217da3e161936f031589',
      );
    });

    it('normalizes cloud provider blocks before caching', async () => {
      mocks.getFromCache.mockResolvedValue(null);
      mocks.translateImage.mockResolvedValue([
        { translated_text: 'Fresh', bbox: [1, 2, 3, 4] },
        { text: 'original only', bbox: [1, 2, 3, 4] },
        { translated: 'reversed', bbox: [4, 4, 2, 1] },
      ]);

      const result = await handleTranslateImage(
        translateRequest({
          imageId: 'image-cloud',
          base64: 'data:image/png;base64,abcd',
          canvasSignature: 'cloud:page:1',
          settings: {
            pipelineMode: 'cloud',
            provider: 'gemini',
            apiKey: 'test-key',
            targetLang: 'English',
          },
        }),
        { tab: { id: 123 } },
      );

      expect(result).toEqual({
        success: true,
        result: { mode: 'cloud', blocks: [{ translated: 'Fresh', bbox: [1, 2, 3, 4] }] },
      });
      expect(mocks.saveToCache).toHaveBeenCalled();
    });

    it('shares one in-flight provider job across tabs', async () => {
      mocks.getFromCache.mockResolvedValue(null);
      let resolveProvider: (blocks: unknown) => void = () => {};
      mocks.translateImage.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveProvider = resolve;
          }),
      );

      const request = translateRequest({
        imageId: 'duplicate-image',
        base64: 'data:image/png;base64,abcd',
        canvasSignature: 'shared:page:1',
        settings: { pipelineMode: 'cloud', provider: 'gemini', apiKey: 'test-key' },
      });
      const first = handleTranslateImage(request, { tab: { id: 101 } });
      const second = handleTranslateImage(
        { ...request, imageId: 'duplicate-image-copy' },
        { tab: { id: 202 } },
      );

      await vi.waitFor(() => expect(mocks.TranslationProvider).toHaveBeenCalledTimes(1));

      resolveProvider([{ translated: 'Shared', bbox: [1, 2, 3, 4] }]);
      await expect(Promise.all([first, second])).resolves.toEqual([
        {
          success: true,
          result: { mode: 'cloud', blocks: [{ translated: 'Shared', bbox: [1, 2, 3, 4] }] },
        },
        {
          success: true,
          result: { mode: 'cloud', blocks: [{ translated: 'Shared', bbox: [1, 2, 3, 4] }] },
        },
      ]);
      expect(mocks.saveToCache).toHaveBeenCalledTimes(1);
    });

    it('rechecks the cache inside a queued job before calling the provider', async () => {
      let cacheReads = 0;
      const cached = {
        mode: 'cloud',
        blocks: [{ translated: 'Recovered', bbox: [1, 2, 3, 4] }],
      };
      mocks.getFromCache.mockImplementation(async () => {
        cacheReads += 1;
        return cacheReads === 1 ? null : cached;
      });

      const result = await handleTranslateImage(
        translateRequest({
          imageId: 'cache-race',
          base64: 'data:image/png;base64,abcd',
          canvasSignature: 'cache-race:page:1',
          settings: { pipelineMode: 'cloud', provider: 'gemini', apiKey: 'test-key' },
        }),
        { tab: { id: 123 } },
      );

      expect(result).toEqual({ success: true, result: cached, fromCache: true });
      expect(cacheReads).toBe(2);
      expect(mocks.TranslationProvider).not.toHaveBeenCalled();
      expect(mocks.saveToCache).not.toHaveBeenCalled();
    });

    it('returns a local Q4 fallback without caching it as Distilled output', async () => {
      mocks.getFromCache.mockResolvedValue(null);
      mocks.blobToBase64.mockResolvedValue(`data:image/png;base64,${'a'.repeat(160)}`);
      mocks.LocalBackendProvider.mockImplementationOnce(
        constructing(() => ({
          requestTimeoutMs: 600000,
          translateImage: vi.fn(async () =>
            Object.assign(new Blob(['fallback-image'], { type: 'image/png' }), {
              inpainterUsed: 'flux2_klein_q4',
              inpainterFallback: true,
            }),
          ),
        })),
      );

      const response = await handleTranslateImage(
        translateRequest({
          imageId: 'fallback-image',
          base64: 'data:image/png;base64,abcd',
          canvasSignature: 'fallback-image:page:1',
          settings: {
            pipelineMode: 'local',
            localBackend: { ...DEFAULT_SETTINGS.localBackend, inpainter: 'flux2_klein_lanpaint' },
          },
        }),
        { tab: { id: 123 } },
      );

      expect(response.success).toBe(true);
      const result = (response as { result: { inpainterFallback: boolean; inpainterUsed: string } })
        .result;
      expect(result.inpainterFallback).toBe(true);
      expect(result.inpainterUsed).toBe('flux2_klein_q4');
      expect(mocks.saveToCache).not.toHaveBeenCalled();
    });

    it('caches a normal local result and returns it with the rendered image', async () => {
      mocks.getFromCache.mockResolvedValue(null);
      mocks.blobToBase64.mockResolvedValue('b'.repeat(160));

      const response = await handleTranslateImage(
        translateRequest({
          imageId: 'local-ok',
          base64: 'data:image/png;base64,abcd',
          canvasSignature: 'local-ok:page:1',
          settings: { pipelineMode: 'local' },
        }),
        { tab: { id: 123 } },
      );

      const expected = {
        translatedImage: 'b'.repeat(160),
        mimeType: 'image/png',
        mode: 'local',
        inpainterUsed: DEFAULT_SETTINGS.localBackend.inpainter,
        inpainterFallback: false,
      };
      expect(response).toEqual({ success: true, result: expected });
      expect(mocks.saveToCache).toHaveBeenCalledWith('manga_cache_force_key', expected);
    });

    it('runs local jobs one at a time and cloud jobs three at a time', () => {
      const { local, cloud } = getTranslationQueues();

      expect(local.concurrency).toBe(1);
      expect(cloud.concurrency).toBe(3);
    });

    it('classifies worker network errors from the local backend path', async () => {
      mocks.getFromCache.mockResolvedValue(null);
      vi.spyOn(console, 'error').mockImplementation(() => {});
      mocks.LocalBackendProvider.mockImplementationOnce(
        constructing(() => ({
          requestTimeoutMs: 600000,
          translateImage: vi.fn(async () => {
            throw new Error('network error in worker');
          }),
        })),
      );

      const response = await handleTranslateImage(
        translateRequest({
          imageId: 'img-test',
          base64: `data:image/png;base64,${Buffer.from('not-an-empty-image').toString('base64')}`,
          canvasSignature: 'bookwalker:img-test',
          settings: { pipelineMode: 'local', localBackend: { url: 'http://127.0.0.1:8001' } },
        }),
        { tab: { id: 123 } },
      );

      expect(response).toEqual({
        success: false,
        error:
          'Cannot connect to local backend at http://127.0.0.1:8001. Worker fetch failed: network error in worker',
      });
    });

    it('preserves local backend request id in surfaced errors', async () => {
      mocks.getFromCache.mockResolvedValue(null);
      vi.spyOn(console, 'error').mockImplementation(() => {});
      mocks.LocalBackendProvider.mockImplementationOnce(
        constructing(() => ({
          requestTimeoutMs: 600000,
          lastRequestId: 'req-worker-1',
          translateImage: vi.fn(async () => {
            throw Object.assign(new Error('Local backend error: 500 - pipeline failed'), {
              requestId: 'req-worker-1',
            });
          }),
        })),
      );

      const response = await handleTranslateImage(
        translateRequest({
          imageId: 'img-test',
          base64: `data:image/png;base64,${Buffer.from('not-an-empty-image').toString('base64')}`,
          canvasSignature: 'bookwalker:img-test',
          settings: { pipelineMode: 'local', localBackend: { url: 'http://127.0.0.1:8001' } },
        }),
        { tab: { id: 123 } },
      );

      expect(response.success).toBe(false);
      expect((response as { error: string }).error).toContain('req-worker-1');
    });

    it('reports a local request timeout with its duration', async () => {
      mocks.getFromCache.mockResolvedValue(null);
      vi.spyOn(console, 'error').mockImplementation(() => {});
      mocks.LocalBackendProvider.mockImplementationOnce(
        constructing(() => ({
          requestTimeoutMs: 240000,
          translateImage: vi.fn(async () => {
            throw Object.assign(new Error('The operation was aborted due to timeout'), {
              name: 'TimeoutError',
            });
          }),
        })),
      );

      const response = await handleTranslateImage(
        translateRequest({
          imageId: 'img-timeout',
          base64: 'data:image/png;base64,abcd',
          canvasSignature: 'timeout:page:1',
          settings: { pipelineMode: 'local' },
        }),
        { tab: { id: 123 } },
      );

      expect(response).toEqual({
        success: false,
        error:
          'Local backend request timed out after 240s. The image may be too large or the server is busy.',
      });
    });

    it('keeps a backend 504 provider timeout detail instead of blaming the local request', async () => {
      mocks.getFromCache.mockResolvedValue(null);
      vi.spyOn(console, 'error').mockImplementation(() => {});
      const detail =
        'Local backend error: 504 - {"detail":"Translation provider request timed out"}';
      mocks.LocalBackendProvider.mockImplementationOnce(
        constructing(() => ({
          requestTimeoutMs: 600000,
          translateImage: vi.fn(async () => {
            throw Object.assign(new Error(detail), { backendStatus: 504 });
          }),
        })),
      );

      const response = await handleTranslateImage(
        translateRequest({
          imageId: 'img-504',
          base64: `data:image/png;base64,${Buffer.from('not-an-empty-image').toString('base64')}`,
          canvasSignature: 'bookwalker:img-504',
          settings: { pipelineMode: 'local', localBackend: { url: 'http://127.0.0.1:8001' } },
        }),
        { tab: { id: 123 } },
      );

      expect(response).toEqual({ success: false, error: detail });
      expect((response as { error: string }).error).not.toContain(
        'Local backend error: Local backend error',
      );
      expect((response as { error: string }).error).not.toContain('after 600s');
    });

    it('refuses the Local LLM provider in cloud mode', async () => {
      mocks.getFromCache.mockResolvedValue(null);
      vi.spyOn(console, 'error').mockImplementation(() => {});

      const response = await handleTranslateImage(
        translateRequest({
          imageId: 'cloud-local-llm',
          base64: 'data:image/png;base64,abcd',
          canvasSignature: 'cloud-local-llm:page:1',
          settings: { pipelineMode: 'cloud', provider: 'local' },
        }),
        { tab: { id: 123 } },
      );

      expect(response.success).toBe(false);
      expect((response as { error: string }).error).toContain(
        'Cloud API mode does not support Local LLM',
      );
    });

    it('does not warn when progress cannot reach a tab without a receiver', async () => {
      mocks.getFromCache.mockResolvedValue(null);
      const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});
      stubs.tabsSendMessage.mockRejectedValue(
        new Error('Could not establish connection. Receiving end does not exist.'),
      );

      await handleTranslateImage(
        translateRequest({
          imageId: 'no-receiver',
          base64: 'data:image/png;base64,abcd',
          canvasSignature: 'no-receiver:page:1',
          settings: { pipelineMode: 'cloud', provider: 'gemini', apiKey: 'test-key' },
        }),
        { tab: { id: 123 } },
      );
      await new Promise((resolve) => setTimeout(resolve, 0));

      expect(stubs.tabsSendMessage).toHaveBeenCalled();
      expect(warning).not.toHaveBeenCalledWith(
        expect.stringContaining('notifyProgress failed'),
        expect.anything(),
      );
    });

    it('sends progress and a final error notice to the requesting tab', async () => {
      mocks.getFromCache.mockResolvedValue(null);
      vi.spyOn(console, 'error').mockImplementation(() => {});
      mocks.translateImage.mockRejectedValue(new Error('provider down'));

      const response = await handleTranslateImage(
        translateRequest({
          imageId: 'tab-notices',
          base64: 'data:image/png;base64,abcd',
          canvasSignature: 'tab-notices:page:1',
          settings: { pipelineMode: 'cloud', provider: 'gemini', apiKey: 'test-key' },
        }),
        { tab: { id: 55 } },
      );

      expect(response).toEqual({ success: false, error: 'provider down' });
      const messages = stubs.tabsSendMessage.mock.calls.map(([tabId, sent]) => [tabId, sent]);
      expect(messages).toContainEqual([
        55,
        { type: 'TRANSLATION_PROGRESS', status: 'Analyzing image...', percent: 25 },
      ]);
      expect(messages).toContainEqual([
        55,
        { type: 'TRANSLATION_ERROR', imageId: 'tab-notices', error: 'provider down' },
      ]);
    });

    it('bypasses and replaces cached translations for force refresh requests', async () => {
      const freshBlocks = [{ text: 'fresh', translated: 'Fresh', bbox: [1, 2, 3, 4] }];
      mocks.translateImage.mockResolvedValue(freshBlocks);
      const removeSpy = vi.spyOn(browser.storage.local, 'remove');

      const result = await handleTranslateImage(
        translateRequest({
          imageId: 'image-1',
          base64: 'data:image/png;base64,abcd',
          canvasSignature: 'reader:page:1',
          forceRefresh: true,
          settings: { pipelineMode: 'cloud', provider: 'gemini', apiKey: 'test-key' },
        }),
        { tab: { id: 123 } },
      );

      expect(mocks.getFromCache).not.toHaveBeenCalled();
      expect(removeSpy).toHaveBeenCalledWith('manga_cache_force_key');
      expect(mocks.saveToCache).toHaveBeenCalledWith('manga_cache_force_key', {
        mode: 'cloud',
        blocks: [{ translated: 'Fresh', bbox: [1, 2, 3, 4] }],
      });
      expect(result).toEqual({
        success: true,
        result: { mode: 'cloud', blocks: [{ translated: 'Fresh', bbox: [1, 2, 3, 4] }] },
      });
    });

    it('rejects oversized decoded payloads before hashing or provider calls', async () => {
      vi.spyOn(console, 'error').mockImplementation(() => {});
      const oversized = `data:image/png;base64,${'A'.repeat(28_000_000)}`;
      const response = await handleTranslateImage(
        translateRequest({
          imageId: 'oversized',
          base64: oversized,
          settings: { pipelineMode: 'cloud', provider: 'gemini', apiKey: 'test-key' },
        }),
        { tab: { id: 123 } },
      );

      expect(response).toEqual({
        success: false,
        error: 'Image exceeds 20 MB decoded image limit',
      });
      expect(mocks.generateCacheKey).not.toHaveBeenCalled();
      expect(mocks.TranslationProvider).not.toHaveBeenCalled();
    });

    it('rejects decoded images over the pixel limit when bitmap decoding is available', async () => {
      vi.stubGlobal(
        'createImageBitmap',
        vi.fn(async () => ({ width: 5001, height: 5000, close: vi.fn() })),
      );

      await expect(validateImagePayload('data:image/png;base64,abcd')).rejects.toThrow(
        'Image exceeds 25 MP pixel limit',
      );
    });

    it('estimates decoded sizes without decoding', () => {
      expect(estimateDecodedBase64Bytes('data:image/png;base64,aGVsbG8=')).toBe(5);
      expect(estimateDecodedBase64Bytes('aGVsbG8gd29ybGQ=')).toBe(11);
      expect(estimateDecodedBase64Bytes('')).toBe(0);
      expect(estimateDecodedBase64Bytes(null)).toBe(0);
    });

    it('does not cache a job whose provider rejected', async () => {
      vi.spyOn(console, 'error').mockImplementation(() => {});
      mocks.getFromCache.mockResolvedValue(null);
      mocks.translateImage.mockRejectedValue(new Error('Failed to parse translation response'));

      const response = await handleTranslateImage(
        translateRequest({
          imageId: 'unparseable',
          base64: 'data:image/png;base64,abcd',
          canvasSignature: 'unparseable:page:1',
          settings: { pipelineMode: 'cloud', provider: 'gemini', apiKey: 'test-key' },
        }),
        { tab: { id: 123 } },
      );

      expect(response).toEqual({ success: false, error: 'Failed to parse translation response' });
      expect(mocks.saveToCache).not.toHaveBeenCalled();
    });
  });

  describe('CHECK_BACKEND', () => {
    it('reports why the backend health check failed', async () => {
      mocks.LocalBackendProvider.mockImplementationOnce(
        constructing(() => ({
          async checkHealth(this: { lastHealthReason?: string }) {
            this.lastHealthReason = 'unauthorized';
            return false;
          },
        })),
      );

      await expect(handleCheckBackend({ pipelineMode: 'local' })).resolves.toEqual({
        success: false,
        reason: 'unauthorized',
      });
    });

    it('reports ok when the backend is ready', async () => {
      mocks.LocalBackendProvider.mockImplementationOnce(
        constructing(() => ({
          async checkHealth(this: { lastHealthReason?: string }) {
            this.lastHealthReason = 'ok';
            return true;
          },
        })),
      );

      await expect(handleCheckBackend(undefined)).resolves.toEqual({ success: true, reason: 'ok' });
    });
  });

  describe('referrer rules (DNR)', () => {
    it('uses transient fixed referrer rules for known hotlink-protected CDNs', async () => {
      const updateSessionRules = vi.fn(async (..._args: unknown[]) => undefined);
      setDeclarativeNetRequest({ updateSessionRules });
      fetchMock.mockResolvedValue(imageResponse());

      const response = await handleFetchImage('https://i.hamreus.com/books/page.png');

      expect(response.success).toBe(true);
      expect(updateSessionRules).toHaveBeenCalledTimes(2);
      const installed = updateSessionRules.mock.calls[0]?.[0] as {
        addRules: Array<{ id: number }>;
      };
      expect(installed.addRules[0]).toEqual(
        expect.objectContaining({
          action: expect.objectContaining({
            requestHeaders: [
              expect.objectContaining({ header: 'Referer', value: 'https://www.manhuagui.com/' }),
            ],
          }),
          condition: {
            requestDomains: ['i.hamreus.com'],
            urlFilter: '||i.hamreus.com/',
            resourceTypes: ['xmlhttprequest'],
          },
        }),
      );
      expect(updateSessionRules.mock.calls[1]?.[0]).toEqual({
        removeRuleIds: [installed.addRules[0]?.id],
      });
      expect(fetchMock.mock.calls[0]?.[1]).toEqual(
        expect.objectContaining({ referrer: 'https://www.manhuagui.com/' }),
      );
    });

    it('keeps every session rule id inside the owned 1000-1999 range', () => {
      for (const rule of CDN_REFERRER_RULES) {
        expect(rule.sessionRuleId).toBeGreaterThanOrEqual(1000);
        expect(rule.sessionRuleId).toBeLessThanOrEqual(1999);
      }
    });

    it('reconciles only owned stale referrer rules and preserves unrelated rules', async () => {
      const staleLegacyRule = {
        id: 1007,
        action: {
          type: 'modifyHeaders',
          requestHeaders: [
            { header: 'Referer', operation: 'set', value: 'https://www.pixiv.net/' },
          ],
        },
        condition: { urlFilter: 'pximg.net', resourceTypes: ['xmlhttprequest'] },
      };
      const unrelatedRule = {
        id: 1008,
        action: { type: 'block' },
        condition: { urlFilter: 'pximg.net', resourceTypes: ['xmlhttprequest'] },
      };
      const getSessionRules = vi.fn(async () => [staleLegacyRule, unrelatedRule]);
      const updateSessionRules = vi.fn(async (..._args: unknown[]) => undefined);
      setDeclarativeNetRequest({ getSessionRules, updateSessionRules });

      await reconcileDnrSessionRules();

      expect(updateSessionRules).toHaveBeenCalledWith({ removeRuleIds: [1007] });
      expect(updateSessionRules).not.toHaveBeenCalledWith({ removeRuleIds: [1008] });
    });

    it('recognizes owned rules only inside the id range with the exact Referer action', () => {
      const pixiv = CDN_REFERRER_RULES[0];
      if (!pixiv) throw new Error('missing pixiv rule');
      const owned = {
        id: 1000,
        action: {
          type: 'modifyHeaders',
          requestHeaders: [{ header: 'Referer', operation: 'set', value: pixiv.referrer }],
        },
        condition: {
          requestDomains: [pixiv.hostname],
          urlFilter: `||${pixiv.hostname}/`,
          resourceTypes: ['xmlhttprequest'],
        },
      };
      type Rule = Parameters<typeof isOwnedDnrRule>[0];

      expect(isOwnedDnrRule(owned as Rule, pixiv)).toBe(true);
      expect(isOwnedDnrRule({ ...owned, id: 2000 } as Rule, pixiv)).toBe(false);
      expect(isOwnedDnrRule({ ...owned, id: 999 } as Rule, pixiv)).toBe(false);
      expect(
        isOwnedDnrRule({ ...owned, action: { type: 'block' } } as unknown as Rule, pixiv),
      ).toBe(false);
    });

    it('falls back to ordinary fetching when a reserved DNR id is unrelated', async () => {
      const getSessionRules = vi.fn(async () => [
        {
          id: 1001,
          action: { type: 'block' },
          condition: { urlFilter: 'i.hamreus.com', resourceTypes: ['xmlhttprequest'] },
        },
      ]);
      const updateSessionRules = vi.fn(async (..._args: unknown[]) => undefined);
      setDeclarativeNetRequest({ getSessionRules, updateSessionRules });
      vi.spyOn(console, 'warn').mockImplementation(() => {});
      fetchMock.mockResolvedValue(imageResponse());

      const response = await handleFetchImage('https://i.hamreus.com/books/page.png');

      expect(response.success).toBe(true);
      expect(updateSessionRules).not.toHaveBeenCalled();
      expect(fetchMock.mock.calls[0]?.[1]).not.toHaveProperty('referrer');
    });

    it('falls back to ordinary fetching when DNR rule installation fails', async () => {
      const updateSessionRules = vi.fn(async (options: unknown) => {
        if ((options as { addRules?: unknown }).addRules) throw new Error('rule collision');
      });
      setDeclarativeNetRequest({ updateSessionRules });
      vi.spyOn(console, 'warn').mockImplementation(() => {});
      fetchMock.mockResolvedValue(imageResponse());

      const response = await handleFetchImage('https://hitomi.la/page.png');

      expect(response.success).toBe(true);
      expect(updateSessionRules).toHaveBeenCalledTimes(1);
      expect(fetchMock.mock.calls[0]?.[1]).not.toHaveProperty('referrer');
    });

    it('shares one DNR lease across concurrent fetches for the same CDN', async () => {
      const updateSessionRules = vi.fn(async (..._args: unknown[]) => undefined);
      setDeclarativeNetRequest({ updateSessionRules });
      const fetchResolvers: Array<(value: unknown) => void> = [];
      fetchMock.mockImplementation(() => new Promise((resolve) => fetchResolvers.push(resolve)));

      const first = handleFetchImage('https://i.hamreus.com/books/one.png');
      const second = handleFetchImage('https://i.hamreus.com/books/two.png');
      await vi.waitFor(() => expect(fetchResolvers).toHaveLength(2));

      expect(updateSessionRules).toHaveBeenCalledTimes(1);
      for (const resolve of fetchResolvers) resolve(imageResponse());

      await expect(Promise.all([first, second])).resolves.toEqual([
        expect.objectContaining({ success: true }),
        expect.objectContaining({ success: true }),
      ]);
      expect(updateSessionRules).toHaveBeenCalledTimes(2);
      expect(updateSessionRules.mock.calls[1]?.[0]).toEqual({ removeRuleIds: [1001] });
    });

    it('removes transient referrer rules when both fetch attempts fail', async () => {
      const updateSessionRules = vi.fn(async (..._args: unknown[]) => undefined);
      setDeclarativeNetRequest({ updateSessionRules });
      vi.spyOn(console, 'error').mockImplementation(() => {});
      fetchMock.mockRejectedValue(new Error('blocked'));

      const response = await handleFetchImage('https://klimv1.xyz/page.png');

      expect(response.success).toBe(false);
      expect(updateSessionRules).toHaveBeenCalledTimes(2);
      expect(updateSessionRules.mock.calls[1]?.[0]).toEqual({
        removeRuleIds: [expect.any(Number)],
      });
    });

    it('does not install a referrer rule for unknown hosts', async () => {
      const updateSessionRules = vi.fn(async (..._args: unknown[]) => undefined);
      setDeclarativeNetRequest({ updateSessionRules });
      fetchMock.mockResolvedValue(imageResponse());

      const response = await handleFetchImage('https://images.example.com/page.png');

      expect(response.success).toBe(true);
      expect(updateSessionRules).not.toHaveBeenCalled();
      expect(fetchMock.mock.calls[0]?.[1]).not.toHaveProperty('referrer');
    });

    it('runs the operation with no rule when declarativeNetRequest is unavailable', async () => {
      const operation = vi.fn(async (rule: unknown) => rule);

      await expect(
        withDnrReferrerRule('https://i.hamreus.com/a.png', operation),
      ).resolves.toBeNull();
    });

    // blobToBase64 settles on a macrotask, so microtask-only loops are not enough here.
    describe('lease races', () => {
      it('reinstalls the rule when a fetch starts while the previous lease is being removed', async () => {
        type Options = { removeRuleIds?: number[]; addRules?: Array<{ id: number }> };
        let rules: Array<{ id: number }> = [];
        let finishRemove: (() => void) | null = null;
        const updateSessionRules = vi.fn((options: Options) => {
          const apply = () => {
            rules = rules.filter((rule) => !(options.removeRuleIds || []).includes(rule.id));
            rules.push(...(options.addRules || []));
          };
          if (options.removeRuleIds && !options.addRules && !finishRemove) {
            return new Promise<void>((resolve) => {
              finishRemove = () => {
                apply();
                resolve();
              };
            });
          }
          apply();
          return Promise.resolve();
        });
        setDeclarativeNetRequest({ updateSessionRules });
        const fetchResolvers: Array<(value: unknown) => void> = [];
        fetchMock
          .mockResolvedValueOnce(imageResponse())
          .mockImplementation(() => new Promise((resolve) => fetchResolvers.push(resolve)));

        const first = handleFetchImage('https://i.hamreus.com/books/one.png');
        await vi.waitFor(() => expect(finishRemove).toEqual(expect.any(Function)));
        expect(updateSessionRules).toHaveBeenCalledTimes(2);

        const second = handleFetchImage('https://i.hamreus.com/books/two.png');
        await new Promise((resolve) => setTimeout(resolve, 20));
        // The install must wait for the pending remove instead of trusting the doomed rule.
        expect(updateSessionRules).toHaveBeenCalledTimes(2);
        expect(fetchResolvers).toHaveLength(0);

        (finishRemove as unknown as () => void)();
        await vi.waitFor(() => expect(fetchResolvers.length).toBeGreaterThan(0));

        expect(fetchResolvers).toHaveLength(1);
        expect(updateSessionRules).toHaveBeenCalledTimes(3);
        expect(updateSessionRules.mock.calls[1]?.[0]).toEqual({ removeRuleIds: [1001] });
        expect(updateSessionRules.mock.calls[2]?.[0].addRules).toHaveLength(1);
        expect(rules.map((rule) => rule.id)).toEqual([1001]);

        fetchResolvers[0]?.(imageResponse());
        await expect(Promise.all([first, second])).resolves.toEqual([
          expect.objectContaining({ success: true }),
          expect.objectContaining({ success: true }),
        ]);
        expect(rules).toEqual([]);
      });

      it('keeps the rule tracked when its removal fails so the next acquire does not double-install', async () => {
        vi.spyOn(console, 'warn').mockImplementation(() => {});
        const updateSessionRules = vi.fn(
          async (options: { removeRuleIds?: number[]; addRules?: unknown[] }) => {
            if (options.removeRuleIds && !options.addRules) throw new Error('remove failed');
          },
        );
        setDeclarativeNetRequest({ updateSessionRules });
        fetchMock.mockResolvedValue(imageResponse());

        await handleFetchImage('https://i.hamreus.com/books/one.png');
        await handleFetchImage('https://i.hamreus.com/books/two.png');

        // add, failed remove, then the second fetch reuses the still-installed rule.
        expect(updateSessionRules.mock.calls.map(([options]) => Object.keys(options))).toEqual([
          ['addRules'],
          ['removeRuleIds'],
          ['removeRuleIds'],
        ]);
      });
    });
  });

  describe('message listener', () => {
    const noResponse = () => vi.fn();

    it('does not handle the removed CHECK_CACHE message or touch arbitrary storage keys', async () => {
      const apiKeys = { apiKey: 'secret-key', localLlmApiKey: '' };
      await browser.storage.local.set({ apiKeys });
      const removeSpy = vi.spyOn(browser.storage.local, 'remove');
      const sendResponse = noResponse();

      expect(
        handleRuntimeMessage({ type: 'CHECK_CACHE', cacheKey: 'apiKeys' }, {}, sendResponse),
      ).toBe(false);
      await Promise.resolve();

      expect(sendResponse).not.toHaveBeenCalled();
      expect(mocks.getFromCache).not.toHaveBeenCalled();
      expect(removeSpy).not.toHaveBeenCalled();
      expect((await browser.storage.local.get('apiKeys')).apiKeys).toEqual(apiKeys);
    });

    it('ignores null, typeless and non-string-typed messages without throwing', () => {
      const sendResponse = noResponse();

      expect(handleRuntimeMessage(null, {}, sendResponse)).toBe(false);
      expect(handleRuntimeMessage(undefined, {}, sendResponse)).toBe(false);
      expect(handleRuntimeMessage({}, {}, sendResponse)).toBe(false);
      expect(handleRuntimeMessage({ type: 42 }, {}, sendResponse)).toBe(false);
      expect(sendResponse).not.toHaveBeenCalled();
    });

    it('ignores broadcast messages addressed to tabs and the popup', () => {
      const sendResponse = noResponse();

      for (const type of [
        'TRANSLATE_PAGE',
        'TRANSLATION_PROGRESS',
        'TRANSLATION_ERROR',
        'TRANSLATION_COMPLETE',
        'SETTINGS_UPDATED',
      ]) {
        expect(handleRuntimeMessage({ type }, {}, sendResponse)).toBe(false);
      }
      expect(sendResponse).not.toHaveBeenCalled();
    });

    it('still answers supported async message types', async () => {
      const response = await new Promise((resolve) => {
        expect(
          handleRuntimeMessage(
            { type: 'FETCH_IMAGE', url: 'ftp://example.test/a.png' },
            {},
            resolve,
          ),
        ).toBe(true);
      });

      expect(response).toEqual({ success: false, error: 'Unsupported URL scheme: ftp' });
    });

    it('answers a handler failure with a failure response', async () => {
      vi.spyOn(console, 'error').mockImplementation(() => {});
      fetchMock.mockRejectedValue(new Error('offline'));
      const response = await new Promise((resolve) => {
        handleRuntimeMessage({ type: 'GET_BUNDLED_FONT', fontId: 'cc-wild-words' }, {}, resolve);
      });

      expect(response).toEqual({ success: false, error: 'offline' });
    });

    it('clears the cache and reports cache statistics', async () => {
      await browser.storage.local.set({
        manga_cache_a: { timestamp: Date.now(), data: { a: 1 } },
        manga_cache_b: { timestamp: Date.now(), data: { b: 2 } },
      });

      const stats = await new Promise<{ success: boolean; stats: { count: number } }>((resolve) => {
        handleRuntimeMessage({ type: 'GET_CACHE_STATS' }, {}, resolve as (r: unknown) => void);
      });
      expect(stats.success).toBe(true);
      expect(stats.stats.count).toBe(2);

      const cleared = await new Promise((resolve) => {
        handleRuntimeMessage({ type: 'CLEAR_CACHE' }, {}, resolve);
      });
      expect(cleared).toEqual({ success: true, cleared: 2 });
    });
  });

  describe('FETCH_IMAGE validation', () => {
    it('rejects invalid URLs', async () => {
      await expect(handleFetchImage('')).resolves.toEqual({
        success: false,
        error: 'Invalid URL for FETCH_IMAGE',
      });
      await expect(handleFetchImage(undefined)).resolves.toEqual({
        success: false,
        error: 'Invalid URL for FETCH_IMAGE',
      });
    });

    it('rejects blobs larger than the decoded image limit', async () => {
      vi.spyOn(console, 'error').mockImplementation(() => {});
      mocks.blobToBase64.mockResolvedValue('data:image/png;base64,abcd');
      fetchMock.mockResolvedValue({
        ok: true,
        blob: async () => ({ size: 20 * 1024 * 1024 + 1, type: 'image/png' }),
      });

      const response = await handleFetchImage('https://images.example.com/huge.png');

      expect(response).toEqual({
        success: false,
        error: 'Image exceeds 20 MB decoded image limit',
      });
      expect(mocks.blobToBase64).not.toHaveBeenCalled();
    });

    it('rejects empty bodies', async () => {
      vi.spyOn(console, 'error').mockImplementation(() => {});
      fetchMock.mockResolvedValue({
        ok: true,
        blob: async () => new Blob([], { type: 'image/png' }),
      });

      await expect(handleFetchImage('https://images.example.com/empty.png')).resolves.toEqual({
        success: false,
        error: 'Image response was empty',
      });
    });

    it('rejects responses with a non-image content type', async () => {
      vi.spyOn(console, 'error').mockImplementation(() => {});
      mocks.blobToBase64.mockResolvedValue('data:text/html;base64,abcd');
      fetchMock.mockResolvedValue({
        ok: true,
        blob: async () => new Blob(['<html></html>'], { type: 'text/html' }),
      });

      const response = await handleFetchImage('https://images.example.com/page.html');

      expect(response.success).toBe(false);
      expect((response as { error: string }).error).toContain('text/html');
      expect(mocks.blobToBase64).not.toHaveBeenCalled();
    });

    it('accepts images with an empty or octet-stream content type', async () => {
      mocks.blobToBase64.mockResolvedValue('data:image/png;base64,abcd');
      fetchMock.mockResolvedValue({ ok: true, blob: async () => new Blob(['image']) });
      await expect(handleFetchImage('https://images.example.com/a')).resolves.toEqual(
        expect.objectContaining({ success: true }),
      );

      fetchMock.mockResolvedValue({
        ok: true,
        blob: async () => new Blob(['image'], { type: 'application/octet-stream' }),
      });
      await expect(handleFetchImage('https://images.example.com/b')).resolves.toEqual(
        expect.objectContaining({ success: true }),
      );
    });

    it('fetches with credentials omitted and caching disabled', async () => {
      fetchMock.mockResolvedValue(imageResponse());

      await handleFetchImage('https://images.example.com/a.png');

      expect(fetchMock.mock.calls[0]?.[1]).toEqual(
        expect.objectContaining({
          method: 'GET',
          mode: 'cors',
          credentials: 'omit',
          cache: 'no-store',
        }),
      );
    });

    it('surfaces the real HTTP failure without a no-cors retry', async () => {
      vi.spyOn(console, 'error').mockImplementation(() => {});
      fetchMock.mockResolvedValue({ ok: false, status: 403 });

      const response = await handleFetchImage('https://images.example.com/denied.png');

      expect(response).toEqual({ success: false, error: 'HTTP 403 fetching image' });
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(fetchMock.mock.calls[0]?.[1]).toEqual(expect.objectContaining({ mode: 'cors' }));
    });

    it('surfaces the network error message', async () => {
      vi.spyOn(console, 'error').mockImplementation(() => {});
      fetchMock.mockRejectedValue(new Error('Failed to fetch'));

      const response = await handleFetchImage('https://images.example.com/down.png');

      expect(response).toEqual({ success: false, error: 'Failed to fetch' });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
  });

  describe('cache pruning', () => {
    it('prunes expired cache entries on startup and install without blocking them', async () => {
      mocks.pruneExpiredCache.mockImplementation(() => new Promise(() => {}));

      await handleExtensionStartup();
      expect(mocks.pruneExpiredCache).toHaveBeenCalledTimes(1);

      await handleExtensionInstalled();
      expect(mocks.pruneExpiredCache).toHaveBeenCalledTimes(2);
    });

    it('returns the pruned count', async () => {
      await expect(pruneCacheSafely()).resolves.toBe(0);

      mocks.pruneExpiredCache.mockResolvedValue(4);
      await expect(pruneCacheSafely()).resolves.toBe(4);
    });

    it('does not throw when pruning fails', async () => {
      const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});
      mocks.pruneExpiredCache.mockRejectedValue(new Error('storage unavailable'));

      await expect(pruneCacheSafely()).resolves.toBe(0);
      await expect(handleExtensionStartup()).resolves.toBeUndefined();
      expect(warning).toHaveBeenCalledWith(
        '[Service Worker] Failed to prune cache:',
        'storage unavailable',
      );
    });
  });

  describe('legacy synced API keys', () => {
    const syncedSettings = (overrides = {}) => ({
      settingsVersion: 2,
      provider: 'openrouter',
      ...overrides,
    });

    it('moves legacy synced API keys to local-only storage', async () => {
      await browser.storage.sync.set({
        settings: syncedSettings({
          apiKey: 'legacy-key',
          localLlm: { apiKey: 'llm-key', model: 'qwen' },
          localBackend: { authToken: 'backend-token' },
        }),
      });

      await expect(migrateStoredSettingsSafely()).resolves.toBe(true);

      const local = await browser.storage.local.get('apiKeys');
      expect(local.apiKeys).toEqual({
        apiKey: 'legacy-key',
        localLlmApiKey: 'llm-key',
        backendAuthToken: 'backend-token',
      });
      const settings = await storedSettings();
      expect(settings.provider).toBe('openrouter');
      expect(settings.localLlm?.model).toBe('qwen');
      expect(settings).not.toHaveProperty('apiKey');
      expect(settings.localLlm!).not.toHaveProperty('apiKey');
      expect(settings.localBackend!).not.toHaveProperty('authToken');
      expect(JSON.stringify(settings)).not.toMatch(/legacy-key|llm-key|backend-token/);
    });

    it('moves legacy keys even when no version migration is pending and does not clobber local keys', async () => {
      await browser.storage.local.set({
        apiKeys: { apiKey: 'local-key', localLlmApiKey: 'local-llm' },
      });
      await browser.storage.sync.set({
        settings: syncedSettings({
          apiKey: 'legacy-key',
          localLlm: { apiKey: 'legacy-llm' },
        }),
      });

      await expect(migrateStoredSettingsSafely()).resolves.toBe(true);

      const local = await browser.storage.local.get('apiKeys');
      expect(local.apiKeys).toEqual({
        apiKey: 'local-key',
        localLlmApiKey: 'local-llm',
      });
      const settings = await storedSettings();
      expect(settings).not.toHaveProperty('apiKey');
      expect(settings.localLlm!).not.toHaveProperty('apiKey');
    });

    it('writes nothing when no legacy keys are synced', async () => {
      await browser.storage.sync.set({
        settings: syncedSettings({ apiKey: '' }),
      });
      const syncSet = vi.spyOn(browser.storage.sync, 'set');
      const localSet = vi.spyOn(browser.storage.local, 'set');

      await expect(migrateStoredSettingsSafely()).resolves.toBe(false);

      expect(syncSet).not.toHaveBeenCalled();
      expect(localSet).not.toHaveBeenCalled();
    });

    it('leaves sync untouched when the local key write fails', async () => {
      vi.spyOn(console, 'warn').mockImplementation(() => {});
      await browser.storage.sync.set({ settings: syncedSettings({ apiKey: 'legacy-key' }) });
      const syncSet = vi.spyOn(browser.storage.sync, 'set');
      vi.spyOn(browser.storage.local, 'set').mockRejectedValueOnce(new Error('quota'));

      await expect(migrateStoredSettingsSafely()).resolves.toBe(false);

      expect(syncSet).not.toHaveBeenCalled();
      expect((await storedSettings()).apiKey).toBe('legacy-key');
    });
  });

  describe('browser API promise handling', () => {
    const rejectedWith = (message: string) => {
      const promise = Promise.reject(new Error(message));
      const then = vi.spyOn(promise, 'then');
      return { promise, then };
    };

    it('attaches a rejection handler to context menu messages and ignores a missing receiver', () => {
      const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});
      const { promise, then } = rejectedWith(
        'Could not establish connection. Receiving end does not exist.',
      );
      stubs.tabsSendMessage.mockReturnValueOnce(promise);

      handleContextMenuClick({ menuItemId: 'translate-page' }, { id: 7 });

      expect(then).toHaveBeenCalledWith(undefined, expect.any(Function));
      return promise
        .then(undefined, () => {})
        .then(() => {
          expect(warning).not.toHaveBeenCalled();
        });
    });

    it('handles rejected action setters for closed tabs', async () => {
      const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});
      const setters = ['setIcon', 'setTitle', 'setBadgeText', 'setBadgeBackgroundColor'] as const;
      const spies = setters.map((name) => {
        const { promise, then } = rejectedWith('No tab with id: 55.');
        stubs.action[name].mockReturnValueOnce(promise as Promise<undefined>);
        return then;
      });

      setActionState(55, false);
      await new Promise((resolve) => setTimeout(resolve, 0));

      for (const then of spies) expect(then).toHaveBeenCalledWith(undefined, expect.any(Function));
      expect(warning).not.toHaveBeenCalled();
    });

    it('warns about unexpected rejections and tolerates non-promise results', async () => {
      const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});

      expect(safeBrowserCall(undefined)).toBeUndefined();
      expect(safeBrowserCall('ok')).toBe('ok');
      safeBrowserCall(Promise.reject(new Error('Extension context invalidated')));
      await new Promise((resolve) => setTimeout(resolve, 0));

      expect(warning).toHaveBeenCalledWith(
        '[Service Worker] Chrome API call failed:',
        'Extension context invalidated',
      );
    });
  });
});
