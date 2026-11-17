/**
 * Service Worker for Manga Translator Extension
 * Handles API calls, image fetching, and message routing
 */
/* global getFromCache, saveToCache, clearAllCache, getCacheStats, pruneExpiredCache, base64ToBlob, blobToBase64, normalizeTranslationBlocks */

// Import shared modules (using importScripts for service worker)
importScripts(
  '../lib/settings.js',
  '../lib/utils.js',
  '../lib/api.js',
  '../lib/cache.js',
  '../lib/translation-queue.js',
  '../lib/site-activation.js',
  '../lib/referrer-rules.js',
  '../lib/font-registry.js',
  '../lib/region-capture.js'
);

const TranslationQueue = globalThis.MangaTranslatorTranslationQueue?.TranslationQueue ||
  (typeof require === 'function' ? require('../lib/translation-queue').TranslationQueue : null);
const SiteActivation = globalThis.MangaTranslatorSiteActivation ||
  (typeof require === 'function' ? require('../lib/site-activation') : null);
const ReferrerRules = globalThis.MangaTranslatorReferrerRules ||
  (typeof require === 'function' ? require('../lib/referrer-rules') : null);
const FontRegistry = globalThis.MangaTranslatorFontRegistry ||
  (typeof require === 'function' ? require('../lib/font-registry') : null);
const RegionCapture = globalThis.MangaTranslatorRegionCapture ||
  (typeof require === 'function' ? require('../lib/region-capture') : null);

const MAX_DECODED_IMAGE_BYTES = 20 * 1024 * 1024;
const MAX_IMAGE_PIXELS = 25_000_000;
const localTranslationQueue = new TranslationQueue({ concurrency: 1 });
const cloudTranslationQueue = new TranslationQueue({ concurrency: 3 });
const DNR_RULE_ID_MIN = 1000;
const DNR_RULE_ID_MAX = 1999;
const dnrRuleLeases = new Map();
let dnrSessionRulesById = new Map();
let dnrReconciliationPromise = null;
let dnrMutexTail = Promise.resolve();

function getBase64Payload(value) {
  return typeof value === 'string'
    ? value.replace(/^data:[^,]*;base64,/i, '').replace(/\s+/g, '')
    : '';
}

function estimateDecodedBase64Bytes(value) {
  const payload = getBase64Payload(value);
  if (!payload) return 0;
  const padding = payload.endsWith('==') ? 2 : payload.endsWith('=') ? 1 : 0;
  return Math.floor((payload.length * 3) / 4) - padding;
}

async function validateImagePayload(base64) {
  const decodedBytes = estimateDecodedBase64Bytes(base64);
  if (decodedBytes > MAX_DECODED_IMAGE_BYTES) {
    throw new Error('Image exceeds 20 MB decoded image limit');
  }

  // Service workers can decode an image without allocating a canvas. Older
  // browsers do not expose createImageBitmap here, so the byte guard remains
  // the safe fallback in that environment.
  if (typeof globalThis.createImageBitmap === 'function' && typeof globalThis.base64ToBlob === 'function') {
    const bitmap = await globalThis.createImageBitmap(globalThis.base64ToBlob(base64));
    try {
      const pixels = Number(bitmap.width || 0) * Number(bitmap.height || 0);
      if (pixels > MAX_IMAGE_PIXELS) {
        throw new Error('Image exceeds 25 MP pixel limit');
      }
    } finally {
      bitmap.close?.();
    }
  }
}

/**
 * Listen for messages from content scripts and popup
 */
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message.type !== 'string') return false;

  const asyncTypes = ['TRANSLATE_IMAGE', 'FETCH_IMAGE', 'CLEAR_CACHE', 'GET_CACHE_STATS', 'CHECK_BACKEND', 'CAPTURE_REGION', 'GET_BUNDLED_FONT'];
  
  if (!asyncTypes.includes(message.type)) {
    return false;
  }
  
  handleMessage(message, sender)
    .then(response => sendResponse(response))
    .catch(error => sendResponse({ success: false, error: error.message }));
  
  return true;
});

/**
 * Route messages to appropriate handlers
 */
async function handleMessage(message, sender) {
  switch (message.type) {
    case 'TRANSLATE_IMAGE':
      return await handleTranslateImage(message, sender);
    
    case 'FETCH_IMAGE':
      return await handleFetchImage(message.url);
    
    case 'CLEAR_CACHE':
      return await handleClearCache();
    
    case 'GET_CACHE_STATS':
      return await handleGetCacheStats();

    case 'CHECK_BACKEND':
      return await handleCheckBackend(message.settings);

    case 'CAPTURE_REGION':
      return await handleCaptureRegion(message, sender);

    case 'GET_BUNDLED_FONT':
      return await handleGetBundledFont(message.fontId);
    
    default:
      return { success: false, error: 'Unknown message type' };
  }
}

