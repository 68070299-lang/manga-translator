/**
 * Reactive state and actions behind the popup.
 *
 * The popup is a thin remote control: it reads the saved settings, sends
 * commands to the active tab's content script, and mirrors the run's progress
 * (also into `storage.session`, so reopening the popup restores it).
 */
import { browser } from 'wxt/browser';
import { DEFAULT_SETTINGS } from '../../lib/settings';
import { getSiteState, setSiteEnabled } from '../../lib/site-activation';
import {
  isMessageOfType,
  type StartRegionSelectionMessage,
  sendToTab,
  type TabMessage,
  type TabResponseOf,
  type TranslatePageMessage,
  type TranslateVisibleImagesMessage,
} from '../../shared/messages';
import type { PipelineMode, Settings, SiteState, TranslationProgress } from '../../shared/types';
import {
  type BackendIndicator,
  checkLocalBackend,
  type IndicatorState,
  type ModelStatusMap,
} from '../backend-health';
import { clearCachedTranslations, countCachedTranslations } from '../cache-stats';
import { loadCurrentSettings, persistSettings } from '../settings-io';
import { ToastState } from '../toast.svelte';
import {
  CONTENT_MESSAGE_TIMEOUT_MS,
  completionLabel,
  formatRunCounts,
  getApiKeyWarning,
  getTranslationSetupError,
  isProgressFresh,
  MISSING_KEY_TEXT,
  PROGRESS_STORAGE_KEY,
  STATUS_HIDE_DELAY_MS,
} from './progress';

export type CommandButton =
  | 'translate'
  | 'translateVisible'
  | 'translateRegion'
  | 'clearPage'
  | 'stop'
  | 'clearCache';

type TranslateCommand =
  | TranslatePageMessage
  | TranslateVisibleImagesMessage
  | StartRegionSelectionMessage;

/** Reasons a message to the page can fail, collapsed into one user-facing hint. */
const PAGE_UNREACHABLE = 'Could not connect to page. Try refreshing the tab.';

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Run a promise-returning call whose failure must not surface (fire-and-forget). */
function ignoreFailure(label: string, run: () => Promise<unknown>): void {
  Promise.resolve()
    .then(run)
    .catch((error: unknown) => console.warn('[Popup]', label, error));
}

export class PopupState {
  readonly toast = new ToastState();

  // Quick settings (mirrors of the saved settings).
  targetLang = $state<string>(DEFAULT_SETTINGS.targetLang);
  fontSize = $state<number>(DEFAULT_SETTINGS.fontSize);
  fontFamily = $state<string>(DEFAULT_SETTINGS.fontFamily);
  autoTranslate = $state<boolean>(DEFAULT_SETTINGS.autoTranslate);
  showProgress = $state<boolean>(DEFAULT_SETTINGS.showProgress);
  pipelineMode = $state<PipelineMode>(DEFAULT_SETTINGS.pipelineMode);

  // Missing-key banner.
  warningVisible = $state(false);
  warningText = $state(MISSING_KEY_TEXT);

  // Local backend indicator.
  backendVisible = $state(false);
  backendState = $state<IndicatorState | ''>('');
  backendLabel = $state('Local backend');
  backendModels = $state<ModelStatusMap | null>(null);

  // Per-site switch.
  siteEnabled = $state(true);
  siteTitle = $state('Toggle translation on this site');
  siteBusy = $state(false);

  // Progress.
  statusVisible = $state(false);
  statusText = $state('Ready');
  percent = $state(0);

  cacheText = $state('0 cached');

  busy = $state<Record<CommandButton, boolean>>({
    translate: false,
    translateVisible: false,
    translateRegion: false,
    clearPage: false,
    stop: false,
    clearCache: false,
  });

  /** Tab whose progress broadcasts this popup shows; `null` when no run is tracked. */
  #activeTabId: number | null = null;
  #statusTimer: ReturnType<typeof setTimeout> | undefined;
  #listener: ((message: unknown, sender: { tab?: { id?: number } }) => void) | undefined;

  // -------------------------------------------------------------------------
  // Lifecycle
  // -------------------------------------------------------------------------

