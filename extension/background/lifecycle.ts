/**
 * Install / browser-startup work: context menus, DNR reconciliation, settings
 * migration, cache pruning, and toolbar state.
 */
import { pruneExpiredCache } from '../lib/cache';
import { errorMessage } from '../lib/utils';
import { refreshActionStatesSafely } from './action-state';
import { registerContextMenus } from './context-menus';
import { ensureDnrSessionRulesReady, resetDnrReconciliation } from './dnr-rules';
import { migrateStoredSettingsSafely } from './settings-migration';

/** Drop expired cache entries. Fire-and-forget; never blocks translation. */
export async function pruneCacheSafely(): Promise<number> {
  try {
    return await pruneExpiredCache();
  } catch (error) {
    console.warn('[Service Worker] Failed to prune cache:', errorMessage(error));
    return 0;
  }
}

export async function handleExtensionStartup(): Promise<void> {
  void pruneCacheSafely();
  await Promise.all([migrateStoredSettingsSafely(), refreshActionStatesSafely()]);
}

export async function handleExtensionInstalled(): Promise<void> {
  registerContextMenus();
  resetDnrReconciliation();
  void ensureDnrSessionRulesReady();
  await handleExtensionStartup();
}
