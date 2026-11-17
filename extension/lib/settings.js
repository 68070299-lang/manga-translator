/**
 * Shared settings defaults and storage helpers.
 *
 * This file is loaded directly in extension pages/content scripts and can also
 * be required from Jest tests.
 */

(function(root) {
  'use strict';

const SETTINGS_VERSION = 2;

const DEFAULT_SETTINGS = Object.freeze({
  settingsVersion: SETTINGS_VERSION,
  provider: 'openrouter',
  apiKey: '',
  model: '',
  baseUrl: '',
  targetLang: 'English',
  fontSize: 16,
  fontFamily: "'CC Wild Words', sans-serif",
  autoTranslate: false,
  showProgress: true,
  pipelineMode: 'local',
  localBackend: {
    url: 'http://localhost:8001',
    authToken: '',
    detector: 'default',
    ocr: 'manga-ocr',
    inpainter: 'flux2_klein_lanpaint',
    translator: '',
    requestTimeoutMs: 600000
  },
  googleOcr: {
    apiKey: '',
    languageHint: ''
  },
  render: {
    textAlignment: 'center',
    textDirection: 'auto',
    uppercase: false,
    lineSpacing: 1.2,
    borderSize: 2,
    textColor: '#000000',
    borderColor: '#ffffff',
    bgColor: '#ffffff',
    bgOpacity: 0,
    glowEnabled: false,
    glowSize: 3,
    glowColor: '#ffffff',
    autoScale: true
  },
  localLlm: {
    baseUrl: '',
    model: '',
    apiKey: ''
  },
  debug: false
});

function cloneSettings(settings) {
  return JSON.parse(JSON.stringify(settings));
}

function migrateStoredSettings(stored) {
  if (!stored || typeof stored !== 'object' || Array.isArray(stored)) {
    return { settings: stored, changed: false };
  }

  const settings = cloneSettings(stored);
  const version = Number.isInteger(settings.settingsVersion)
    ? settings.settingsVersion
    : 0;
  if (version >= SETTINGS_VERSION) {
    return { settings, changed: false };
  }

  if (version < 1 && settings.localBackend?.inpainter === 'lama_large') {
    settings.localBackend.inpainter = 'flux2_klein_lanpaint';
  }
  // Version 1 shipped with 14px as the implicit default. Move only that old
  // default to 16px; preserve explicit custom sizes.
  if (version < 2 && (settings.fontSize === undefined || settings.fontSize === 14)) {
    settings.fontSize = 16;
  }
  settings.settingsVersion = SETTINGS_VERSION;
  return { settings, changed: true };
}

function normalizeInpainter(value) {
  const normalized = typeof value === 'string' ? value.trim().toLowerCase() : '';
  if (normalized === 'lama') {
    return 'lama_large';
  }
  if (normalized === 'none') {
    return 'none';
  }
  if (normalized === 'lama_large') {
    return 'lama_large';
  }
  if (
    normalized === 'flux2_klein_lanpaint' ||
    normalized === 'flux2-klein-lanpaint' ||
    normalized === 'flux_lanpaint' ||
    normalized === 'lanpaint'
  ) {
    return 'flux2_klein_lanpaint';
  }
  if (normalized === 'flux2_klein_q4' || normalized === 'flux2-klein-q4' || normalized === 'flux_klein_q4' || normalized === 'q4') {
    return 'flux2_klein_q4';
  }
  return DEFAULT_SETTINGS.localBackend.inpainter;
}

function normalizeDetector(value) {
  const normalized = typeof value === 'string' ? value.trim().toLowerCase() : '';
  if (normalized === 'default' || normalized === 'rtdetr') {
    return normalized;
  }
  if (
    normalized === 'animetext' ||
    normalized === 'animetext-yolo' ||
    normalized === 'animetext_yolo' ||
    normalized === 'anime-text-yolo'
  ) {
    return 'animetext-yolo';
  }
  return DEFAULT_SETTINGS.localBackend.detector;
}

function mergeSettings(loaded = {}) {
  const defaults = cloneSettings(DEFAULT_SETTINGS);
  const merged = {
    ...defaults,
    ...loaded
  };

  merged.localBackend = {
    ...defaults.localBackend,
    ...(loaded.localBackend || {})
  };
  merged.localBackend.detector = normalizeDetector(merged.localBackend.detector);
  merged.localBackend.inpainter = normalizeInpainter(merged.localBackend.inpainter);
  merged.googleOcr = {
    ...defaults.googleOcr,
    ...(loaded.googleOcr || {})
  };
  merged.render = {
    ...defaults.render,
    ...(loaded.render || {})
  };
  merged.localLlm = {
    ...defaults.localLlm,
    ...(loaded.localLlm || {})
  };

  return merged;
}

function applyStoredApiKeys(settings, apiKeys = {}) {
  const merged = mergeSettings(settings);

  if (Object.prototype.hasOwnProperty.call(apiKeys, 'apiKey')) {
    merged.apiKey = apiKeys.apiKey;
  }
  if (Object.prototype.hasOwnProperty.call(apiKeys, 'googleOcrApiKey')) {
    merged.googleOcr.apiKey = apiKeys.googleOcrApiKey;
  }
  if (Object.prototype.hasOwnProperty.call(apiKeys, 'localLlmApiKey')) {
    merged.localLlm.apiKey = apiKeys.localLlmApiKey;
  }
  if (Object.prototype.hasOwnProperty.call(apiKeys, 'backendAuthToken')) {
    merged.localBackend.authToken = apiKeys.backendAuthToken;
  }

  return merged;
}

function splitSettingsForStorage(settings) {
  const merged = mergeSettings(settings);
  const apiKeys = {
    apiKey: merged.apiKey || '',
    googleOcrApiKey: merged.googleOcr.apiKey || '',
    localLlmApiKey: merged.localLlm.apiKey || ''
  };
  if (merged.localBackend.authToken) {
    apiKeys.backendAuthToken = merged.localBackend.authToken;
  }

  const syncSettings = cloneSettings(merged);
  delete syncSettings.apiKey;
  delete syncSettings.googleOcr.apiKey;
  delete syncSettings.localLlm.apiKey;
  delete syncSettings.localBackend.authToken;

  return { syncSettings, apiKeys };
}

function isLoopbackHostname(hostname) {
  return hostname === 'localhost' ||
    hostname === '127.0.0.1' ||
    hostname === '::1' ||
    hostname === '[::1]';
}

function parseLocalBackendUrl(baseUrl) {
  try {
    return new URL(baseUrl || DEFAULT_SETTINGS.localBackend.url);
  } catch (_error) {
    return null;
  }
}

function isValidLocalBackendUrl(baseUrl) {
  const parsed = parseLocalBackendUrl(baseUrl);
  return Boolean(parsed && parsed.protocol === 'http:' && isLoopbackHostname(parsed.hostname));
}

function normalizeLocalBackendBaseUrl(baseUrl) {
  const parsed = parseLocalBackendUrl(baseUrl);
  if (!parsed || parsed.protocol !== 'http:' || !isLoopbackHostname(parsed.hostname)) {
    throw new Error('Local backend URL must be http://localhost or http://127.0.0.1');
  }

  return `${parsed.origin}${parsed.pathname.replace(/\/$/, '')}`;
}

function buildLocalBackendUrl(baseUrl, path) {
  return `${normalizeLocalBackendBaseUrl(baseUrl)}${path}`;
}

function resolveTranslator(settings = {}) {
  const merged = mergeSettings(settings);

  if (merged.pipelineMode === 'cloud') {
    return merged.provider || 'openrouter';
  }

  const explicitTranslator = merged.localBackend?.translator;

  if (explicitTranslator) {
    return explicitTranslator;
  }

  return merged.provider || 'openrouter';
}

function translatorNeedsApiKey(translator) {
  return Boolean(translator && translator !== 'none' && translator !== 'local');
}

// Translated pages make local storage large, so avoid reading values just to list keys.
// storage.getKeys needs Chrome 130; older supported versions fall back to a full read.
async function listLocalStorageKeys() {
  const storage = chrome.storage.local;
  if (typeof storage.getKeys === 'function') {
    return storage.getKeys();
  }
  return Object.keys(await storage.get(null));
}

const exported = {
  SETTINGS_VERSION,
  DEFAULT_SETTINGS,
  migrateStoredSettings,
  mergeSettings,
  applyStoredApiKeys,
  splitSettingsForStorage,
  isValidLocalBackendUrl,
  normalizeLocalBackendBaseUrl,
  buildLocalBackendUrl,
  normalizeDetector,
  normalizeInpainter,
  resolveTranslator,
  translatorNeedsApiKey,
  listLocalStorageKeys
};

if (root) {
  root.MangaTranslatorSettings = exported;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = exported;
}

})(typeof globalThis !== 'undefined' ? globalThis : this);