  async init(): Promise<void> {
    this.#listen();
    await this.loadSettings();
    await this.refreshSiteActivation();
    void this.updateCacheStats();
    await this.restoreProgress();
  }

  destroy(): void {
    if (this.#listener) browser.runtime.onMessage.removeListener(this.#listener);
    this.#listener = undefined;
    clearTimeout(this.#statusTimer);
    this.toast.dispose();
  }

  // -------------------------------------------------------------------------
  // Settings
  // -------------------------------------------------------------------------

  async loadSettings(): Promise<void> {
    try {
      const settings = await loadCurrentSettings();

      this.targetLang = settings.targetLang;
      this.fontSize = settings.fontSize;
      this.fontFamily = settings.fontFamily;
      this.autoTranslate = settings.autoTranslate;
      this.showProgress = settings.showProgress;
      this.#applyWarning(settings);

      this.pipelineMode = settings.pipelineMode === 'local' ? 'local' : 'cloud';
      this.backendVisible = this.pipelineMode === 'local';
      if (this.backendVisible) {
        void this.#checkBackend(settings);
      }
    } catch (error) {
      console.error('Error loading settings:', error);
    }
  }

  /** Save the quick settings, keeping every other (and private) setting as stored. */
  async saveSettings(): Promise<void> {
    try {
      const existing = await loadCurrentSettings();
      const syncSettings = await persistSettings({
        ...existing,
        targetLang: this.targetLang,
        fontSize: Math.trunc(Number(this.fontSize)),
        fontFamily: this.fontFamily,
        autoTranslate: this.autoTranslate,
        showProgress: this.showProgress,
        pipelineMode: this.pipelineMode,
      });

      const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
      const tabId = tab?.id;
      if (tabId) {
        ignoreFailure('settings update', () =>
          sendToTab(tabId, { type: 'SETTINGS_UPDATED', settings: syncSettings }),
        );
      }
    } catch (error) {
      console.error('Error saving settings:', error);
    }
  }

  async setPipelineMode(mode: PipelineMode): Promise<void> {
    this.pipelineMode = mode;
    this.backendVisible = mode === 'local';
    await this.saveSettings();
    await this.refreshApiKeyWarning();
    if (mode === 'local') {
      const settings = await loadCurrentSettings();
      void this.#checkBackend(settings);
    }
  }

  async refreshApiKeyWarning(): Promise<void> {
    try {
      this.#applyWarning(await loadCurrentSettings());
    } catch (error) {
      console.error('Error refreshing API key warning:', error);
    }
  }

  openSettings(): void {
    ignoreFailure('open settings', () => browser.runtime.openOptionsPage());
  }

  #applyWarning(settings: Settings): void {
    const warning = getApiKeyWarning(settings);
    this.warningVisible = warning.visible;
    this.warningText = warning.text;
  }

  // -------------------------------------------------------------------------
  // Backend indicator
  // -------------------------------------------------------------------------

  async #checkBackend(settings: Settings): Promise<void> {
    const result = await checkLocalBackend(
      settings.localBackend.url,
      settings.localBackend.authToken,
    );
    this.#applyIndicator(result);
  }

