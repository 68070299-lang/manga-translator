/**
 * Service-worker wiring. Every listener is registered synchronously when the
 * worker script evaluates, as MV3 requires, before any async startup work.
 */
import { browser } from 'wxt/browser';
import { refreshActionStatesSafely, registerActionStateListeners } from './action-state';
import { handleContextMenuClick } from './context-menus';
import { ensureDnrSessionRulesReady } from './dnr-rules';
import { handleExtensionInstalled, handleExtensionStartup, pruneCacheSafely } from './lifecycle';
import { handleRuntimeMessage } from './message-router';
import { migrateStoredSettingsSafely } from './settings-migration';

/** The part of `ServiceWorkerGlobalScope` this worker uses. */
interface WorkerScope {
  addEventListener(
    type: 'activate',
    listener: (event: Event & { waitUntil(p: Promise<unknown>): void }) => void,
  ): void;
  clients: { claim(): Promise<void> };
}

export function registerBackground(): void {
  // Synchronous top-level listener registration.
  browser.runtime.onMessage.addListener(handleRuntimeMessage);
  browser.runtime.onInstalled.addListener(handleExtensionInstalled);
  browser.runtime.onStartup.addListener(handleExtensionStartup);
  registerActionStateListeners();
  browser.contextMenus.onClicked.addListener(handleContextMenuClick);

  const worker = self as unknown as WorkerScope;
  worker.addEventListener('activate', (event) => {
    event.waitUntil(worker.clients.claim());
  });

  // Work done on every worker start (the worker is suspended and restarted often).
  void ensureDnrSessionRulesReady();
  void migrateStoredSettingsSafely();
  void refreshActionStatesSafely();
  void pruneCacheSafely();

  console.log('[Service Worker] Manga Translator service worker loaded');
}