/**
 * Screenshot the sender's visible tab and return the selected part as PNG base64.
 */
async function handleCaptureRegion(message, sender) {
  const tab = sender.tab;
  if (!tab?.id || sender.frameId) {
    return { success: false, error: 'Region capture must start from the top page of a tab' };
  }
  // captureVisibleTab reads whichever tab is active, which must be the requester.
  if (!tab.active) {
    return { success: false, error: 'Region capture needs the tab to be visible' };
  }

  try {
    const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: 'png' });
    const bitmap = await createImageBitmap(base64ToBlob(dataUrl, 'image/png'));
    try {
      const region = RegionCapture.calibrateCaptureRegion(message.rect, message.viewport, bitmap);
      const canvas = new OffscreenCanvas(region.width, region.height);
      canvas.getContext('2d').drawImage(
        bitmap,
        region.x, region.y, region.width, region.height,
        0, 0, region.width, region.height
      );
      const cropped = await canvas.convertToBlob({ type: 'image/png' });
      return {
        success: true,
        base64: await blobToBase64(cropped),
        width: region.width,
        height: region.height
      };
    } finally {
      bitmap.close();
    }
  } catch (error) {
    console.error('[Service Worker] Region capture error:', error);
    return { success: false, error: error?.message || String(error) };
  }
}

async function handleGetBundledFont(fontId) {
  const font = FontRegistry.getBundledFontById(fontId);
  if (!font) {
    return { success: false, error: 'Unknown bundled font' };
  }
  const response = await fetch(chrome.runtime.getURL(font.file));
  if (!response.ok) {
    return { success: false, error: `Could not read bundled font: HTTP ${response.status}` };
  }
  return { success: true, base64: await blobToBase64(await response.blob()) };
}

/**
 * Handle image translation request
 */
async function handleTranslateImage(message, sender) {
  const { imageId, base64, settings, canvasSignature, forceRefresh = false } = message;
  const tabId = sender.tab?.id;
  const normalizedSettings = MangaTranslatorSettings.mergeSettings(settings || {});

  try {
    await validateImagePayload(base64);
    // Generate cache key - use canvas signature for stable canvas caching
    const imageHash = canvasSignature
      ? `canvas_${canvasSignature.replace(/[:/]/g, '_')}`
      : await generateImageHash(base64);
    const cacheKey = globalThis.generateCacheKey(imageHash, normalizedSettings);
    
    if (forceRefresh) {
      await chrome.storage.local.remove(cacheKey);
    } else {
      // Check cache first
      const cached = await getFromCache(cacheKey);
      if (cached) {
        if (isCacheableTranslationResult(cached)) {
          console.log('[Service Worker] Cache hit for image');
          notifyProgress(tabId, 'Using cached translation...', 80);
          return { success: true, result: cached, fromCache: true };
        }
        await chrome.storage.local.remove(cacheKey);
      }
    }

    const queue = normalizedSettings.pipelineMode === 'local'
      ? localTranslationQueue
      : cloudTranslationQueue;
    const queueKey = forceRefresh
      ? `${cacheKey}:force:${Date.now()}:${Math.random().toString(36).slice(2)}`
      : cacheKey;
    const subscriber = {
      onProgress: (status, percent) => notifyProgress(tabId, status, percent)
    };

    return await queue.enqueue(
      queueKey,
      notify => handleTranslationJob(base64, normalizedSettings, cacheKey, notify, { forceRefresh }),
      subscriber
    );

  } catch (error) {
    console.error('[Service Worker] Translation error:', error);

    notifyTranslationError(tabId, imageId, error.message);
    return { success: false, error: error.message };
  }
}

async function handleTranslationJob(base64, settings, cacheKey, notify, options = {}) {
  const forceRefresh = Boolean(options.forceRefresh);
  if (!forceRefresh) {
    const cached = await getFromCache(cacheKey);
    if (cached) {
      if (isCacheableTranslationResult(cached)) {
        notify('Using cached translation...', 80);
        return { success: true, result: cached, fromCache: true };
      }
      await chrome.storage.local.remove(cacheKey);
    }
  }

  const result = settings.pipelineMode === 'local'
    ? await handleLocalBackendTranslation(base64, settings, notify)
    : await handleCloudTranslation(base64, settings, notify);

  if (!isUsableTranslationResult(result)) {
    throw new Error('Backend did not return a usable translated image');
  }

  if (isCacheableTranslationResult(result)) {
    await saveToCache(cacheKey, result);
  }
  notify('Complete!', 100);
  return { success: true, result };
}

