/**
 * Toolbar state: an OFF badge and title on tabs where the site is disabled.
 */
import { browser } from 'wxt/browser';
import { getSiteState } from '../lib/site-activation';
import { safeBrowserCall } from './tab-messaging';

const ACTION_ICON_PATHS = Object.freeze({
  16: 'icons/icon16.png',
  48: 'icons/icon48.png',
  128: 'icons/icon128.png',
});

function warn(message: string, error: unknown): void {
  console.warn(message, (error as { message?: string } | null | undefined)?.message || error);
}

export function setActionState(tabId: number | undefined, enabled: boolean): void {
  if (!tabId || !browser.action) return;

  safeBrowserCall(browser.action.setIcon({ tabId, path: { ...ACTION_ICON_PATHS } }));
  safeBrowserCall(
    browser.action.setTitle({
      tabId,
      title: enabled
        ? 'Manga Translator enabled on this site'
        : 'Manga Translator disabled on this site',
    }),
  );
  safeBrowserCall(browser.action.setBadgeText({ tabId, text: enabled ? '' : 'OFF' }));
  safeBrowserCall(browser.action.setBadgeBackgroundColor({ tabId, color: '#7d3029' }));
}

/** Update one tab's toolbar state from its URL; resolves to whether the site is enabled. */
export async function updateActionState(
  tabId: number | undefined,
  url: string | undefined,
): Promise<boolean> {
  if (!tabId) return false;

  const { enabled } = await getSiteState(url || '');
  setActionState(tabId, enabled);
  return enabled;
}

export async function updateAllActionStates(): Promise<void> {
  const tabs = await browser.tabs.query({});
  await Promise.all(
    (tabs || []).filter((tab) => tab?.id).map((tab) => updateActionState(tab.id, tab.url)),
  );
}

export function refreshActionStatesSafely(): Promise<void> {
  return updateAllActionStates().catch((error) => {
    warn('[Service Worker] Failed to refresh action states:', error);
  });
}

/** Keep toolbar state current as tabs navigate, activate, and activation lists change. */
export function registerActionStateListeners(): void {
  browser.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (changeInfo?.url || tab?.url) {
      updateActionState(tabId, changeInfo?.url || tab.url).catch((error) => {
        warn('[Service Worker] Failed to update action state:', error);
      });
    }
  });

  browser.tabs.onActivated.addListener((activeInfo) => {
    browser.tabs
      .get(activeInfo.tabId)
      .then((tab) => updateActionState(activeInfo.tabId, tab?.url))
      .catch((error) => warn('[Service Worker] Failed to read active tab:', error));
  });

  browser.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== 'local' || (!changes.activeUrls && !changes.disabledUrls)) return;
    updateAllActionStates().catch((error) => {
      warn('[Service Worker] Failed to refresh action states:', error);
    });
  });
}
