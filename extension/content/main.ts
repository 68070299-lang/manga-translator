/**
 * Content script start-up. Registers the message and storage listeners (cheap),
 * reads the per-hostname activation state, and only when the site is enabled
 * loads settings and starts the runtime. A disabled site does nothing more.
 */
import { browser } from 'wxt/browser';
import { clearPageTranslations, requestStopTranslation } from './clear';
import { handleMessage } from './messages';
import { reconcileVisibleCanvases } from './reconcile';
import { setCanvasReconciler } from './reconcile-scheduler';
import { startRegionSelection } from './region';
import { refreshSiteActivation, startInitialSiteRuntime } from './runtime';
import { loadSettings } from './settings-store';
import { state } from './state';
import { translatePage } from './translate';
import { setUiActions } from './ui';
import { debugLog } from './utils';

type StorageChanges = Record<string, unknown>;

function handleStorageChanged(changes: StorageChanges, areaName: string): void {
  if ((areaName === 'sync' && changes.settings) || (areaName === 'local' && changes.apiKeys)) {
    // Settings that were never needed are loaded on first use, with fresh values.
    if (state.settings) void loadSettings();
  }
  if (areaName === 'local' && (changes.activeUrls || changes.disabledUrls)) {
    void refreshSiteActivation();
  }
}

export async function initContentScript(): Promise<void> {
  setCanvasReconciler(reconcileVisibleCanvases);
  setUiActions({
    translatePage,
    startRegionSelection,
    requestStopTranslation,
    clearPageTranslations,
  });
  browser.runtime.onMessage.addListener(handleMessage);
  browser.storage?.onChanged?.addListener(handleStorageChanged);

  await refreshSiteActivation();
  if (state.siteEnabled) await startInitialSiteRuntime();

  debugLog('Content script initialized');
}

/** Run `initContentScript` once the DOM is available. */
export function bootContentScript(): void {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => void initContentScript(), { once: true });
  } else {
    void initContentScript();
  }
}
