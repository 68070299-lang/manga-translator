/**
 * Settings migration run at install, browser startup and worker load.
 */
import { browser } from 'wxt/browser';
import {
  API_KEYS_STORAGE_KEY,
  migrateStoredSettings,
  SETTINGS_STORAGE_KEY,
  splitSettingsForStorage,
} from '../lib/settings';
import { errorMessage } from '../lib/utils';
import type { ApiKeys, PublicSettings, SettingsInput } from '../shared/types';

function hasNonEmptyString(value: unknown): boolean {
  return typeof value === 'string' && value.trim() !== '';
}

function isRecord(value: unknown): value is SettingsInput {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function hasLegacySyncedSecrets(settings: unknown): settings is SettingsInput {
  return (
    isRecord(settings) &&
    (hasNonEmptyString(settings.apiKey) ||
      hasNonEmptyString(settings.localLlm?.apiKey) ||
      hasNonEmptyString(settings.localBackend?.authToken))
  );
}

/** Synced settings left behind by removed features (Cloud API mode, Google Vision OCR). */
function hasRemovedSettings(settings: unknown): settings is SettingsInput {
  return (
    isRecord(settings) &&
    (Object.hasOwn(settings, 'pipelineMode') || Object.hasOwn(settings, 'googleOcr'))
  );
}

function withoutRemovedSettings(settings: SettingsInput): SettingsInput {
  const {
    pipelineMode: _pipelineMode,
    googleOcr: _googleOcr,
    ...kept
  } = settings as Record<string, unknown>;
  return kept as SettingsInput;
}

/** Delete the Google Vision OCR key an older version kept in private storage. */
async function dropRemovedApiKeys(): Promise<boolean> {
  const stored = await browser.storage.local.get(API_KEYS_STORAGE_KEY);
  const keys: unknown = stored?.[API_KEYS_STORAGE_KEY];
  if (!isRecord(keys) || !Object.hasOwn(keys, 'googleOcrApiKey')) return false;
  const { googleOcrApiKey: _googleOcrApiKey, ...kept } = keys as Record<string, unknown>;
  await browser.storage.local.set({ [API_KEYS_STORAGE_KEY]: kept });
  return true;
}

/**
 * Move API keys that an older version synced into `chrome.storage.sync` over to
 * private local storage. Existing non-empty local values always win.
 * Returns the settings with the keys removed.
 */
async function moveLegacySyncedSecretsToLocal(settings: SettingsInput): Promise<PublicSettings> {
  const { syncSettings, apiKeys: legacyKeys } = splitSettingsForStorage(settings);
  const stored = await browser.storage.local.get(API_KEYS_STORAGE_KEY);
  const storedKeys: unknown = stored?.[API_KEYS_STORAGE_KEY];
  const localKeys: ApiKeys = isRecord(storedKeys) ? (storedKeys as ApiKeys) : {};
  const mergedKeys: ApiKeys = { ...localKeys };

  for (const [name, value] of Object.entries(legacyKeys) as Array<[keyof ApiKeys, string]>) {
    if (hasNonEmptyString(value) && !hasNonEmptyString(mergedKeys[name])) {
      mergedKeys[name] = value;
    }
  }

  await browser.storage.local.set({ [API_KEYS_STORAGE_KEY]: mergedKeys });
  return syncSettings;
}

/**
 * Upgrade stored synced settings and move legacy synced keys to local storage.
 * Resolves to whether anything was written; storage failures are logged, not thrown.
 */
export async function migrateStoredSettingsSafely(): Promise<boolean> {
  try {
    const stored = await browser.storage.sync.get(SETTINGS_STORAGE_KEY);
    const migration = migrateStoredSettings(stored?.[SETTINGS_STORAGE_KEY] as unknown);
    let settings = migration.settings;
    let changed = migration.changed;

    // Independent of the version migration: keys must not keep syncing.
    if (hasLegacySyncedSecrets(settings)) {
      settings = await moveLegacySyncedSecretsToLocal(settings);
      changed = true;
    }
    if (hasRemovedSettings(settings)) {
      settings = withoutRemovedSettings(settings);
      changed = true;
    }
    // Local cleanup runs before the sync write, so a failure leaves sync as it was.
    const droppedLocalKey = await dropRemovedApiKeys();

    if (!changed) return droppedLocalKey;
    await browser.storage.sync.set({ [SETTINGS_STORAGE_KEY]: settings });
    return true;
  } catch (error) {
    console.warn('[Service Worker] Failed to migrate settings:', errorMessage(error));
    return false;
  }
}
