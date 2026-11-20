import { beforeEach, describe, expect, it, vi } from 'vitest';
import { browser } from 'wxt/browser';
import { enqueueBookWalkerCanvas } from '../../extension/content/bookwalker-queue';
import { restoreCanvasTranslationFromMemory } from '../../extension/content/canvas-memory';
import {
  assertCaptureBase64Size,
  captureCanvasImageBase64,
  fetchAndConvertToBase64,
  isCaptureDimensionsAllowed,
} from '../../extension/content/capture';
import {
  clearBackgroundTranslation,
  clearCompositeTranslation,
  clearImgTranslation,
  clearPageTranslations,
  requestStopTranslation,
} from '../../extension/content/clear';
import { isAlreadyTranslatedElement, isTranslatableImage } from '../../extension/content/discovery';
import { isTranslationTransportError } from '../../extension/content/errors';
import { ensureBundledFontLoaded } from '../../extension/content/fonts';
import { handleMessage } from '../../extension/content/messages';
import { startRegionSelection } from '../../extension/content/region';
import {
  cleanupBlobUrls,
  getSiteRuntimeState,
  refreshSiteActivation,
  startSiteRuntime,
  stopSiteRuntime,
} from '../../extension/content/runtime';
import { loadSettings } from '../../extension/content/settings-store';
import { resetState, state } from '../../extension/content/state';
import {
  handleTranslationResult,
  processImage,
  sendTranslationImageRequest,
  translateImageData,
  translatePage,
} from '../../extension/content/translate';
import type { TranslateImageRequest } from '../../extension/shared/messages';
import {
  createLoadedImage,
  createVisibleBookWalkerCanvas,
  mockCanvasSampling,
  mockFlatCanvasSampling,
  mockImageCapture,
  mockRuntimeSendMessage,
  type SendMessageMock,
  sentMessages,
  setElementsFromPoint,
} from '../helpers/content-env';

const translatedImage = vi.hoisted(() => ({
  getTranslatedSrc: vi.fn(),
  revokeTranslatedSrc: vi.fn(),
  actual: null as null | typeof import('../../extension/lib/translated-image'),
}));

vi.mock('../../extension/lib/translated-image', async (importOriginal) => {
  translatedImage.actual =
    await importOriginal<typeof import('../../extension/lib/translated-image')>();
  return {
    getTranslatedSrc: translatedImage.getTranslatedSrc,
    revokeTranslatedSrc: translatedImage.revokeTranslatedSrc,
  };
});

vi.setConfig({ testTimeout: 20_000 });

function base64Of(text: string): string {
  return Buffer.from(text).toString('base64');
}

function bannerText(): string {
  return document.getElementById('manga-error-banner')?.shadowRoot?.textContent ?? '';
}

function progressText(): string | null {
  return (
    document
      .querySelector('#manga-global-progress')
      ?.shadowRoot?.querySelector('.manga-progress-text')?.textContent ?? null
  );
}

