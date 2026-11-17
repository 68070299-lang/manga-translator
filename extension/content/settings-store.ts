/**
 * Settings as the content script sees them: public synced settings merged with
 * the private keys in local storage. Loaded lazily, the first time a command
 * or an enabled site needs them, so pages that stay inactive never read them.
 */
import { browser } from 'wxt/browser';
import { applyStoredApiKeys, mergeSettings } from '../lib/settings';
import type { ApiKeys, SettingsInput } from '../shared/types';
import { LOG_PREFIX } from './constants';
import { state } from './state';

/** Read settings and API keys from storage into `state.settings`. */
export async function loadSettings(): Promise<void> {
  try {
    const result = await browser.storage.sync.get('settings');
    const localResult = await browser.storage.local.get('apiKeys');
    const loaded = (result.settings || {}) as SettingsInput;
    const apiKeys = (localResult.apiKeys || {}) as ApiKeys;
    state.settings = mergeSettings(applyStoredApiKeys(loaded, apiKeys));
    state.lastKnownSettings = state.settings;
  } catch (error) {
    console.error(`${LOG_PREFIX} Error loading settings:`, error);
    state.settings = state.lastKnownSettings || mergeSettings();
  }
}

/** Load settings unless they already are (concurrent callers share one read). */
export function ensureSettingsLoaded(): Promise<void> {
  if (state.settings) return Promise.resolve();
  if (!state.settingsLoad) {
    state.settingsLoad = loadSettings().finally(() => {
      state.settingsLoad = null;
    });
  }
  return state.settingsLoad;
}
