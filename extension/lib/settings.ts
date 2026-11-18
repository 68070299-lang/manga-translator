/**
 * Shared settings defaults, normalization, migration and storage helpers.
 *
 * Public settings live in `chrome.storage.sync` under `settings`; key material
 * is split out into `chrome.storage.local` under `apiKeys` (see
 * `splitSettingsForStorage`).
 */
import { browser } from 'wxt/browser';
import type {
  ApiKeys,
  Detector,
  Inpainter,
  PublicSettings,
  Settings,
  SettingsInput,
  Translator,
} from '../shared/types';

export const SETTINGS_VERSION = 2;

export const SETTINGS_STORAGE_KEY = 'settings';
export const API_KEYS_STORAGE_KEY = 'apiKeys';

export const DEFAULT_SETTINGS: Readonly<Settings> = Object.freeze<Settings>({
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
    requestTimeoutMs: 600000,
  },
  googleOcr: {
    apiKey: '',
    languageHint: '',
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
    autoScale: true,
  },
  localLlm: {
    baseUrl: '',
    model: '',
    apiKey: '',
  },
  debug: false,
});

export interface MigrationResult<T> {
  settings: T;
  changed: boolean;
}

/**
 * Upgrade stored synced settings to `SETTINGS_VERSION`. The input is never
 * mutated; non-object input is returned untouched with `changed: false`.
 */