describe('content script', () => {
  let sendMessage: SendMessageMock;

  /** Answer worker messages by type; anything unlisted resolves `undefined`. */
  function respond(handlers: Record<string, (message: { type: string }) => unknown>) {
    sendMessage.mockImplementation(async (message) => handlers[message.type]?.(message));
  }

  beforeEach(() => {
    resetState();
    translatedImage.getTranslatedSrc.mockReset();
    translatedImage.revokeTranslatedSrc.mockReset();
    sendMessage = mockRuntimeSendMessage();
    setElementsFromPoint(() => []);
    vi.stubGlobal('fetch', vi.fn());
  });

  it('converts data URL background sources locally without service worker fetch', async () => {
    const base64 = base64Of('inline-image');

    const result = await fetchAndConvertToBase64(`data:image/png;base64,${base64}`);

    expect(result).toBe(base64);
    expect(sendMessage).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('routes relative remote image fetches through the background with a normalized URL', async () => {
    const base = document.createElement('base');
    base.href = 'https://reader.example.com/chapter/page.html';
    document.head.appendChild(base);

    sendMessage.mockResolvedValueOnce({ success: true, base64: 'cmVtb3RlLWltYWdl' });

    try {
      await expect(fetchAndConvertToBase64('panel.png')).resolves.toBe('cmVtb3RlLWltYWdl');

      expect(sendMessage).toHaveBeenCalledWith({
        type: 'FETCH_IMAGE',
        url: 'https://reader.example.com/chapter/panel.png',
      });
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      base.remove();
    }
  });

  it('retries a translation request once after a worker transport failure', async () => {
    const message: TranslateImageRequest = {
      type: 'TRANSLATE_IMAGE',
      imageId: 'retry-1',
      base64: 'image-data',
      settings: {},
    };
    const transportError = new Error(
      'Could not establish connection. Receiving end does not exist.',
    );
    sendMessage.mockRejectedValueOnce(transportError).mockResolvedValueOnce({ success: true });

    await expect(sendTranslationImageRequest(message)).resolves.toEqual({ success: true });
    expect(sendMessage).toHaveBeenCalledTimes(2);
    expect(isTranslationTransportError(transportError)).toBe(true);

    sendMessage.mockReset();
    sendMessage.mockRejectedValue(transportError);
    await expect(sendTranslationImageRequest({ ...message, forceRefresh: true })).rejects.toThrow(
      'Could not establish connection',
    );
    expect(sendMessage).toHaveBeenCalledTimes(1);
  });

  describe('bundled fonts', () => {
    const WILD_WORDS = "'CC Wild Words', sans-serif";
    let fontAdd: ReturnType<typeof vi.fn>;
    const faces: Array<{ family: string; source: ArrayBuffer }> = [];

    beforeEach(() => {
      faces.length = 0;
      class FakeFontFace {
        family: string;
        source: ArrayBuffer;
        constructor(family: string, source: ArrayBuffer) {
          this.family = family;
          this.source = source;
          faces.push(this);
        }
        async load() {
          return this;
        }
      }
      vi.stubGlobal('FontFace', FakeFontFace);
      fontAdd = vi.fn();
      Object.defineProperty(document, 'fonts', {
        configurable: true,
        value: { add: fontAdd },
      });
    });

    it('registers a bundled font once from worker-provided bytes', async () => {
      sendMessage.mockResolvedValue({ success: true, base64: btoa('font') });

      await expect(ensureBundledFontLoaded(WILD_WORDS)).resolves.toBe(true);
      await expect(ensureBundledFontLoaded(WILD_WORDS)).resolves.toBe(true);

      expect(sentMessages(sendMessage, 'GET_BUNDLED_FONT')).toEqual([
        { type: 'GET_BUNDLED_FONT', fontId: 'cc-wild-words' },
      ]);
      expect(faces[0]?.family).toBe('CC Wild Words');
      expect(Buffer.from(faces[0]?.source as ArrayBuffer).toString()).toBe('font');
      expect(fontAdd).toHaveBeenCalledTimes(1);
    });

    it('does not request fonts that are not bundled', async () => {
      await expect(ensureBundledFontLoaded('sans-serif')).resolves.toBe(false);
      expect(sendMessage).not.toHaveBeenCalled();
    });

    it('falls back on failure and retries on the next render', async () => {
      const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
      sendMessage.mockResolvedValueOnce({ success: false, error: 'read failed' });
      await expect(ensureBundledFontLoaded(WILD_WORDS)).resolves.toBe(false);
      expect(fontAdd).not.toHaveBeenCalled();

      sendMessage.mockResolvedValueOnce({ success: true, base64: btoa('font') });
      await expect(ensureBundledFontLoaded(WILD_WORDS)).resolves.toBe(true);
      expect(fontAdd).toHaveBeenCalledTimes(1);
      warn.mockRestore();
    });
  });

  describe('region selection', () => {
    function getSelector(): HTMLElement | null {
      return document.getElementById('manga-region-selector');
    }

    function drag(
      selector: HTMLElement,
      from: { x: number; y: number },
      to: { x: number; y: number },
    ) {
      const fire = (type: string, point: { x: number; y: number }) =>
        selector.dispatchEvent(
          new MouseEvent(type, { bubbles: true, clientX: point.x, clientY: point.y }),
        );
      fire('pointerdown', from);
      fire('pointermove', to);
      fire('pointerup', to);
    }

    const captureCalls = () => sentMessages(sendMessage, 'CAPTURE_REGION');

    it('cancels on Escape without capturing anything', () => {
      startRegionSelection();
      expect(getSelector()).not.toBeNull();

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));

      expect(getSelector()).toBeNull();
      expect(captureCalls()).toEqual([]);
    });

    it('opens one selector at a time and removes it when the runtime stops', () => {
      startRegionSelection();
      startRegionSelection();
      expect(document.querySelectorAll('#manga-region-selector')).toHaveLength(1);

      stopSiteRuntime();
      expect(getSelector()).toBeNull();
    });

    it('does not capture a selection below the minimum size', () => {
      startRegionSelection();
      drag(getSelector() as HTMLElement, { x: 10, y: 10 }, { x: 20, y: 200 });

      expect(getSelector()).toBeNull();
      expect(captureCalls()).toEqual([]);
    });

    it('sends only the dragged rectangle and removes the crop when capture fails', async () => {
      (window as unknown as { innerWidth: number }).innerWidth = 1000;
      (window as unknown as { innerHeight: number }).innerHeight = 800;
      respond({ CAPTURE_REGION: () => ({ success: false, error: 'capture denied' }) });

      startRegionSelection();
      drag(getSelector() as HTMLElement, { x: 300, y: 200 }, { x: 100, y: 50 });

      await new Promise((resolve) => setTimeout(resolve, 200));

      expect(captureCalls()).toEqual([
        {
          type: 'CAPTURE_REGION',
          rect: { left: 100, top: 50, width: 200, height: 150 },
          viewport: { width: 1000, height: 800 },
        },
      ]);
      expect(document.querySelector('.manga-region-translation')).toBeNull();
      expect(bannerText()).toContain('capture denied');
    });

    it('never treats a region crop as a page image', () => {
      const crop = document.createElement('img');
      crop.dataset.mangaOriginalSrc = 'region-capture';
      document.body.appendChild(crop);
      expect(isTranslatableImage(crop)).toBe(false);
    });
  });

  describe('site runtime', () => {
    it('disposes site runtime listeners and recreates them after re-enable', async () => {
      startSiteRuntime();
      expect(getSiteRuntimeState()).toEqual({ enabled: true, started: true, hasScope: true });
      expect(document.querySelectorAll('#manga-reader-controls')).toHaveLength(1);
      expect(document.querySelector('#manga-panel-controls')).toBeNull();

      await browser.storage.local.set({ disabledUrls: [location.hostname] });
      await refreshSiteActivation();
      expect(getSiteRuntimeState()).toEqual({ enabled: false, started: false, hasScope: false });
      expect(document.querySelector('#manga-reader-controls')).toBeNull();

      vi.spyOn(browser.storage.local, 'get').mockRejectedValueOnce(
        new Error('storage unavailable'),
      );
      const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
      await refreshSiteActivation();
      expect(getSiteRuntimeState().enabled).toBe(false);
      warn.mockRestore();

      await browser.storage.local.set({ disabledUrls: [] });
      await refreshSiteActivation();
      expect(getSiteRuntimeState()).toEqual({ enabled: true, started: true, hasScope: true });
      expect(document.querySelectorAll('#manga-reader-controls')).toHaveLength(1);
      expect(document.querySelector('#manga-panel-controls')).toBeNull();

      stopSiteRuntime();
    });

    it('loads settings lazily: nothing is read until the site is active or a command arrives', async () => {
      await browser.storage.local.set({ disabledUrls: [location.hostname] });
      await refreshSiteActivation();

      expect(state.settings).toBeNull();
      expect(document.querySelector('#manga-reader-controls')).toBeNull();
      expect(state.blobUrlCleanupObserver).toBeNull();
    });
  });

  describe('message handling', () => {
    function dispatch(message: unknown): Promise<unknown> {
      return new Promise((resolve) => {
        const handled = handleMessage(message, {}, resolve);
        if (!handled) resolve('unhandled');
      });
    }

    it('ignores messages it does not own', async () => {
      await expect(dispatch({ type: 'CHECK_BACKEND' })).resolves.toBe('unhandled');
      await expect(dispatch(null)).resolves.toBe('unhandled');
    });

    it('rejects gated commands on a disabled site but still clears and stops', async () => {
      state.siteEnabled = false;

      await expect(dispatch({ type: 'TRANSLATE_PAGE' })).resolves.toEqual({
        success: false,
        error: 'Manga Translator is disabled on this site',
      });
      await expect(dispatch({ type: 'START_REGION_SELECTION' })).resolves.toEqual({
        success: false,
        error: 'Manga Translator is disabled on this site',
      });
      await expect(dispatch({ type: 'STOP_TRANSLATION' })).resolves.toEqual({
        success: true,
        stopped: false,
      });
      await expect(dispatch({ type: 'CLEAR_PAGE_TRANSLATIONS' })).resolves.toEqual({
        success: true,
        cleared: 0,
      });
    });

    it('acknowledges translate commands before running them', async () => {
      await expect(dispatch({ type: 'TRANSLATE_VISIBLE_IMAGES' })).resolves.toEqual({
        success: true,
      });
    });

    it('applies worker error reports to the processing element', async () => {
      const img = document.createElement('img');
      img.setAttribute('data-manga-processing', 'err-1');
      document.body.appendChild(img);

      await expect(
        dispatch({ type: 'TRANSLATION_ERROR', imageId: 'err-1', error: 'boom' }),
      ).resolves.toEqual({ success: true });
      expect(img.hasAttribute('data-manga-processing')).toBe(false);
    });

    it('reloads settings on SETTINGS_UPDATED', async () => {
      await browser.storage.sync.set({ settings: { targetLang: 'French' } });

      await expect(dispatch({ type: 'SETTINGS_UPDATED', settings: {} })).resolves.toEqual({
        success: true,
      });
      expect(state.settings?.targetLang).toBe('French');
    });
  });

  it('rejects empty fetch URLs before messaging the background', async () => {
    await expect(fetchAndConvertToBase64('')).rejects.toThrow('empty URL');

    expect(sendMessage).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('rejects oversized images before capture allocates or uploads them', () => {
    expect(isCaptureDimensionsAllowed(5000, 5000)).toBe(true);
    expect(isCaptureDimensionsAllowed(5001, 5000)).toBe(false);
    expect(() => assertCaptureBase64Size('x'.repeat(28_000_000))).toThrow('20 MB capture limit');
  });

  it('restores all mutated background styles when clearing a translation', () => {
    translatedImage.getTranslatedSrc.mockReturnValue('translated-bg.png');

    const element = document.createElement('section');
    element.setAttribute('data-manga-processing', 'bg-1');
    element.style.backgroundImage = 'url("original.png")';
    element.style.backgroundSize = 'contain';
    element.style.backgroundRepeat = 'repeat-x';
    element.style.backgroundPosition = 'left top';
    document.body.appendChild(element);

    const result = handleTranslationResult('bg-1', {
      translatedImage: base64Of('translated'),
      mimeType: 'image/png',
      mode: 'local',
    });

    expect(result).toBe(true);
    expect(element.style.backgroundImage).toContain('translated-bg.png');
    expect(element.style.backgroundSize).toBe('cover');
    expect(element.style.backgroundRepeat).toBe('no-repeat');
    expect(element.style.backgroundPosition).toMatch(/^center( center)?$/);

    clearBackgroundTranslation(element);
    expect(element.style.backgroundImage).toContain('original.png');
    expect(element.style.backgroundSize).toBe('contain');
    expect(element.style.backgroundRepeat).toBe('repeat-x');
    expect(element.style.backgroundPosition).toBe('left top');
  });

  it('does not treat a sibling canvas overlay as this slide translation', () => {
    const container = document.createElement('div');
    const translatedCanvas = document.createElement('canvas');
    const nextCanvas = document.createElement('canvas');
    const overlay = document.createElement('img');
    translatedCanvas.setAttribute('data-manga-translated', 'true');
    translatedCanvas.dataset.mangaCanvasOverlayId = 'first-slide';
    overlay.className = 'manga-translated-img-overlay';
    overlay.dataset.mangaCanvasOverlayId = 'first-slide';
    container.append(translatedCanvas, nextCanvas, overlay);
    document.body.appendChild(container);

    expect(isAlreadyTranslatedElement(translatedCanvas)).toBe(true);
    expect(isAlreadyTranslatedElement(nextCanvas)).toBe(false);
  });

  describe('BookWalker canvases', () => {
    /** A canvas whose stored source no longer matches what it shows. */
    function markCanvasWithStaleStoredSource(canvas: HTMLCanvasElement, imageId = 'canvas-stale') {
      canvas.setAttribute('data-manga-processing', imageId);
      canvas.dataset.mangaCanvasTranslationKey = 'bookwalker:old-page';
      canvas.setAttribute('data-manga-translation-key', 'bookwalker:old-page');
      canvas.dataset.mangaCanvasWidth = String(canvas.width);
      canvas.dataset.mangaCanvasHeight = String(canvas.height);
      return imageId;
    }

    const LATE_BLOCKS = [
      { bbox: [0, 0, 100, 40] as [number, number, number, number], translated: 'late' },
    ];

    it('stops page translation before image processing when backend readiness fails', async () => {
      const { canvas } = createVisibleBookWalkerCanvas();
      respond({ CHECK_BACKEND: () => ({ success: false, error: 'offline' }) });

      await translatePage();

      expect(sentMessages(sendMessage, 'CHECK_BACKEND')).not.toHaveLength(0);
      expect(sentMessages(sendMessage, 'TRANSLATE_IMAGE')).toHaveLength(0);
      expect(canvas.hasAttribute('data-manga-processing')).toBe(false);
      expect(canvas.hasAttribute('data-manga-translated')).toBe(false);
    });

    it('blocks normal BookWalker canvas translation after a stale canvas result', async () => {
      mockCanvasSampling();
      const { canvas } = createVisibleBookWalkerCanvas();
      setElementsFromPoint(() => [canvas]);
      respond({ CHECK_BACKEND: () => ({ success: true }) });

      const imageId = markCanvasWithStaleStoredSource(canvas);

      const result = handleTranslationResult(imageId, { blocks: LATE_BLOCKS });

      expect(result).toBe(false);
      expect(canvas.hasAttribute('data-manga-processing')).toBe(false);
      expect(canvas.hasAttribute('data-manga-translated')).toBe(false);

      await translatePage();

      expect(sentMessages(sendMessage, 'CHECK_BACKEND')).not.toHaveLength(0);
      expect(sentMessages(sendMessage, 'TRANSLATE_IMAGE')).toHaveLength(0);
      expect(canvas.hasAttribute('data-manga-processing')).toBe(false);
      expect(canvas.hasAttribute('data-manga-translated')).toBe(false);
    });

    it('lets manual BookWalker translation bypass a stale canvas retry block', async () => {
      mockCanvasSampling();
      const canvasBase64 = base64Of('canvas-image-with-enough-content'.repeat(8));
      const { canvas } = createVisibleBookWalkerCanvas();
      setElementsFromPoint(() => [canvas]);
      vi.spyOn(canvas, 'toDataURL').mockReturnValue(`data:image/png;base64,${canvasBase64}`);

      const imageId = markCanvasWithStaleStoredSource(canvas);
      expect(handleTranslationResult(imageId, { blocks: LATE_BLOCKS })).toBe(false);

      respond({
        CHECK_BACKEND: () => ({ success: true }),
        TRANSLATE_IMAGE: () => ({
          success: true,
          result: { blocks: [{ bbox: [0, 0, 100, 40], translated: 'manual' }] },
        }),
      });

      await translatePage({ bypassRetryBlock: true });

      expect(sentMessages(sendMessage, 'CHECK_BACKEND')).not.toHaveLength(0);
      expect(sentMessages(sendMessage, 'TRANSLATE_IMAGE')[0]).toEqual(
        expect.objectContaining({ type: 'TRANSLATE_IMAGE', base64: canvasBase64 }),
      );
      expect(canvas.getAttribute('data-manga-translated')).toBe('true');
      expect(canvas.hasAttribute('data-manga-processing')).toBe(false);
    });

    it('clears remembered canvas translations so translate all can requeue', () => {
      mockFlatCanvasSampling();
      translatedImage.getTranslatedSrc.mockReturnValue('translated-canvas.png');

      const canvas = document.createElement('canvas');
      canvas.width = 930;
      canvas.height = 665;
      canvas.setAttribute('data-manga-processing', 'canvas-1');
      document.body.appendChild(canvas);

      const result = handleTranslationResult('canvas-1', {
        translatedImage: base64Of('cleared cache'),
        mimeType: 'image/png',
        mode: 'local',
      });

      expect(result).toBe(true);
      expect(canvas.getAttribute('data-manga-translated')).toBe('true');
      expect(restoreCanvasTranslationFromMemory(canvas)).toBe(true);

      clearPageTranslations();

      expect(canvas.hasAttribute('data-manga-translated')).toBe(false);
      expect(canvas.hasAttribute('data-manga-processing')).toBe(false);
      expect(restoreCanvasTranslationFromMemory(canvas)).toBe(false);
      expect(canvas.hasAttribute('data-manga-translated')).toBe(false);
    });

    it('stops BookWalker queued canvases when the general processing flag is idle', () => {
      const canvas = document.createElement('canvas');
      canvas.width = 930;
      canvas.height = 665;
      canvas.setAttribute('data-manga-translation-key', 'queued-canvas');
      canvas.setAttribute('data-manga-queued', 'true');
      canvas.setAttribute('data-manga-queue-state', 'queued');
      document.body.appendChild(canvas);

      const result = requestStopTranslation();

      expect(result).toEqual({ success: true, stopped: true });
      expect(canvas.hasAttribute('data-manga-queued')).toBe(false);
      expect(canvas.hasAttribute('data-manga-queue-state')).toBe(false);
      expect(enqueueBookWalkerCanvas(canvas, 'reconcile-visible')).toBe(false);
      expect(canvas.hasAttribute('data-manga-queued')).toBe(false);
    });

    it('stops a lone BookWalker canvas that is already processing outside translatePage', () => {
      const canvas = document.createElement('canvas');
      canvas.width = 930;
      canvas.height = 665;
      canvas.setAttribute('data-manga-processing', 'canvas-in-flight');
      document.body.appendChild(canvas);

      const result = requestStopTranslation();

      expect(result).toEqual({ success: true, stopped: true });
      canvas.removeAttribute('data-manga-processing');
      expect(enqueueBookWalkerCanvas(canvas, 'reconcile-visible')).toBe(false);
    });

    it('keeps BookWalker auto-translation cancelled after page translation exits', async () => {
      let resolveBackendCheck: (value: unknown) => void = () => {};
      sendMessage.mockReturnValueOnce(
        new Promise((resolve) => {
          resolveBackendCheck = resolve;
        }),
      );

      const translation = translatePage();
      expect(requestStopTranslation()).toEqual({ success: true, stopped: true });
      resolveBackendCheck({ success: true });
      await translation;

      const canvas = document.createElement('canvas');
      canvas.width = 930;
      canvas.height = 665;
      document.body.appendChild(canvas);

      expect(enqueueBookWalkerCanvas(canvas, 'reconcile-visible')).toBe(false);
      expect(canvas.hasAttribute('data-manga-queued')).toBe(false);
    });

    it('does not treat terminal BookWalker queue states as active work', () => {
      const translated = document.createElement('canvas');
      translated.setAttribute('data-manga-queue-state', 'translated');
      const failed = document.createElement('canvas');
      failed.setAttribute('data-manga-queue-state', 'failed');
      document.body.append(translated, failed);

      expect(requestStopTranslation()).toEqual({ success: true, stopped: false });
    });

    it('cancels and clears a real queued canvas without translation identity attributes', async () => {
      mockFlatCanvasSampling();

      const canvas = document.createElement('canvas');
      canvas.width = 930;
      canvas.height = 665;
      canvas.id = 'queued-canvas';
      document.body.appendChild(canvas);

      expect(enqueueBookWalkerCanvas(canvas, 'test')).toBe(true);
      expect(canvas.hasAttribute('data-manga-queued')).toBe(true);
      expect(canvas.hasAttribute('data-manga-translation-key')).toBe(false);

      const result = clearPageTranslations();

      await Promise.resolve();

      expect(result.cleared).toBe(1);
      expect(canvas.hasAttribute('data-manga-queued')).toBe(false);
      expect(canvas.hasAttribute('data-manga-queue-state')).toBe(false);
      expect(enqueueBookWalkerCanvas(canvas, 'after-clear')).toBe(false);
      expect(sentMessages(sendMessage, 'TRANSLATE_IMAGE')).toHaveLength(0);
    });
  });

  describe('image URL filtering', () => {
    function createSizedImage(src: string): HTMLImageElement {
      const img = createLoadedImage(src, { width: 800, height: 1200, append: false });
      Object.defineProperty(img, 'clientWidth', { configurable: true, value: 800 });
      Object.defineProperty(img, 'clientHeight', { configurable: true, value: 1200 });
      return img;
    }

    it.each([
      'https://reader.example.com/uploads/page1.jpg',
      'https://mangadex.org/data/x/a1.png',
      'https://readcomiconline.li/download/1.jpg',
      '/download/1.jpg',
    ])('treats %s as translatable', (src) => {
      expect(isTranslatableImage(createSizedImage(src))).toBe(true);
    });

    it.each([
      'https://x.example.com/ad/banner.png',
      'https://x.example.com/img/avatar_1.png',
      'https://x.example.com/static/site-logo.png',
      'https://x.example.com/ads.jpg?size=300',
      'https://cdn.site.com/thumbnails/series123.jpg',
      'https://x.example.com/uploads/avatars/u1.png',
      'https://x.example.com/static/banners/promo.jpg',
      'https://x.example.com/assets/icons/menu.png',
      'https://x.example.com/img/logos/x.png',
    ])('skips %s as a non-manga image', (src) => {
      expect(isTranslatableImage(createSizedImage(src))).toBe(false);
    });

    it('does not match skip words that only appear in the host name', () => {
      expect(isTranslatableImage(createSizedImage('https://ad.example.com/chapter/1.jpg'))).toBe(
        true,
      );
    });

    it('keeps accepting blob and data image sources', () => {
      expect(
        isTranslatableImage(createSizedImage('blob:https://reader.example.com/ad-banner')),
      ).toBe(true);
      expect(isTranslatableImage(createSizedImage('data:image/png;base64,AAAA'))).toBe(true);
    });
  });

  describe('translated blob URL lifecycle', () => {
    it('does not revoke blob URLs of nodes that were only moved', () => {
      const wrapper = document.createElement('div');
      const img = document.createElement('img');
      img.dataset.mangaTranslatedUrl = 'blob:https://reader.example.com/moved';
      wrapper.appendChild(img);
      document.body.appendChild(wrapper);

      cleanupBlobUrls(wrapper);
      cleanupBlobUrls(img);

      expect(translatedImage.revokeTranslatedSrc).not.toHaveBeenCalled();
      expect(img.dataset.mangaTranslatedUrl).toBe('blob:https://reader.example.com/moved');
    });

    it('revokes the node and translated descendants once they are really detached', () => {
      const wrapper = document.createElement('div');
      const img = document.createElement('img');
      img.dataset.mangaTranslatedUrl = 'blob:https://reader.example.com/removed';
      wrapper.appendChild(img);

      cleanupBlobUrls(wrapper);

      expect(translatedImage.revokeTranslatedSrc).toHaveBeenCalledTimes(2);
      expect(translatedImage.revokeTranslatedSrc).toHaveBeenCalledWith(wrapper);
      expect(translatedImage.revokeTranslatedSrc).toHaveBeenCalledWith(img);
    });

    it('deletes the cached keys and revokes once when clearing a translation', () => {
      const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
      translatedImage.revokeTranslatedSrc.mockImplementation(
        translatedImage.actual?.revokeTranslatedSrc as () => void,
      );

      const cases: Array<[HTMLElement, (element: HTMLElement) => void]> = [
        [document.createElement('img'), clearImgTranslation],
        [document.createElement('div'), clearCompositeTranslation],
        [document.createElement('section'), clearBackgroundTranslation],
      ];

      cases.forEach(([element, clear], index) => {
        const url = `blob:https://reader.example.com/translated-${index}`;
        element.dataset.mangaTranslatedUrl = url;
        element.dataset.mangaTranslatedBase64 = 'dHJhbnNsYXRlZA==';
        document.body.appendChild(element);

        clear(element);

        expect(element.dataset.mangaTranslatedUrl).toBeUndefined();
        expect(element.dataset.mangaTranslatedBase64).toBeUndefined();
        expect(revoke).toHaveBeenCalledTimes(index + 1);
        expect(revoke).toHaveBeenLastCalledWith(url);
      });
    });
  });

  describe('image capture failures', () => {
    const IMAGE_BASE64 = base64Of('captured-image-data'.repeat(12));
    const IMAGE_DATA_URL = `data:image/png;base64,${IMAGE_BASE64}`;

    function securityError() {
      return new DOMException('Tainted canvases may not be exported.', 'SecurityError');
    }

    beforeEach(() => {
      vi.spyOn(console, 'warn').mockImplementation(() => {});
      vi.spyOn(console, 'error').mockImplementation(() => {});
    });

    it('reports a tainted canvas as unable to capture and leaves no processing marker', async () => {
      mockCanvasSampling();
      vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockImplementation(() => {
        throw securityError();
      });
      const { canvas } = createVisibleBookWalkerCanvas('tainted-page');
      setElementsFromPoint(() => [canvas]);

      await expect(captureCanvasImageBase64(canvas)).rejects.toThrow(
        'Unable to capture canvas image data',
      );

      const result = await processImage(canvas, 'canvas', null, null, { forceRefresh: true });

      expect(result.success).toBe(false);
      expect(result.retryable).toBe(true);
      expect(result.error).toContain('Failed to extract image data');
      expect(canvas.hasAttribute('data-manga-processing')).toBe(false);
      expect(canvas.hasAttribute('data-manga-translated')).toBe(false);
      expect(sentMessages(sendMessage, 'TRANSLATE_IMAGE')).toHaveLength(0);
    });

    it.each(['drawImage', 'toDataURL'] as const)(
      'falls back to the background fetch when img %s throws a SecurityError',
      async (failAt) => {
        mockImageCapture(IMAGE_DATA_URL, { failAt });
        const img = createLoadedImage('https://reader.example.com/chapter/page-1.png');
        respond({
          FETCH_IMAGE: () => ({ success: true, base64: IMAGE_BASE64 }),
          TRANSLATE_IMAGE: () => ({ success: true, result: { blocks: [] } }),
        });

        const result = await processImage(img, 'img');

        expect(result).toEqual({ success: true });
        expect(sentMessages(sendMessage, 'FETCH_IMAGE')).toEqual([
          { type: 'FETCH_IMAGE', url: 'https://reader.example.com/chapter/page-1.png' },
        ]);
        expect(sentMessages(sendMessage, 'TRANSLATE_IMAGE')[0]?.base64).toBe(IMAGE_BASE64);
        expect(img.hasAttribute('data-manga-processing')).toBe(false);
      },
    );

    it('stops cleanly when the img canvas and the background fetch both fail', async () => {
      mockImageCapture(IMAGE_DATA_URL, { failAt: 'toDataURL' });
      const img = createLoadedImage('https://reader.example.com/chapter/page-1.png');
      respond({ FETCH_IMAGE: () => ({ success: false, error: 'blocked by host' }) });

      const result = await processImage(img, 'img');

      expect(result.success).toBe(false);
      expect(result.error).toContain('Failed to fetch image via background: blocked by host');
      expect(result.retryable).toBe(true);
      expect(sentMessages(sendMessage, 'FETCH_IMAGE')).toHaveLength(1);
      expect(sentMessages(sendMessage, 'TRANSLATE_IMAGE')).toHaveLength(0);
      expect(img.hasAttribute('data-manga-processing')).toBe(false);
      expect(img.hasAttribute('data-manga-translated')).toBe(false);
    });

    it('surfaces the 20 MB background fetch limit as a non-retryable error', async () => {
      mockImageCapture(IMAGE_DATA_URL, { failAt: 'toDataURL' });
      const img = createLoadedImage('https://reader.example.com/chapter/page-1.png');
      respond({
        FETCH_IMAGE: () => ({ success: false, error: 'Image exceeds 20 MB decoded image limit' }),
      });

      const result = await processImage(img, 'img');

      expect(result.success).toBe(false);
      expect(result.error).toContain('Image exceeds 20 MB decoded image limit');
      expect(result.error).not.toContain('Failed to extract image data');
      expect(result.retryable).toBe(false);
      expect(sentMessages(sendMessage, 'TRANSLATE_IMAGE')).toHaveLength(0);
    });

    it('includes a URL preview when the background fetch reports a failure', async () => {
      const url = 'https://reader.example.com/chapter/a.png';
      sendMessage.mockResolvedValueOnce({ success: false, error: 'blocked by host' });

      await expect(fetchAndConvertToBase64(url)).rejects.toThrow(
        `Failed to fetch image via background: blocked by host (${url})`,
      );
    });

    it('includes a URL preview when the background returns no response', async () => {
      const url = 'https://reader.example.com/chapter/b.png';
      sendMessage.mockResolvedValueOnce(undefined);

      await expect(fetchAndConvertToBase64(url)).rejects.toThrow(
        `Failed to fetch image via background: no response (${url})`,
      );
    });

    it('includes a URL preview when sending the fetch message rejects', async () => {
      const url = 'https://reader.example.com/chapter/c.png';
      sendMessage.mockRejectedValueOnce(new Error('Extension context invalidated.'));

      await expect(fetchAndConvertToBase64(url)).rejects.toThrow(
        `Failed to fetch image via background (sendMessage): Extension context invalidated. (${url})`,
      );
    });

    it('fetches blob: sources inside the content script instead of the service worker', async () => {
      const blobUrl = 'blob:https://reader.example.com/0f6b1a6e';
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: true,
        blob: async () => new Blob(['blob-image']),
      } as Response);

      await expect(fetchAndConvertToBase64(blobUrl)).resolves.toBe(base64Of('blob-image'));

      expect(fetch).toHaveBeenCalledWith(blobUrl, { method: 'GET', cache: 'no-store' });
      expect(sentMessages(sendMessage, 'FETCH_IMAGE')).toHaveLength(0);
    });
  });

  describe('inpainter fallback notice', () => {
    const IMAGE_BASE64 = base64Of('translated-source-image'.repeat(10));
    const BLOCKS = [
      { bbox: [0, 0, 100, 40] as [number, number, number, number], translated: 'hello' },
    ];

    function createChapterImage(name: string): HTMLImageElement {
      return createLoadedImage(`https://reader.example.com/chapter/${name}.png`);
    }

    function respondWith(result: unknown) {
      respond({ TRANSLATE_IMAGE: () => ({ success: true, result }) });
    }

    beforeEach(() => {
      mockImageCapture(`data:image/png;base64,${IMAGE_BASE64}`);
    });

    it('tells the user once per page session that the inpainter fell back', async () => {
      await browser.storage.sync.set({
        settings: { localBackend: { inpainter: 'flux2_klein_lanpaint' } },
      });
      await loadSettings();

      respondWith({ blocks: BLOCKS, inpainterUsed: 'lama', inpainterFallback: false });
      await processImage(createChapterImage('plain'), 'img');
      expect(progressText()).toBeNull();

      respondWith({ blocks: BLOCKS, inpainterUsed: 'lama', inpainterFallback: true });
      await processImage(createChapterImage('first'), 'img');
      expect(progressText()).toBe(
        'Inpainter flux2_klein_lanpaint unavailable; used lama. Result not cached.',
      );

      document.getElementById('manga-global-progress')?.remove();
      await processImage(createChapterImage('second'), 'img');
      expect(progressText()).toBeNull();
    });

    it('keeps the fallback notice in the final progress line of a single-image run', async () => {
      await browser.storage.sync.set({
        settings: { localBackend: { inpainter: 'flux2_klein_lanpaint' } },
      });
      await loadSettings();

      respond({
        CHECK_BACKEND: () => ({ success: true, reason: 'ok' }),
        TRANSLATE_IMAGE: () => ({
          success: true,
          result: { blocks: BLOCKS, inpainterUsed: 'lama', inpainterFallback: true },
        }),
      });

      const img = createChapterImage('single');
      const result = await translateImageData(
        { element: img, type: 'img', src: img.src },
        { successLabel: 'Image translated' },
      );

      expect(result.success).toBe(true);
      expect(progressText()).toBe(
        'Image translated. Inpainter flux2_klein_lanpaint unavailable; used lama. Result not cached.',
      );
    });
  });

  describe('backend readiness and page-level failures', () => {
    it.each([
      ['loading', 'still loading its models'],
      ['degraded', 'models are not ready'],
      ['unauthorized', 'rejected the Backend Access Token'],
      ['auth_unconfigured', 'has no access token configured'],
      ['unreachable', 'ensure the local server is running'],
      [undefined, 'ensure the local server is running'],
    ])('explains a %s backend check distinctly', async (reason, expected) => {
      respond({ CHECK_BACKEND: () => ({ success: false, reason }) });

      await translatePage();

      expect(bannerText()).toContain(expected);
    });

    it('shows the first backend error when every image in a page run fails', async () => {
      mockImageCapture(`data:image/png;base64,${base64Of('translated-source-image'.repeat(10))}`);
      createLoadedImage('blob:https://reader.example.com/fail-page');
      const detail =
        'Local backend error: 504 - {"detail":"Translation provider request timed out"}';
      respond({
        CHECK_BACKEND: () => ({ success: true, reason: 'ok' }),
        TRANSLATE_IMAGE: () => ({ success: false, error: detail }),
      });

      await translatePage();

      expect(bannerText()).toContain('Translation provider request timed out');
      const complete = sentMessages(sendMessage, 'TRANSLATION_COMPLETE')[0];
      expect(complete).toEqual(
        expect.objectContaining({
          status: 'error',
          succeeded: 0,
          error: expect.stringContaining('Translation provider request timed out'),
        }),
      );
    });
  });
});

describe('content script entrypoint', () => {
  it('starts without posting a page-visible message that names the extension', async () => {
    const { default: definition } = await import('../../extension/entrypoints/content/index');
    const { ContentScriptContext } = await import('wxt/utils/content-script-context');
    const { main: _main, ...options } = definition;
    const postMessage = vi.spyOn(window, 'postMessage');

    // The same construction WXT's isolated-world wrapper runs before main().
    const ctx = new ContentScriptContext('content', options);
    ctx.abort();

    expect(postMessage).not.toHaveBeenCalled();
    expect(options).toMatchObject({ noScriptStartedPostMessage: true });
  });
});