  #applyIndicator(result: BackendIndicator): void {
    this.backendState = result.state;
    this.backendLabel = result.label;
    if (result.models) {
      this.backendModels = result.models;
    } else if (result.state === 'disconnected') {
      this.backendModels = null;
    }
  }

  // -------------------------------------------------------------------------
  // Site activation
  // -------------------------------------------------------------------------

  async refreshSiteActivation(): Promise<void> {
    try {
      const tab = await this.#getActiveTab();
      this.#applySiteState(await getSiteState(tab.url || ''));
    } catch (error) {
      console.warn('[Popup] Failed to read site activation:', errorMessage(error));
    }
  }

  async toggleSite(): Promise<void> {
    try {
      this.siteBusy = true;
      const tab = await this.#getActiveTab();
      const url = tab.url || '';
      const current = await getSiteState(url);
      const next = await setSiteEnabled(url, !current.enabled);
      this.#applySiteState(next);
      ignoreFailure('site activation', () =>
        sendToTab(tab.id, { type: 'SITE_ACTIVATION_CHANGED' }),
      );
      this.toast.show(
        next.enabled ? 'Translation enabled on this site' : 'Translation disabled on this site',
        'success',
      );
    } catch (error) {
      console.error('[Popup] Site activation error:', error);
      this.toast.show(`Site activation failed: ${errorMessage(error)}`, 'error');
    } finally {
      this.siteBusy = false;
    }
  }

  #applySiteState(state: Pick<SiteState, 'enabled' | 'hostname'>): void {
    const enabled = Boolean(state.enabled);
    this.siteEnabled = enabled;
    this.siteTitle = enabled
      ? `Disable translation on ${state.hostname}`
      : `Enable translation on ${state.hostname}`;
  }

  // -------------------------------------------------------------------------
  // Translation commands
  // -------------------------------------------------------------------------

  async translatePage(): Promise<void> {
    await this.#runCommand(
      { type: 'TRANSLATE_PAGE' },
      'translate',
      'Starting page translation...',
      'Page translation started',
    );
  }

  async translateVisibleImages(): Promise<void> {
    await this.#runCommand(
      { type: 'TRANSLATE_VISIBLE_IMAGES' },
      'translateVisible',
      'Starting visible image translation...',
      'Visible image translation started',
    );
  }

  async translateRegion(): Promise<void> {
    await this.#runCommand(
      { type: 'START_REGION_SELECTION' },
      'translateRegion',
      'Starting region selection...',
      'Drag over the area to translate',
    );
    // The open popup would take the first click of the drag.
    window.close();
  }

  async clearPageTranslations(): Promise<void> {
    try {
      this.busy.clearPage = true;
      const tab = await this.#getActiveTab();
      this.#activeTabId = tab.id;

      const response = await this.#sendToPage(tab.id, { type: 'CLEAR_PAGE_TRANSLATIONS' });

      this.toast.show(`Cleared ${response?.cleared ?? 0} page translations`, 'success');
      await browser.storage.session.remove(PROGRESS_STORAGE_KEY);
      this.statusVisible = false;
      this.#activeTabId = null;
    } catch (error) {
      console.error('Clear page translation error:', error);
      this.toast.show(`Clear page failed: ${errorMessage(error)}`, 'error');
      this.#activeTabId = null;
    } finally {
      this.busy.clearPage = false;
    }
  }

  async stopTranslation(): Promise<void> {
    try {
      this.busy.stop = true;
      const tab = await this.#getActiveTab();
      this.#activeTabId = tab.id;

      this.statusVisible = true;
      this.#updateStatus('Stopping after current image...', 96);

      const response = await this.#sendToPage(tab.id, { type: 'STOP_TRANSLATION' });

      this.toast.show(
        response?.stopped ? 'Stopping translation' : 'No translation is running',
        'success',
      );
    } catch (error) {
      console.error('Stop translation error:', error);
      this.toast.show(`Stop failed: ${errorMessage(error)}`, 'error');
      this.#activeTabId = null;
    } finally {
      this.busy.stop = false;
    }
  }

  async clearCache(): Promise<void> {
    try {
      const cleared = await clearCachedTranslations();
      void this.updateCacheStats();
      this.toast.show(`Cleared ${cleared} cached translations`, 'success');
    } catch (error) {
      console.error('Clear cache error:', error);
      this.toast.show('Failed to clear cache', 'error');
    }
  }

  async updateCacheStats(): Promise<void> {
    try {
      this.cacheText = `${await countCachedTranslations()} cached translations`;
    } catch {
      this.cacheText = '0 cached';
    }
  }

  async #runCommand(
    message: TranslateCommand,
    button: CommandButton,
    startStatus: string,
    successMessage: string,
  ): Promise<void> {
    try {
      const settings = await loadCurrentSettings();
      const setupError = getTranslationSetupError(settings);
      if (setupError) {
        this.toast.show(setupError, 'error');
        return;
      }

      this.busy[button] = true;
      this.statusVisible = true;
      this.#updateStatus(startStatus, 10);

      const tab = await this.#getActiveTab();
      this.#activeTabId = tab.id;

      const response = await this.#sendToPage(tab.id, message);
      if (response?.success === false) {
        throw new Error(response.error || 'Translation is disabled on this site');
      }

      this.toast.show(successMessage, 'success');
    } catch (error) {
      console.error('Translation error:', error);
      this.toast.show(`Translation failed: ${errorMessage(error)}`, 'error');
      this.#activeTabId = null;
    } finally {
      this.busy[button] = false;
    }
  }

  // -------------------------------------------------------------------------
  // Tabs and messaging
  // -------------------------------------------------------------------------

  async #getActiveTab(): Promise<{ id: number; url?: string }> {
    const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) {
      throw new Error('No active tab found');
    }
    return { id: tab.id, url: tab.url };
  }

  /** Message the content script; any failure or a stalled reply becomes one friendly error. */
  async #sendToPage<T extends TabMessage>(tabId: number, message: T): Promise<TabResponseOf<T>> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(
        () => reject(new Error('Content script message timed out')),
        CONTENT_MESSAGE_TIMEOUT_MS,
      );
    });
    try {
      return await Promise.race([sendToTab(tabId, message), timeout]);
    } catch {
      throw new Error(PAGE_UNREACHABLE);
    } finally {
      clearTimeout(timer);
    }
  }

  // -------------------------------------------------------------------------
  // Progress
  // -------------------------------------------------------------------------

  async restoreProgress(): Promise<void> {
    try {
      const stored = await browser.storage.session.get(PROGRESS_STORAGE_KEY);
      const progress = stored[PROGRESS_STORAGE_KEY] as TranslationProgress | undefined;
      if (!progress) return;

      if (isProgressFresh(progress)) {
        this.#activeTabId = progress.tabId;
        this.statusVisible = true;
        this.#updateStatus(progress.status, progress.percent);
      } else {
        await browser.storage.session.remove(PROGRESS_STORAGE_KEY);
      }
    } catch (error) {
      console.error('Error restoring progress state:', error);
    }
  }

  #updateStatus(text: string, percent: number): void {
    this.statusText = text;
    this.percent = percent;
    const tabId = this.#activeTabId;
    if (tabId !== null) {
      const progress: TranslationProgress = { status: text, percent, tabId, timestamp: Date.now() };
      ignoreFailure('progress', () =>
        browser.storage.session.set({ [PROGRESS_STORAGE_KEY]: progress }),
      );
    }
  }

  #endRun(hideStatusLater: boolean): void {
    ignoreFailure('progress', () => browser.storage.session.remove(PROGRESS_STORAGE_KEY));
    if (hideStatusLater) {
      clearTimeout(this.#statusTimer);
      this.#statusTimer = setTimeout(() => {
        this.statusVisible = false;
      }, STATUS_HIDE_DELAY_MS);
    }
    this.#activeTabId = null;
  }

  /** Progress and result broadcasts from the tab being translated. */
  #listen(): void {
    if (this.#listener) return;
    this.#listener = (message, sender) => {
      if (this.#activeTabId === null || sender?.tab?.id !== this.#activeTabId) return;

      if (isMessageOfType(message, 'TRANSLATION_PROGRESS')) {
        this.statusVisible = true;
        this.#updateStatus(message.status, message.percent);
      } else if (isMessageOfType(message, 'TRANSLATION_COMPLETE')) {
        this.#updateStatus(`${completionLabel(message.status)}${formatRunCounts(message)}`, 100);
        void this.updateCacheStats();
        this.#endRun(true);
      } else if (isMessageOfType(message, 'TRANSLATION_STOPPED')) {
        this.#updateStatus(`Stopped${formatRunCounts(message)}`, 100);
        this.#endRun(true);
      } else if (isMessageOfType(message, 'TRANSLATION_ERROR')) {
        this.toast.show(`Error: ${message.error}`, 'error');
        this.statusVisible = false;
        this.#endRun(false);
      }
    };
    browser.runtime.onMessage.addListener(this.#listener);
  }
}