function isCacheableTranslationResult(result) {
  return isUsableTranslationResult(result) && !result?.inpainterFallback;
}

function isUsableTranslationResult(result) {
  if (Array.isArray(result)) {
    return true;
  }

  if (result?.mode === 'cloud' && Array.isArray(result.blocks)) {
    return true;
  }

  return Boolean(
    result &&
    result.mode === 'local' &&
    typeof result.translatedImage === 'string' &&
    result.translatedImage.length > 100 &&
    !result.translationFailed
  );
}

/**
 * Handle translation using direct cloud OCR/translation providers.
 */
async function handleCloudTranslation(base64, settings, notify) {
  const providerName = settings.provider || 'gemini';
  if (providerName === 'local') {
    throw new Error('Cloud API mode does not support Local LLM directly. Select Local mode to use a local LLM through the backend.');
  }

  notify('Analyzing image...', 25);

  const provider = new TranslationProvider({
    provider: providerName,
    apiKey: settings.apiKey,
    model: settings.model,
    baseUrl: settings.baseUrl
  });

  const base64Data = typeof base64 === 'string'
    ? base64.replace(/^data:[^,]*;base64,/, '')
    : base64;

  notify('Translating...', 60);
  const blocks = await provider.translateImage(base64Data, settings.targetLang || 'English');
  notify('Rendering overlay...', 90);

  return {
    mode: 'cloud',
    blocks: normalizeTranslationBlocks(blocks)
  };
}

function createLocalBackendProvider(normalizedSettings, extra = {}) {
  return new LocalBackendProvider({
    localBackend: normalizedSettings.localBackend,
    apiKey: normalizedSettings.apiKey,
    googleOcr: normalizedSettings.googleOcr,
    authToken: normalizedSettings.localBackend?.authToken || normalizedSettings.backendAuthToken,
    provider: normalizedSettings.provider,
    model: normalizedSettings.model,
    targetLang: normalizedSettings.targetLang,
    ...extra
  });
}

/**
 * Verify local backend is reachable before translating
 */
async function handleCheckBackend(settings) {
  const normalizedSettings = MangaTranslatorSettings.mergeSettings(settings || {});
  const provider = createLocalBackendProvider(normalizedSettings);

  const ok = await provider.checkHealth();
  return { success: ok, reason: provider.lastHealthReason || (ok ? 'ok' : 'unreachable') };
}

/**
 * Handle translation using Local Backend
 */
async function handleLocalBackendTranslation(base64, settings, notify) {
  const normalizedSettings = MangaTranslatorSettings.mergeSettings(settings || {});
  // Create local backend provider with all settings
  const provider = createLocalBackendProvider(normalizedSettings, {
    fontSize: normalizedSettings.fontSize,
    render: normalizedSettings.render,
    localLlm: normalizedSettings.localLlm
  });
  
  const backendUrl = normalizedSettings.localBackend.url;
  
  try {
    // Step 1: Detecting text
    notify('Detecting text...', 10);
    
    // Convert base64 to blob for local backend
    const imageBlob = base64ToBlob(base64);
    
    // Step 2: Extracting text
    notify('Extracting text...', 30);
    
    // Step 3: Translating
    notify('Translating...', 50);
    
    // Step 4: Cleaning image
    notify('Cleaning image...', 70);
    
    // Call local backend (all steps happen on server)
    const translatedBlob = await provider.translateImage(imageBlob);
    
    // Step 5: Rendering
    notify('Rendering...', 90);
    
    // Convert response blob back to base64
    const translatedBase64 = await blobToBase64(translatedBlob);
    if (!translatedBase64 || translatedBase64.length < 100) {
      throw new Error('Local backend returned an empty translated image');
    }
    
    // Return result in format compatible with overlay rendering
    // Local backend returns a fully rendered image, so we return it as translatedImage
    return {
      translatedImage: translatedBase64,
      mimeType: translatedBlob.type || 'image/png',
      mode: 'local',
      inpainterUsed: translatedBlob.inpainterUsed || normalizedSettings.localBackend.inpainter,
      inpainterFallback: Boolean(translatedBlob.inpainterFallback)
    };
    
  } catch (error) {
    const requestSuffix = getLocalBackendRequestSuffix(error, provider);
    // The backend answered with an HTTP error: its own detail (including a
    // provider "timed out" 504) is the real cause, so do not reinterpret it as
    // a local request timeout or connection failure.
    if (error?.backendStatus) {
      throw new Error(`${error.message}${requestSuffix}`);
    }
    // Handle specific local backend errors
    if (isTimeoutError(error)) {
      const timeoutSeconds = Math.round(provider.requestTimeoutMs / 1000);
      throw new Error(`Local backend request timed out after ${timeoutSeconds}s. The image may be too large or the server is busy.${requestSuffix}`);
    }
    if (isLocalBackendNetworkError(error)) {
      throw new Error(`Cannot connect to local backend at ${backendUrl}. Worker fetch failed: ${error.message}${requestSuffix}`);
    }
    if (error.message.includes('ECONNREFUSED') || error.message.includes('Connection refused')) {
      throw new Error(`Connection refused to ${backendUrl}. Please start the local translation server.${requestSuffix}`);
    }
    // Re-throw other errors with context
    throw new Error(`Local backend error: ${error.message}${requestSuffix}`);
  }
}

