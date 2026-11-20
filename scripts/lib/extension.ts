/**
 * Shared helpers for scripts that drive the built extension in Chromium.
 * The extension is loaded unpacked from `dist/chrome-mv3` (built by `wxt build`).
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { BrowserContext, Worker } from '@playwright/test';
import type { browser } from 'wxt/browser';

/** The `chrome` global inside extension pages and the service worker. */
export type ChromeApi = typeof browser;

export const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
export const EXTENSION_PATH = path.join(REPO_ROOT, 'dist', 'chrome-mv3');

/** Fail early with an actionable message when the extension has not been built. */
export function assertExtensionBuilt(extensionPath: string = EXTENSION_PATH): string {
  if (!fs.existsSync(path.join(extensionPath, 'manifest.json'))) {
    throw new Error(`Built extension not found at ${extensionPath}. Run \`npm run build\` first.`);
  }
  return extensionPath;
}

export async function getExtensionWorker(
  context: BrowserContext,
  timeout = 15000,
): Promise<Worker> {
  const existing = context.serviceWorkers()[0];
  if (existing) return existing;
  return context.waitForEvent('serviceworker', { timeout });
}

/** The extension id is the host of the service worker URL. */
export function extensionIdFromWorker(worker: Worker): string {
  const id = worker.url().split('/')[2];
  if (!id) throw new Error(`Cannot read extension id from worker URL: ${worker.url()}`);
  return id;
}

export function extensionLaunchArgs(extensionPath: string = EXTENSION_PATH): string[] {
  return [`--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`];
}
