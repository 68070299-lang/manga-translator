/**
 * Content Script for Manga Translator
 * Handles image discovery, translation overlay, and DOM manipulation
 */

/* global blobToBase64 */

(function() {
  'use strict';

  const { mergeSettings, applyStoredApiKeys } = MangaTranslatorSettings;
  const { getTranslatedSrc, revokeTranslatedSrc } = MangaTranslatorTranslatedImage;
  const {
    normalizeSourceUrl,
    sourceUrlsMatch,
    getImageSourceCandidates,
    getBackgroundImageUrls: parseBackgroundImageUrls,
    shouldFetchInContentScript,
    fetchLocalUrlAsBase64
  } = globalThis.MangaTranslatorContentHelpers || require('./content-helpers');
  const siteActivation = globalThis.MangaTranslatorSiteActivation ||
    (typeof require === 'function' ? require('../lib/site-activation') : null);
  const { createShadowHost, mountInShadow, createImageOverlay } = globalThis.MangaTranslatorOverlayDom ||
    require('../lib/overlay-dom');
  const fontRegistry = globalThis.MangaTranslatorFontRegistry ||
    (typeof require === 'function' ? require('../lib/font-registry') : null);
  const regionCapture = globalThis.MangaTranslatorRegionCapture ||
    (typeof require === 'function' ? require('../lib/region-capture') : null);

  // Debug logging gated by settings.debug
  function debugLog(...args) {
    if (settings?.debug) {
      console.log('[Manga Translator]', ...args);
    }
  }

  // Configuration
  const MIN_IMAGE_WIDTH = 200;
  const MIN_IMAGE_HEIGHT = 200;
  const NON_MANGA_PATH_PATTERN =
    /(^|[/_.-])(ads?|banners?|avatars?|profiles?|icons?|logos?|emojis?|flags?|loading|thumbnails?|buttons?)([/_.\-?]|$)/;
  const TRANSLATION_ATTR = 'data-manga-translated';
  const PROCESSING_ATTR = 'data-manga-processing';
  const CANVAS_SOURCE_ATTR = 'data-manga-source-signature';
  const CANVAS_TRANSLATION_KEY_ATTR = 'data-manga-translation-key';
  const CANVAS_OVERLAY_CLASS = 'manga-canvas-overlay';
  const CANVAS_READER_SELECTOR = 'canvas, #renderer, [id^="viewport"], .currentScreen';
  const BLACK_IMAGE_MESSAGE = 'Translated image appears black - please try again or clear cache';
  const REGION_CONTAINER_CLASS = 'manga-region-translation';
  const CANVAS_OVERLAY_STYLE = `
    position: absolute;
    z-index: 9999;
    pointer-events: none;
    display: none;
  `;
  const CANVAS_RECONCILE_INTERVAL_MS = 750;
  const CANVAS_TRANSLATION_MEMORY_MAX = 12;
  // Minimum base64 string length to consider image data valid (rejects corrupted/empty data)
  const MIN_IMAGE_DATA_LENGTH = 100;
  const MAX_DECODED_IMAGE_BYTES = 20 * 1024 * 1024;
  const MAX_IMAGE_PIXELS = 25_000_000;
  const NON_RETRYABLE_IMAGE_BACKOFF_MS = 5 * 60 * 1000;
  const IMAGE_RETRY_BLOCK_MEMORY_MAX = 128;
  const STALE_CANVAS_BACKOFF_MS = 1500;
  const BOOKWALKER_QUEUE_ATTR = 'data-manga-queued';
  const BOOKWALKER_QUEUE_STATE_ATTR = 'data-manga-queue-state';
  const BOOKWALKER_QUEUE_AHEAD_ATTR = 'data-manga-queue-ahead';
  const BOOKWALKER_WAIT_LABEL = 'wait_for translate';
  const BOOKWALKER_QUEUE_LABEL_PREFIX = 'load_img in queue';

  // State
  let settings = null;
  let lastKnownSettings = null;
  let siteEnabled = true;
  let siteRuntimeStarted = false;
  let siteRuntimeScope = null;
  let isProcessing = false;
  let translationCancelRequested = false;
  let inpainterFallbackNotified = false;
  // Fallback text held back while a managed run owns the progress bar, so the
  // run's final progress line carries it instead of overwriting it.
  let pendingInpainterNotice = '';
  // Page-based image cache for readers like cmoa.jp
  // Maps page container ID -> base64 image data
  const PAGE_IMAGE_CACHE_MAX = 10;
  const pageImageCache = new Map();
  const pageCacheStatus = new Map(); // 'pending' | 'cached' | 'failed'

  let lastScrollPosition = 0;
  let scrollCacheDebounce = null;
  let canvasReconcileDebounce = null;
  let canvasReconcileInterval = null;
  let isRestoringOverlay = false;
  let regionSelectionCleanup = null;
  const bundledFontLoads = new Map();
  let blobUrlCleanupObserver = null;
  const canvasTranslationMemory = new Map();

  function isCaptureDimensionsAllowed(width, height) {
    const numericWidth = Number(width);
    const numericHeight = Number(height);
    return Number.isFinite(numericWidth) && Number.isFinite(numericHeight) &&
      numericWidth > 0 && numericHeight > 0 && numericWidth * numericHeight <= MAX_IMAGE_PIXELS;
  }

  function createImageLimitError(message) {
    const error = new Error(message);
    error.nonRetryable = true;
    error.retryable = false;
    return error;
  }

  function assertCaptureDimensions(width, height) {
    if (!isCaptureDimensionsAllowed(width, height)) {
      throw createImageLimitError('Image exceeds 25 MP pixel limit');
    }
  }

  function assertCaptureBase64Size(base64) {
    const payload = typeof base64 === 'string'
      ? base64.replace(/^data:[^,]*;base64,/i, '').replace(/\s+/g, '')
      : '';
    const padding = payload.endsWith('==') ? 2 : payload.endsWith('=') ? 1 : 0;
    const decodedBytes = payload ? Math.floor((payload.length * 3) / 4) - padding : 0;
    if (decodedBytes > MAX_DECODED_IMAGE_BYTES) {
      throw createImageLimitError('Image exceeds 20 MB capture limit');
    }
    return true;
  }

  async function processWithConcurrency(items, concurrency, fn) {
    const results = [];
    let index = 0;
    async function worker() {
      while (index < items.length) {
        const i = index++;
        results[i] = await fn(items[i], i);
      }
    }
    const workers = Array.from({ length: Math.min(concurrency, items.length) }, () => worker());
    await Promise.all(workers);
    return results;
  }

  function delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  function lruSet(map, key, value, maxSize, onEvict) {
    if (map.has(key)) {
      map.delete(key);
    } else if (map.size >= maxSize) {
      const oldestKey = map.keys().next().value;
      map.delete(oldestKey);
      onEvict?.(oldestKey);
    }
    map.set(key, value);
  }

  function makeElement(tag, props) {
    return Object.assign(document.createElement(tag), props);
  }

  function ensurePositioned(element) {
    if (getComputedStyle(element).position === 'static') {
      saveOriginalStyle(element, 'position');
      element.style.position = 'relative';
    }
  }

  /**
   * Save original computed style values before mutation, so they can be restored on clear.
   */
  function saveOriginalStyle(element, ...props) {
    if (!element || !element.dataset) return;
    for (const prop of props) {
      const dataKey = `mangaOriginalStyle${prop.charAt(0).toUpperCase() + prop.slice(1)}`;
      if (element.dataset[dataKey] === undefined) {
        const computed = getComputedStyle(element)[prop];
        element.dataset[dataKey] = computed || '';
      }
    }
  }

  /**
   * Restore original style values that were saved before mutation.
   */
  function restoreOriginalStyles(element) {
    if (!element || !element.dataset) return;
    const keys = Object.keys(element.dataset).filter(k => k.startsWith('mangaOriginalStyle'));
    for (const dataKey of keys) {
      const prop = dataKey.replace('mangaOriginalStyle', '');
      const cssProp = prop.charAt(0).toLowerCase() + prop.slice(1);
      const originalValue = element.dataset[dataKey];
      element.style[cssProp] = originalValue;
      delete element.dataset[dataKey];
    }
  }

  const imageRetryBlockMemory = new Map();
  const canvasElementRetryBlocks = new WeakMap();
  const bookWalkerCanvasQueue = [];
  const bookWalkerQueuedCanvases = new WeakSet();
  const bookWalkerQueueStateByKey = new Map();
  let isDrainingBookWalkerCanvasQueue = false;

  function canAutoProcessImages() {
    return siteEnabled && Boolean(settings?.autoTranslate) && !isProcessing;
  }

  function createRuntimeScope() {
    const cleanups = [];
    let disposed = false;

    function addCleanup(cleanup) {
      if (typeof cleanup !== 'function') return cleanup;
      if (disposed) {
        cleanup();
      } else {
        cleanups.push(cleanup);
      }
      return cleanup;
    }

    return {
      get disposed() {
        return disposed;
      },
      addCleanup,
      listen(target, type, handler, options) {
        target?.addEventListener?.(type, handler, options);
        addCleanup(() => target?.removeEventListener?.(type, handler, options));
        return handler;
      },
      observe(observer) {
        addCleanup(() => observer?.disconnect?.());
        return observer;
      },
      timer(timerId, clear = clearTimeout) {
        if (timerId !== null && timerId !== undefined) {
          addCleanup(() => clear(timerId));
        }
        return timerId;
      },
      dispose() {
        if (disposed) return;
        disposed = true;
        cleanups.splice(0).reverse().forEach(cleanup => {
          try {
            cleanup();
          } catch (error) {
            console.warn('[Manga Translator] Runtime cleanup failed:', error?.message || error);
          }
        });
      }
    };
  }

  function setRuntimeTimeout(scope, callback, delayMs) {
    const timerId = setTimeout(() => {
      if (!scope?.disposed) callback();
    }, delayMs);
    scope?.timer(timerId);
    return timerId;
  }

  function setupBlobUrlCleanup() {
    if (blobUrlCleanupObserver) return;

    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        for (const node of mutation.removedNodes) {
          if (node.nodeType === Node.ELEMENT_NODE) {
            cleanupBlobUrls(node);
          }
        }
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    blobUrlCleanupObserver = observer;
  }

  function cleanupBlobUrls(element) {
    // Moves (re-parenting, reordering) are reported as removals; a node that is
    // connected again still owns its translated blob URL.
    if (!element.dataset || element.isConnected === true) return;
    revokeTranslatedSrc(element);
    element.querySelectorAll?.('[data-manga-translated-url]').forEach(child => revokeTranslatedSrc(child));
  }

  async function init() {
    await loadSettings();

    chrome.runtime.onMessage.addListener(handleMessage);
    setupBlobUrlCleanup();

    await refreshSiteActivation();
    if (siteEnabled) startSiteRuntime();

    debugLog('Content script initialized');
  }

  async function refreshSiteActivation() {
    if (!siteActivation?.getSiteState) {
      siteEnabled = true;
      return siteEnabled;
    }

    try {
      const nextState = await siteActivation.getSiteState(location.href);
      const wasEnabled = siteEnabled;
      siteEnabled = nextState.enabled;

      if (siteEnabled && !wasEnabled) {
        startSiteRuntime();
      } else if (!siteEnabled && wasEnabled) {
        stopSiteRuntime();
      }
    } catch (error) {
      console.warn('[Manga Translator] Failed to read site activation:', error?.message || error);
      // Do not fail open after an explicit disabled state has been observed.
    }

    return siteEnabled;
  }

  function startSiteRuntime() {
    if (!siteEnabled || siteRuntimeStarted) return;
    siteRuntimeStarted = true;
    const scope = createRuntimeScope();
    siteRuntimeScope = scope;

    if (translationCancelRequested) {
      waitForTranslationIdle().then(() => {
        if (siteEnabled && siteRuntimeScope === scope) {
          translationCancelRequested = false;
          if (settings?.autoTranslate) translatePage();
        }
      });
    }

    try {
      setupReaderControls(scope);
      setupMutationObserver(scope);
      setupScrollBasedCaching(scope);
      setupCanvasViewportObserver(scope);

      const pageLoaded = document.readyState === 'complete';

      // Auto-translate waits for the page load so image sizes are known.
      if (settings?.autoTranslate) {
        const autoTranslate = () => {
          if (siteEnabled && !translationCancelRequested) translatePage();
        };
        if (pageLoaded) {
          setRuntimeTimeout(scope, autoTranslate, 300);
        } else {
          scope.listen(window, 'load', autoTranslate, { once: true });
        }
      }

      const schedulePreCache = () => setRuntimeTimeout(scope, () => {
        if (siteEnabled) preCacheVisiblePages();
      }, 500);
      if (pageLoaded) {
        schedulePreCache();
      } else {
        scope.listen(window, 'load', schedulePreCache, { once: true });
      }
    } catch (error) {
      scope.dispose();
      siteRuntimeScope = null;
      siteRuntimeStarted = false;
      throw error;
    }
  }

  function hasActiveTranslationWork() {
    return isProcessing ||
      isDrainingBookWalkerCanvasQueue ||
      bookWalkerCanvasQueue.length > 0 ||
      document.querySelector(`[${PROCESSING_ATTR}]`) !== null;
  }

  async function waitForTranslationIdle() {
    while (hasActiveTranslationWork()) {
      await delay(50);
    }
  }

  function stopSiteRuntime() {
    if (hasActiveTranslationWork()) {
      requestStopTranslation();
    } else {
      translationCancelRequested = false;
    }

    siteRuntimeScope?.dispose();
    siteRuntimeScope = null;
    siteRuntimeStarted = false;

    clearTimeout(scrollCacheDebounce);
    scrollCacheDebounce = null;
    clearTimeout(canvasReconcileDebounce);
    canvasReconcileDebounce = null;
    clearInterval(canvasReconcileInterval);
    canvasReconcileInterval = null;
    document.getElementById('manga-reader-controls')?.remove?.();
    regionSelectionCleanup?.();
    hideProgress();
    hidePersistentError();
  }

  async function loadSettings() {
    try {
      const result = await chrome.storage.sync.get('settings');
      const localResult = await chrome.storage.local.get('apiKeys');
      const loaded = result.settings || {};
      const apiKeys = localResult.apiKeys || {};
      settings = mergeSettings(applyStoredApiKeys(loaded, apiKeys));
      lastKnownSettings = settings;
    } catch (error) {
      console.error('[Manga Translator] Error loading settings:', error);
      settings = lastKnownSettings || mergeSettings();
    }
  }

  chrome.storage?.onChanged?.addListener((changes, areaName) => {
    if ((areaName === 'sync' && changes.settings) || (areaName === 'local' && changes.apiKeys)) {
      loadSettings();
    }
    if (areaName === 'local' && (changes.activeUrls || changes.disabledUrls)) {
      refreshSiteActivation();
    }
  });

  function handleMessage(message, sender, sendResponse) {
    const gatedCommandTypes = new Set([
      'TRANSLATE_PAGE',
      'TRANSLATE_VISIBLE_IMAGES',
      'TRANSLATE_IMAGE_BY_SRC',
      'START_REGION_SELECTION'
    ]);

    if (!siteEnabled && gatedCommandTypes.has(message.type)) {
      sendResponse({ success: false, error: 'Manga Translator is disabled on this site' });
      return true;
    }

    switch (message.type) {
      case 'TRANSLATE_PAGE':
        sendResponse({ success: true });
        setTimeout(() => translatePage(), 0);
        return true;
      case 'TRANSLATE_VISIBLE_IMAGES':
        sendResponse({ success: true });
        setTimeout(() => translatePage({ visibleOnly: true }), 0);
        return true;
      case 'START_REGION_SELECTION':
        sendResponse({ success: true });
        setTimeout(() => startRegionSelection(), 0);
        return true;
      case 'CLEAR_PAGE_TRANSLATIONS':
        sendResponse(clearPageTranslations());
        return true;
      case 'STOP_TRANSLATION':
        sendResponse(requestStopTranslation());
        return true;
      case 'TRANSLATE_IMAGE_BY_SRC':
        translateImageData(resolveImageDataBySource(message.src), { bypassRetryBlock: true })
          .then(sendResponse)
          .catch((error) => {
            console.error('[Manga Translator] Error translating selected image:', error);
            sendResponse({ success: false, error: error.message });
          });
        return true;
      case 'SETTINGS_UPDATED':
        loadSettings()
          .then(() => sendResponse({ success: true }))
          .catch((error) => {
            console.error('[Manga Translator] Error refreshing settings:', error);
            settings = mergeSettings({ ...(settings || {}), ...(message.settings || {}) });
            sendResponse({ success: false, error: error.message });
          });
        return true;
      case 'SITE_ACTIVATION_CHANGED':
        refreshSiteActivation()
          .then(enabled => sendResponse({ success: true, enabled }))
          .catch(error => sendResponse({ success: false, error: error.message }));
        return true;
      case 'TRANSLATION_ERROR':
        handleTranslationError(message.imageId, message.error);
        sendResponse({ success: true });
        return true;
    }
    return false;
  }

  function setupMutationObserver(scope) {
    let pendingMutations = [];
    let rafScheduled = false;
    let rafId = null;

    function processBatch() {
      rafScheduled = false;
      rafId = null;
      if (!siteEnabled) {
        pendingMutations = [];
        return;
      }
      const batch = pendingMutations;
      pendingMutations = [];

      const processIfTranslatable = img => {
        if (canAutoProcessImages() && isTranslatableImage(img)) {
          processImage(img, 'img', null, null, { skipScroll: true });
        }
      };

      batch.forEach(mutation => mutation.addedNodes.forEach(node => {
        if (node.nodeName === 'IMG') processIfTranslatable(node);
        node.querySelectorAll?.('img').forEach(processIfTranslatable);
      }));
    }

    const observer = new MutationObserver((mutations) => {
      pendingMutations.push(...mutations);
      if (!rafScheduled) {
        rafScheduled = true;
        rafId = requestAnimationFrame(processBatch);
      }
    });

    observer.observe(document.body, { childList: true, subtree: true });
    scope?.observe(observer);
    scope?.addCleanup(() => {
      pendingMutations = [];
      if (rafScheduled && rafId !== null) globalThis.cancelAnimationFrame?.(rafId);
      rafScheduled = false;
      rafId = null;
    });
  }

  function setupReaderControls(scope) {
    if (document.getElementById('manga-reader-controls')) return;

    const controls = createShadowHost({
      id: 'manga-reader-controls',
      styleText: `
        position: fixed;
        right: 18px;
        bottom: 92px;
        z-index: 100000;
        display: grid;
        grid-template-columns: repeat(5, 36px);
        gap: 6px;
        padding: 6px;
        border: 1px solid rgba(111, 45, 39, 0.26);
        border-radius: 8px;
        background: rgba(255, 248, 249, 0.96);
        box-shadow: 0 12px 28px rgba(111, 45, 39, 0.16);
      `
    });
    controls.setAttribute('role', 'toolbar');
    controls.setAttribute('aria-label', 'Manga translation controls');

    const buttons = [
      {
        action: 'visible',
        label: 'Translate visible images',
        svg: '<path d="M4 5h16v14H4z"></path><path d="M8 9h8"></path><path d="M8 13h5"></path>',
        run: () => translatePage({ visibleOnly: true })
      },
      {
        action: 'all',
        label: 'Translate all images',
        svg: '<path d="M3 12a9 9 0 0 1 15.3-6.4"></path><path d="M21 12a9 9 0 0 1-15.3 6.4"></path><path d="M18 3v5h-5"></path><path d="M6 21v-5h5"></path>',
        run: () => translatePage()
      },
      {
        action: 'region',
        label: 'Translate a selected region',
        svg: '<path d="M4 8V4h4"></path><path d="M16 4h4v4"></path><path d="M20 16v4h-4"></path><path d="M8 20H4v-4"></path>',
        run: () => startRegionSelection()
      },
      {
        action: 'stop',
        label: 'Stop translation',
        svg: '<rect x="6" y="6" width="12" height="12"></rect>',
        run: () => requestStopTranslation()
      },
      {
        action: 'clear',
        label: 'Clear translations',
        svg: '<path d="M3 6h18"></path><path d="M8 6V4h8v2"></path><path d="M19 6l-1 14H6L5 6"></path><path d="M10 11v5"></path><path d="M14 11v5"></path>',
        run: () => clearPageTranslations()
      }
    ];

    const controlButtons = buttons.map(({ action, label, svg, run }) => {
      const button = makeElement('button', { type: 'button', className: 'manga-reader-control-btn', title: label });
      button.dataset.mangaAction = action;
      button.setAttribute('aria-label', label);
      button.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${svg}</svg>`;
      button.addEventListener('click', run);
      return button;
    });

    mountInShadow(controls, ...controlButtons);

    document.body.appendChild(controls);
    scope?.addCleanup(() => controls.remove());
  }

  // Pre-caches upcoming pages while the user scrolls cmoa.jp style readers.
  function setupScrollBasedCaching(scope) {
    const schedulePreCache = (delayMs, shouldCache = () => true) => {
      if (!siteEnabled || !document.querySelector('[id^="content-p"], .pt-img')) return;
      clearTimeout(scrollCacheDebounce);
      scrollCacheDebounce = setTimeout(() => {
        if (!scope?.disposed && siteEnabled && shouldCache()) preCacheVisiblePages();
      }, delayMs);
    };

    scope?.listen(window, 'scroll', () => schedulePreCache(300, () => {
      const scrolledFar = Math.abs(window.scrollY - lastScrollPosition) > 200;
      if (scrolledFar) lastScrollPosition = window.scrollY;
      return scrolledFar;
    }), { passive: true });
    scope?.listen(document, 'wheel', () => schedulePreCache(500), { passive: true });

    scope?.addCleanup(() => {
      clearTimeout(scrollCacheDebounce);
      scrollCacheDebounce = null;
    });
  }

  function setupCanvasViewportObserver(scope) {
    const observer = new MutationObserver((mutations) => {
      if (!siteEnabled || isRestoringOverlay) return;
      if (mutations.some(mutationTouchesCanvasReader)) {
        scheduleCanvasReconcile(settings?.autoTranslate);
      }
    });

    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['class', 'style']
    });
    scope?.observe(observer);

    scope?.listen(window, 'resize', () => scheduleCanvasReconcile(false), { passive: true });
    scope?.listen(window, 'orientationchange', () => scheduleCanvasReconcile(false), { passive: true });
    scope?.listen(document, 'visibilitychange', () => scheduleCanvasReconcile(settings?.autoTranslate));

    scheduleCanvasReconcile(settings?.autoTranslate, 500);

    if (isBookWalkerViewer() && !canvasReconcileInterval) {
      canvasReconcileInterval = setInterval(() => {
        if (scope?.disposed || !siteEnabled) return;
        reconcileVisibleCanvases(settings?.autoTranslate);
      }, CANVAS_RECONCILE_INTERVAL_MS);
    }

    scope?.addCleanup(() => {
      clearTimeout(canvasReconcileDebounce);
      canvasReconcileDebounce = null;
      clearInterval(canvasReconcileInterval);
      canvasReconcileInterval = null;
    });
  }

  function isBookWalkerViewer() {
    return /(^|\.)viewer(?:-trial)?\.bookwalker\.jp$/i.test(window.location.hostname) ||
      Boolean(document.querySelector('#renderer .currentScreen canvas, #renderer [id^="viewport"] canvas'));
  }

  function getActiveBookWalkerCanvases() {
    if (!isBookWalkerViewer()) return [];

    const isVisibleMangaCanvas = canvas => isPotentialMangaCanvas(canvas) && isCanvasVisible(canvas);
    const active = new Set();
    const points = [
      [window.innerWidth / 2, window.innerHeight / 2],
      [window.innerWidth * 0.25, window.innerHeight / 2],
      [window.innerWidth * 0.75, window.innerHeight / 2],
      [window.innerWidth / 2, window.innerHeight * 0.35],
      [window.innerWidth / 2, window.innerHeight * 0.65]
    ];

    for (const [x, y] of points) {
      const elements = document.elementsFromPoint(
        Math.max(1, Math.min(window.innerWidth - 1, x)),
        Math.max(1, Math.min(window.innerHeight - 1, y))
      );
      const canvas = elements.find(element => element?.tagName === 'CANVAS' && isVisibleMangaCanvas(element));
      if (canvas) active.add(canvas);
    }

    if (active.size > 0) return Array.from(active);

    const currentVisible = Array.from(document.querySelectorAll('#renderer .currentScreen canvas')).filter(isVisibleMangaCanvas);
    if (currentVisible.length > 0) return currentVisible;

    return Array.from(document.querySelectorAll('#renderer [id^="viewport"] canvas, #renderer canvas'))
      .filter(isVisibleMangaCanvas)
      .sort((a, b) => getViewportIntersectionArea(b) - getViewportIntersectionArea(a))
      .slice(0, 2);
  }

  function getViewportIntersectionArea(element) {
    const rect = element.getBoundingClientRect();
    const left = Math.max(0, rect.left);
    const top = Math.max(0, rect.top);
    const right = Math.min(window.innerWidth, rect.right);
    const bottom = Math.min(window.innerHeight, rect.bottom);
    return Math.max(0, right - left) * Math.max(0, bottom - top);
  }

  function getErrorMessage(error) {
    return String(error?.message || error || '');
  }

  function isNonRetryableImageError(error) {
    const message = getErrorMessage(error);
    return Boolean(error?.nonRetryable) || /timed?\s*out|timeout|server is busy|too large|20 MB|25 MP|pixel limit|capture limit/i.test(message);
  }

  function isTranslationTransportError(error) {
    const message = getErrorMessage(error).toLowerCase();
    return /could not establish connection|receiving end does not exist|message port closed|message channel closed|extension context invalidated|disconnected port|service worker.*(stopped|unavailable|terminated)/i.test(message);
  }

  async function sendTranslationImageRequest(message) {
    const canRetry = !message?.forceRefresh;
    let attempt = 0;

    while (true) {
      try {
        const response = await chrome.runtime.sendMessage(message);
        if (response === undefined || response === null) {
          throw new Error('Could not establish connection. Receiving end does not exist.');
        }
        return response;
      } catch (error) {
        if (!canRetry || attempt >= 1 || !isTranslationTransportError(error)) {
          throw error;
        }
        attempt += 1;
      }
    }
  }

  function getStoredCanvasSignature(canvas) {
    return canvas.getAttribute(CANVAS_SOURCE_ATTR) || probeCanvas(canvas).signature;
  }

  function fnv1aHash(text) {
    let hash = 2166136261;
    for (let i = 0; i < text.length; i++) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
  }

  function compactRetrySignaturePart(value) {
    const normalized = normalizeSourceUrl(value);
    if (!normalized) return '';
    if (normalized.length <= 160) return normalized;
    return `${normalized.slice(0, 80)}#${fnv1aHash(normalized).toString(36)}:${normalized.length}`;
  }

  function compactSourceList(sources, limit) {
    return sources.map(source => compactRetrySignaturePart(source)).filter(Boolean).slice(0, limit).join(',');
  }

  function getImageRetryBlockSignature(element, type = 'img', src = null) {
    if (!element) return '';

    if (type === 'canvas' || element.tagName === 'CANVAS') {
      return getStoredCanvasSignature(element);
    }

    const rect = element.getBoundingClientRect?.() || { width: 0, height: 0 };
    const dimensions = [
      element.naturalWidth || element.width || Math.round(rect.width) || 0,
      element.naturalHeight || element.height || Math.round(rect.height) || 0,
      Math.round(rect.width) || 0,
      Math.round(rect.height) || 0
    ].join('x');

    if (type === 'composite') {
      const containerId = element.id || element.parentElement?.id || '';
      if (containerId) return `composite|${containerId}`;
      const childImages = Array.from(element.querySelectorAll?.('img') || []);
      const childSources = compactSourceList(childImages.flatMap(img => getImageSourceCandidates(img)), 12);
      return childSources ? `composite|${childImages.length}|${childSources}` : `composite|${dimensions}`;
    }

    if (type === 'background') {
      return `background|${compactSourceList([src, ...getBackgroundImageUrls(element)]) || dimensions}`;
    }

    return `img|${compactSourceList([src, ...getImageSourceCandidates(element)], 8) || dimensions}`;
  }

  function clearImageRetryBlock(element, type = null, src = null) {
    if (element?.dataset?.mangaRetryBlockedSignature) {
      imageRetryBlockMemory.delete(element.dataset.mangaRetryBlockedSignature);
    }
    if (element && type) {
      imageRetryBlockMemory.delete(getImageRetryBlockSignature(element, type, src));
    }

    if (!element?.dataset) return;
    delete element.dataset.mangaRetryBlockedSignature;
    delete element.dataset.mangaRetryBlockedUntil;
    delete element.dataset.mangaRetryBlockedReason;
  }

  function storeRetryBlock(element, signature, record) {
    element.dataset.mangaRetryBlockedSignature = signature;
    element.dataset.mangaRetryBlockedUntil = String(record.blockedUntil);
    element.dataset.mangaRetryBlockedReason = record.reason;
  }

  function markImageRetryBlocked(element, type, error, src = null) {
    if (!element?.dataset) return;

    const signature = getImageRetryBlockSignature(element, type, src);
    if (!signature) return;

    const record = {
      blockedUntil: Date.now() + NON_RETRYABLE_IMAGE_BACKOFF_MS,
      reason: getErrorMessage(error)
    };
    lruSet(imageRetryBlockMemory, signature, record, IMAGE_RETRY_BLOCK_MEMORY_MAX);
    storeRetryBlock(element, signature, record);
  }

  function isImageRetryBlocked(element, type = 'img', src = null) {
    if (!element?.dataset) return false;

    const blockedUntil = parseInt(element.dataset.mangaRetryBlockedUntil || '0', 10);
    const now = Date.now();
    const blockedSignature = element.dataset.mangaRetryBlockedSignature || '';
    const currentSignature = getImageRetryBlockSignature(element, type, src);

    if (blockedUntil) {
      if (now >= blockedUntil) {
        clearImageRetryBlock(element, type, src);
      } else if (blockedSignature && currentSignature && blockedSignature !== currentSignature) {
        clearImageRetryBlock(element);
      } else {
        return true;
      }
    }

    if (!currentSignature) return false;

    const memoryRecord = imageRetryBlockMemory.get(currentSignature);
    if (!memoryRecord) return false;

    if (now >= memoryRecord.blockedUntil) {
      imageRetryBlockMemory.delete(currentSignature);
      return false;
    }

    storeRetryBlock(element, currentSignature, memoryRecord);
    return true;
  }

  function isCanvasElementRetryBlocked(canvas) {
    const record = canvasElementRetryBlocks.get(canvas);
    if (!record) return false;
    if (Date.now() >= record.blockedUntil) {
      canvasElementRetryBlocks.delete(canvas);
      return false;
    }
    return true;
  }

  function getCanvasLogId(canvas) {
    if (!canvas) return 'img-unknown';
    return canvas.dataset?.mangaCanvasLogId ||
      canvas.getAttribute(PROCESSING_ATTR) ||
      canvas.id ||
      canvas.parentElement?.id ||
      canvas.closest?.('[id]')?.id ||
      'img-unknown';
  }

  function hasProcessingBookWalkerCanvas() {
    return document.querySelector(`canvas[${PROCESSING_ATTR}]`) !== null;
  }

  function getBookWalkerQueueKey(canvas) {
    if (!canvas) return '';
    return canvas.getAttribute?.(CANVAS_TRANSLATION_KEY_ATTR) ||
      canvas.dataset?.mangaCanvasTranslationKey ||
      getCanvasTranslationKey(canvas) ||
      canvas.id ||
      canvas.parentElement?.id ||
      canvas.closest?.('[id]')?.id ||
      '';
  }

  function renderBookWalkerQueueState(canvas) {
    if (!canvas || !settings?.showProgress) return;
    const record = bookWalkerQueueStateByKey.get(getBookWalkerQueueKey(canvas));
    if (record?.state === 'processing') {
      showImageProgress(canvas, BOOKWALKER_WAIT_LABEL);
    } else if (record?.state === 'queued') {
      showImageProgress(canvas, `${BOOKWALKER_QUEUE_LABEL_PREFIX}${Math.max(1, record.queueAhead)}`);
    }
  }

  function setBookWalkerQueueState(canvas, state, queueAhead = 0) {
    if (!canvas) return;
    const key = getBookWalkerQueueKey(canvas);
    if (!key) return;

    const record = {
      state,
      queueAhead: Math.max(0, Number(queueAhead) || 0),
      updatedAt: Date.now()
    };
    bookWalkerQueueStateByKey.set(key, record);
    canvas.setAttribute?.(BOOKWALKER_QUEUE_STATE_ATTR, state);
    if (state === 'queued') {
      canvas.setAttribute?.(BOOKWALKER_QUEUE_AHEAD_ATTR, String(record.queueAhead));
    } else {
      canvas.removeAttribute?.(BOOKWALKER_QUEUE_AHEAD_ATTR);
    }
    renderBookWalkerQueueState(canvas);
  }

  function clearBookWalkerQueueState(canvas) {
    if (!canvas) return;
    bookWalkerQueuedCanvases.delete(canvas);
    canvas.removeAttribute?.(BOOKWALKER_QUEUE_ATTR);
    canvas.removeAttribute?.(BOOKWALKER_QUEUE_AHEAD_ATTR);
    // Always delete the record, or compaction would orphan entries in the map.
    const key = getBookWalkerQueueKey(canvas);
    if (key) bookWalkerQueueStateByKey.delete(key);
    canvas.removeAttribute?.(BOOKWALKER_QUEUE_STATE_ATTR);
  }

  function completeBookWalkerQueueState(canvas, state = 'translated') {
    if (!canvas) return;
    clearBookWalkerQueueState(canvas);
    setBookWalkerQueueState(canvas, state);
    if (state === 'translated') {
      hideImageProgress(canvas);
    }
  }

  function isBookWalkerQueueEligible(canvas) {
    return canvas?.isConnected &&
      !canvas.hasAttribute(TRANSLATION_ATTR) &&
      !canvas.hasAttribute(PROCESSING_ATTR) &&
      isPotentialMangaCanvas(canvas);
  }

  function compactBookWalkerCanvasQueue() {
    for (let index = bookWalkerCanvasQueue.length - 1; index >= 0; index--) {
      const canvas = bookWalkerCanvasQueue[index];
      if (!isBookWalkerQueueEligible(canvas)) {
        bookWalkerCanvasQueue.splice(index, 1);
        clearBookWalkerQueueState(canvas);
      }
    }
  }

  // skipCompact is for the drain loop, which owns the queue while it iterates.
  function refreshBookWalkerQueueStates({ skipCompact = false } = {}) {
    if (!skipCompact) {
      compactBookWalkerCanvasQueue();
    }
    let queueAhead = hasProcessingBookWalkerCanvas() ? 1 : 0;
    for (const canvas of bookWalkerCanvasQueue) {
      setBookWalkerQueueState(canvas, 'queued', Math.max(1, queueAhead));
      queueAhead += 1;
    }
  }

  // Returns false if the canvas was ineligible (disconnected, already translated, etc.).
  function enqueueBookWalkerCanvas(canvas, reason = 'auto') {
    if (translationCancelRequested) return false;
    if (!canvas ||
        !canvas.isConnected ||
        canvas.hasAttribute(TRANSLATION_ATTR) ||
        canvas.hasAttribute(PROCESSING_ATTR) ||
        canvas.hasAttribute(BOOKWALKER_QUEUE_ATTR) ||
        bookWalkerQueuedCanvases.has(canvas)) {
      return false;
    }

    bookWalkerQueuedCanvases.add(canvas);
    canvas.setAttribute(BOOKWALKER_QUEUE_ATTR, 'true');
    bookWalkerCanvasQueue.push(canvas);
    refreshBookWalkerQueueStates();
    debugLog('BookWalker canvas queued for translate', getCanvasLogId(canvas), reason);
    drainBookWalkerCanvasQueue();
    return true;
  }

  function dropQueuedCanvas(canvas) {
    bookWalkerCanvasQueue.shift();
    clearBookWalkerQueueState(canvas);
    refreshBookWalkerQueueStates({ skipCompact: true });
  }

  async function waitForBookWalkerQueueSlot() {
    while (!translationCancelRequested && hasProcessingBookWalkerCanvas()) {
      await delay(150);
    }
  }

  // Single consumer of the queue: isDrainingBookWalkerCanvasQueue guards against
  // re-entrance, and items added while draining restart the loop at the end.
  async function drainBookWalkerCanvasQueue() {
    if (isDrainingBookWalkerCanvasQueue) return;
    isDrainingBookWalkerCanvasQueue = true;

    try {
      compactBookWalkerCanvasQueue();
      refreshBookWalkerQueueStates({ skipCompact: true });

      while (bookWalkerCanvasQueue.length > 0 && !translationCancelRequested) {
        const canvas = bookWalkerCanvasQueue[0];

        if (!isBookWalkerQueueEligible(canvas)) {
          dropQueuedCanvas(canvas);
          continue;
        }

        // A revisited page restores from memory instead of re-translating.
        if (restoreCanvasTranslationFromMemory(canvas)) {
          bookWalkerCanvasQueue.shift();
          completeBookWalkerQueueState(canvas, 'translated');
          refreshBookWalkerQueueStates({ skipCompact: true });
          continue;
        }

        await waitForBookWalkerQueueSlot();
        if (translationCancelRequested) break;
        if (!isBookWalkerQueueEligible(canvas)) {
          dropQueuedCanvas(canvas);
          continue;
        }

        bookWalkerCanvasQueue.shift();
        clearBookWalkerQueueState(canvas);
        setBookWalkerQueueState(canvas, 'processing');
        refreshBookWalkerQueueStates({ skipCompact: true });
        const logId = getCanvasLogId(canvas);
        debugLog('BookWalker canvas wait for translate', logId);
        renderBookWalkerQueueState(canvas);

        try {
          const result = await processImage(canvas, 'canvas', null, null, { skipScroll: true });
          if (!result?.success && result?.error) {
            setBookWalkerQueueState(canvas, 'failed');
            console.warn('[Manga Translator] BookWalker queued canvas translation failed', logId, result.error);
          }
        } catch (err) {
          setBookWalkerQueueState(canvas, 'failed');
          console.warn('[Manga Translator] BookWalker queued canvas translation error', logId, err);
        }
        refreshBookWalkerQueueStates({ skipCompact: true });
      }
    } finally {
      isDrainingBookWalkerCanvasQueue = false;
      if (bookWalkerCanvasQueue.length > 0 && !translationCancelRequested) {
        setTimeout(() => drainBookWalkerCanvasQueue(), 0);
      }
    }
  }

  function getRetryBlockedError(element, type = 'img') {
    const label = type === 'canvas' ? 'Canvas' : 'Image';
    return element?.dataset?.mangaRetryBlockedReason ||
      `${label} translation is paused after a recent timeout`;
  }

  function mutationTouchesCanvasReader(mutation) {
    return isCanvasReaderNode(mutation.target) ||
      Array.from(mutation.addedNodes || []).some(node => isCanvasReaderNode(node));
  }

  function isCanvasReaderNode(node) {
    if (!node || node.nodeType !== Node.ELEMENT_NODE) return false;
    return node.matches?.(CANVAS_READER_SELECTOR) || node.querySelector?.(CANVAS_READER_SELECTOR);
  }

  function scheduleCanvasReconcile(autoProcess = false, delayMs = 120) {
    if (!siteEnabled) return;
    clearTimeout(canvasReconcileDebounce);
    canvasReconcileDebounce = setTimeout(() => {
      reconcileVisibleCanvases(autoProcess);
    }, delayMs);
  }

  function reconcileVisibleCanvases(autoProcess = false) {
    if (!siteEnabled) return;
    syncCanvasOverlays();

    const isBookWalker = isBookWalkerViewer();
    const activeBookWalkerCanvases = isBookWalker ? new Set(getActiveBookWalkerCanvases()) : null;
    const allowAutoProcess = autoProcess && (isBookWalker ? Boolean(settings?.autoTranslate) : canAutoProcessImages());
    for (const canvas of document.querySelectorAll('canvas')) {
      if (!isPotentialMangaCanvas(canvas)) continue;

      if (isBookWalker && !activeBookWalkerCanvases.has(canvas)) {
        if (canvas.hasAttribute(PROCESSING_ATTR) || canvas.hasAttribute(BOOKWALKER_QUEUE_ATTR)) {
          renderBookWalkerQueueState(canvas);
          continue;
        }
        hideImageProgress(canvas);
        if (canvas.hasAttribute(TRANSLATION_ATTR) || canvas.dataset?.mangaCanvasOverlayId) {
          clearCanvasTranslation(canvas);
        }
        continue;
      }

      if (clearCanvasTranslationIfChanged(canvas)) {
        debugLog('Canvas content changed; cleared stale translation overlay');
      }

      if (isBookWalker) {
        renderBookWalkerQueueState(canvas);
      }

      if ((isBookWalker && isImageRetryBlocked(canvas, 'canvas')) || isCanvasElementRetryBlocked(canvas)) {
        continue;
      }

      if (canvas.hasAttribute(TRANSLATION_ATTR) || canvas.hasAttribute(PROCESSING_ATTR) || !isCanvasVisible(canvas)) {
        continue;
      }

      if (!isBookWalker) {
        if (allowAutoProcess) processImage(canvas, 'canvas', null, null, { skipScroll: true });
      } else if (!restoreCanvasTranslationFromMemory(canvas) && allowAutoProcess && !canvas.hasAttribute(BOOKWALKER_QUEUE_ATTR)) {
        enqueueBookWalkerCanvas(canvas, 'reconcile-visible');
      }
    }
  }

  function getPageId(container) {
    return container.id || container.parentElement?.id;
  }

  async function waitForSliceImages(container, attempts, intervalMs) {
    let slices = container.querySelectorAll('img');
    for (let attempt = 0; slices.length === 0 && attempt < attempts; attempt++) {
      await delay(intervalMs);
      slices = container.querySelectorAll('img');
    }
    return slices;
  }

  // Some slices may never load; the capture works with whichever did.
  function waitForSlicesToLoad(slices, timeoutMs) {
    return Promise.all(Array.from(slices, img =>
      waitForImageLoad(img, timeoutMs).catch(error => debugLog('Slice load warning:', error.message))
    ));
  }

  // Caches visible and nearby pages up front, because scrolling can revoke their blob URLs.
  async function preCacheVisiblePages() {
    if (!siteEnabled) return;
    const pageContainers = document.querySelectorAll('[id^="content-p"], .pt-img');
    if (pageContainers.length === 0) return;

    const viewportHeight = window.innerHeight;
    const viewportCenter = window.scrollY + viewportHeight / 2;

    const pages = Array.from(pageContainers).map(container => {
      const rect = container.getBoundingClientRect();
      const pageCenter = window.scrollY + rect.top + rect.height / 2;
      return {
        container,
        rect,
        distance: Math.abs(pageCenter - viewportCenter),
        isVisible: rect.bottom > 0 && rect.top < viewportHeight
      };
    });
    pages.sort((a, b) => Number(b.isVisible) - Number(a.isVisible) || a.distance - b.distance);

    // Four pages cover two double-page spreads.
    for (const { container, rect } of pages.slice(0, 4)) {
      const pageId = getPageId(container);
      if (!pageId || pageImageCache.has(pageId) || pageCacheStatus.get(pageId) === 'pending') continue;
      if (container.hasAttribute(TRANSLATION_ATTR) || container.querySelector('.manga-composite-overlay')) continue;
      if (rect.width === 0) continue;

      const slices = container.querySelectorAll('img');
      if (slices.length === 0) continue;

      pageCacheStatus.set(pageId, 'pending');
      try {
        await waitForSlicesToLoad(slices, 3000);
        const base64 = await captureCompositeImage(container);

        if (base64 && base64.length > MIN_IMAGE_DATA_LENGTH) {
          lruSet(pageImageCache, pageId, base64, PAGE_IMAGE_CACHE_MAX, key => pageCacheStatus.delete(key));
          pageCacheStatus.set(pageId, 'cached');
          debugLog(`Pre-cached page: ${pageId}`);
        } else {
          pageCacheStatus.set(pageId, 'failed');
        }
      } catch (e) {
        console.warn(`[Manga Translator] Failed to pre-cache page ${pageId}:`, e.message);
        pageCacheStatus.set(pageId, 'failed');
      }
    }
  }

  function getCachedPageImage(container) {
    const pageId = getPageId(container);
    return (pageId && pageImageCache.get(pageId)) || null;
  }

  // SPA readers can swap src on a reused element without changing the URL.
  function isImageSrcStale(img) {
    const originalSrc = img.dataset?.mangaOriginalSrc;
    const currentSrc = img.src || img.dataset?.src || '';
    // Blob and data URLs are unique per creation, so comparing them says nothing.
    const isEphemeral = url => url.startsWith('blob:') || url.startsWith('data:');
    return Boolean(originalSrc) && originalSrc !== currentSrc && !isEphemeral(originalSrc) && !isEphemeral(currentSrc);
  }

  function discoverImages() {
    const images = [];
    const processedElements = new Set();
    const isBookWalker = isBookWalkerViewer();
    const activeBookWalkerCanvases = isBookWalker ? new Set(getActiveBookWalkerCanvases()) : null;

    function scan(doc, offsetTop = 0, offsetLeft = 0) {
      if (!doc) return;

      const addImage = (element, type, src = null, selector = null) => {
        images.push({ element, selector, type, src, offsetTop, offsetLeft });
      };

      doc.querySelectorAll(`img[${TRANSLATION_ATTR}]`).forEach(img => {
        if (isImageSrcStale(img)) {
          debugLog('Img src changed; clearing stale translation');
          clearImgTranslation(img);
        }
      });

      doc.querySelectorAll(`.pt-img[${TRANSLATION_ATTR}]`).forEach(container => {
        if (Array.from(container.querySelectorAll('img')).some(isImageSrcStale)) {
          debugLog('Composite container src changed; clearing stale translation');
          clearCompositeTranslation(container);
        }
      });

      // Sliced readers like cmoa.jp: content-p* > pt-img > div[inset] > img (slices).
      // Hidden containers count too, because cmoa.jp hides off-screen pages.
      doc.querySelectorAll('.pt-img').forEach(container => {
        const childImages = container.querySelectorAll('img');
        childImages.forEach(img => processedElements.add(img));

        if (container.hasAttribute(TRANSLATION_ATTR) ||
            container.hasAttribute(PROCESSING_ATTR) ||
            container.querySelector('.manga-composite-overlay') ||
            isImageRetryBlocked(container, 'composite')) {
          return;
        }

        const containerId = container.id || container.parentElement?.id || null;
        addImage(container, 'composite', null, containerId ? `[id="${containerId}"]` : null);
        processedElements.add(container);
        debugLog('Found composite:', containerId || 'unknown', `(${childImages.length} slices)`);
      });

      // A content-p* page without .pt-img yet is likely still lazy loading.
      doc.querySelectorAll('[id^="content-p"]').forEach(pageContainer => {
        if (processedElements.has(pageContainer) ||
            isImageRetryBlocked(pageContainer, 'composite') ||
            pageContainer.querySelector('.pt-img')) {
          return;
        }
        addImage(pageContainer, 'composite', null, `[id="${pageContainer.id}"]`);
        processedElements.add(pageContainer);
        debugLog('Found potential page container:', pageContainer.id);
      });

      doc.querySelectorAll('img').forEach(img => {
        if (!processedElements.has(img) && isTranslatableImage(img)) {
          addImage(img, 'img', img.src || img.dataset.src);
        }
      });

      doc.querySelectorAll('canvas').forEach(canvas => {
        if (processedElements.has(canvas) || !isPotentialMangaCanvas(canvas)) return;
        if (isBookWalker && !activeBookWalkerCanvases.has(canvas)) return;
        clearCanvasTranslationIfChanged(canvas);
        if (!isCanvasVisible(canvas)) return;
        if (isBookWalker && isImageRetryBlocked(canvas, 'canvas')) return;
        addImage(canvas, 'canvas');
      });

      doc.querySelectorAll('[style*="background-image"]').forEach(el => {
        if (processedElements.has(el)) return;
        const urlMatch = getComputedStyle(el).backgroundImage.match(/url\(["']?([^"')]+)["']?\)/);
        if (urlMatch &&
            (el.offsetWidth >= MIN_IMAGE_WIDTH || el.offsetHeight >= MIN_IMAGE_HEIGHT) &&
            !isImageRetryBlocked(el, 'background', urlMatch[1])) {
          addImage(el, 'background', urlMatch[1]);
        }
      });

      // Same-origin iframes only.
      doc.querySelectorAll('iframe').forEach(iframe => {
        try {
          const iframeSrc = iframe.src || iframe.getAttribute('src') || '';
          if (iframeSrc && !iframeSrc.startsWith('about:') && !iframeSrc.startsWith('javascript:')) {
            const iframeUrl = new URL(iframeSrc, window.location.href);
            if (iframeUrl.origin !== window.location.origin) return;
          }
          const frameDoc = iframe.contentDocument || iframe.contentWindow.document;
          if (!frameDoc) return;
          const rect = iframe.getBoundingClientRect();
          scan(frameDoc, offsetTop + rect.top, offsetLeft + rect.left);
        } catch (e) {
          // Ignore cross-origin iframes
        }
      });
    }

    scan(document);

    // Images in the viewport first, then by distance from its center.
    const viewportHeight = window.innerHeight;
    const viewportPlacement = ({ element, offsetTop }) => {
      const rect = element.getBoundingClientRect();
      const top = offsetTop + rect.top;
      return {
        inViewport: top < viewportHeight && offsetTop + rect.bottom > 0,
        distance: Math.abs(top + rect.height / 2 - viewportHeight / 2)
      };
    };
    images.sort((a, b) => {
      const placementA = viewportPlacement(a);
      const placementB = viewportPlacement(b);
      return Number(placementB.inViewport) - Number(placementA.inViewport) || placementA.distance - placementB.distance;
    });

    return images;
  }

  function getBackgroundImageUrls(element) {
    return parseBackgroundImageUrls(getComputedStyle(element).backgroundImage);
  }

  function getCompositeContainerForImage(img) {
    const ptImg = img.closest?.('.pt-img');
    if (ptImg) return ptImg;

    const pageContainer = img.closest?.('[id^="content-p"]');
    if (pageContainer && pageContainer.querySelectorAll('img').length > 1) {
      return pageContainer.querySelector('.pt-img') || pageContainer;
    }

    return null;
  }

  function isAlreadyTranslatedElement(element) {
    return Boolean(
      element?.hasAttribute?.(TRANSLATION_ATTR) ||
      element?.hasAttribute?.(PROCESSING_ATTR) ||
      element?.querySelector?.('.manga-composite-overlay') ||
      element?.querySelector?.('.manga-translated-img-overlay') ||
      getAssociatedImageOverlay(element)
    );
  }

  function getAssociatedImageOverlay(element) {
    if (!element) return null;
    const nextOverlay = element.nextElementSibling;
    if (nextOverlay?.classList?.contains('manga-translated-img-overlay')) {
      const overlayId = element.dataset?.mangaCanvasOverlayId;
      if (element.tagName !== 'CANVAS' || nextOverlay.dataset?.mangaCanvasOverlayId === overlayId) {
        return nextOverlay;
      }
    }
    const wrapper = element.parentElement?.classList?.contains('manga-translate-wrapper')
      ? element.parentElement
      : null;
    return wrapper?.querySelector?.('.manga-translated-img-overlay') || null;
  }

  function isElementInViewport(element) {
    if (!element?.isConnected) return false;
    const rect = element.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return false;
    return rect.bottom > 0 &&
      rect.right > 0 &&
      rect.top < window.innerHeight &&
      rect.left < window.innerWidth;
  }

  function isRenderedWithAncestors(element) {
    for (let node = element; node?.nodeType === Node.ELEMENT_NODE; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity || '1') === 0) {
        return false;
      }
    }
    return true;
  }

  function resolveImageDataBySource(src) {
    const target = normalizeSourceUrl(src);
    if (!target) return null;

    for (const img of document.querySelectorAll('img')) {
      const matchesTarget = getImageSourceCandidates(img).some(candidate => sourceUrlsMatch(candidate, target));
      if (!matchesTarget) continue;

      const composite = getCompositeContainerForImage(img);
      if (composite) {
        return {
          element: composite,
          selector: composite.id ? `[id="${composite.id}"]` : null,
          type: 'composite',
          src: null
        };
      }

      return { element: img, type: 'img', src: img.currentSrc || img.src || img.getAttribute('src') || null };
    }

    for (const element of document.querySelectorAll('[style*="background-image"]')) {
      if (getBackgroundImageUrls(element).some(candidate => sourceUrlsMatch(candidate, target))) {
        return { element, type: 'background', src: target };
      }
    }

    return null;
  }

  function reportTranslationError(message, popupError = message) {
    hideProgress();
    showNotification(message, true);
    notifyPopup({ type: 'TRANSLATION_ERROR', error: popupError });
  }

  async function ensureBackendReady() {
    if ((settings?.pipelineMode || 'local') !== 'local') {
      updateProgress('Preparing cloud translation...', 2);
      return true;
    }

    updateProgress('Checking backend connection...', 2);
    const backendCheck = await chrome.runtime.sendMessage({ type: 'CHECK_BACKEND', settings });

    if (backendCheck?.success) {
      return true;
    }

    const backendUrl = settings?.localBackend?.url || 'http://localhost:8001';
    const problem = {
      loading: 'is still loading its models. Wait a moment and try again.',
      degraded: 'is running but its models are not ready. Check the backend console, then try again.',
      unauthorized: 'rejected the Backend Access Token. Check the token in the extension options.',
      auth_unconfigured: 'has no access token configured. Set BACKEND_AUTH_TOKEN on the backend, then restart it.'
    }[backendCheck?.reason];
    reportTranslationError(problem
      ? `The local backend at ${backendUrl} ${problem}`
      : `Backend connection failed. Please ensure the local server is running at ${backendUrl}`);
    return false;
  }

  function clearImageDataTranslation({ element, type }) {
    const clear = {
      canvas: clearCanvasTranslation,
      composite: clearCompositeTranslation,
      background: clearBackgroundTranslation,
      img: clearImgTranslation
    }[type];
    clear(element);
  }

  async function translateImageData(imageData, options = {}) {
    const force = Boolean(options.force);
    const progressLabel = options.progressLabel || 'Translating selected image...';
    const successLabel = options.successLabel || 'Selected image translated';

    if (isProcessing) {
      const message = 'Translation is already running';
      showNotification(message);
      return { success: false, error: message };
    }

    isProcessing = true;
    translationCancelRequested = false;
    hidePersistentError();

    try {
      if (!imageData?.element?.isConnected) {
        const message = 'Could not find the selected image on this page';
        reportTranslationError(message);
        return { success: false, error: message };
      }

      const ready = await ensureBackendReady();
      if (!ready) {
        return { success: false, error: 'Backend connection failed' };
      }

      const shouldForceRefresh = force && isAlreadyTranslatedElement(imageData.element);
      if (shouldForceRefresh) {
        clearImageDataTranslation(imageData);
      }

      const singleImageComplete = { type: 'TRANSLATION_COMPLETE', status: 'complete', total: 1, succeeded: 1, failed: 0 };

      if (isAlreadyTranslatedElement(imageData.element)) {
        hideProgress();
        showNotification('Selected image is already translated');
        notifyPopup(singleImageComplete);
        return { success: true, skipped: true };
      }

      updateProgress(progressLabel, 15);
      const cachedBase64 = imageData.base64 || (imageData.type === 'composite'
        ? getCachedPageImage(imageData.element)
        : null);
      const result = await processImage(imageData.element, imageData.type, imageData.src, cachedBase64, {
        forceRefresh: shouldForceRefresh,
        bypassRetryBlock: Boolean(options.bypassRetryBlock || force)
      });

      if (result?.success) {
        if (imageData.element?.dataset) {
          imageData.element.dataset.mangaTranslationHidden = 'false';
        }
        const finalLabel = withPendingInpainterNotice(successLabel);
        updateProgress(finalLabel, 100);
        notifyPopup(singleImageComplete);
        setTimeout(() => hideProgress(), finalLabel === successLabel ? 1600 : 6000);
        return result;
      }

      const message = result?.error || 'Selected image translation failed';
      reportTranslationError(message);
      return { success: false, error: message };
    } catch (error) {
      console.error('[Manga Translator] Selected image translation failed:', error);
      reportTranslationError(`Translation failed: ${error.message}`, error.message);
      return { success: false, error: error.message };
    } finally {
      isProcessing = false;
    }
  }

  function waitForFrames(count) {
    return new Promise(resolve => {
      const step = remaining => {
        if (remaining <= 0) {
          resolve();
          return;
        }
        requestAnimationFrame(() => step(remaining - 1));
      };
      step(count);
    });
  }

  /**
   * Let the user drag a rectangle over the visible tab, then translate that part
   * of a screenshot. Nothing is captured until the drag finishes.
   */
  function startRegionSelection() {
    if (regionSelectionCleanup || !regionCapture) return;
    if (isProcessing) {
      showNotification('Translation is already running');
      return;
    }

    const box = makeElement('div', { className: 'manga-region-box' });
    const hint = makeElement('div', {
      className: 'manga-region-hint',
      textContent: 'Drag over the area to translate. Press Esc to cancel.'
    });
    const selector = createShadowHost({
      id: 'manga-region-selector',
      styleText: `
        position: fixed;
        top: 0;
        left: 0;
        width: 100vw;
        height: 100vh;
        z-index: 2147483646;
        background: rgba(0, 0, 0, 0.2);
        cursor: crosshair;
        touch-action: none;
      `
    });
    mountInShadow(selector, box, hint);

    let start = null;
    const viewport = () => ({ width: window.innerWidth, height: window.innerHeight });
    const pointFrom = event => ({ x: event.clientX, y: event.clientY });

    const onKeyDown = event => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      cleanup();
    };
    const cleanup = () => {
      regionSelectionCleanup = null;
      window.removeEventListener('keydown', onKeyDown, true);
      selector.remove();
    };
    regionSelectionCleanup = cleanup;

    selector.addEventListener('pointerdown', event => {
      event.preventDefault();
      start = pointFrom(event);
      selector.setPointerCapture?.(event.pointerId);
      selector.style.background = 'transparent';
      hint.remove();
    });
    selector.addEventListener('pointermove', event => {
      if (!start) return;
      const rect = regionCapture.normalizeSelection(start, pointFrom(event), viewport());
      box.style.display = 'block';
      box.style.left = `${rect.left}px`;
      box.style.top = `${rect.top}px`;
      box.style.width = `${rect.width}px`;
      box.style.height = `${rect.height}px`;
    });
    selector.addEventListener('pointerup', event => {
      if (!start) return;
      const rect = regionCapture.normalizeSelection(start, pointFrom(event), viewport());
      cleanup();
      if (!regionCapture.isSelectionLargeEnough(rect)) {
        showNotification('Selected region is too small to translate');
        return;
      }
      translateSelectedRegion(rect).catch(error => {
        console.error('[Manga Translator] Region translation failed:', error);
        showNotification(`Region translation failed: ${error.message}`, true);
      });
    });
    selector.addEventListener('pointercancel', cleanup);

    window.addEventListener('keydown', onKeyDown, true);
    document.documentElement.appendChild(selector);
  }

  async function captureRegion(rect) {
    const extensionUi = document.querySelectorAll('#manga-reader-controls, #manga-global-progress, #manga-error-banner');
    extensionUi.forEach(element => { element.style.visibility = 'hidden'; });
    try {
      // Give the browser time to repaint without the selector and controls.
      await waitForFrames(2);
      return await chrome.runtime.sendMessage({
        type: 'CAPTURE_REGION',
        rect,
        viewport: { width: window.innerWidth, height: window.innerHeight }
      });
    } finally {
      extensionUi.forEach(element => { element.style.visibility = ''; });
    }
  }

  async function translateSelectedRegion(rect) {
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    const capture = await captureRegion(rect);
    if (!capture?.success) {
      throw new Error(capture?.error || 'Could not capture the selected region');
    }

    // The crop stands in for a page image so the normal translation path applies.
    // It lives outside <body> so the page's observers and body offsets ignore it.
    const container = document.createElement('div');
    container.className = REGION_CONTAINER_CLASS;
    container.style.cssText = `
      all: initial;
      position: absolute;
      display: block;
      left: ${rect.left + scrollX}px;
      top: ${rect.top + scrollY}px;
      width: ${rect.width}px;
      height: ${rect.height}px;
      z-index: 99990;
      pointer-events: none;
    `;
    const source = document.createElement('img');
    source.alt = '';
    // Marks the crop as extension-owned so page scans never pick it up.
    source.dataset.mangaOriginalSrc = 'region-capture';
    source.style.cssText = 'all: initial; display: block; width: 100%; height: 100%;';
    source.src = `data:image/png;base64,${capture.base64}`;
    container.appendChild(source);
    document.documentElement.appendChild(container);

    const result = await translateImageData(
      { element: source, type: 'img', src: null, base64: capture.base64 },
      {
        progressLabel: 'Translating selected region...',
        successLabel: 'Selected region translated',
        bypassRetryBlock: true
      }
    );
    if (!result?.success) container.remove();
    return result;
  }

  function requestStopTranslation() {
    const queuedCanvases = document.querySelectorAll(
      `canvas[${BOOKWALKER_QUEUE_ATTR}="true"], canvas[${BOOKWALKER_QUEUE_STATE_ATTR}="queued"]`
    );
    const processingCanvases = document.querySelectorAll(`canvas[${PROCESSING_ATTR}]`);
    if (!isProcessing &&
        queuedCanvases.length === 0 &&
        processingCanvases.length === 0 &&
        bookWalkerCanvasQueue.length === 0) {
      showNotification('No translation is running');
      return { success: true, stopped: false };
    }

    translationCancelRequested = true;
    queuedCanvases.forEach(canvas => clearBookWalkerQueueState(canvas));
    bookWalkerCanvasQueue.splice(0).forEach(canvas => clearBookWalkerQueueState(canvas));
    updateProgress('Stopping after current image...', 96);
    return { success: true, stopped: true };
  }

  function resetTranslationState(element) {
    restoreOriginalStyles(element);
    element.removeAttribute(TRANSLATION_ATTR);
    element.removeAttribute(PROCESSING_ATTR);
    delete element.dataset.mangaTranslationHidden;
    revokeTranslatedSrc(element);
  }

  function unwrapFromTranslateWrapper(element) {
    const wrapper = element.parentElement;
    if (!wrapper?.classList.contains('manga-translate-wrapper')) return;
    wrapper.querySelectorAll('.manga-translate-overlay, .manga-translated-img-overlay, .manga-composite-overlay')
      .forEach(item => item.remove());
    wrapper.parentElement?.insertBefore(element, wrapper);
    if (wrapper.children.length === 0) {
      wrapper.remove();
    }
  }

  function clearBackgroundTranslation(element) {
    resetTranslationState(element);
    hideImageProgress(element);
    delete element.dataset.mangaTranslatedBg;
    clearImageRetryBlock(element, 'background');

    if (element.dataset?.mangaOriginalBg !== undefined) {
      element.style.backgroundImage = element.dataset.mangaOriginalBg;
      delete element.dataset.mangaOriginalBg;
    }
  }

  function clearPageTranslations() {
    const cleared = new Set();
    const clearAll = (selector, clear) => {
      document.querySelectorAll(selector).forEach(element => {
        clear(element);
        cleared.add(element);
      });
    };
    imageRetryBlockMemory.clear();
    canvasTranslationMemory.clear();

    // Clearing a page is also a cancellation boundary. Otherwise canvases that
    // are only queued (and have no translation key yet) can resume after the
    // visible overlays have been removed.
    translationCancelRequested = true;
    bookWalkerCanvasQueue.splice(0).forEach(canvas => {
      clearBookWalkerQueueState(canvas);
      cleared.add(canvas);
    });
    clearAll(`canvas[${BOOKWALKER_QUEUE_ATTR}], canvas[${BOOKWALKER_QUEUE_STATE_ATTR}]`, canvas => {
      clearBookWalkerQueueState(canvas);
      hideImageProgress(canvas);
    });

    clearAll(`canvas[${TRANSLATION_ATTR}], canvas[${PROCESSING_ATTR}], canvas[data-manga-canvas-overlay-id], canvas[data-manga-retry-blocked-until], canvas[${CANVAS_TRANSLATION_KEY_ATTR}]`, clearCanvasTranslation);
    clearAll(`img[${TRANSLATION_ATTR}], img[${PROCESSING_ATTR}], img[data-manga-original-src], img[data-manga-retry-blocked-until]`, clearImgTranslation);
    clearAll(`.pt-img[${TRANSLATION_ATTR}], .pt-img[${PROCESSING_ATTR}], .pt-img[data-manga-retry-blocked-until], [id^="content-p"][${TRANSLATION_ATTR}], [id^="content-p"][${PROCESSING_ATTR}], [id^="content-p"][data-manga-retry-blocked-until]`, clearCompositeTranslation);

    document.querySelectorAll('.manga-composite-overlay').forEach(overlay => {
      const container = overlay.closest('.pt-img, [id^="content-p"]') || overlay.parentElement;
      if (container) {
        clearCompositeTranslation(container);
        cleared.add(container);
      } else {
        overlay.remove();
      }
    });

    clearAll('[data-manga-original-bg]', clearBackgroundTranslation);
    clearAll('[data-manga-retry-blocked-until]', element => clearImageRetryBlock(element));

    document.querySelectorAll('.manga-translate-wrapper').forEach(wrapper => {
      const translatedElement = wrapper.querySelector(`[${TRANSLATION_ATTR}], [${PROCESSING_ATTR}]`);
      if (translatedElement) {
        (translatedElement.tagName === 'IMG' ? clearImgTranslation : clearCompositeTranslation)(translatedElement);
        cleared.add(translatedElement);
      } else {
        wrapper.querySelectorAll('.manga-translate-overlay').forEach(overlay => overlay.remove());
      }
    });

    document.querySelectorAll(`.${REGION_CONTAINER_CLASS}`).forEach(container => container.remove());
    regionSelectionCleanup?.();

    document.querySelectorAll('.manga-image-progress').forEach(progress => progress.remove());
    hideProgress();
    hidePersistentError();

    const count = cleared.size;
    showNotification(count > 0 ? `Cleared ${count} translations` : 'No translations to clear');
    notifyPopup({ type: 'TRANSLATION_COMPLETE' });

    return { success: true, cleared: count };
  }

  function isTranslatableImage(img) {
    if (img.hasAttribute(TRANSLATION_ATTR) || img.hasAttribute(PROCESSING_ATTR)) {
      return false;
    }

    if (isImageRetryBlocked(img, 'img', img.currentSrc || img.src || img.getAttribute('src') || null)) {
      return false;
    }

    if (img.parentElement?.classList.contains('manga-translate-wrapper') ||
        img.dataset.mangaOriginalSrc) {
      return false;
    }

    if (isBookWalkerViewer()) {
      const rect = img.getBoundingClientRect();
      if (!img.closest?.('.pt-img, [id^="content-p"]') ||
          rect.width < 1 || rect.height < 1 ||
          !isRenderedWithAncestors(img)) {
        return false;
      }
    }

    const src = (img.src || '').toLowerCase();

    // In some readers a blob or data image is very likely a manga page.
    if (src.startsWith('blob:') || src.startsWith('data:image/')) {
      return true;
    }

    // Loaded but displayed at 0x0: loading spinners, tracking pixels, etc.
    if (img.complete && img.clientWidth === 0 && img.clientHeight === 0) {
      const rect = img.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) {
        return false;
      }
    }

    // Skip tiny images (icons, etc.) but be lenient if they might be loading
    const width = img.naturalWidth || img.width || img.clientWidth || (img.complete ? 0 : 999);
    const height = img.naturalHeight || img.height || img.clientHeight || (img.complete ? 0 : 999);

    if (width < MIN_IMAGE_WIDTH && height < MIN_IMAGE_HEIGHT && img.complete) {
      return false;
    }

    // Skip common non-manga images. Match whole path tokens only, so hosts and
    // words such as "uploads", "download" or "mangadex" are not mistaken for ads.
    if (src) {
      let path = src;
      try {
        path = new URL(src, document.baseURI).pathname;
      } catch (_error) {
        // Fall back to the raw string when the URL cannot be parsed.
      }
      if (NON_MANGA_PATH_PATTERN.test(path)) {
        return false;
      }
    }

    return true;
  }

  // Resolves once the image is loaded with valid dimensions.
  function waitForImageLoad(img, timeoutMs = 5000) {
    return new Promise((resolve, reject) => {
      if (!img) {
        return reject(new Error('No image element'));
      }

      const isLoaded = () => img.complete && img.naturalWidth > 0 && img.naturalHeight > 0;
      if (isLoaded()) {
        return resolve();
      }

      const finish = settle => {
        clearTimeout(timer);
        clearInterval(poll);
        img.removeEventListener('load', onLoad);
        img.removeEventListener('error', onError);
        settle();
      };

      // Dimensions may need a frame to be computed.
      const onLoad = () => requestAnimationFrame(() => {
        if (isLoaded()) finish(resolve);
      });
      const onError = () => finish(() => reject(new Error('Image failed to load')));

      // Blob URLs may load without firing events.
      const poll = setInterval(() => {
        if (isLoaded()) finish(resolve);
      }, 100);

      // A partially loaded image is still usable at the timeout.
      const timer = setTimeout(() => {
        finish(img.naturalWidth > 0 ? resolve : () => reject(new Error('Image load timeout')));
      }, timeoutMs);

      img.addEventListener('load', onLoad, { once: true });
      img.addEventListener('error', onError, { once: true });
    });
  }

  function isPotentialMangaCanvas(canvas) {
    if (!canvas || canvas.classList?.contains('dummy')) return false;
    const rect = canvas.getBoundingClientRect();
    const width = canvas.width || canvas.clientWidth || rect.width;
    const height = canvas.height || canvas.clientHeight || rect.height;
    return width >= MIN_IMAGE_WIDTH && height >= MIN_IMAGE_HEIGHT;
  }

  function isCanvasVisible(canvas) {
    return isElementInViewport(canvas) && isRenderedWithAncestors(canvas);
  }

  function probeCanvas(canvas) {
    const width = canvas.width || canvas.clientWidth;
    const height = canvas.height || canvas.clientHeight;
    if (!width || !height) {
      return { signature: '', blank: true };
    }

    const sampleSize = 64;
    const sample = document.createElement('canvas');
    sample.width = sampleSize;
    sample.height = sampleSize;

    try {
      const ctx = sample.getContext('2d', { willReadFrequently: true });
      ctx.drawImage(canvas, 0, 0, sampleSize, sampleSize);
      const data = ctx.getImageData(0, 0, sampleSize, sampleSize).data;
      let hash = 2166136261;
      let opaque = 0;
      let min = 255;
      let max = 0;

      for (let i = 0; i < data.length; i += 4) {
        const lum = Math.round((data[i] * 0.299) + (data[i + 1] * 0.587) + (data[i + 2] * 0.114));
        min = Math.min(min, lum);
        max = Math.max(max, lum);
        if (data[i + 3] > 0) opaque++;

        for (let channel = 0; channel < 4; channel++) {
          hash ^= data[i + channel];
          hash = Math.imul(hash, 16777619);
        }
      }

      const signature = [width, height, (hash >>> 0).toString(36), opaque, min, max].join(':');
      const blank = opaque === 0 || max - min < 3;

      return { signature, blank };
    } catch (error) {
      return {
        signature: [width, height, 'unreadable'].join(':'),
        blank: false,
        unreadable: true,
        error
      };
    }
  }

  async function hashString(value) {
    if (globalThis.crypto?.subtle && typeof TextEncoder !== 'undefined') {
      const data = new TextEncoder().encode(value);
      const digest = await globalThis.crypto.subtle.digest('SHA-256', data);
      return Array.from(new Uint8Array(digest))
        .map(byte => byte.toString(16).padStart(2, '0'))
        .join('');
    }

    return fnv1aHash(value).toString(16);
  }

  async function createCanvasCacheSignature(canvas, base64) {
    const hash = await hashString(base64);
    return `canvas:${canvas.width || canvas.clientWidth || 0}:${canvas.height || canvas.clientHeight || 0}:${hash}`;
  }

  async function waitForCanvasReady(canvas, timeoutMs = 5000, options = {}) {
    const start = Date.now();
    let lastSignature = '';
    let stableCount = 0;
    let lastNonBlankSignature = '';
    const allowOffscreen = Boolean(options.allowOffscreen);

    while (Date.now() - start < timeoutMs) {
      if (isPotentialMangaCanvas(canvas) && (allowOffscreen || isCanvasVisible(canvas))) {
        await waitForFrames(2);
        const probe = probeCanvas(canvas);
        if (probe.signature) {
          lastSignature = probe.signature;
          if (!probe.blank) {
            if (probe.signature === lastNonBlankSignature) {
              stableCount++;
            } else {
              stableCount = 0;
              lastNonBlankSignature = probe.signature;
            }

            if (stableCount >= 1 || probe.unreadable) {
              return probe.signature;
            }
          }
        }
      }

      await delay(150);
    }

    return lastNonBlankSignature || lastSignature;
  }

  function getCanvasOverlayHost(canvas) {
    return canvas.closest?.('.currentScreen, [id^="viewport"]') ||
      canvas.parentElement ||
      canvas;
  }

  function getCanvasTranslationKey(canvas) {
    if (!canvas) return '';

    if (isBookWalkerViewer()) {
      const id = canvas.id?.trim() || getCanvasOverlayHost(canvas).id?.trim();
      if (id) return `bookwalker:${id}`;
    }

    return getStoredCanvasSignature(canvas);
  }

  function storeCanvasTranslationKey(canvas, key = getCanvasTranslationKey(canvas)) {
    canvas.dataset.mangaCanvasTranslationKey = key;
    canvas.setAttribute(CANVAS_TRANSLATION_KEY_ATTR, key);
  }

  function getCanvasDimensions(canvas) {
    return {
      width: Math.round(canvas.width || canvas.clientWidth || 0),
      height: Math.round(canvas.height || canvas.clientHeight || 0)
    };
  }

  function storeCanvasDimensions(canvas, { width, height }) {
    canvas.dataset.mangaCanvasWidth = String(width);
    canvas.dataset.mangaCanvasHeight = String(height);
  }

  function canvasDimensionsChanged(canvas) {
    const { width, height } = getCanvasDimensions(canvas);
    return width !== parseInt(canvas.dataset?.mangaCanvasWidth || '0', 10) ||
      height !== parseInt(canvas.dataset?.mangaCanvasHeight || '0', 10);
  }

  function canvasTranslationMemorySet(key, record) {
    if (key && record) lruSet(canvasTranslationMemory, key, record, CANVAS_TRANSLATION_MEMORY_MAX);
  }

  function normalizeCanvasTranslationResult(result) {
    if (result?.translatedImage) {
      return { type: 'image', translatedImage: result.translatedImage, mimeType: result.mimeType || 'image/png' };
    }

    const blocks = Array.isArray(result) ? result : result?.blocks;
    return Array.isArray(blocks) && blocks.length > 0 ? { type: 'blocks', blocks } : null;
  }

  function rememberCanvasTranslation(canvas, result) {
    const record = normalizeCanvasTranslationResult(result);
    if (!record) return;

    const sourceSignature = getStoredCanvasSignature(canvas);
    const { width, height } = getCanvasDimensions(canvas);
    Object.assign(record, { sourceSignature, canvasWidth: width, canvasHeight: height });

    canvasTranslationMemorySet(canvas.dataset?.mangaCanvasTranslationKey || getCanvasTranslationKey(canvas), record);
    canvasTranslationMemorySet(sourceSignature, record);
  }

  function restoreCanvasTranslationFromMemory(canvas) {
    const translationKey = canvas.dataset?.mangaCanvasTranslationKey || getCanvasTranslationKey(canvas);
    const sourceSignature = probeCanvas(canvas).signature;
    const dimensions = getCanvasDimensions(canvas);
    const { width, height } = dimensions;
    const sourceRecord = canvasTranslationMemory.get(sourceSignature);
    const keyRecord = canvasTranslationMemory.get(translationKey);
    // A BookWalker canvas/viewport id is a reusable display slot, not a page
    // identity. Prefer the exact captured source so translating a later page in
    // the same slot does not hide an older page's still-valid memory record.
    const record = sourceRecord?.sourceSignature === sourceSignature
      ? sourceRecord
      : keyRecord || sourceRecord;
    if (!record) return false;

    if (record.sourceSignature && sourceSignature && record.sourceSignature !== sourceSignature &&
        record.canvasWidth === width && record.canvasHeight === height) {
      return false;
    }

    storeCanvasTranslationKey(canvas, translationKey);
    canvas.setAttribute(CANVAS_SOURCE_ATTR, sourceSignature);
    storeCanvasDimensions(canvas, dimensions);

    let restored = true;
    if (record.type === 'image') {
      restored = replaceWithTranslatedImage(canvas, record.translatedImage, record.mimeType);
    } else {
      renderTranslationOverlay(canvas, record.blocks);
    }

    if (!restored) {
      clearCanvasTranslation(canvas);
      return false;
    }

    canvas.setAttribute(TRANSLATION_ATTR, 'true');
    Object.assign(record, { sourceSignature, canvasWidth: width, canvasHeight: height });
    canvasTranslationMemorySet(sourceSignature, record);
    canvasTranslationMemorySet(translationKey, record);
    debugLog('Restored canvas translation from page memory');
    return true;
  }

  function clearCanvasTranslationIfChanged(canvas) {
    if (!canvas.hasAttribute(TRANSLATION_ATTR) &&
        !canvas.hasAttribute(PROCESSING_ATTR) &&
        !canvas.dataset?.mangaCanvasOverlayId) {
      return false;
    }

    const previousSignature = canvas.getAttribute(CANVAS_SOURCE_ATTR);
    if (!previousSignature) return false;

    const current = probeCanvas(canvas).signature;
    if (!current || current === previousSignature) return false;

    if (isBookWalkerViewer()) {
      const previousKey = canvas.dataset?.mangaCanvasTranslationKey || '';
      if (previousKey && previousKey === getCanvasTranslationKey(canvas) && canvasDimensionsChanged(canvas)) {
        const dimensions = getCanvasDimensions(canvas);
        canvas.setAttribute(CANVAS_SOURCE_ATTR, current);
        storeCanvasDimensions(canvas, dimensions);

        const record = canvasTranslationMemory.get(previousKey);
        if (record) {
          canvasTranslationMemorySet(previousKey, {
            ...record,
            sourceSignature: current,
            canvasWidth: dimensions.width,
            canvasHeight: dimensions.height
          });
        }

        return false;
      }
    }

    clearCanvasTranslation(canvas);
    hideImageProgress(canvas);
    return true;
  }

  function canvasOverlaySelector(overlayId) {
    return `.${CANVAS_OVERLAY_CLASS}[data-manga-canvas-overlay-id="${overlayId}"]`;
  }

  function removeCanvasOverlays(overlayId) {
    document.querySelectorAll(canvasOverlaySelector(overlayId)).forEach(overlay => overlay.remove());
  }

  function ensureCanvasOverlayId(canvas) {
    if (!canvas.dataset.mangaCanvasOverlayId) {
      canvas.dataset.mangaCanvasOverlayId = generateImageId();
    }
    return canvas.dataset.mangaCanvasOverlayId;
  }

  function clearCanvasTranslation(canvas) {
    resetTranslationState(canvas);
    clearBookWalkerQueueState(canvas);
    canvas.removeAttribute(CANVAS_SOURCE_ATTR);
    canvas.removeAttribute(CANVAS_TRANSLATION_KEY_ATTR);
    hideImageProgress(canvas);
    delete canvas.dataset.mangaCanvasTranslationKey;
    delete canvas.dataset.mangaCanvasWidth;
    delete canvas.dataset.mangaCanvasHeight;
    delete canvas.dataset.mangaCanvasLogId;
    delete canvas.dataset.mangaTranslatedSrc;
    delete canvas.dataset.mangaLastRestoreTime;
    clearImageRetryBlock(canvas, 'canvas');
    canvasElementRetryBlocks.delete(canvas);

    const overlayId = canvas.dataset?.mangaCanvasOverlayId;
    if (overlayId) {
      removeCanvasOverlays(overlayId);
      delete canvas.dataset.mangaCanvasOverlayId;
    }
  }

  function clearImgTranslation(img) {
    resetTranslationState(img);
    delete img.dataset.mangaOriginalSrc;
    clearImageRetryBlock(img, 'img');
    img.style.visibility = '';

    const overlay = img.nextElementSibling;
    if (overlay?.classList.contains('manga-translated-img-overlay')) {
      overlay.remove();
    }
    unwrapFromTranslateWrapper(img);
  }

  function restoreCanvasOverlay(canvas) {
    const translatedSrc = canvas.dataset.mangaTranslatedSrc;
    const overlayId = canvas.dataset.mangaCanvasOverlayId;
    if (!translatedSrc || !overlayId) return false;

    const lastRestore = parseInt(canvas.dataset.mangaLastRestoreTime || '0', 10);
    const now = Date.now();
    if (now - lastRestore < 3000) {
      return true;
    }
    canvas.dataset.mangaLastRestoreTime = now.toString();

    return mountCanvasOverlay(canvas, translatedSrc, overlayId, {
      restoring: true,
      loadErrorMessage: 'Failed to restore translated canvas image',
      logArgs: ['Canvas overlay restored']
    }) !== null;
  }

  // Returns the mounted overlay, or null when the image was already loaded and rejected.
  function mountCanvasOverlay(canvas, translatedSrc, overlayId, { restoring = false, loadErrorMessage, logArgs }) {
    const host = getCanvasOverlayHost(canvas);
    if (getComputedStyle(host).position === 'static') {
      if (!restoring) saveOriginalStyle(host, 'position');
      host.style.position = 'relative';
    }

    const { host: overlay, image: overlayImg } = createImageOverlay({
      className: `${CANVAS_OVERLAY_CLASS} manga-translated-img-overlay`,
      styleText: CANVAS_OVERLAY_STYLE
    });
    overlay.dataset.mangaCanvasOverlayId = overlayId;

    positionCanvasOverlay(canvas, overlay);

    const reveal = (...logSuffix) => {
      if (isImageMostlyBlack(overlayImg)) {
        rejectCanvasTranslatedOverlay(canvas, overlay, BLACK_IMAGE_MESSAGE);
        return false;
      }
      positionCanvasOverlay(canvas, overlay);
      overlay.style.display = 'block';
      debugLog(...logArgs, ...logSuffix);
      return true;
    };

    overlayImg.onload = () => reveal();
    overlayImg.onerror = () => {
      rejectCanvasTranslatedOverlay(canvas, overlay, loadErrorMessage);
    };

    isRestoringOverlay = restoring;
    host.appendChild(overlay);
    overlayImg.src = translatedSrc;
    isRestoringOverlay = false;

    // If image is already loaded (cached/blob URL), trigger display immediately
    // so the user sees the translated image right after Step 6 completes
    if (overlayImg.complete && overlayImg.naturalWidth > 0 && !reveal('(cached)')) {
      return null;
    }
    return overlay;
  }

  function clearCompositeTranslation(container) {
    resetTranslationState(container);
    clearImageRetryBlock(container, 'composite');
    container.querySelector('.manga-composite-overlay')?.remove();
    unwrapFromTranslateWrapper(container);
  }

  function syncCanvasOverlays() {
    document.querySelectorAll(`.${CANVAS_OVERLAY_CLASS}`).forEach(overlay => {
      const overlayId = overlay.dataset.mangaCanvasOverlayId;
      const canvas = overlayId
        ? document.querySelector(`canvas[data-manga-canvas-overlay-id="${overlayId}"]`)
        : null;

      if (canvas?.isConnected) {
        positionCanvasOverlay(canvas, overlay);
      } else {
        overlay.remove();
      }
    });

    // BookWalker's viewer sometimes removes foreign DOM elements, so translated
    // canvases that lost their overlay get it back from what was stored.
    if (isBookWalkerViewer()) {
      document.querySelectorAll(`canvas[${TRANSLATION_ATTR}]`).forEach(canvas => {
        const overlayId = canvas.dataset?.mangaCanvasOverlayId;
        if (!overlayId) {
          debugLog('Canvas translated but has no overlay id; trying to restore from memory');
          if (!restoreCanvasTranslationFromMemory(canvas)) {
            clearCanvasTranslation(canvas);
          }
        } else if (!document.querySelector(canvasOverlaySelector(overlayId))) {
          debugLog('Canvas overlay missing; restoring from stored translation');
          if (!restoreCanvasOverlay(canvas)) {
            clearCanvasTranslation(canvas);
          }
        }
      });
    }
  }

  async function translatePage(options = {}) {
    const visibleOnly = Boolean(options.visibleOnly);

    if (!siteEnabled) {
      return { success: false, error: 'Manga Translator is disabled on this site' };
    }

    if (isProcessing) {
      debugLog('Already processing');
      return;
    }

    isProcessing = true;
    translationCancelRequested = false;
    hidePersistentError();

    try {
      const ready = await ensureBackendReady();
      if (!ready) {
        return;
      }

      const images = discoverImages();
      const scopedImages = visibleOnly
        ? images.filter(img => isElementInViewport(img.element))
        : images;
      const untranslatedImages = scopedImages.filter(({ element }) =>
        !isAlreadyTranslatedElement(element) && !element.hasAttribute(BOOKWALKER_QUEUE_ATTR)
      );

      debugLog(`Found ${images.length} images, ${untranslatedImages.length} need translation`);

      if (untranslatedImages.length === 0) {
        if (scopedImages.length > 0) {
          showNotification(visibleOnly ? 'Visible images already translated' : 'All images already translated');
        } else {
          showNotification(visibleOnly ? 'No visible translatable images found' : 'No translatable images found on this page');
        }
        hideProgress();
        notifyPopup({ type: 'TRANSLATION_COMPLETE', status: 'complete', total: 0, succeeded: 0, failed: 0 });
        return;
      }

      // Page order, for readers with ids like content-p1, content-p2.
      const getPageNum = element => {
        const match = (element.id || element.parentElement?.id || '').match(/content-p(\d+)/i);
        return match ? parseInt(match[1], 10) : 999;
      };
      untranslatedImages.sort((a, b) => getPageNum(a.element) - getPageNum(b.element));

      // Cache visible composites before scrolling, which can revoke their blob URLs.
      const imageCache = new Map();
      updateProgress(visibleOnly ? 'Caching visible images...' : 'Caching images...', 5);

      for (const { type, element } of untranslatedImages) {
        if (translationCancelRequested) break;
        if (type !== 'composite' || !element.isConnected) continue;

        const rect = element.getBoundingClientRect();
        if (rect.top >= window.innerHeight || rect.bottom <= 0 || rect.width <= 0) continue;

        try {
          const slices = await waitForSliceImages(element, 1, 100);
          if (slices.length === 0) continue;

          await waitForSlicesToLoad(slices);
          const base64 = await captureCompositeImage(element);
          if (base64 && base64.length > MIN_IMAGE_DATA_LENGTH) {
            imageCache.set(element, base64);
            debugLog(`Pre-cached: ${getPageId(element) || 'unknown'}`);
          }
        } catch (e) {
          console.warn('[Manga Translator] Pre-cache failed:', e.message);
        }
      }

      debugLog(`Pre-cached ${imageCache.size} visible images`);

      // Also include pages already cached while scrolling.
      for (const [pageId, base64] of pageImageCache.entries()) {
        const element = document.getElementById(pageId);
        if (element && !imageCache.has(element)) {
          imageCache.set(element, base64);
          debugLog(`Using global pre-cached page: ${pageId}`);
        }
      }

      let succeeded = 0;

      // Returns the failure, if any, and whether the image is worth retrying.
      const attemptImage = async (imageData, isRetry) => {
        try {
          // Readers like cmoa.jp can detach the element while the run is going.
          let element = imageData.element;
          if (!element.isConnected && imageData.selector) {
            element = document.querySelector(imageData.selector) || element;
          }

          if (isRetry && !element.isConnected) return {};
          if (element.hasAttribute(TRANSLATION_ATTR)) {
            if (!isRetry) succeeded++;
            return {};
          }

          const cachedBase64 = isRetry ? null : imageCache.get(imageData.element) ?? imageCache.get(element);
          const result = await processImage(element, imageData.type, imageData.src, cachedBase64);
          if (result?.success) {
            succeeded++;
            return {};
          }
          if (result?.stale && imageData.type === 'canvas' && isBookWalkerViewer()) {
            debugLog('Skipping retry for stale BookWalker canvas');
            return {};
          }
          return { error: result?.error || 'Processing failed', retryable: result?.retryable !== false };
        } catch (error) {
          console.error('[Manga Translator] Error processing image:', error);
          return { error: getErrorMessage(error), retryable: !isNonRetryableImageError(error) };
        }
      };

      const retryImages = [];
      // The first failure reason is shown if every image fails (per-image
      // errors only flash for 3 seconds).
      let firstFailureMessage = '';

      for (const imageData of untranslatedImages) {
        if (translationCancelRequested) break;
        const { error, retryable } = await attemptImage(imageData, false);
        if (error && !firstFailureMessage) firstFailureMessage = String(error);
        if (retryable) retryImages.push(imageData);
      }

      // Retry once after a delay, because the images may have loaded since.
      if (!translationCancelRequested && retryImages.length > 0) {
        debugLog(`Retrying ${retryImages.length} failed images...`);
        updateProgress(`Retrying ${retryImages.length} failed images...`, 95);
        await delay(1000);

        for (const imageData of retryImages) {
          if (translationCancelRequested) break;
          const { error } = await attemptImage(imageData, true);
          if (error) console.error('[Manga Translator] Retry also failed:', error);
        }
      }

      const total = untranslatedImages.length;
      const failed = total - succeeded;
      const outcome = { status: succeeded === 0 ? 'error' : failed > 0 ? 'partial' : 'complete', total, succeeded, failed };

      const hasInpainterNotice = Boolean(pendingInpainterNotice);
      if (translationCancelRequested) {
        updateProgress(withPendingInpainterNotice(`Stopped after ${succeeded}/${total} images`), 100);
        notifyPopup({ type: 'TRANSLATION_STOPPED', ...outcome });
      } else {
        updateProgress(withPendingInpainterNotice(
          `${visibleOnly ? 'Visible translated' : 'Translated'} ${succeeded}/${total} images`
        ), 100);
        const completion = { type: 'TRANSLATION_COMPLETE', ...outcome };
        if (succeeded === 0 && firstFailureMessage) {
          const failureReason = firstFailureMessage.length > 400
            ? `${firstFailureMessage.slice(0, 400)}...`
            : firstFailureMessage;
          completion.error = failureReason;
          showNotification(failureReason, true);
        }
        notifyPopup(completion);
      }
      setTimeout(() => hideProgress(), hasInpainterNotice ? 6000 : 2000);
    } catch (error) {
      console.error('[Manga Translator] Page translation failed:', error);
      reportTranslationError(`Translation failed: ${error.message}`, error.message);
    } finally {
      isProcessing = false;
    }
  }

  async function waitForCompositeSlices(container, skipScroll) {
    // Scrolling triggers lazy loading; background reconciliation skips it to keep the user's position.
    if (!skipScroll) {
      container.scrollIntoView({ behavior: 'auto', block: 'center' });
    }
    await delay(300);
    await waitForFrames(2);

    const slices = await waitForSliceImages(container, 10, 200);
    if (slices.length === 0) {
      throw new Error('No slice images found in composite container after waiting');
    }
    await waitForSlicesToLoad(slices);

    // Extra wait for blob URLs to stabilize
    await delay(100);
  }

  /**
   * @param {string} type - 'img', 'composite', 'canvas' or 'background'
   * @param {string|null} cachedBase64 - Pre-cached data to use instead of re-capturing
   * @param {boolean} options.forceRefresh - Bypass the background translation cache for this request
   * @param {boolean} options.bypassRetryBlock - Ignore the local timeout backoff for manual retries
   */
  async function processImage(element, type = 'img', src = null, cachedBase64 = null, options = {}) {
    const blocked = error => ({ success: false, retryable: false, error });

    if (!siteEnabled) {
      return blocked('Manga Translator is disabled on this site');
    }

    let canvasSourceSignature = null;

    if (type === 'canvas') {
      clearCanvasTranslationIfChanged(element);
    }

    if (options.forceRefresh || options.bypassRetryBlock) {
      clearImageRetryBlock(element, type, src);
      if (type === 'canvas') canvasElementRetryBlocks.delete(element);
    } else if (isImageRetryBlocked(element, type, src)) {
      return blocked(getRetryBlockedError(element, type));
    } else if (type === 'canvas' && isCanvasElementRetryBlocked(element)) {
      return blocked(canvasElementRetryBlocks.get(element).reason);
    }

    if (element.hasAttribute(TRANSLATION_ATTR)) {
      debugLog(`Skipping already translated element`);
      return { success: true, skipped: true };
    }

    if (element.hasAttribute(PROCESSING_ATTR)) {
      debugLog(`Skipping element already in processing`);
      return { success: true, skipped: true, alreadyProcessing: true };
    }

    const isBookWalkerCanvas = type === 'canvas' && isBookWalkerViewer();
    const imageId = generateImageId();
    element.setAttribute(PROCESSING_ATTR, imageId);
    if (type === 'canvas') {
      clearBookWalkerQueueState(element);
      element.dataset.mangaCanvasLogId = imageId;
      if (isBookWalkerCanvas) {
        setBookWalkerQueueState(element, 'processing');
        refreshBookWalkerQueueStates();
      }
    }

    if (settings?.showProgress) {
      if (isBookWalkerCanvas) {
        renderBookWalkerQueueState(element);
      } else {
        showImageProgress(element, 'Extracting text...');
      }
    }

    try {
      let base64 = cachedBase64;

      if (!base64 && type === 'composite') {
        base64 = getCachedPageImage(element);
        if (base64) {
          debugLog(`Using globally cached page: ${getPageId(element) || 'unknown'}`);
        }
      }

      if (!base64) {
        if (type === 'img' && element?.tagName === 'IMG') {
          await waitForImageLoad(element);
        } else if (type === 'canvas') {
          canvasSourceSignature = await waitForCanvasReady(element, 6000, {
            allowOffscreen: isBookWalkerViewer()
          });
          if (!canvasSourceSignature) {
            throw new Error('Canvas did not finish rendering');
          }
          element.setAttribute(CANVAS_SOURCE_ATTR, canvasSourceSignature);
        } else if (type === 'composite') {
          await waitForCompositeSlices(element, options.skipScroll);
        }

        base64 = await getImageBase64(element, type, src);
      } else {
        debugLog(`Using pre-cached data for ${element.parentElement?.id || 'unknown'}`);
      }

      if (!base64) {
        throw new Error(`Failed to extract image data from ${element.outerHTML?.substring(0, 200) || element.tagName}`);
      }

      // Reject corrupted or truncated data before sending it anywhere.
      if (typeof base64 !== 'string' || base64.length < MIN_IMAGE_DATA_LENGTH) {
        throw new Error('Image data is empty or corrupted (too small)');
      }
      assertCaptureBase64Size(base64);

      let cacheSignature = null;
      if (type === 'canvas') {
        element.setAttribute(CANVAS_SOURCE_ATTR, canvasSourceSignature || probeCanvas(element).signature || '');
        storeCanvasTranslationKey(element);
        storeCanvasDimensions(element, getCanvasDimensions(element));
        cacheSignature = await createCanvasCacheSignature(element, base64);
      }

      if (isBookWalkerCanvas) {
        renderBookWalkerQueueState(element);
      } else {
        showImageProgress(element, 'Translating...');
      }

      const response = await sendTranslationImageRequest({
        type: 'TRANSLATE_IMAGE',
        imageId,
        base64,
        settings,
        canvasSignature: cacheSignature,
        forceRefresh: Boolean(options.forceRefresh)
      });

      if (!response?.success) {
        throw new Error(response?.error || 'Translation failed');
      }

      if (response.result) {
        if (!handleTranslationResult(imageId, response.result)) {
          return {
            success: false,
            stale: type === 'canvas',
            error: 'Canvas changed before translation finished'
          };
        }
        notifyInpainterFallbackOnce(response.result);
      }

      return { success: true };
    } catch (error) {
      console.error('[Manga Translator] Image processing error:', error);
      if (type === 'canvas') {
        clearBookWalkerQueueState(element);
        if (isBookWalkerCanvas) {
          setBookWalkerQueueState(element, 'failed');
        }
      }
      element.removeAttribute(PROCESSING_ATTR);
      hideImageProgress(element);
      showImageError(element, error.message);
      if (isNonRetryableImageError(error)) {
        markImageRetryBlocked(element, type, error, src);
      }
      return {
        success: false,
        error: error.message,
        retryable: !isNonRetryableImageError(error)
      };
    }
  }

  /**
   * Tell the user (once per page session) when the backend used a different
   * inpainter than the one requested; such results are not cached.
   */
  function notifyInpainterFallbackOnce(result) {
    if (inpainterFallbackNotified || result?.inpainterFallback !== true) return;
    inpainterFallbackNotified = true;
    const requested = settings?.localBackend?.inpainter || 'configured';
    const notice = `Inpainter ${requested} unavailable; used ${result.inpainterUsed || 'a fallback'}. Result not cached.`;
    if (isProcessing) {
      pendingInpainterNotice = notice;
    } else {
      showNotification(notice);
    }
  }

  /**
   * Append (and clear) a held-back inpainter fallback notice to a final progress label.
   */
  function withPendingInpainterNotice(label) {
    if (!pendingInpainterNotice) return label;
    const notice = pendingInpainterNotice;
    pendingInpainterNotice = '';
    return `${label}. ${notice}`;
  }

  async function getImageBase64(element, type, src) {
    try {
      if (type === 'composite') {
        return await captureCompositeImage(element);
      }

      if (type === 'canvas') {
        return await captureCanvasImageBase64(element);
      }

      if (type === 'background') {
        return await fetchAndConvertToBase64(src);
      }

      try {
        const width = element.naturalWidth || element.width;
        const height = element.naturalHeight || element.height;
        return renderToDataUrl(element, width, height).split(',')[1];
      } catch (corsError) {
        debugLog('Canvas blocked by CORS, using fetch');
        return await fetchAndConvertToBase64(element.src);
      }
    } catch (error) {
      console.error('[Manga Translator] Base64 conversion error:', error);
      if (error?.nonRetryable) throw error;
      // Keep the worker's real reason (HTTP status, content type, 20 MB limit)
      // instead of the generic "Failed to extract image data" message.
      if (String(error?.message || '').startsWith('Failed to fetch image via background')) throw error;
      return null;
    }
  }

  function createCaptureCanvas(width, height) {
    assertCaptureDimensions(width, height);
    return Object.assign(document.createElement('canvas'), { width, height });
  }

  function renderToDataUrl(source, width, height) {
    const canvas = createCaptureCanvas(width, height);
    canvas.getContext('2d').drawImage(source, 0, 0);
    return canvas.toDataURL('image/png');
  }

  async function captureCanvasImageBase64(canvas) {
    await waitForCanvasReady(canvas, 3000).catch(() => null);
    assertCaptureDimensions(canvas.width || canvas.clientWidth, canvas.height || canvas.clientHeight);

    const strategies = [
      () => canvas.toDataURL('image/png'),
      () => renderToDataUrl(canvas, canvas.width, canvas.height),
      () => captureWebGLCanvasToDataUrl(canvas)
    ];

    for (const strategy of strategies) {
      try {
        const dataUrl = await strategy();
        if (isUsableImageDataUrl(dataUrl)) {
          return dataUrl.split(',')[1];
        }
      } catch (error) {
        console.warn('[Manga Translator] Canvas capture strategy failed:', error.message);
      }
    }

    throw new Error('Unable to capture canvas image data');
  }

  function captureWebGLCanvasToDataUrl(canvas) {
    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
    if (!gl) {
      return null;
    }

    const width = gl.drawingBufferWidth || canvas.width;
    const height = gl.drawingBufferHeight || canvas.height;
    const output = createCaptureCanvas(width, height);
    const pixels = new Uint8Array(width * height * 4);
    gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);

    const ctx = output.getContext('2d');
    const imageData = ctx.createImageData(width, height);

    for (let y = 0; y < height; y++) {
      const sourceStart = (height - y - 1) * width * 4;
      const targetStart = y * width * 4;
      imageData.data.set(pixels.subarray(sourceStart, sourceStart + width * 4), targetStart);
    }

    ctx.putImageData(imageData, 0, 0);
    return output.toDataURL('image/png');
  }

  function isUsableImageDataUrl(dataUrl) {
    return typeof dataUrl === 'string' &&
      dataUrl.startsWith('data:image/') &&
      dataUrl.includes(',') &&
      dataUrl.split(',')[1]?.length > 100;
  }

  // Stitches the slices of a container into one base64 image.
  async function captureCompositeImage(container) {
    const rect = container.getBoundingClientRect();
    const scale = window.devicePixelRatio || 1;

    let containerWidth = container.offsetWidth;
    let containerHeight = container.offsetHeight;

    if (containerWidth === 0 || containerHeight === 0) {
      const firstImg = container.querySelector('img');
      if (!firstImg || firstImg.naturalWidth <= 0) {
        console.error('[Manga Translator] Cannot determine container dimensions');
        return null;
      }
      containerWidth = firstImg.naturalWidth;
      containerHeight = firstImg.naturalHeight;
    }

    const canvas = createCaptureCanvas(Math.round(containerWidth * scale), Math.round(containerHeight * scale));
    const ctx = canvas.getContext('2d');
    ctx.scale(scale, scale);

    await waitForFrames(2);

    const images = Array.from(container.querySelectorAll('img'));
    debugLog(`Capturing ${images.length} slices from composite, container size: ${containerWidth}x${containerHeight}`);

    if (images.length === 0) {
      console.error('[Manga Translator] No images found in composite container');
      return null;
    }

    const captureSlice = async img => {
      const imgRect = img.getBoundingClientRect();
      if (imgRect.width === 0 || imgRect.height === 0) return null;

      const dataUrl = await getImageDataUrlWithRetry(img, 3);
      if (!dataUrl) {
        console.warn(`[Manga Translator] Could not get data for image: ${(img.currentSrc || img.src || '').substring(0, 50)}`);
        return null;
      }

      return { dataUrl, x: imgRect.left - rect.left, y: imgRect.top - rect.top, width: imgRect.width, height: imgRect.height };
    };

    // Data URLs cannot taint the canvas. Converting concurrently reduces blob revocation races.
    const slices = await processWithConcurrency(images, 6, img => captureSlice(img).catch(() => null));
    const captured = slices.filter(Boolean);

    if (captured.length > 0 && captured.length < images.length) {
      debugLog(`Only captured ${captured.length}/${images.length} images, retrying missed ones...`);
      await delay(500);

      for (const [index, img] of images.entries()) {
        if (!slices[index] && img.complete && img.naturalWidth > 0) {
          const slice = await captureSlice(img);
          if (slice) captured.push(slice);
        }
      }
    }

    if (captured.length === 0) {
      console.error('[Manga Translator] Failed to convert any images to data URLs');
      return captureCompositeDirectDraw(container, images, scale);
    }

    debugLog(`Successfully converted ${captured.length}/${images.length} images`);

    for (const slice of captured) {
      try {
        const tempImg = new Image();
        await new Promise((resolve, reject) => {
          tempImg.onload = resolve;
          tempImg.onerror = () => reject(new Error('Failed to load converted image'));
          tempImg.src = slice.dataUrl;
        });
        ctx.drawImage(tempImg, slice.x, slice.y, slice.width, slice.height);
      } catch (drawError) {
        console.error('[Manga Translator] Error drawing image to canvas:', drawError);
      }
    }

    try {
      const base64 = canvas.toDataURL('image/png').split(',')[1];
      debugLog('Composite capture successful');
      return base64;
    } catch (taintError) {
      console.error('[Manga Translator] Canvas export failed:', taintError);
      return null;
    }
  }

  async function getImageDataUrlWithRetry(img, maxRetries = 3) {
    const isImageReady = () => img.complete && img.naturalWidth > 0 && img.naturalHeight > 0;

    for (let attempt = 0; attempt < maxRetries; attempt++) {
      if (!isImageReady()) {
        try {
          await waitForImageLoad(img, 2000);
          await waitForFrames(2);
        } catch (e) {
          console.warn(`[Manga Translator] Image not ready on attempt ${attempt + 1}:`, e.message);
        }
      }

      // Drawing directly is fastest but fails on tainted images.
      if (isImageReady()) {
        try {
          const dataUrl = renderToDataUrl(img, img.naturalWidth, img.naturalHeight);
          if (dataUrl && dataUrl.length > 100 && !dataUrl.includes('data:,')) {
            return dataUrl;
          }
        } catch (e) {
          if (e?.nonRetryable) throw e;
          console.warn(`[Manga Translator] Canvas draw attempt ${attempt + 1} failed:`, e.message);
        }
      }

      const imageSrc = img.currentSrc || img.src;
      if (imageSrc?.startsWith('data:')) return imageSrc;

      if (imageSrc) {
        try {
          if (imageSrc.startsWith('blob:')) {
            const dataUrl = await fetchBlobAsBase64(imageSrc);
            if (dataUrl?.length > 100) return dataUrl;
          } else {
            const base64 = await fetchAndConvertToBase64(imageSrc);
            if (base64) return `data:image/png;base64,${base64}`;
          }
        } catch (e) {
          console.warn(`[Manga Translator] Fetch attempt ${attempt + 1} failed:`, e.message);
        }
      }

      if (attempt < maxRetries - 1) {
        await delay(150 * Math.pow(2, attempt));
      }
    }

    return null;
  }

  // Fallback without pre-conversion, which may produce a tainted canvas.
  async function captureCompositeDirectDraw(container, images, scale) {
    debugLog('Attempting direct draw fallback');

    const rect = container.getBoundingClientRect();
    const canvas = createCaptureCanvas(Math.round(container.offsetWidth * scale), Math.round(container.offsetHeight * scale));
    const ctx = canvas.getContext('2d');
    ctx.scale(scale, scale);

    let drawnCount = 0;
    for (const img of images) {
      if (!img.complete || img.naturalWidth === 0) continue;

      try {
        const imgRect = img.getBoundingClientRect();
        ctx.drawImage(img, imgRect.left - rect.left, imgRect.top - rect.top, imgRect.width, imgRect.height);
        drawnCount++;
      } catch (e) {
        // Individual image draw failed
      }
    }

    if (drawnCount === 0) {
      console.error('[Manga Translator] Direct draw failed - no images drawn');
      return null;
    }

    try {
      const base64 = canvas.toDataURL('image/png').split(',')[1];
      debugLog(`Direct draw succeeded with ${drawnCount} images`);
      return base64;
    } catch (e) {
      console.error('[Manga Translator] Direct draw canvas tainted:', e);
      return null;
    }
  }

  // Throws if the blob was revoked or is inaccessible, so the caller can try a fallback.
  async function fetchBlobAsBase64(blobUrl) {
    const response = await fetch(blobUrl);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    const blob = await response.blob();

    if (blob.size === 0) {
      throw new Error('Blob is empty (possibly revoked)');
    }

    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onloadend = () => {
        if (reader.result && reader.result.startsWith('data:')) {
          resolve(reader.result);
        } else {
          reject(new Error('FileReader did not produce valid data URL'));
        }
      };
      reader.onerror = () => reject(reader.error || new Error('FileReader error'));
      reader.readAsDataURL(blob);
    });
  }

  async function fetchAndConvertToBase64(url) {
    const rawUrl = typeof url === 'string' ? url.trim() : '';
    if (!rawUrl) throw new Error('Cannot fetch image: empty URL');
    const dataMatch = /^data:[^,]*;base64,(.*)$/i.exec(rawUrl);
    if (dataMatch) return decodeURIComponent(dataMatch[1]).replace(/\s+/g, '');

    const normalizedUrl = normalizeSourceUrl(rawUrl);
    // blob:/file: sources are only reachable from the page; the service worker rejects them.
    if (shouldFetchInContentScript(normalizedUrl)) {
      return fetchLocalUrlAsBase64(normalizedUrl, {
        fetch: globalThis.fetch.bind(globalThis),
        blobToBase64
      });
    }
    const urlPreview = normalizedUrl.slice(0, 200);
    let response;

    try {
      response = await chrome.runtime.sendMessage({ type: 'FETCH_IMAGE', url: normalizedUrl });
    } catch (e) {
      throw new Error(`Failed to fetch image via background (sendMessage): ${e?.message || String(e)} (${urlPreview})`);
    }

    if (response?.success && response.base64) {
      return response.base64;
    }

    const reason = response ? response.error || 'Unknown error' : 'no response';
    throw new Error(`Failed to fetch image via background: ${reason} (${urlPreview})`);
  }

  function handleTranslationResult(imageId, result) {
    const element = document.querySelector(`[${PROCESSING_ATTR}="${imageId}"]`);
    if (!element) return false;

    if (element.tagName === 'CANVAS' && !canvasMatchesStoredSource(element)) {
      debugLog('Ignoring stale canvas translation result; source changed while processing');
      canvasElementRetryBlocks.set(element, {
        blockedUntil: Date.now() + STALE_CANVAS_BACKOFF_MS,
        reason: 'Canvas source changed during translation'
      });
      clearCanvasTranslation(element);
      hideImageProgress(element);
      scheduleCanvasReconcile(false, 0);
      return false;
    }

    element.removeAttribute(PROCESSING_ATTR);
    hideImageProgress(element);

    if (result?.translatedImage) {
      // Rendered image from the local backend.
      if (!replaceWithTranslatedImage(element, result.translatedImage, result.mimeType, imageId)) return false;
    } else if (Array.isArray(result) || result?.blocks?.length > 0) {
      // Text blocks from cloud mode get an SVG overlay.
      renderTranslationOverlay(element, result.blocks || result);
    } else {
      showImageProgress(element, 'No text found');
      setTimeout(() => hideImageProgress(element), 2000);
    }

    element.setAttribute(TRANSLATION_ATTR, 'true');
    if (element.dataset) {
      element.dataset.mangaTranslationHidden = 'false';
    }
    if (element.tagName === 'CANVAS') {
      storeCanvasTranslationKey(element);
      rememberCanvasTranslation(element, result);
      if (isBookWalkerViewer()) {
        completeBookWalkerQueueState(element, 'translated');
        refreshBookWalkerQueueStates();
      }
    }

    return true;
  }

  function canvasMatchesStoredSource(canvas) {
    if (isBookWalkerViewer()) {
      const previousKey = canvas.dataset?.mangaCanvasTranslationKey;
      if (previousKey) {
        if (getCanvasTranslationKey(canvas) !== previousKey) return false;
        if (canvasDimensionsChanged(canvas)) return true;
      }
    }

    const previousSignature = canvas.getAttribute(CANVAS_SOURCE_ATTR);
    if (!previousSignature) return true;

    const currentSignature = probeCanvas(canvas).signature;
    return Boolean(currentSignature && currentSignature === previousSignature);
  }

  /**
   * Quick check if an image element is mostly black (indicates backend
   * rendering failure or corrupted blob). Draws a small thumbnail sample.
   */
  function isImageMostlyBlack(img, threshold = 0.95) {
    try {
      const canvas = document.createElement('canvas');
      canvas.width = 32;
      canvas.height = 32;
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      ctx.drawImage(img, 0, 0, 32, 32);
      const data = ctx.getImageData(0, 0, 32, 32).data;
      let darkPixels = 0;
      for (let i = 0; i < data.length; i += 4) {
        const brightness = (data[i] + data[i + 1] + data[i + 2]) / 3;
        if (brightness < 10) darkPixels++;
      }
      return darkPixels / (data.length / 4) > threshold;
    } catch (e) {
      return false;
    }
  }

  function replaceWithTranslatedImage(element, base64, mimeType, imageId = '') {
    let translatedSrc = null;
    try {
      translatedSrc = getTranslatedSrc(element, base64, mimeType);
    } catch (error) {
      showImageError(element, `Invalid translated image: ${error.message}`);
      return false;
    }

    if (!translatedSrc) {
      showImageError(element, 'Empty translated image');
      return false;
    }

    // Composite containers like cmoa.jp sliced readers get an overlay without wrapping.
    if (element.tagName === 'DIV') {
      ensurePositioned(element);
      element.querySelector('.manga-composite-overlay')?.remove();

      const { host: overlay, image: overlayImg } = createImageOverlay({
        className: 'manga-composite-overlay',
        styleText: `
          position: absolute;
          top: 0;
          left: 0;
          width: 100%;
          height: 100%;
          z-index: 9999;
          pointer-events: none;
        `
      });
      element.appendChild(overlay);
      overlayImg.src = translatedSrc;

      debugLog('Composite overlay applied to', element.id || element.className);
      return true;
    }

    saveOriginalStyle(element, 'cursor');
    element.style.cursor = 'default';

    if (element.tagName === 'IMG') {
      applyImgTranslatedOverlay(element, translatedSrc);
      return true;
    }

    if (element.tagName === 'CANVAS') {
      return applyCanvasTranslatedOverlay(element, translatedSrc, imageId);
    }

    saveOriginalStyle(element, 'backgroundSize', 'backgroundRepeat', 'backgroundPosition');
    if (!element.dataset.mangaOriginalBg) {
      element.dataset.mangaOriginalBg = getComputedStyle(element).backgroundImage || '';
    }
    element.style.backgroundImage = `url("${translatedSrc}")`;
    element.dataset.mangaTranslatedBg = element.style.backgroundImage;
    element.style.backgroundSize = 'cover';
    element.style.backgroundRepeat = 'no-repeat';
    element.style.backgroundPosition = 'center';
    return true;
  }

  // Uses a sibling overlay because moving an <img> in the DOM can reload its src
  // and fail with ERR_FILE_NOT_FOUND on blob URLs.
  function applyImgTranslatedOverlay(img, translatedSrc) {
    if (!img.dataset.mangaOriginalSrc) {
      img.dataset.mangaOriginalSrc = img.src || '';
    }

    // Keep the layout space of the original image.
    saveOriginalStyle(img, 'width', 'height');
    img.style.width = img.style.width || '100%';
    img.style.height = img.style.height || '100%';

    const existingOverlay = img.nextElementSibling;
    if (existingOverlay?.classList.contains('manga-translated-img-overlay')) {
      existingOverlay.remove();
    }

    ensurePositioned(img.parentElement);

    const { host: overlay, image: overlayImg } = createImageOverlay({
      className: 'manga-translated-img-overlay',
      alt: img.alt || '',
      objectFit: getComputedStyle(img).objectFit || 'fill',
      styleText: `
        position: absolute;
        z-index: 9999;
        pointer-events: none;
        display: none;
      `
    });
    img.after(overlay);

    const reveal = () => {
      if (isImageMostlyBlack(overlayImg)) {
        overlay.style.display = 'none';
        showImageError(img, BLACK_IMAGE_MESSAGE);
        return;
      }
      overlay.style.top = img.offsetTop + 'px';
      overlay.style.left = img.offsetLeft + 'px';
      overlay.style.width = img.offsetWidth + 'px';
      overlay.style.height = img.offsetHeight + 'px';
      overlay.style.display = 'block';
      img.style.visibility = 'hidden';
    };

    overlayImg.onload = reveal;
    overlayImg.onerror = () => {
      img.style.visibility = '';
      overlay.style.display = 'none';
      showImageError(img, 'Failed to load translated image');
    };

    overlayImg.src = translatedSrc;

    // A cached or blob image may already be loaded.
    if (overlayImg.complete && overlayImg.naturalWidth > 0) {
      reveal();
    }
  }

  function rejectCanvasTranslatedOverlay(canvas, overlay, message) {
    if (overlay) {
      overlay.style.display = 'none';
    }
    clearCanvasTranslation(canvas);
    showImageError(canvas, message);
  }

  function applyCanvasTranslatedOverlay(canvas, translatedSrc, imageId = '') {
    const overlayId = ensureCanvasOverlayId(canvas);
    canvas.dataset.mangaTranslatedSrc = translatedSrc;
    removeCanvasOverlays(overlayId);

    const overlay = mountCanvasOverlay(canvas, translatedSrc, overlayId, {
      loadErrorMessage: 'Failed to load translated canvas image',
      logArgs: ['Canvas translated overlay applied', imageId || getCanvasLogId(canvas)]
    });
    if (!overlay) return false;

    requestAnimationFrame(() => {
      positionCanvasOverlay(canvas, overlay);
      setTimeout(() => positionCanvasOverlay(canvas, overlay), 100);
    });
    return true;
  }

  function positionCanvasOverlay(canvas, overlay) {
    if (!canvas || !overlay) return;

    const preferredHost = getCanvasOverlayHost(canvas);
    if (!preferredHost) return;
    if (!overlay.parentElement && !overlay.isConnected) return;

    ensurePositioned(preferredHost);
    if (overlay.parentElement && overlay.parentElement !== preferredHost) {
      preferredHost.appendChild(overlay);
    }

    const host = overlay.parentElement || preferredHost;
    const canvasRect = canvas.getBoundingClientRect();
    const hostRect = host.getBoundingClientRect();

    overlay.style.left = `${canvasRect.left - hostRect.left + host.scrollLeft}px`;
    overlay.style.top = `${canvasRect.top - hostRect.top + host.scrollTop}px`;
    overlay.style.width = `${canvasRect.width}px`;
    overlay.style.height = `${canvasRect.height}px`;
  }

  function handleTranslationError(imageId, error) {
    const element = document.querySelector(`[${PROCESSING_ATTR}="${imageId}"]`);
    if (!element) return;

    element.removeAttribute(PROCESSING_ATTR);
    hideImageProgress(element);
    showImageError(element, error);
  }

  /**
   * Register a bundled font in this document. The bytes come from the worker
   * because the page's CSP can block a page-context load of extension URLs, and
   * shipping the file as web-accessible would let any site detect the extension.
   * Text falls back to the next family and reflows when the font arrives.
   */
  function ensureBundledFontLoaded(fontFamily) {
    const font = fontRegistry?.findBundledFont(fontFamily);
    if (!font || typeof FontFace !== 'function' || !document.fonts) {
      return Promise.resolve(false);
    }
    if (!bundledFontLoads.has(font.id)) {
      bundledFontLoads.set(font.id, loadBundledFont(font).catch(error => {
        bundledFontLoads.delete(font.id);
        console.warn('[Manga Translator] Could not load bundled font:', error?.message || error);
        return false;
      }));
    }
    return bundledFontLoads.get(font.id);
  }

  async function loadBundledFont(font) {
    const response = await chrome.runtime.sendMessage({ type: 'GET_BUNDLED_FONT', fontId: font.id });
    if (!response?.success) {
      throw new Error(response?.error || 'Font request failed');
    }
    const bytes = Uint8Array.from(atob(response.base64), character => character.charCodeAt(0));
    const face = await new FontFace(font.family, bytes.buffer).load();
    document.fonts.add(face);
    return true;
  }

  function svgElement(tag, attributes = {}, ...children) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [name, value] of Object.entries(attributes)) {
      node.setAttribute(name, value);
    }
    node.append(...children);
    return node;
  }

  function createGlowFilter(id, size, color) {
    return svgElement('filter', { id, x: '-50%', y: '-50%', width: '200%', height: '200%' },
      svgElement('feGaussianBlur', { stdDeviation: (size / 10).toFixed(2), result: 'coloredBlur' }),
      svgElement('feFlood', { 'flood-color': color, result: 'glowColor' }),
      svgElement('feComposite', { in: 'glowColor', in2: 'coloredBlur', operator: 'in', result: 'softGlow' }),
      svgElement('feMerge', {},
        svgElement('feMergeNode', { in: 'softGlow' }),
        svgElement('feMergeNode', { in: 'SourceGraphic' })
      )
    );
  }

  function wrapOverlayText(text, maxCharsPerLine) {
    const wrapped = [];

    if (/[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uffef]/.test(text)) {
      const characters = Array.from(text);
      for (let index = 0; index < characters.length; index += maxCharsPerLine) {
        wrapped.push(characters.slice(index, index + maxCharsPerLine).join(''));
      }
      return wrapped;
    }

    let currentLine = '';
    for (const word of text.split(/\s+/).filter(Boolean)) {
      const candidateLine = currentLine ? `${currentLine} ${word}` : word;
      if (candidateLine.length <= maxCharsPerLine) {
        currentLine = candidateLine;
        continue;
      }
      if (currentLine) wrapped.push(currentLine);
      currentLine = word;
      while (currentLine.length > maxCharsPerLine) {
        wrapped.push(currentLine.slice(0, maxCharsPerLine));
        currentLine = currentLine.slice(maxCharsPerLine);
      }
    }
    if (currentLine) wrapped.push(currentLine);
    return wrapped.length ? wrapped : [''];
  }

  // Model output can use percentages or pixels, so boxes are normalized to image pixels.
  function resolveOverlayBox(item, imageWidth, imageHeight) {
    const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
    const looksLikePercent = values => Math.max(...values) <= 100 && (imageWidth > 100 || imageHeight > 100);
    const toPixelsX = value => value / 100 * imageWidth;
    const toPixelsY = value => value / 100 * imageHeight;

    const layoutBox = Array.isArray(item.bubble_bbox) && item.bubble_bbox.length === 4
      ? item.bubble_bbox
      : item.bbox;
    let [x1, y1, x2, y2] = layoutBox.map(Number);
    if (looksLikePercent([x1, y1, x2, y2])) {
      [x1, y1, x2, y2] = [toPixelsX(x1), toPixelsY(y1), toPixelsX(x2), toPixelsY(y2)];
    }

    x1 = clamp(x1, 0, imageWidth);
    x2 = clamp(x2, 0, imageWidth);
    y1 = clamp(y1, 0, imageHeight);
    y2 = clamp(y2, 0, imageHeight);

    let centerX = (x1 + x2) / 2;
    let centerY = (y1 + y2) / 2;
    if (
      Array.isArray(item.bubble_center) &&
      item.bubble_center.length === 2 &&
      item.bubble_center.every(value => Number.isFinite(Number(value)))
    ) {
      [centerX, centerY] = item.bubble_center.map(Number);
      if (looksLikePercent([centerX, centerY])) {
        centerX = toPixelsX(centerX);
        centerY = toPixelsY(centerY);
      }
      centerX = clamp(centerX, x1, x2);
      centerY = clamp(centerY, y1, y2);
    }

    return { x1, y1, x2, y2, centerX, centerY, width: x2 - x1, height: y2 - y1 };
  }

  function renderTranslationOverlay(imageElement, translations) {
    const isCanvasElement = imageElement.tagName === 'CANVAS';
    let overlayParent = imageElement.parentElement;

    if (isCanvasElement) {
      overlayParent = getCanvasOverlayHost(imageElement);
      ensurePositioned(overlayParent);
      removeCanvasOverlays(ensureCanvasOverlayId(imageElement));
    } else {
      if (!overlayParent.classList.contains('manga-translate-wrapper')) {
        const wrapper = makeElement('div', { className: 'manga-translate-wrapper' });
        wrapper.style.cssText = `
          position: relative;
          display: inline-block;
          width: ${imageElement.offsetWidth || imageElement.width}px;
          height: ${imageElement.offsetHeight || imageElement.height}px;
        `;
        overlayParent.insertBefore(wrapper, imageElement);
        wrapper.appendChild(imageElement);
        overlayParent = wrapper;
      }
      overlayParent.querySelector('.manga-translate-overlay')?.remove();
    }

    const render = settings?.render || {};
    const lineSpacing = render.lineSpacing || 1.2;
    const borderSize = render.borderSize || 2;
    const glowEnabled = render.glowEnabled || false;
    const autoScale = render.autoScale !== false;
    const textDirection = render.textDirection || 'auto';
    const textAlignment = render.textAlignment || 'center';

    const imageWidth = imageElement.naturalWidth || imageElement.width || imageElement.offsetWidth || 100;
    const imageHeight = imageElement.naturalHeight || imageElement.height || imageElement.offsetHeight || 100;

    // Unique so overlays on the same page do not collide.
    const glowFilterId = `manga-glow-mg-${Math.random().toString(36).slice(2, 9)}`;

    ensureBundledFontLoaded(settings?.fontFamily);

    const overlayElement = createShadowHost({
      className: isCanvasElement
        ? `manga-translate-overlay ${CANVAS_OVERLAY_CLASS}`
        : 'manga-translate-overlay',
      styleText: `
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        pointer-events: none;
        z-index: ${isCanvasElement ? '9999' : '100'};
      `
    });
    if (isCanvasElement) {
      overlayElement.dataset.mangaCanvasOverlayId = imageElement.dataset.mangaCanvasOverlayId;
    }

    const overlay = svgElement('svg', {
      viewBox: `0 0 ${imageWidth} ${imageHeight}`,
      preserveAspectRatio: 'none'
    });
    overlay.classList.add('manga-overlay-svg');
    mountInShadow(overlayElement, overlay);

    if (glowEnabled) {
      overlay.appendChild(svgElement('defs', {},
        createGlowFilter(glowFilterId, render.glowSize || 3, render.glowColor || '#ffffff')
      ));
    }

    translations.forEach(item => {
      if (!item.translated || !item.bbox) return;

      const displayText = render.uppercase ? item.translated.toUpperCase() : item.translated;
      const { x1, y1, x2, centerX, centerY, width, height } = resolveOverlayBox(item, imageWidth, imageHeight);

      const useVertical = textDirection === 'vertical' || (textDirection === 'auto' && height > width * 1.5);
      const textX = textAlignment === 'left' ? x1 + width * 0.02 :
                    textAlignment === 'right' ? x2 - width * 0.02 : centerX;

      overlay.appendChild(svgElement('rect', {
        x: x1,
        y: y1,
        width,
        height,
        fill: render.bgColor || '#ffffff',
        'fill-opacity': render.bgOpacity ?? 0.9,
        rx: '0.5'
      }));

      const preferredFontSize = Math.max(6, Math.min(96, Number(settings?.fontSize) || 16));
      const minimumFontSize = Math.min(10, preferredFontSize);
      const lineExtent = useVertical ? height : width;
      const stackExtent = useVertical ? width : height;
      const wrapAt = size => wrapOverlayText(displayText, Math.max(1, Math.floor(lineExtent / (size * 0.55))));

      let fontSize = preferredFontSize;
      let lines = wrapAt(fontSize);
      while (autoScale && fontSize > minimumFontSize && lines.length * fontSize * lineSpacing > stackExtent) {
        fontSize -= 1;
        lines = wrapAt(fontSize);
      }

      const text = svgElement('text', {
        x: textX,
        y: centerY,
        'text-anchor': { left: 'start', center: 'middle', right: 'end' }[textAlignment] || 'middle',
        'dominant-baseline': 'central',
        'font-size': fontSize,
        'font-family': settings?.fontFamily || 'sans-serif',
        fill: render.textColor || '#000000',
        ...(useVertical && { 'writing-mode': 'vertical-rl', 'text-orientation': 'upright' }),
        ...(borderSize > 0 && {
          stroke: render.borderColor || '#ffffff',
          'stroke-width': (borderSize / 10 * fontSize).toFixed(2),
          'paint-order': 'stroke fill'
        }),
        ...(glowEnabled && { filter: `url(#${glowFilterId})` })
      });

      lines.forEach((line, lineIndex) => {
        const tspan = svgElement('tspan', {
          x: textX,
          dy: lineIndex === 0 ? -((lines.length - 1) * fontSize * lineSpacing * 0.5) : fontSize * lineSpacing
        });
        tspan.textContent = line;
        text.appendChild(tspan);
      });

      overlay.appendChild(text);
    });

    overlayParent.appendChild(overlayElement);
    if (isCanvasElement) {
      positionCanvasOverlay(imageElement, overlayElement);
      setTimeout(() => positionCanvasOverlay(imageElement, overlayElement), 50);
      setTimeout(() => positionCanvasOverlay(imageElement, overlayElement), 250);
    }
  }

  function showImageProgress(element, text) {
    const parent = element.parentElement;
    let progress = parent?.querySelector('.manga-image-progress');

    if (!progress) {
      progress = createShadowHost({
        className: 'manga-image-progress',
        styleText: `
          position: absolute;
          bottom: 10px;
          left: 10px;
          background: rgba(255, 248, 249, 0.96);
          color: #6f2d27;
          padding: 8px 12px;
          border: 1px solid rgba(111, 45, 39, 0.24);
          border-radius: 8px;
          font-size: 12px;
          z-index: 10000;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
          box-shadow: 0 8px 20px rgba(111, 45, 39, 0.16);
        `
      });
      const content = makeElement('div', { className: 'manga-chip-content' });
      content.append(
        makeElement('div', { className: 'manga-progress-spinner' }),
        makeElement('span', { className: 'manga-progress-text' })
      );
      mountInShadow(progress, content);

      if (parent) {
        ensurePositioned(parent);
        parent.appendChild(progress);
      }
    }

    progress.shadowRoot.querySelector('.manga-progress-text').textContent = text;
  }

  function hideImageProgress(element) {
    element.parentElement?.querySelector('.manga-image-progress')?.remove();
  }

  function showImageError(element, errorText) {
    showImageProgress(element, `Error: ${errorText}`);
    setTimeout(() => hideImageProgress(element), 3000);
  }

  function updateProgress(text, percent) {
    let progressBar = document.getElementById('manga-global-progress');

    if (!progressBar) {
      progressBar = createShadowHost({
        id: 'manga-global-progress',
        styleText: `
          position: fixed;
          bottom: 20px;
          right: 20px;
          background: rgba(255, 248, 249, 0.98);
          color: #6f2d27;
          padding: 12px 16px;
          border: 1px solid rgba(111, 45, 39, 0.24);
          border-radius: 8px;
          z-index: 100000;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
          font-size: 13px;
          min-width: 200px;
          box-shadow: 0 12px 28px rgba(111, 45, 39, 0.16);
        `
      });

      const bar = makeElement('div', { className: 'manga-progress-bar' });
      bar.appendChild(makeElement('div', { className: 'manga-progress-fill' }));
      const content = makeElement('div', { className: 'manga-progress-content' });
      content.append(makeElement('span', { className: 'manga-progress-text' }), bar);
      mountInShadow(progressBar, content);
      document.body.appendChild(progressBar);
    }

    progressBar.shadowRoot.querySelector('.manga-progress-text').textContent = text;
    progressBar.shadowRoot.querySelector('.manga-progress-fill').style.width = `${percent}%`;

    notifyPopup({ type: 'TRANSLATION_PROGRESS', status: text, percent });
  }

  function hideProgress() {
    document.getElementById('manga-global-progress')?.remove();
  }

  // An error shows as a persistent banner; anything else as a brief progress message.
  function showNotification(message, isError = false) {
    if (isError) {
      showPersistentError(message);
    } else {
      updateProgress(message, 100);
      setTimeout(hideProgress, 3000);
    }
  }

  function notifyPopup(message) {
    chrome.runtime.sendMessage(message).catch(() => {
      // Ignore when the popup or background is not available.
    });
  }

  // Stays until dismissed or the backend connects.
  function showPersistentError(message) {
    let errorBanner = document.getElementById('manga-error-banner');

    if (!errorBanner) {
      errorBanner = createShadowHost({
        id: 'manga-error-banner',
        styleText: `
          position: fixed;
          top: 0;
          left: 0;
          right: 0;
          background: linear-gradient(135deg, #c94f45 0%, #a53d35 100%);
          color: #fff;
          padding: 12px 20px;
          z-index: 100001;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
          font-size: 14px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          box-shadow: 0 10px 24px rgba(111, 45, 39, 0.24);
        `
      });
      document.body.appendChild(errorBanner);
    }

    const left = document.createElement('div');
    left.style.cssText = 'display:flex; align-items:center; gap:10px;';
    const icon = makeElement('span', { textContent: '!' });
    icon.style.fontSize = '18px';
    left.append(icon, makeElement('span', { textContent: message }));

    const right = document.createElement('div');
    right.style.cssText = 'display:flex; align-items:center; gap:10px;';

    const retryBtn = makeElement('button', { id: 'manga-retry-btn', textContent: 'Retry Connection' });
    retryBtn.style.cssText = 'background: rgba(255,255,255,0.2); border: 1px solid rgba(255,255,255,0.3); color: #fff; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 12px';

    const dismissBtn = makeElement('button', { id: 'manga-dismiss-btn', textContent: 'x' });
    dismissBtn.style.cssText = 'background: transparent; border: none; color: #fff; cursor: pointer; font-size: 18px; padding: 4px';

    right.append(retryBtn, dismissBtn);
    mountInShadow(errorBanner, left, right);

    retryBtn.addEventListener('click', () => {
      hidePersistentError();
      translatePage();
    });

    dismissBtn.addEventListener('click', hidePersistentError);
  }

  function hidePersistentError() {
    document.getElementById('manga-error-banner')?.remove();
  }

  function generateImageId() {
    return `img_${Date.now().toString(36)}_${Math.random().toString(36).substring(2, 11)}${Math.random().toString(36).substring(2, 6)}`;
  }

  // Initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
      handleTranslationResult,
      isAlreadyTranslatedElement,
      translatePage,
      translateImageData,
      clearBackgroundTranslation,
      clearImgTranslation,
      clearCompositeTranslation,
      clearPageTranslations,
      cleanupBlobUrls,
      requestStopTranslation,
      restoreCanvasTranslationFromMemory,
      fetchAndConvertToBase64,
      captureCanvasImageBase64,
      isTranslatableImage,
      processImage,
      assertCaptureBase64Size,
      isCaptureDimensionsAllowed,
      enqueueBookWalkerCanvas,
      startSiteRuntime,
      stopSiteRuntime,
      refreshSiteActivation,
      getSiteRuntimeState: () => ({
        enabled: siteEnabled,
        started: siteRuntimeStarted,
        hasScope: Boolean(siteRuntimeScope)
      }),
      isTranslationTransportError,
      sendTranslationImageRequest,
      startRegionSelection,
      ensureBundledFontLoaded
    };
  }
})();