function getLocalBackendRequestSuffix(error, provider) {
  const requestId = error?.requestId || provider?.lastRequestId || '';
  if (!requestId || String(error?.message || '').includes(requestId)) return '';
  return ` (request id: ${requestId})`;
}

function isTimeoutError(error) {
  const message = String(error?.message || error || '').toLowerCase();
  const name = String(error?.name || '').toLowerCase();
  return name === 'timeouterror' || name === 'aborterror' || message.includes('timeout') || message.includes('timed out');
}

function isLocalBackendNetworkError(error) {
  const message = String(error?.message || error || '').toLowerCase();
  const name = String(error?.name || '').toLowerCase();
  return name === 'networkerror' ||
    message.includes('failed to fetch') ||
    message.includes('networkerror') ||
    message.includes('network error') ||
    message.includes('network error in worker');
}

/**
 * Fetch image from URL (bypasses CORS)
 */
async function handleFetchImage(url) {
  try {
    if (!url || typeof url !== 'string') {
      return { success: false, error: 'Invalid URL for FETCH_IMAGE' };
    }
    const scheme = url.split(':', 1)[0].toLowerCase();
    if (scheme !== 'http' && scheme !== 'https') {
      return { success: false, error: `Unsupported URL scheme: ${scheme}` };
    }

    return await withDnrReferrerRule(url, async (referrerRule) => {
      const referrerOptions = referrerRule
        ? { referrer: referrerRule.referrer, referrerPolicy: 'no-referrer-when-downgrade' }
        : {};

      // Extension host permissions already bypass CORS; a no-cors retry would
      // only return an opaque, empty body, so surface the real failure instead.
      const response = await fetch(url, {
        method: 'GET',
        mode: 'cors',
        credentials: 'omit',
        cache: 'no-store',
        ...referrerOptions
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status} fetching image`);
      }

      const blob = await response.blob();
      if (!blob || blob.size === 0) {
        throw new Error('Image response was empty');
      }
      if (blob.size > MAX_DECODED_IMAGE_BYTES) {
        throw new Error('Image exceeds 20 MB decoded image limit');
      }
      // Some CDNs serve images as octet-stream, so only clearly non-image types are refused.
      const blobType = String(blob.type || '').toLowerCase();
      if (blobType && !blobType.startsWith('image/') && !blobType.endsWith('/octet-stream')) {
        throw new Error(`Unexpected content type fetching image: ${blobType}`);
      }

      const base64 = await blobToBase64(blob);
      return { success: true, base64 };
    });

  } catch (error) {
    console.error('[Service Worker] Fetch error:', url, error);
    return { success: false, error: error?.message || String(error) };
  }
}

function getDnrApi() {
  return chrome.declarativeNetRequest;
}

function isDnrRuleIdInOwnedRange(ruleId) {
  return Number.isInteger(ruleId) && ruleId >= DNR_RULE_ID_MIN && ruleId <= DNR_RULE_ID_MAX;
}

function getDnrRuleId(referrerRule) {
  return Number.isInteger(referrerRule?.sessionRuleId)
    ? referrerRule.sessionRuleId
    : DNR_RULE_ID_MIN;
}

function hasExpectedReferrerAction(sessionRule, referrerRule) {
  const headers = sessionRule?.action?.requestHeaders;
  const header = Array.isArray(headers) && headers.length === 1 ? headers[0] : null;
  return sessionRule?.action?.type === 'modifyHeaders' &&
    header?.header === 'Referer' &&
    header?.operation === 'set' &&
    header?.value === referrerRule.referrer;
}

function hasCurrentDnrCondition(sessionRule, referrerRule) {
  const condition = sessionRule?.condition || {};
  return Array.isArray(condition.requestDomains) &&
    condition.requestDomains.length === 1 &&
    condition.requestDomains[0] === referrerRule.hostname &&
    condition.urlFilter === `||${referrerRule.hostname}/` &&
    Array.isArray(condition.resourceTypes) &&
    condition.resourceTypes.length === 1 &&
    condition.resourceTypes[0] === 'xmlhttprequest';
}

function hasLegacyDnrCondition(sessionRule, referrerRule) {
  const condition = sessionRule?.condition || {};
  return !condition.requestDomains &&
    condition.urlFilter === referrerRule.urlFilter &&
    Array.isArray(condition.resourceTypes) &&
    condition.resourceTypes.length === 1 &&
    condition.resourceTypes[0] === 'xmlhttprequest';
}

function isOwnedDnrRule(sessionRule, referrerRule) {
  return isDnrRuleIdInOwnedRange(sessionRule?.id) &&
    hasExpectedReferrerAction(sessionRule, referrerRule) &&
    (hasCurrentDnrCondition(sessionRule, referrerRule) || hasLegacyDnrCondition(sessionRule, referrerRule));
}

function findOwnedDnrRule(sessionRule) {
  return ReferrerRules?.CDN_REFERRER_RULES?.find(rule => isOwnedDnrRule(sessionRule, rule)) || null;
}

/**
 * Serialize every session-rule read-modify-write so an install can never
 * interleave with a pending remove (or reconcile) of the same rule id.
 */
function withDnrMutex(task) {
  const run = dnrMutexTail.then(task);
  dnrMutexTail = run.then(() => undefined, () => undefined);
  return run;
}

function reconcileDnrSessionRules() {
  return withDnrMutex(reconcileDnrSessionRulesLocked);
}

async function reconcileDnrSessionRulesLocked() {
  const api = getDnrApi();
  if (typeof api?.getSessionRules !== 'function') return;

  try {
    const rules = await api.getSessionRules();
    dnrSessionRulesById = new Map((Array.isArray(rules) ? rules : []).map(rule => [rule.id, rule]));
    const ownedStaleRules = (Array.isArray(rules) ? rules : []).filter(rule => findOwnedDnrRule(rule));
    if (ownedStaleRules.length === 0 || typeof api.updateSessionRules !== 'function') return;

    await api.updateSessionRules({
      removeRuleIds: ownedStaleRules.map(rule => rule.id)
    });
    ownedStaleRules.forEach(rule => dnrSessionRulesById.delete(rule.id));
  } catch (error) {
    // A failed reconciliation must not prevent ordinary image fetching.
    console.warn('[Service Worker] Failed to reconcile transient referrer rules:', error?.message || error);
  }
}

function ensureDnrSessionRulesReady() {
  const api = getDnrApi();
  if (typeof api?.getSessionRules !== 'function') return Promise.resolve();
  if (!dnrReconciliationPromise) {
    dnrReconciliationPromise = reconcileDnrSessionRules();
  }
  return dnrReconciliationPromise;
}

function createDnrLease(referrerRule) {
  return withDnrMutex(() => createDnrLeaseLocked(referrerRule));
}

async function createDnrLeaseLocked(referrerRule) {
  const api = getDnrApi();
  const updateSessionRules = api?.updateSessionRules;
  if (typeof updateSessionRules !== 'function') return null;

  const ruleId = getDnrRuleId(referrerRule);
  const existingRule = dnrSessionRulesById.get(ruleId);
  if (existingRule && !isOwnedDnrRule(existingRule, referrerRule)) {
    console.warn('[Service Worker] Referrer rule id is occupied by an unrelated rule:', ruleId);
    return null;
  }

  const dnrRule = ReferrerRules.buildDnrRule(referrerRule, ruleId);
  const needsInstall = !existingRule || !hasCurrentDnrCondition(existingRule, referrerRule);

  try {
    if (needsInstall) {
      await updateSessionRules.call(api, {
        ...(existingRule ? { removeRuleIds: [ruleId] } : {}),
        addRules: [dnrRule]
      });
    }
    dnrSessionRulesById.set(ruleId, dnrRule);
    return {
      referrerRule,
      ruleId,
      refCount: 1,
      removeOnRelease: true
    };
  } catch (error) {
    console.warn('[Service Worker] Failed to install transient referrer rule; using ordinary fetch:', error?.message || error);
    return null;
  }
}

async function acquireDnrLease(referrerRule) {
  const key = referrerRule.id;
  const current = dnrRuleLeases.get(key);
  if (current) {
    current.refCount += 1;
    await current.ready;
    return current.enabled ? current : null;
  }

  const lease = {
    referrerRule,
    refCount: 1,
    enabled: false,
    ready: null,
    ruleId: getDnrRuleId(referrerRule),
    removeOnRelease: false
  };
  lease.ready = createDnrLease(referrerRule)
    .then(result => {
      if (!result) return;
      lease.enabled = true;
      lease.ruleId = result.ruleId;
      lease.removeOnRelease = result.removeOnRelease;
    })
    .catch(error => {
      console.warn('[Service Worker] Failed to acquire transient referrer rule:', error?.message || error);
    });
  dnrRuleLeases.set(key, lease);
  await lease.ready;
  if (!lease.enabled && dnrRuleLeases.get(key) === lease) {
    dnrRuleLeases.delete(key);
  }
  return lease.enabled ? lease : null;
}

async function releaseDnrLease(lease) {
  lease.refCount -= 1;
  if (lease.refCount > 0 || dnrRuleLeases.get(lease.referrerRule.id) !== lease) return;
  dnrRuleLeases.delete(lease.referrerRule.id);

  if (!lease.removeOnRelease) return;
  const api = getDnrApi();
  if (typeof api?.updateSessionRules !== 'function') return;

  await withDnrMutex(async () => {
    // Forget the rule before the remove is issued so an acquire queued behind
    // this task reinstalls it instead of trusting a rule that is going away.
    const removedRule = dnrSessionRulesById.get(lease.ruleId);
    dnrSessionRulesById.delete(lease.ruleId);
    try {
      await api.updateSessionRules.call(api, { removeRuleIds: [lease.ruleId] });
    } catch (error) {
      if (removedRule && !dnrSessionRulesById.has(lease.ruleId)) {
        dnrSessionRulesById.set(lease.ruleId, removedRule);
      }
      console.warn('[Service Worker] Failed to remove transient referrer rule:', error?.message || error);
    }
  });
}

async function withDnrReferrerRule(url, operation) {
  const referrerRule = ReferrerRules?.getReferrerRule(url);
  const updateSessionRules = getDnrApi()?.updateSessionRules;

  if (!referrerRule || typeof updateSessionRules !== 'function') {
    return operation(null);
  }

  await ensureDnrSessionRulesReady();
  const lease = await acquireDnrLease(referrerRule);
  if (!lease) return operation(null);

  try {
    return await operation(referrerRule);
  } finally {
    await releaseDnrLease(lease);
  }
}

/**
 * Clear all cache
 */
async function handleClearCache() {
  const count = await clearAllCache();
  return { success: true, cleared: count };
}

/**
 * Get cache statistics
 */
async function handleGetCacheStats() {
  const stats = await getCacheStats();
  return { success: true, stats: stats };
}

/**
 * Send progress notification to tab
 */
function notifyProgress(tabId, status, percent) {
  if (!tabId) return;
  
  chrome.tabs.sendMessage(tabId, {
    type: 'TRANSLATION_PROGRESS',
    status: status,
    percent: percent
  }).catch(err => {
    // Tab may be closed or navigated away — only log unexpected errors
    if (err?.message && !IGNORABLE_CHROME_CALL_ERRORS.test(err.message)) {
      console.warn('[Service Worker] notifyProgress failed:', err.message);
    }
  });
}

function notifyTranslationError(tabId, imageId, error) {
  if (!tabId) return;

  try {
    const response = chrome.tabs.sendMessage(tabId, {
      type: 'TRANSLATION_ERROR',
      imageId,
      error
    });
    response?.catch?.(sendError => {
      if (!IGNORABLE_CHROME_CALL_ERRORS.test(sendError?.message || '')) {
        console.warn('[Service Worker] notifyTranslationError failed:', sendError?.message || sendError);
      }
    });
  } catch (sendError) {
    console.warn('[Service Worker] notifyTranslationError failed:', sendError?.message || sendError);
  }
}

/**
 * Generate hash from base64 image
 */
async function generateImageHash(base64) {
  const normalizedBase64 = typeof base64 === 'string'
    ? base64.replace(/^data:[^,]*;base64,/, '')
    : '';

  if (!normalizedBase64) {
    throw new Error('Cannot hash empty image data');
  }

  const encoder = new TextEncoder();
  const data = encoder.encode(normalizedBase64);
  const hashBuffer = await globalThis.crypto.subtle.digest('SHA-256', data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
}

const ACTION_ICON_PATHS = Object.freeze({
  16: 'icons/icon16.png',
  48: 'icons/icon48.png',
  128: 'icons/icon128.png'
});

const IGNORABLE_CHROME_CALL_ERRORS = /Receiving end does not exist|No tab with id|closed|Could not establish connection/i;

/**
 * MV3 chrome.* calls return promises that reject for closed tabs or pages
 * without a content script. Swallow those expected failures, warn on the rest.
 */
function safeChromeCall(result) {
  if (result && typeof result.then === 'function') {
    result.then(undefined, error => {
      const message = error?.message || String(error);
      if (!IGNORABLE_CHROME_CALL_ERRORS.test(message)) {
        console.warn('[Service Worker] Chrome API call failed:', message);
      }
    });
  }
  return result;
}

function setActionState(tabId, enabled) {
  if (!tabId || !chrome.action) return;

  safeChromeCall(chrome.action.setIcon?.({ tabId, path: ACTION_ICON_PATHS }));
  safeChromeCall(chrome.action.setTitle?.({
    tabId,
    title: enabled ? 'Manga Translator enabled on this site' : 'Manga Translator disabled on this site'
  }));
  safeChromeCall(chrome.action.setBadgeText?.({ tabId, text: enabled ? '' : 'OFF' }));
  safeChromeCall(chrome.action.setBadgeBackgroundColor?.({ tabId, color: '#7d3029' }));
}

async function updateActionState(tabId, url) {
  if (!tabId) return false;

  let enabled = true;
  if (SiteActivation && typeof SiteActivation.getSiteState === 'function') {
    const siteState = await SiteActivation.getSiteState(url || '');
    enabled = siteState.enabled;
  }

  setActionState(tabId, enabled);
  return enabled;
}

async function updateAllActionStates() {
  if (!chrome.tabs?.query) return;

  const tabs = await chrome.tabs.query({});
  await Promise.all((tabs || []).filter(tab => tab?.id).map(tab => updateActionState(tab.id, tab.url)));
}

function refreshActionStatesSafely() {
  return updateAllActionStates().catch(error => {
    console.warn('[Service Worker] Failed to refresh action states:', error?.message || error);
  });
}

function registerActionStateListeners() {
  if (chrome.tabs?.onUpdated?.addListener) {
    chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
      if (changeInfo?.url || tab?.url) {
        updateActionState(tabId, changeInfo?.url || tab.url).catch(error => {
          console.warn('[Service Worker] Failed to update action state:', error?.message || error);
        });
      }
    });
  }

  if (chrome.tabs?.onActivated?.addListener) {
    chrome.tabs.onActivated.addListener(activeInfo => {
      chrome.tabs.get?.(activeInfo.tabId)
        ?.then(tab => updateActionState(activeInfo.tabId, tab?.url))
        ?.catch(error => console.warn('[Service Worker] Failed to read active tab:', error?.message || error));
    });
  }

  if (chrome.storage?.onChanged?.addListener) {
    chrome.storage.onChanged.addListener((changes, areaName) => {
      if (areaName !== 'local' || (!changes.activeUrls && !changes.disabledUrls)) return;
      updateAllActionStates().catch(error => {
        console.warn('[Service Worker] Failed to refresh action states:', error?.message || error);
      });
    });
  }
}

/**
 * Context menu for right-click translation
 */
const CONTEXT_MENU_ITEMS = [
  {
    id: 'translate-image',
    title: 'Translate this manga image',
    contexts: ['image']
  },
  {
    id: 'translate-visible-page',
    title: 'Translate visible manga on page',
    contexts: ['page']
  },
  {
    id: 'translate-page',
    title: 'Translate all manga on page',
    contexts: ['page']
  },
  {
    id: 'translate-region',
    title: 'Translate a selected region',
    contexts: ['page', 'image']
  },
  {
    id: 'stop-translation',
    title: 'Stop manga translation',
    contexts: ['page']
  },
  {
    id: 'clear-page-translations',
    title: 'Clear manga translations on page',
    contexts: ['page']
  }
];

function registerContextMenus() {
  CONTEXT_MENU_ITEMS.forEach(item => chrome.contextMenus.create(item));
  console.log('[Service Worker] Extension installed, context menus created');
}

function getContextMenuMessage(info) {
  switch (info.menuItemId) {
    case 'translate-image':
      return {
        type: 'TRANSLATE_IMAGE_BY_SRC',
        src: info.srcUrl
      };
    case 'translate-visible-page':
      return { type: 'TRANSLATE_VISIBLE_IMAGES' };
    case 'translate-page':
      return { type: 'TRANSLATE_PAGE' };
    case 'translate-region':
      return { type: 'START_REGION_SELECTION' };
    case 'stop-translation':
      return { type: 'STOP_TRANSLATION' };
    case 'clear-page-translations':
      return { type: 'CLEAR_PAGE_TRANSLATIONS' };
    default:
      return null;
  }
}

function handleContextMenuClick(info, tab) {
  const message = getContextMenuMessage(info);
  if (!message || !tab?.id) return;
  safeChromeCall(chrome.tabs.sendMessage(tab.id, message));
}

function hasNonEmptyString(value) {
  return typeof value === 'string' && value.trim() !== '';
}

function hasLegacySyncedSecrets(settings) {
  return Boolean(settings && typeof settings === 'object' && !Array.isArray(settings)) && (
    hasNonEmptyString(settings.apiKey) ||
    hasNonEmptyString(settings.googleOcr?.apiKey) ||
    hasNonEmptyString(settings.localLlm?.apiKey) ||
    hasNonEmptyString(settings.localBackend?.authToken)
  );
}

/**
 * Move API keys that an older version synced into chrome.storage.sync over to
 * private local storage. Existing non-empty local values always win.
 */
async function moveLegacySyncedSecretsToLocal(settings) {
  const { syncSettings, apiKeys: legacyKeys } = MangaTranslatorSettings.splitSettingsForStorage(settings);
  const stored = await chrome.storage.local.get('apiKeys');
  const localKeys = stored?.apiKeys && typeof stored.apiKeys === 'object' ? stored.apiKeys : {};
  const mergedKeys = { ...localKeys };

  for (const [name, value] of Object.entries(legacyKeys)) {
    if (hasNonEmptyString(value) && !hasNonEmptyString(mergedKeys[name])) {
      mergedKeys[name] = value;
    }
  }

  await chrome.storage.local.set({ apiKeys: mergedKeys });
  return syncSettings;
}

async function migrateStoredSettingsSafely() {
  const storage = chrome.storage?.sync;
  if (!storage?.get || !storage?.set) return false;

  try {
    const stored = await storage.get('settings');
    const migration = MangaTranslatorSettings.migrateStoredSettings(stored?.settings);
    let settings = migration.settings;
    let changed = migration.changed;

    // Independent of the version migration: keys must not keep syncing.
    if (hasLegacySyncedSecrets(settings) && chrome.storage?.local?.get && chrome.storage?.local?.set) {
      settings = await moveLegacySyncedSecretsToLocal(settings);
      changed = true;
    }

    if (!changed) return false;
    await storage.set({ settings });
    return true;
  } catch (error) {
    console.warn('[Service Worker] Failed to migrate settings:', error?.message || error);
    return false;
  }
}

/**
 * Drop expired cache entries. Fire-and-forget; never blocks translation.
 */
async function pruneCacheSafely() {
  if (typeof pruneExpiredCache !== 'function') return 0;
  try {
    return await pruneExpiredCache();
  } catch (error) {
    console.warn('[Service Worker] Failed to prune cache:', error?.message || error);
    return 0;
  }
}

async function handleExtensionInstalled() {
  registerContextMenus();
  dnrReconciliationPromise = null;
  dnrSessionRulesById = new Map();
  ensureDnrSessionRulesReady();
  await handleExtensionStartup();
}

async function handleExtensionStartup() {
  pruneCacheSafely();
  await Promise.all([
    migrateStoredSettingsSafely(),
    refreshActionStatesSafely()
  ]);
}

chrome.runtime.onInstalled.addListener(handleExtensionInstalled);
chrome.runtime.onStartup?.addListener(handleExtensionStartup);
registerActionStateListeners();
ensureDnrSessionRulesReady();
migrateStoredSettingsSafely();
refreshActionStatesSafely();
pruneCacheSafely();

/**
 * Handle context menu clicks
 */
chrome.contextMenus.onClicked.addListener(handleContextMenuClick);

self.addEventListener('activate', event => {
  event.waitUntil(self.clients.claim());
});

console.log('[Service Worker] Manga Translator service worker loaded');

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    CONTEXT_MENU_ITEMS,
    getContextMenuMessage,
    handleTranslateImage,
    handleContextMenuClick,
    registerContextMenus,
    updateActionState,
    updateAllActionStates,
    refreshActionStatesSafely,
    migrateStoredSettingsSafely,
    pruneCacheSafely,
    safeChromeCall,
    handleExtensionStartup,
    handleExtensionInstalled,
    setActionState,
    handleFetchImage,
    handleCheckBackend,
    handleCaptureRegion,
    handleGetBundledFont,
    withDnrReferrerRule,
    reconcileDnrSessionRules,
    isOwnedDnrRule,
    getTranslationQueues: () => ({ local: localTranslationQueue, cloud: cloudTranslationQueue }),
    estimateDecodedBase64Bytes,
    validateImagePayload,
  };
}