export function migrateStoredSettings(stored: SettingsInput): MigrationResult<SettingsInput>;
export function migrateStoredSettings(stored: unknown): MigrationResult<unknown>;
export function migrateStoredSettings(stored: unknown): MigrationResult<unknown> {
  if (!stored || typeof stored !== 'object' || Array.isArray(stored)) {
    return { settings: stored, changed: false };
  }

  const settings = structuredClone(stored) as SettingsInput;
  const version = Number.isInteger(settings.settingsVersion) ? (settings.settingsVersion ?? 0) : 0;
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

/** Canonicalize an inpainter name; unknown values fall back to the default. */
export function normalizeInpainter(value: unknown): Inpainter {
  const normalized = typeof value === 'string' ? value.trim().toLowerCase() : '';
  switch (normalized) {
    case 'lama':
    case 'lama_large':
      return 'lama_large';
    case 'none':
      return 'none';
    case 'flux2_klein_lanpaint':
    case 'flux2-klein-lanpaint':
    case 'flux_lanpaint':
    case 'lanpaint':
      return 'flux2_klein_lanpaint';
    case 'flux2_klein_q4':
    case 'flux2-klein-q4':
    case 'flux_klein_q4':
    case 'q4':
      return 'flux2_klein_q4';
    default:
      return DEFAULT_SETTINGS.localBackend.inpainter;
  }
}

/** Canonicalize a detector name; unknown values fall back to the default. */
export function normalizeDetector(value: unknown): Detector {
  const normalized = typeof value === 'string' ? value.trim().toLowerCase() : '';
  switch (normalized) {
    case 'default':
    case 'rtdetr':
      return normalized;
    case 'animetext':
    case 'animetext-yolo':
    case 'animetext_yolo':
    case 'anime-text-yolo':
      return 'animetext-yolo';
    default:
      return DEFAULT_SETTINGS.localBackend.detector;
  }
}

/** Fill every missing setting from the defaults and normalize detector/inpainter. */
export function mergeSettings(loaded: SettingsInput = {}): Settings {
  const defaults = structuredClone(DEFAULT_SETTINGS) as Settings;
  const merged: Settings = { ...defaults, ...loaded } as Settings;

  merged.localBackend = { ...defaults.localBackend, ...(loaded.localBackend || {}) };
  merged.localBackend.detector = normalizeDetector(merged.localBackend.detector);
  merged.localBackend.inpainter = normalizeInpainter(merged.localBackend.inpainter);
  merged.googleOcr = { ...defaults.googleOcr, ...(loaded.googleOcr || {}) };
  merged.render = { ...defaults.render, ...(loaded.render || {}) };
  merged.localLlm = { ...defaults.localLlm, ...(loaded.localLlm || {}) };

  return merged;
}

/**
 * Overlay the private `apiKeys` record on settings. A key that is present in
 * `apiKeys` is authoritative, even when empty.
 */
export function applyStoredApiKeys(settings: SettingsInput, apiKeys: ApiKeys = {}): Settings {
  const merged = mergeSettings(settings);

  if (Object.hasOwn(apiKeys, 'apiKey')) {
    merged.apiKey = apiKeys.apiKey as string;
  }
  if (Object.hasOwn(apiKeys, 'googleOcrApiKey')) {
    merged.googleOcr.apiKey = apiKeys.googleOcrApiKey as string;
  }
  if (Object.hasOwn(apiKeys, 'localLlmApiKey')) {
    merged.localLlm.apiKey = apiKeys.localLlmApiKey as string;
  }
  if (Object.hasOwn(apiKeys, 'backendAuthToken')) {
    merged.localBackend.authToken = apiKeys.backendAuthToken as string;
  }

  return merged;
}

/** Split settings into the synced public part and the private `apiKeys` record. */
export function splitSettingsForStorage(settings: SettingsInput): {
  syncSettings: PublicSettings;
  apiKeys: Required<Omit<ApiKeys, 'backendAuthToken'>> & Pick<ApiKeys, 'backendAuthToken'>;
} {
  const merged = mergeSettings(settings);
  const apiKeys: ReturnType<typeof splitSettingsForStorage>['apiKeys'] = {
    apiKey: merged.apiKey || '',
    googleOcrApiKey: merged.googleOcr.apiKey || '',
    localLlmApiKey: merged.localLlm.apiKey || '',
  };
  if (merged.localBackend.authToken) {
    apiKeys.backendAuthToken = merged.localBackend.authToken;
  }

  const { apiKey: _apiKey, googleOcr, localBackend, localLlm, ...rest } = merged;
  const { apiKey: _googleKey, ...publicGoogleOcr } = googleOcr;
  const { authToken: _authToken, ...publicLocalBackend } = localBackend;
  const { apiKey: _llmKey, ...publicLocalLlm } = localLlm;

  return {
    syncSettings: {
      ...rest,
      googleOcr: publicGoogleOcr,
      localBackend: publicLocalBackend,
      localLlm: publicLocalLlm,
    },
    apiKeys,
  };
}

export function isLoopbackHostname(hostname: string): boolean {
  return (
    hostname === 'localhost' ||
    hostname === '127.0.0.1' ||
    hostname === '::1' ||
    hostname === '[::1]'
  );
}

function parseLocalBackendUrl(baseUrl: string | undefined): URL | null {
  try {
    return new URL(baseUrl || DEFAULT_SETTINGS.localBackend.url);
  } catch {
    return null;
  }
}

/** True for `http://` URLs on a loopback host (localhost, 127.0.0.1, ::1). */
export function isValidLocalBackendUrl(baseUrl: string | undefined): boolean {
  const parsed = parseLocalBackendUrl(baseUrl);
  return Boolean(parsed && parsed.protocol === 'http:' && isLoopbackHostname(parsed.hostname));
}

/** Validate a backend URL and strip any trailing slash. Throws when not loopback HTTP. */
export function normalizeLocalBackendBaseUrl(baseUrl: string | undefined): string {
  const parsed = parseLocalBackendUrl(baseUrl);
  if (parsed?.protocol !== 'http:' || !isLoopbackHostname(parsed.hostname)) {
    throw new Error('Local backend URL must be http://localhost or http://127.0.0.1');
  }

  return `${parsed.origin}${parsed.pathname.replace(/\/$/, '')}`;
}

export function buildLocalBackendUrl(baseUrl: string | undefined, path: string): string {
  return `${normalizeLocalBackendBaseUrl(baseUrl)}${path}`;
}

/**
 * The translator the backend/cloud path will use: in local mode an explicit
 * `localBackend.translator` wins, otherwise the provider.
 */
export function resolveTranslator(settings: SettingsInput = {}): Translator {
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

/** Whether a translator needs a provider API key (`none` and `local` do not). */
export function translatorNeedsApiKey(translator: string | null | undefined): boolean {
  return Boolean(translator && translator !== 'none' && translator !== 'local');
}

/**
 * List keys in `chrome.storage.local` without reading values: translated pages
 * make local storage large. `storage.getKeys` needs Chrome 130; older supported
 * versions fall back to a full read.
 */
export async function listLocalStorageKeys(): Promise<string[]> {
  const storage = browser.storage.local as typeof browser.storage.local & {
    getKeys?: () => Promise<string[]>;
  };
  if (typeof storage.getKeys === 'function') {
    return storage.getKeys();
  }
  return Object.keys(await storage.get(null));
}
