/**
 * @jest-environment jsdom
 */

describe('content script translation result handling', () => {
  let handleTranslationResult;
  let clearBackgroundTranslation;
  let clearPageTranslations;
  let enqueueBookWalkerCanvas;
  let fetchAndConvertToBase64;
  let restoreCanvasTranslationFromMemory;
  let requestStopTranslation;
  let cleanupBlobUrls;
  let captureCanvasImageBase64;
  let isTranslatableImage;
  let processImage;

  let translatePage;
  let translateImageData;
  let assertCaptureBase64Size;
  let isCaptureDimensionsAllowed;
  let isAlreadyTranslatedElement;
  let startSiteRuntime;
  let stopSiteRuntime;
  let refreshSiteActivation;
  let getSiteRuntimeState;
  let isTranslationTransportError;
  let sendTranslationImageRequest;
  beforeEach(() => {
    jest.resetModules();

    document.body.innerHTML = '';

    try {
      Object.defineProperty(document, 'readyState', {
        configurable: true,
        get: () => 'loading'
      });
    } catch (_error) {
      // jsdom may already expose a configurable readyState getter.
    }

    global.MangaTranslatorSettings = {
      mergeSettings: jest.fn((settings = {}) => settings),
      applyStoredApiKeys: jest.fn((settings) => settings)
    };
    global.MangaTranslatorTranslatedImage = {
      getTranslatedSrc: jest.fn(),
      revokeTranslatedSrc: jest.fn()
    };
    global.MangaTranslatorSiteActivation = {
      getSiteState: jest.fn(async () => ({ enabled: true }))
    };
    global.blobToBase64 = require('../../extension/lib/utils').blobToBase64;
    global.chrome = {
      runtime: {
        onMessage: {
          addListener: jest.fn()
        },
        sendMessage: jest.fn(() => Promise.resolve())
      },
      storage: {
        sync: {
          get: jest.fn(async () => ({ settings: {} }))
        },
        local: {
          get: jest.fn(async () => ({ apiKeys: {} }))
        },
        onChanged: {
          addListener: jest.fn()
        }
      }
    };

    global.fetch = jest.fn();

    if (typeof global.requestAnimationFrame !== 'function') {
      global.requestAnimationFrame = (callback) => setTimeout(callback, 0);
    }
    if (typeof global.MutationObserver !== 'function') {
      global.MutationObserver = class {
        observe() {}
        disconnect() {}
      };
    }

    ({
      clearBackgroundTranslation,
      clearPageTranslations,
      cleanupBlobUrls,
      captureCanvasImageBase64,
      isTranslatableImage,
      processImage,
      assertCaptureBase64Size,
      isCaptureDimensionsAllowed,
      enqueueBookWalkerCanvas,
      fetchAndConvertToBase64,
      handleTranslationResult,
      isAlreadyTranslatedElement,
      requestStopTranslation,
      restoreCanvasTranslationFromMemory,
      translatePage,
      translateImageData,
      startSiteRuntime,
      stopSiteRuntime,
      refreshSiteActivation,
      getSiteRuntimeState,
      isTranslationTransportError,
      sendTranslationImageRequest
    } = require('../../extension/content/content.js'));
  });

  afterEach(() => {
    delete global.MangaTranslatorSettings;
    delete global.MangaTranslatorTranslatedImage;
    delete global.MangaTranslatorSiteActivation;
    delete global.blobToBase64;
    delete global.MangaTranslatorContentHelpers;
    delete global.chrome;
  });

  function createCanvasSample(seed) {
    const data = new Uint8ClampedArray(64 * 64 * 4);

    for (let pixel = 0; pixel < 64 * 64; pixel++) {
      const offset = pixel * 4;
      data[offset] = (seed + pixel) % 251;
      data[offset + 1] = (seed * 3 + pixel * 7) % 251;
      data[offset + 2] = (seed * 5 + pixel * 11) % 251;
      data[offset + 3] = 255;
    }

    return data;
  }

  function mockCanvasSampling(seed = 17) {
    const originalGetContext = HTMLCanvasElement.prototype.getContext;
    let sampleSeed = seed;

    HTMLCanvasElement.prototype.getContext = jest.fn(() => ({
      drawImage: jest.fn(),
      getImageData: jest.fn(() => ({
        data: createCanvasSample(sampleSeed)
      }))
    }));

    return {
      setSeed: value => {
        sampleSeed = value;
      },
      restore: () => {
        HTMLCanvasElement.prototype.getContext = originalGetContext;
      }
    };
  }

  function createVisibleBookWalkerCanvas(id = 'bookwalker-page') {
    const renderer = document.createElement('div');
    renderer.id = 'renderer';

    const viewport = document.createElement('div');
    viewport.id = 'viewport0';
    viewport.className = 'currentScreen';

    const canvas = document.createElement('canvas');
    canvas.id = id;
    canvas.width = 930;
    canvas.height = 665;

    const rect = {
      x: 10,
      y: 10,
      left: 10,
      top: 10,
      right: 940,
      bottom: 675,
      width: 930,
      height: 665,
      toJSON: () => ({})
    };

    canvas.getBoundingClientRect = jest.fn(() => rect);
    viewport.getBoundingClientRect = jest.fn(() => rect);
    renderer.getBoundingClientRect = jest.fn(() => rect);

    viewport.appendChild(canvas);
    renderer.appendChild(viewport);
    document.body.appendChild(renderer);

    return { canvas, renderer, viewport };
  }

  function markCanvasWithStaleStoredSource(canvas, imageId = 'canvas-stale') {
    canvas.setAttribute('data-manga-processing', imageId);
    canvas.dataset.mangaCanvasTranslationKey = 'bookwalker:old-page';
    canvas.setAttribute('data-manga-translation-key', 'bookwalker:old-page');
    canvas.dataset.mangaCanvasWidth = String(canvas.width);
    canvas.dataset.mangaCanvasHeight = String(canvas.height);
    return imageId;
  }

  it('treats an empty cloud result as a successful no-op', () => {
    const element = document.createElement('img');
    element.setAttribute('data-manga-processing', 'image-1');
    document.body.appendChild(element);

    const result = handleTranslationResult('image-1', {
      mode: 'cloud',
      blocks: []
    });

    expect(result).toBe(true);
    expect(element.hasAttribute('data-manga-processing')).toBe(false);
    expect(element.getAttribute('data-manga-translated')).toBe('true');
  });

  it('converts data URL background sources locally without service worker fetch', async () => {
    const base64 = Buffer.from('inline-image').toString('base64');

    const result = await fetchAndConvertToBase64(`data:image/png;base64,${base64}`);

    expect(result).toBe(base64);
    expect(chrome.runtime.sendMessage).not.toHaveBeenCalled();
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('routes relative remote image fetches through the background with a normalized URL', async () => {
    const base = document.createElement('base');
    base.href = 'https://reader.example.com/chapter/page.html';
    document.head.appendChild(base);

    chrome.runtime.sendMessage.mockResolvedValueOnce({
      success: true,
      base64: 'cmVtb3RlLWltYWdl'
    });

    try {
      await expect(fetchAndConvertToBase64('panel.png')).resolves.toBe('cmVtb3RlLWltYWdl');

      expect(chrome.runtime.sendMessage).toHaveBeenCalledWith({
        type: 'FETCH_IMAGE',
        url: 'https://reader.example.com/chapter/panel.png'
      });
      expect(global.fetch).not.toHaveBeenCalled();
    } finally {
      base.remove();
    }
  });

  it('retries a translation request once after a worker transport failure', async () => {
    const message = {
      type: 'TRANSLATE_IMAGE',
      imageId: 'retry-1',
      base64: 'image-data',
      settings: {}
    };
    const transportError = new Error('Could not establish connection. Receiving end does not exist.');
    chrome.runtime.sendMessage
      .mockRejectedValueOnce(transportError)
      .mockResolvedValueOnce({ success: true });

    await expect(sendTranslationImageRequest(message)).resolves.toEqual({ success: true });
    expect(chrome.runtime.sendMessage).toHaveBeenCalledTimes(2);
    expect(isTranslationTransportError(transportError)).toBe(true);

    chrome.runtime.sendMessage.mockReset();
    chrome.runtime.sendMessage.mockRejectedValue(transportError);
    await expect(sendTranslationImageRequest({ ...message, forceRefresh: true }))
      .rejects.toThrow('Could not establish connection');
    expect(chrome.runtime.sendMessage).toHaveBeenCalledTimes(1);
  });

  describe('bundled fonts', () => {
    let ensureBundledFontLoaded;
    const WILD_WORDS = "'CC Wild Words', sans-serif";

    beforeEach(() => {
      ({ ensureBundledFontLoaded } = require('../../extension/content/content.js'));
      global.FontFace = jest.fn(function FontFace(family, source) {
        this.family = family;
        this.source = source;
        this.load = async () => this;
      });
      Object.defineProperty(document, 'fonts', {
        configurable: true,
        value: { add: jest.fn() }
      });
    });

    afterEach(() => {
      delete global.FontFace;
      delete document.fonts;
    });

    it('registers a bundled font once from worker-provided bytes', async () => {
      chrome.runtime.sendMessage.mockResolvedValue({ success: true, base64: btoa('font') });

      await expect(ensureBundledFontLoaded(WILD_WORDS)).resolves.toBe(true);
      await expect(ensureBundledFontLoaded(WILD_WORDS)).resolves.toBe(true);

      const fontCalls = chrome.runtime.sendMessage.mock.calls.filter(([message]) => message.type === 'GET_BUNDLED_FONT');
      expect(fontCalls).toEqual([[{ type: 'GET_BUNDLED_FONT', fontId: 'cc-wild-words' }]]);
      expect(global.FontFace.mock.calls[0][0]).toBe('CC Wild Words');
      expect(Buffer.from(global.FontFace.mock.calls[0][1]).toString()).toBe('font');
      expect(document.fonts.add).toHaveBeenCalledTimes(1);
    });

    it('does not request fonts that are not bundled', async () => {
      await expect(ensureBundledFontLoaded('sans-serif')).resolves.toBe(false);
      expect(chrome.runtime.sendMessage).not.toHaveBeenCalled();
    });

    it('falls back on failure and retries on the next render', async () => {
      const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
      chrome.runtime.sendMessage.mockResolvedValueOnce({ success: false, error: 'read failed' });
      await expect(ensureBundledFontLoaded(WILD_WORDS)).resolves.toBe(false);
      expect(document.fonts.add).not.toHaveBeenCalled();

      chrome.runtime.sendMessage.mockResolvedValueOnce({ success: true, base64: btoa('font') });
      await expect(ensureBundledFontLoaded(WILD_WORDS)).resolves.toBe(true);
      expect(document.fonts.add).toHaveBeenCalledTimes(1);
      warn.mockRestore();
    });
  });

  describe('region selection', () => {
    let startRegionSelection;

    function getSelector() {
      return document.getElementById('manga-region-selector');
    }

    function drag(selector, from, to) {
      const fire = (type, point) => selector.dispatchEvent(
        new MouseEvent(type, { bubbles: true, clientX: point.x, clientY: point.y })
      );
      fire('pointerdown', from);
      fire('pointermove', to);
      fire('pointerup', to);
    }

    function captureCalls() {
      return chrome.runtime.sendMessage.mock.calls.filter(([message]) => message.type === 'CAPTURE_REGION');
    }

    beforeEach(() => {
      ({ startRegionSelection } = require('../../extension/content/content.js'));
    });

    afterEach(() => {
      getSelector()?.remove();
      document.querySelectorAll('.manga-region-translation').forEach(item => item.remove());
    });

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
      drag(getSelector(), { x: 10, y: 10 }, { x: 20, y: 200 });

      expect(getSelector()).toBeNull();
      expect(captureCalls()).toEqual([]);
    });

    it('sends only the dragged rectangle and removes the crop when capture fails', async () => {
      window.innerWidth = 1000;
      window.innerHeight = 800;
      chrome.runtime.sendMessage.mockImplementation(async message => (
        message.type === 'CAPTURE_REGION' ? { success: false, error: 'capture denied' } : undefined
      ));

      startRegionSelection();
      drag(getSelector(), { x: 300, y: 200 }, { x: 100, y: 50 });

      await new Promise(resolve => setTimeout(resolve, 50));

      expect(captureCalls()).toEqual([[{
        type: 'CAPTURE_REGION',
        rect: { left: 100, top: 50, width: 200, height: 150 },
        viewport: { width: 1000, height: 800 }
      }]]);
      expect(document.querySelector('.manga-region-translation')).toBeNull();
      expect(document.getElementById('manga-error-banner').shadowRoot.textContent).toContain('capture denied');
    });

    it('never treats a region crop as a page image', () => {
      const crop = document.createElement('img');
      crop.dataset.mangaOriginalSrc = 'region-capture';
      document.body.appendChild(crop);
      expect(isTranslatableImage(crop)).toBe(false);
    });
  });

  it('disposes site runtime listeners and recreates them after re-enable', async () => {
    startSiteRuntime();
    expect(getSiteRuntimeState()).toEqual({ enabled: true, started: true, hasScope: true });
    expect(document.querySelectorAll('#manga-reader-controls')).toHaveLength(1);
    expect(document.querySelector('#manga-panel-controls')).toBeNull();

    global.MangaTranslatorSiteActivation.getSiteState.mockResolvedValueOnce({ enabled: false });
    await refreshSiteActivation();
    expect(getSiteRuntimeState()).toEqual({ enabled: false, started: false, hasScope: false });
    expect(document.querySelector('#manga-reader-controls')).toBeNull();

    global.MangaTranslatorSiteActivation.getSiteState.mockRejectedValueOnce(new Error('storage unavailable'));
    await refreshSiteActivation();
    expect(getSiteRuntimeState().enabled).toBe(false);

    global.MangaTranslatorSiteActivation.getSiteState.mockResolvedValueOnce({ enabled: true });
    await refreshSiteActivation();
    expect(getSiteRuntimeState()).toEqual({ enabled: true, started: true, hasScope: true });
    expect(document.querySelectorAll('#manga-reader-controls')).toHaveLength(1);
    expect(document.querySelector('#manga-panel-controls')).toBeNull();

    stopSiteRuntime();
  });

  it('rejects empty fetch URLs before messaging the background', async () => {
    await expect(fetchAndConvertToBase64('')).rejects.toThrow('empty URL');

    expect(chrome.runtime.sendMessage).not.toHaveBeenCalled();
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('rejects oversized images before capture allocates or uploads them', () => {
    expect(isCaptureDimensionsAllowed(5000, 5000)).toBe(true);
    expect(isCaptureDimensionsAllowed(5001, 5000)).toBe(false);
    expect(() => assertCaptureBase64Size('x'.repeat(28_000_000))).toThrow('20 MB capture limit');
  });

  it('restores all mutated background styles when clearing a translation', () => {
    global.MangaTranslatorTranslatedImage.getTranslatedSrc.mockReturnValue('translated-bg.png');

    const element = document.createElement('section');
    element.setAttribute('data-manga-processing', 'bg-1');
    element.style.backgroundImage = 'url("original.png")';
    element.style.backgroundSize = 'contain';
    element.style.backgroundRepeat = 'repeat-x';
    element.style.backgroundPosition = 'left top';
    document.body.appendChild(element);

    const result = handleTranslationResult('bg-1', {
      translatedImage: Buffer.from('translated').toString('base64'),
      mimeType: 'image/png',
      mode: 'local'
    });

    expect(result).toBe(true);
    expect(element.style.backgroundImage).toContain('translated-bg.png');
    expect(element.style.backgroundSize).toBe('cover');
    expect(element.style.backgroundRepeat).toBe('no-repeat');
    expect(element.style.backgroundPosition).toBe('center');

    clearBackgroundTranslation(element);
    expect(element.style.backgroundImage).toContain('original.png');
    expect(element.style.backgroundSize).toBe('contain');
    expect(element.style.backgroundRepeat).toBe('repeat-x');
    expect(element.style.backgroundPosition).toBe('left top');
  });

  it('uses 16px as the preferred overlay size when the text fits', () => {
    const element = document.createElement('img');
    element.setAttribute('data-manga-processing', 'img-1');
    element.width = 500;
    element.height = 200;
    document.body.appendChild(element);

    const result = handleTranslationResult('img-1', {
      blocks: [
        {
          bbox: [0, 0, 360, 120],
          translated: 'THAT.'
        }
      ]
    });

    const text = document.querySelector('.manga-translate-overlay').shadowRoot.querySelector('text');
    expect(result).toBe(true);
    expect(Number(text.getAttribute('font-size'))).toBe(16);
  });

  it('centers browser overlays on exported bubble geometry', () => {
    const element = document.createElement('img');
    element.setAttribute('data-manga-processing', 'img-bubble-center');
    element.width = 500;
    element.height = 200;
    document.body.appendChild(element);

    const result = handleTranslationResult('img-bubble-center', {
      blocks: [{
        bbox: [100, 20, 160, 100],
        bubble_bbox: [80, 10, 200, 110],
        bubble_center: [145, 60],
        translated: 'CENTERED'
      }]
    });

    const overlay = document.querySelector('.manga-translate-overlay').shadowRoot;
    const text = overlay.querySelector('text');
    const background = overlay.querySelector('rect');
    expect(result).toBe(true);
    expect(text.getAttribute('text-anchor')).toBe('middle');
    expect(Number(text.getAttribute('x'))).toBe(145);
    expect(Number(background.getAttribute('x'))).toBe(80);
    expect(Number(background.getAttribute('width'))).toBe(120);
  });

  it('shrinks overlay text to fit without dropping translated words', () => {
    const element = document.createElement('img');
    element.setAttribute('data-manga-processing', 'img-tight');
    element.width = 160;
    element.height = 60;
    document.body.appendChild(element);

    const translated = 'THIS TRANSLATED SENTENCE MUST STAY COMPLETE';
    const result = handleTranslationResult('img-tight', {
      blocks: [{ bbox: [0, 0, 80, 24], translated }]
    });

    const text = document.querySelector('.manga-translate-overlay').shadowRoot.querySelector('text');
    const rendered = Array.from(text.querySelectorAll('tspan'))
      .map(line => line.textContent)
      .join(' ');
    expect(result).toBe(true);
    expect(Number(text.getAttribute('font-size'))).toBeLessThan(16);
    expect(rendered).toBe(translated);
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

  it('stops page translation before image processing when backend readiness fails', async () => {
    const { canvas } = createVisibleBookWalkerCanvas();
    chrome.runtime.sendMessage.mockImplementation(message => {
      if (message.type === 'CHECK_BACKEND') {
        return Promise.resolve({ success: false, error: 'offline' });
      }
      return Promise.resolve();
    });

    await translatePage();

    const messages = chrome.runtime.sendMessage.mock.calls.map(([message]) => message);
    expect(messages.some(message => message.type === 'CHECK_BACKEND')).toBe(true);
    expect(messages.some(message => message.type === 'TRANSLATE_IMAGE')).toBe(false);
    expect(canvas.hasAttribute('data-manga-processing')).toBe(false);
    expect(canvas.hasAttribute('data-manga-translated')).toBe(false);
  });
  it('blocks normal BookWalker canvas translation after a stale canvas result', async () => {
    const canvasMock = mockCanvasSampling();
    const originalElementsFromPoint = document.elementsFromPoint;
    const { canvas } = createVisibleBookWalkerCanvas();
    document.elementsFromPoint = jest.fn(() => [canvas]);
    chrome.runtime.sendMessage.mockImplementation(message => {
      if (message.type === 'CHECK_BACKEND') {
        return Promise.resolve({ success: true });
      }
      return Promise.resolve();
    });

    try {
      const imageId = markCanvasWithStaleStoredSource(canvas);

      const result = handleTranslationResult(imageId, {
        blocks: [
          {
            bbox: [0, 0, 100, 40],
            translated: 'late'
          }
        ]
      });

      expect(result).toBe(false);
      expect(canvas.hasAttribute('data-manga-processing')).toBe(false);
      expect(canvas.hasAttribute('data-manga-translated')).toBe(false);

      await translatePage();

      const messages = chrome.runtime.sendMessage.mock.calls.map(([message]) => message);
      expect(messages.some(message => message.type === 'CHECK_BACKEND')).toBe(true);
      expect(messages.some(message => message.type === 'TRANSLATE_IMAGE')).toBe(false);
      expect(canvas.hasAttribute('data-manga-processing')).toBe(false);
      expect(canvas.hasAttribute('data-manga-translated')).toBe(false);
    } finally {
      document.elementsFromPoint = originalElementsFromPoint;
      canvasMock.restore();
    }
  });

  it('lets manual BookWalker translation bypass a stale canvas retry block', async () => {
    const canvasMock = mockCanvasSampling();
    const originalElementsFromPoint = document.elementsFromPoint;
    const { canvas } = createVisibleBookWalkerCanvas();
    const canvasBase64 = Buffer.from('canvas-image-with-enough-content'.repeat(8)).toString('base64');
    document.elementsFromPoint = jest.fn(() => [canvas]);
    canvas.toDataURL = jest.fn(() => 'data:image/png;base64,' + canvasBase64);

    try {
      const imageId = markCanvasWithStaleStoredSource(canvas);

      expect(handleTranslationResult(imageId, {
        blocks: [
          {
            bbox: [0, 0, 100, 40],
            translated: 'late'
          }
        ]
      })).toBe(false);

      chrome.runtime.sendMessage.mockImplementation(message => {
        if (message.type === 'CHECK_BACKEND') {
          return Promise.resolve({ success: true });
        }
        if (message.type === 'TRANSLATE_IMAGE') {
          return Promise.resolve({
            success: true,
            result: {
              blocks: [
                {
                  bbox: [0, 0, 100, 40],
                  translated: 'manual'
                }
              ]
            }
          });
        }
        return Promise.resolve();
      });

      await translatePage({ bypassRetryBlock: true });

      const messages = chrome.runtime.sendMessage.mock.calls.map(([message]) => message);
      const translateMessage = messages.find(message => message.type === 'TRANSLATE_IMAGE');

      expect(messages.some(message => message.type === 'CHECK_BACKEND')).toBe(true);
      expect(translateMessage).toEqual(expect.objectContaining({
        type: 'TRANSLATE_IMAGE',
        base64: canvasBase64
      }));
      expect(canvas.getAttribute('data-manga-translated')).toBe('true');
      expect(canvas.hasAttribute('data-manga-processing')).toBe(false);
    } finally {
      document.elementsFromPoint = originalElementsFromPoint;
      canvasMock.restore();
    }
  });

  it('clears remembered canvas translations so translate all can requeue', () => {
    const originalGetContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = jest.fn(() => ({
      drawImage: jest.fn(),
      getImageData: jest.fn(() => ({
        data: new Uint8ClampedArray(64 * 64 * 4).fill(255)
      }))
    }));

    try {
      const canvas = document.createElement('canvas');
      canvas.width = 930;
      canvas.height = 665;
      canvas.setAttribute('data-manga-processing', 'canvas-1');
      document.body.appendChild(canvas);

      const result = handleTranslationResult('canvas-1', {
        blocks: [
          {
            bbox: [20, 20, 200, 80],
            translated: 'Cleared cache'
          }
        ]
      });

      expect(result).toBe(true);
      expect(canvas.getAttribute('data-manga-translated')).toBe('true');
      expect(restoreCanvasTranslationFromMemory(canvas)).toBe(true);

      clearPageTranslations();

      expect(canvas.hasAttribute('data-manga-translated')).toBe(false);
      expect(canvas.hasAttribute('data-manga-processing')).toBe(false);
      expect(restoreCanvasTranslationFromMemory(canvas)).toBe(false);
      expect(canvas.hasAttribute('data-manga-translated')).toBe(false);
    } finally {
      HTMLCanvasElement.prototype.getContext = originalGetContext;
    }
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
    let resolveBackendCheck;
    chrome.runtime.sendMessage.mockReturnValueOnce(new Promise(resolve => {
      resolveBackendCheck = resolve;
    }));

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
    const originalGetContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = jest.fn(() => ({
      drawImage: jest.fn(),
      getImageData: jest.fn(() => ({
        data: new Uint8ClampedArray(64 * 64 * 4).fill(255)
      }))
    }));

    const canvas = document.createElement('canvas');
    canvas.width = 930;
    canvas.height = 665;
    canvas.id = 'queued-canvas';
    document.body.appendChild(canvas);

    try {
      expect(enqueueBookWalkerCanvas(canvas, 'test')).toBe(true);
      expect(canvas.hasAttribute('data-manga-queued')).toBe(true);
      expect(canvas.hasAttribute('data-manga-translation-key')).toBe(false);

      const result = clearPageTranslations();

      await Promise.resolve();

      expect(result.cleared).toBe(1);
      expect(canvas.hasAttribute('data-manga-queued')).toBe(false);
      expect(canvas.hasAttribute('data-manga-queue-state')).toBe(false);
      expect(enqueueBookWalkerCanvas(canvas, 'after-clear')).toBe(false);
      expect(chrome.runtime.sendMessage).not.toHaveBeenCalledWith(
        expect.objectContaining({ type: 'TRANSLATE_IMAGE' })
      );
    } finally {
      HTMLCanvasElement.prototype.getContext = originalGetContext;
    }
  });

  describe('image URL filtering', () => {
    function createLoadedImage(src) {
      const img = document.createElement('img');
      img.src = src;
      Object.defineProperty(img, 'complete', { configurable: true, value: true });
      Object.defineProperty(img, 'naturalWidth', { configurable: true, value: 800 });
      Object.defineProperty(img, 'naturalHeight', { configurable: true, value: 1200 });
      Object.defineProperty(img, 'clientWidth', { configurable: true, value: 800 });
      Object.defineProperty(img, 'clientHeight', { configurable: true, value: 1200 });
      return img;
    }

    it.each([
      'https://reader.example.com/uploads/page1.jpg',
      'https://mangadex.org/data/x/a1.png',
      'https://readcomiconline.li/download/1.jpg',
      '/download/1.jpg'
    ])('treats %s as translatable', src => {
      expect(isTranslatableImage(createLoadedImage(src))).toBe(true);
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
      'https://x.example.com/img/logos/x.png'
    ])('skips %s as a non-manga image', src => {
      expect(isTranslatableImage(createLoadedImage(src))).toBe(false);
    });

    it('does not match skip words that only appear in the host name', () => {
      expect(isTranslatableImage(createLoadedImage('https://ad.example.com/chapter/1.jpg'))).toBe(true);
    });

    it('keeps accepting blob and data image sources', () => {
      expect(isTranslatableImage(createLoadedImage('blob:https://reader.example.com/ad-banner'))).toBe(true);
      expect(isTranslatableImage(createLoadedImage('data:image/png;base64,AAAA'))).toBe(true);
    });
  });

  describe('translated blob URL lifecycle', () => {
    it('does not revoke blob URLs of nodes that were only moved', () => {
      const { revokeTranslatedSrc } = global.MangaTranslatorTranslatedImage;
      const wrapper = document.createElement('div');
      const img = document.createElement('img');
      img.dataset.mangaTranslatedUrl = 'blob:https://reader.example.com/moved';
      wrapper.appendChild(img);
      document.body.appendChild(wrapper);

      cleanupBlobUrls(wrapper);
      cleanupBlobUrls(img);

      expect(revokeTranslatedSrc).not.toHaveBeenCalled();
      expect(img.dataset.mangaTranslatedUrl).toBe('blob:https://reader.example.com/moved');
    });

    it('revokes the node and translated descendants once they are really detached', () => {
      const { revokeTranslatedSrc } = global.MangaTranslatorTranslatedImage;
      const wrapper = document.createElement('div');
      const img = document.createElement('img');
      img.dataset.mangaTranslatedUrl = 'blob:https://reader.example.com/removed';
      wrapper.appendChild(img);

      cleanupBlobUrls(wrapper);

      expect(revokeTranslatedSrc).toHaveBeenCalledTimes(2);
      expect(revokeTranslatedSrc).toHaveBeenCalledWith(wrapper);
      expect(revokeTranslatedSrc).toHaveBeenCalledWith(img);
    });

    it('deletes the cached keys and revokes once when clearing a translation', () => {
      const originalRevoke = URL.revokeObjectURL;
      URL.revokeObjectURL = jest.fn();
      global.MangaTranslatorTranslatedImage = require('../../extension/lib/translated-image');
      jest.resetModules();
      const content = require('../../extension/content/content.js');

      try {
        const cases = [
          [document.createElement('img'), content.clearImgTranslation],
          [document.createElement('div'), content.clearCompositeTranslation],
          [document.createElement('section'), content.clearBackgroundTranslation]
        ];

        cases.forEach(([element, clear], index) => {
          const url = `blob:https://reader.example.com/translated-${index}`;
          element.dataset.mangaTranslatedUrl = url;
          element.dataset.mangaTranslatedBase64 = 'dHJhbnNsYXRlZA==';
          document.body.appendChild(element);

          clear(element);

          expect(element.dataset.mangaTranslatedUrl).toBeUndefined();
          expect(element.dataset.mangaTranslatedBase64).toBeUndefined();
          expect(URL.revokeObjectURL).toHaveBeenCalledTimes(index + 1);
          expect(URL.revokeObjectURL).toHaveBeenLastCalledWith(url);
        });
      } finally {
        URL.revokeObjectURL = originalRevoke;
      }
    });
  });

  describe('image capture failures', () => {
    const IMAGE_BASE64 = Buffer.from('captured-image-data'.repeat(12)).toString('base64');
    let consoleSpies;

    function securityError() {
      return new DOMException('Tainted canvases may not be exported.', 'SecurityError');
    }

    function createLoadedImage(src = 'https://reader.example.com/chapter/page-1.png') {
      const img = document.createElement('img');
      img.src = src;
      img.width = 400;
      img.height = 600;
      Object.defineProperty(img, 'complete', { configurable: true, value: true });
      Object.defineProperty(img, 'naturalWidth', { configurable: true, value: 400 });
      Object.defineProperty(img, 'naturalHeight', { configurable: true, value: 600 });
      document.body.appendChild(img);
      return img;
    }

    function mockImageCapture({ failAt = null } = {}) {
      const originalGetContext = HTMLCanvasElement.prototype.getContext;
      const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
      HTMLCanvasElement.prototype.getContext = jest.fn(() => ({
        drawImage: jest.fn(() => {
          if (failAt === 'drawImage') throw securityError();
        })
      }));
      HTMLCanvasElement.prototype.toDataURL = jest.fn(() => {
        if (failAt === 'toDataURL') throw securityError();
        return `data:image/png;base64,${IMAGE_BASE64}`;
      });
      return () => {
        HTMLCanvasElement.prototype.getContext = originalGetContext;
        HTMLCanvasElement.prototype.toDataURL = originalToDataURL;
      };
    }

    function getSentMessages(type) {
      return chrome.runtime.sendMessage.mock.calls
        .map(([message]) => message)
        .filter(message => message?.type === type);
    }

    beforeEach(() => {
      consoleSpies = [
        jest.spyOn(console, 'warn').mockImplementation(() => {}),
        jest.spyOn(console, 'error').mockImplementation(() => {})
      ];
    });

    afterEach(() => {
      consoleSpies.forEach(spy => spy.mockRestore());
    });

    it('reports a tainted canvas as unable to capture and leaves no processing marker', async () => {
      const canvasMock = mockCanvasSampling();
      const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
      const originalElementsFromPoint = document.elementsFromPoint;
      HTMLCanvasElement.prototype.toDataURL = jest.fn(() => {
        throw securityError();
      });
      const { canvas } = createVisibleBookWalkerCanvas('tainted-page');
      document.elementsFromPoint = jest.fn(() => [canvas]);

      try {
        await expect(captureCanvasImageBase64(canvas)).rejects.toThrow('Unable to capture canvas image data');

        const result = await processImage(canvas, 'canvas', null, null, { forceRefresh: true });

        expect(result.success).toBe(false);
        expect(result.retryable).toBe(true);
        expect(result.error).toContain('Failed to extract image data');
        expect(canvas.hasAttribute('data-manga-processing')).toBe(false);
        expect(canvas.hasAttribute('data-manga-translated')).toBe(false);
        expect(getSentMessages('TRANSLATE_IMAGE')).toHaveLength(0);
      } finally {
        HTMLCanvasElement.prototype.toDataURL = originalToDataURL;
        document.elementsFromPoint = originalElementsFromPoint;
        canvasMock.restore();
      }
    });

    it.each(['drawImage', 'toDataURL'])(
      'falls back to the background fetch when img %s throws a SecurityError',
      async failAt => {
        const restoreCapture = mockImageCapture({ failAt });
        const img = createLoadedImage();
        chrome.runtime.sendMessage.mockImplementation(message => {
          if (message.type === 'FETCH_IMAGE') {
            return Promise.resolve({ success: true, base64: IMAGE_BASE64 });
          }
          if (message.type === 'TRANSLATE_IMAGE') {
            return Promise.resolve({ success: true, result: { blocks: [] } });
          }
          return Promise.resolve();
        });

        try {
          const result = await processImage(img, 'img');

          expect(result).toEqual({ success: true });
          expect(getSentMessages('FETCH_IMAGE')).toEqual([
            { type: 'FETCH_IMAGE', url: 'https://reader.example.com/chapter/page-1.png' }
          ]);
          expect(getSentMessages('TRANSLATE_IMAGE')[0].base64).toBe(IMAGE_BASE64);
          expect(img.hasAttribute('data-manga-processing')).toBe(false);
        } finally {
          restoreCapture();
        }
      }
    );

    it('stops cleanly when the img canvas and the background fetch both fail', async () => {
      const restoreCapture = mockImageCapture({ failAt: 'toDataURL' });
      const img = createLoadedImage();
      chrome.runtime.sendMessage.mockImplementation(message => {
        if (message.type === 'FETCH_IMAGE') {
          return Promise.resolve({ success: false, error: 'blocked by host' });
        }
        return Promise.resolve();
      });

      try {
        const result = await processImage(img, 'img');

        expect(result.success).toBe(false);
        expect(result.error).toContain('Failed to fetch image via background: blocked by host');
        expect(result.retryable).toBe(true);
        expect(getSentMessages('FETCH_IMAGE')).toHaveLength(1);
        expect(getSentMessages('TRANSLATE_IMAGE')).toHaveLength(0);
        expect(img.hasAttribute('data-manga-processing')).toBe(false);
        expect(img.hasAttribute('data-manga-translated')).toBe(false);
      } finally {
        restoreCapture();
      }
    });

    it('surfaces the 20 MB background fetch limit as a non-retryable error', async () => {
      const restoreCapture = mockImageCapture({ failAt: 'toDataURL' });
      const img = createLoadedImage();
      chrome.runtime.sendMessage.mockImplementation(message => {
        if (message.type === 'FETCH_IMAGE') {
          return Promise.resolve({ success: false, error: 'Image exceeds 20 MB decoded image limit' });
        }
        return Promise.resolve();
      });

      try {
        const result = await processImage(img, 'img');

        expect(result.success).toBe(false);
        expect(result.error).toContain('Image exceeds 20 MB decoded image limit');
        expect(result.error).not.toContain('Failed to extract image data');
        expect(result.retryable).toBe(false);
        expect(getSentMessages('TRANSLATE_IMAGE')).toHaveLength(0);
      } finally {
        restoreCapture();
      }
    });

    it('includes a URL preview when the background fetch reports a failure', async () => {
      const url = 'https://reader.example.com/chapter/a.png';
      chrome.runtime.sendMessage.mockResolvedValueOnce({ success: false, error: 'blocked by host' });

      await expect(fetchAndConvertToBase64(url)).rejects.toThrow(
        `Failed to fetch image via background: blocked by host (${url})`
      );
    });

    it('includes a URL preview when the background returns no response', async () => {
      const url = 'https://reader.example.com/chapter/b.png';
      chrome.runtime.sendMessage.mockResolvedValueOnce(undefined);

      await expect(fetchAndConvertToBase64(url)).rejects.toThrow(
        `Failed to fetch image via background: no response (${url})`
      );
    });

    it('includes a URL preview when sending the fetch message rejects', async () => {
      const url = 'https://reader.example.com/chapter/c.png';
      chrome.runtime.sendMessage.mockRejectedValueOnce(new Error('Extension context invalidated.'));

      await expect(fetchAndConvertToBase64(url)).rejects.toThrow(
        `Failed to fetch image via background (sendMessage): Extension context invalidated. (${url})`
      );
    });

    it('fetches blob: sources inside the content script instead of the service worker', async () => {
      jest.resetModules();
      require('../../extension/content/content-helpers');
      const content = require('../../extension/content/content.js');
      const blobUrl = 'blob:https://reader.example.com/0f6b1a6e';
      global.fetch.mockResolvedValueOnce({
        ok: true,
        blob: async () => new Blob(['blob-image'])
      });

      await expect(content.fetchAndConvertToBase64(blobUrl))
        .resolves.toBe(Buffer.from('blob-image').toString('base64'));

      expect(global.fetch).toHaveBeenCalledWith(blobUrl, { method: 'GET', cache: 'no-store' });
      expect(getSentMessages('FETCH_IMAGE')).toHaveLength(0);
    });
  });

  describe('inpainter fallback notice', () => {
    const IMAGE_BASE64 = Buffer.from('translated-source-image'.repeat(10)).toString('base64');
    const BLOCKS = [{ bbox: [0, 0, 100, 40], translated: 'hello' }];
    let restoreCapture;

    function createLoadedImage(name) {
      const img = document.createElement('img');
      img.src = `https://reader.example.com/chapter/${name}.png`;
      img.width = 400;
      img.height = 600;
      Object.defineProperty(img, 'complete', { configurable: true, value: true });
      Object.defineProperty(img, 'naturalWidth', { configurable: true, value: 400 });
      Object.defineProperty(img, 'naturalHeight', { configurable: true, value: 600 });
      document.body.appendChild(img);
      return img;
    }

    function respondWith(result) {
      chrome.runtime.sendMessage.mockImplementation(message => {
        if (message.type === 'TRANSLATE_IMAGE') {
          return Promise.resolve({ success: true, result });
        }
        return Promise.resolve();
      });
    }

    function getNoticeText() {
      return document.querySelector('#manga-global-progress')?.shadowRoot.querySelector('.manga-progress-text')?.textContent ?? null;
    }

    beforeEach(() => {
      const originalGetContext = HTMLCanvasElement.prototype.getContext;
      const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
      HTMLCanvasElement.prototype.getContext = jest.fn(() => ({ drawImage: jest.fn() }));
      HTMLCanvasElement.prototype.toDataURL = jest.fn(() => `data:image/png;base64,${IMAGE_BASE64}`);
      restoreCapture = () => {
        HTMLCanvasElement.prototype.getContext = originalGetContext;
        HTMLCanvasElement.prototype.toDataURL = originalToDataURL;
      };
    });

    afterEach(() => {
      restoreCapture();
    });

    it('tells the user once per page session that the inpainter fell back', async () => {
      chrome.storage.sync.get.mockResolvedValue({
        settings: { localBackend: { inpainter: 'flux2_klein_lanpaint' } }
      });
      chrome.storage.onChanged.addListener.mock.calls[0][0]({ settings: {} }, 'sync');
      await new Promise(resolve => setTimeout(resolve, 0));

      respondWith({ blocks: BLOCKS, inpainterUsed: 'lama', inpainterFallback: false });
      await processImage(createLoadedImage('plain'), 'img');
      expect(getNoticeText()).toBeNull();

      respondWith({ blocks: BLOCKS, inpainterUsed: 'lama', inpainterFallback: true });
      await processImage(createLoadedImage('first'), 'img');
      expect(getNoticeText()).toBe(
        'Inpainter flux2_klein_lanpaint unavailable; used lama. Result not cached.'
      );

      document.getElementById('manga-global-progress').remove();
      await processImage(createLoadedImage('second'), 'img');
      expect(getNoticeText()).toBeNull();
    });

    it('keeps the fallback notice in the final progress line of a single-image run', async () => {
      chrome.storage.sync.get.mockResolvedValue({
        settings: { localBackend: { inpainter: 'flux2_klein_lanpaint' } }
      });
      chrome.storage.onChanged.addListener.mock.calls[0][0]({ settings: {} }, 'sync');
      await new Promise(resolve => setTimeout(resolve, 0));

      chrome.runtime.sendMessage.mockImplementation(message => {
        if (message.type === 'CHECK_BACKEND') {
          return Promise.resolve({ success: true, reason: 'ok' });
        }
        if (message.type === 'TRANSLATE_IMAGE') {
          return Promise.resolve({
            success: true,
            result: { blocks: BLOCKS, inpainterUsed: 'lama', inpainterFallback: true }
          });
        }
        return Promise.resolve();
      });

      const img = createLoadedImage('single');
      const result = await translateImageData(
        { element: img, type: 'img', src: img.src },
        { successLabel: 'Image translated' }
      );

      expect(result.success).toBe(true);
      expect(getNoticeText()).toBe(
        'Image translated. Inpainter flux2_klein_lanpaint unavailable; used lama. Result not cached.'
      );
    });
  });

  describe('backend readiness and page-level failures', () => {
    function getBannerText() {
      return document.getElementById('manga-error-banner')?.shadowRoot.textContent ?? '';
    }

    it.each([
      ['loading', 'still loading its models'],
      ['degraded', 'models are not ready'],
      ['unauthorized', 'rejected the Backend Access Token'],
      ['auth_unconfigured', 'has no access token configured'],
      ['unreachable', 'ensure the local server is running'],
      [undefined, 'ensure the local server is running']
    ])('explains a %s backend check distinctly', async (reason, expected) => {
      chrome.runtime.sendMessage.mockImplementation(message => {
        if (message.type === 'CHECK_BACKEND') {
          return Promise.resolve({ success: false, reason });
        }
        return Promise.resolve();
      });

      await translatePage();

      expect(getBannerText()).toContain(expected);
    });

    it('shows the first backend error when every image in a page run fails', async () => {
      const restoreContext = HTMLCanvasElement.prototype.getContext;
      const restoreDataUrl = HTMLCanvasElement.prototype.toDataURL;
      const imageBase64 = Buffer.from('translated-source-image'.repeat(10)).toString('base64');
      HTMLCanvasElement.prototype.getContext = jest.fn(() => ({ drawImage: jest.fn() }));
      HTMLCanvasElement.prototype.toDataURL = jest.fn(() => `data:image/png;base64,${imageBase64}`);
      const img = document.createElement('img');
      img.src = 'blob:https://reader.example.com/fail-page';
      img.width = 400;
      img.height = 600;
      Object.defineProperty(img, 'complete', { configurable: true, value: true });
      Object.defineProperty(img, 'naturalWidth', { configurable: true, value: 400 });
      Object.defineProperty(img, 'naturalHeight', { configurable: true, value: 600 });
      document.body.appendChild(img);
      const detail = 'Local backend error: 504 - {"detail":"Translation provider request timed out"}';
      chrome.runtime.sendMessage.mockImplementation(message => {
        if (message.type === 'CHECK_BACKEND') {
          return Promise.resolve({ success: true, reason: 'ok' });
        }
        if (message.type === 'TRANSLATE_IMAGE') {
          return Promise.resolve({ success: false, error: detail });
        }
        return Promise.resolve();
      });

      try {
        await translatePage();

        expect(getBannerText()).toContain('Translation provider request timed out');
        const complete = chrome.runtime.sendMessage.mock.calls
          .map(([message]) => message)
          .find(message => message.type === 'TRANSLATION_COMPLETE');
        expect(complete).toEqual(expect.objectContaining({
          status: 'error',
          succeeded: 0,
          error: expect.stringContaining('Translation provider request timed out')
        }));
      } finally {
        HTMLCanvasElement.prototype.getContext = restoreContext;
        HTMLCanvasElement.prototype.toDataURL = restoreDataUrl;
      }
    });
  });
});
