/**
 * Reactive state and actions behind the options page: load/migrate settings
 * into the forms, save them back (public part to sync storage, keys to local
 * storage), test the backend connection and manage the translation cache.
 */
import { browser } from 'wxt/browser';
import {
  API_KEYS_STORAGE_KEY,
  applyStoredApiKeys,
  DEFAULT_SETTINGS,
  isValidLocalBackendUrl,
  migrateStoredSettings,
  SETTINGS_STORAGE_KEY,
  splitSettingsForStorage,
  translatorNeedsApiKey,
} from '../../lib/settings';
import type { SettingsInput } from '../../shared/types';
import {
  type ConnectionStatus,
  LOOPBACK_ONLY_MESSAGE,
  testBackendConnection,
} from '../backend-health';
import { clearCachedTranslations, countCachedTranslations } from '../cache-stats';
import {
  asSettingsInput,
  persistSettings,
  readStoredApiKeys,
  readStoredSettings,
} from '../settings-io';
import { ToastState } from '../toast.svelte';
import { BehaviorForm, LocalBackendForm, ProviderForm, RenderForm } from './forms.svelte';

/** Settings as the options page edits them: stored values over the defaults. */
function existingForSave(stored: unknown): SettingsInput {
  if (!stored || typeof stored !== 'object') return {};
  const settings = stored as SettingsInput;
  return {
    ...DEFAULT_SETTINGS,
    ...settings,
    localBackend: { ...DEFAULT_SETTINGS.localBackend, ...(settings.localBackend || {}) },
  };
}

export class OptionsState {
  readonly toast = new ToastState();

  readonly provider = new ProviderForm();
  readonly backend = new LocalBackendForm();
  readonly render = new RenderForm();
  readonly behavior = new BehaviorForm();

  connection = $state<ConnectionStatus | null>(null);
  testingConnection = $state(false);

  cacheCount = $state('0');
  cacheSize = $state('0 KB');
  cacheAge = $state('-');

  /** A translator provider is in use but no API key is configured. */
  get apiKeyWarningVisible(): boolean {
    const translator = this.backend.translator || this.provider.provider;
    return translatorNeedsApiKey(translator) && !this.provider.apiKey.trim();
  }

  async init(): Promise<void> {
    await this.loadSettings();
    await this.updateCacheStats();
  }

  destroy(): void {
    this.toast.dispose();
  }

  // -------------------------------------------------------------------------
  // Settings
  // -------------------------------------------------------------------------

  async loadSettings(): Promise<void> {
    try {
      const migration = migrateStoredSettings(await readStoredSettings());
      if (migration.changed) {
        await browser.storage.sync.set({ [SETTINGS_STORAGE_KEY]: migration.settings });
      }
      const settings = applyStoredApiKeys(
        asSettingsInput(migration.settings),
        await readStoredApiKeys(),
      );

      this.provider.load(settings);
      this.backend.load(settings);
      this.render.load(settings);
      this.behavior.load(settings);
    } catch (error) {
      console.error('Error loading settings:', error);
      this.toast.show('Error loading settings', 'error');
    }
  }

  async saveSettings(): Promise<void> {
    try {
      const backendUrl = this.backend.url.trim();
      if (backendUrl && !isValidLocalBackendUrl(backendUrl)) {
        this.toast.show(LOOPBACK_ONLY_MESSAGE, 'error');
        return;
      }

      const existing = existingForSave(await readStoredSettings());
      const settings: SettingsInput = {
        ...existing,
        ...this.provider.toSettings(),
        localBackend: { ...existing.localBackend, ...this.backend.backendToSettings() },
        localLlm: this.backend.llmToSettings(),
        ...this.render.toSettings(),
        ...this.behavior.toSettings(),
      };

      await persistSettings(settings);
      this.toast.show('Settings saved successfully!', 'success');
    } catch (error) {
      console.error('Error saving settings:', error);
      this.toast.show('Error saving settings', 'error');
    }
  }

  async resetSettings(): Promise<void> {
    if (!confirm('Are you sure you want to reset all settings to defaults?')) return;

    try {
      const { syncSettings } = splitSettingsForStorage(DEFAULT_SETTINGS);
      await browser.storage.sync.set({ [SETTINGS_STORAGE_KEY]: syncSettings });
      await browser.storage.local.remove(API_KEYS_STORAGE_KEY);
      await this.loadSettings();
      this.toast.show('Settings reset to defaults', 'success');
    } catch (error) {
      console.error('Error resetting settings:', error);
      this.toast.show('Error resetting settings', 'error');
    }
  }

  // -------------------------------------------------------------------------
  // Backend connection
  // -------------------------------------------------------------------------

  async testConnection(): Promise<void> {
    const url = this.backend.url.trim();
    if (!url) {
      this.connection = { message: 'Please enter a URL', type: 'error' };
      return;
    }
    if (!isValidLocalBackendUrl(url)) {
      this.connection = { message: LOOPBACK_ONLY_MESSAGE, type: 'error' };
      return;
    }

    this.connection = { message: 'Testing...', type: 'testing' };
    this.testingConnection = true;
    try {
      this.connection = await testBackendConnection(url, this.backend.authToken.trim());
    } finally {
      this.testingConnection = false;
    }
  }

  // -------------------------------------------------------------------------
  // Cache
  // -------------------------------------------------------------------------

  async clearCache(): Promise<void> {
    if (!confirm('Are you sure you want to clear all cached translations?')) return;

    try {
      const cleared = await clearCachedTranslations();
      await this.updateCacheStats();
      this.toast.show(`Cleared ${cleared} cached translations`, 'success');
    } catch (error) {
      console.error('Error clearing cache:', error);
      this.toast.show('Error clearing cache', 'error');
    }
  }

  async updateCacheStats(): Promise<void> {
    try {
      this.cacheCount = String(await countCachedTranslations());
      // Size and age would require reading every entry.
      this.cacheSize = '—';
      this.cacheAge = '—';
    } catch (error) {
      console.error('Error getting cache stats:', error);
      this.cacheCount = '0';
    }
  }
}
