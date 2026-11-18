/**
 * Reading and writing the settings record shared by the popup and options page.
 *
 * Public settings live in `storage.sync` under `settings`; key material is split
 * out into `storage.local` under `apiKeys` (see `splitSettingsForStorage`).
 */
import { browser } from 'wxt/browser';
import {
  API_KEYS_STORAGE_KEY,
  applyStoredApiKeys,
  SETTINGS_STORAGE_KEY,
  splitSettingsForStorage,
} from '../lib/settings';
import type { ApiKeys, PublicSettings, Settings, SettingsInput } from '../shared/types';

/** Stored settings as an object; anything else (missing, corrupt) becomes `{}`. */
export function asSettingsInput(value: unknown): SettingsInput {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as SettingsInput)
    : {};
}

export function asApiKeys(value: unknown): ApiKeys {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as ApiKeys) : {};
}

/** Raw synced settings exactly as stored (not merged with defaults). */
export async function readStoredSettings(): Promise<unknown> {
  const stored = await browser.storage.sync.get(SETTINGS_STORAGE_KEY);
  return stored[SETTINGS_STORAGE_KEY];
}

export async function readStoredApiKeys(): Promise<ApiKeys> {
  const stored = await browser.storage.local.get(API_KEYS_STORAGE_KEY);
  return asApiKeys(stored[API_KEYS_STORAGE_KEY]);
}

/** Synced settings merged with defaults and overlaid with the private keys. */
export async function loadCurrentSettings(): Promise<Settings> {
  const settings = asSettingsInput(await readStoredSettings());
  return applyStoredApiKeys(settings, await readStoredApiKeys());
}

/**
 * Persist full settings: the public part to sync storage, keys to local
 * storage. Resolves to the public settings that were written.
 */
export async function persistSettings(settings: SettingsInput): Promise<PublicSettings> {
  const { syncSettings, apiKeys } = splitSettingsForStorage(settings);
  await browser.storage.sync.set({ [SETTINGS_STORAGE_KEY]: syncSettings });
  await browser.storage.local.set({ [API_KEYS_STORAGE_KEY]: apiKeys });
  return syncSettings;
}
