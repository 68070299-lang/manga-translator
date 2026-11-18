/**
 * Prepares a Chrome profile for the native BookWalker UAT
 * (`scripts/bookwalker-real-uat.ps1`): loads the built extension from
 * `dist/chrome-mv3`, writes local-backend settings, and establishes the
 * BookWalker product-page session. Configured entirely through environment
 * variables; it never prints values.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { type BrowserContext, chromium, type Page, type Worker } from '@playwright/test';
import type { Translator } from '../extension/shared/types.ts';
import {
  type ChromeApi,
  EXTENSION_PATH,
  extensionIdFromWorker,
  extensionLaunchArgs,
} from './lib/extension.ts';

declare const chrome: ChromeApi;

const extensionPath = path.resolve(EXTENSION_PATH);

export function requiredEnv(name: string): string {
  const value = String(process.env[name] ?? '').trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
}

export function resolveNativeChrome(): string {
  const override = String(process.env.BOOKWALKER_CHROME_PATH ?? '').trim();
  if (override) {
    const resolved = path.resolve(override);
    if (!fs.existsSync(resolved)) throw new Error('BOOKWALKER_CHROME_PATH does not exist');
    return resolved;
  }

  const browsersDir = path.join(os.homedir(), '.agent-browser', 'browsers');
  if (fs.existsSync(browsersDir)) {
    const candidates = fs
      .readdirSync(browsersDir)
      .filter((name) => /^chrome-\d+(?:\.\d+)*$/.test(name))
      .sort((left, right) => right.localeCompare(left, undefined, { numeric: true }))
      .map((name) => path.join(browsersDir, name, 'chrome.exe'))
      .filter((candidate) => fs.existsSync(candidate));
    if (candidates[0]) return candidates[0];
  }
  return chromium.executablePath();
}

async function getExtensionWorker(context: BrowserContext): Promise<Worker> {
  return context.serviceWorkers()[0] ?? context.waitForEvent('serviceworker', { timeout: 30000 });
}

export async function establishProductSession(
  context: BrowserContext,
  productUrl: string,
): Promise<string | null> {
  const product = await context.newPage();
  await product.goto(productUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });

  for (const name of ['Allow only essential cookies', 'Allow all cookies']) {
    const button = product.getByRole('button', { name, exact: true });
    if ((await button.count()) && (await button.first().isVisible())) {
      await button.first().click({ timeout: 10000 });
      break;
    }
  }

  const freeRead = product.getByRole('link', { name: '無料で読む', exact: true });
  await freeRead.waitFor({ state: 'visible', timeout: 30000 });
  await freeRead.click({ noWaitAfter: true, timeout: 30000 });
  await new Promise((resolve) => setTimeout(resolve, 2500));
  return (
    context
      .pages()
      .find((page: Page) => page.url().includes('viewer.bookwalker.jp'))
      ?.url() ?? null
  );
}

async function main(): Promise<void> {
  const profileDir = path.resolve(requiredEnv('BOOKWALKER_PROFILE_DIR'));
  const backendUrl = requiredEnv('BOOKWALKER_BACKEND_URL');
  const authToken = requiredEnv('BACKEND_AUTH_TOKEN');
  const translator = String(process.env.BOOKWALKER_TRANSLATOR ?? 'none').trim() || 'none';
  const browserApiKey = String(
    process.env.BOOKWALKER_API_KEY ?? process.env.GEMINI_API_KEY ?? '',
  ).trim();
  const model = String(process.env.BOOKWALKER_MODEL ?? process.env.GEMINI_MODEL ?? '').trim();
  const resultPath = path.resolve(requiredEnv('BOOKWALKER_BOOTSTRAP_FILE'));
  const inspectOnly = process.env.BOOKWALKER_INSPECT_ONLY === '1';
  const productUrl = inspectOnly ? '' : requiredEnv('BOOKWALKER_PRODUCT_URL');

  if (!fs.existsSync(path.join(extensionPath, 'manifest.json'))) {
    throw new Error(`Built extension not found at ${extensionPath}. Run \`npm run build\` first.`);
  }

  // Settings shape and the sync/local key split come from the extension's own
  // settings module so the UAT cannot drift from what the options page writes.
  const { mergeSettings, splitSettingsForStorage } = await import('../extension/lib/settings.ts');
  const settings = mergeSettings({
    pipelineMode: 'local',
    provider: translator === 'gemini' ? 'gemini' : 'openrouter',
    apiKey: browserApiKey,
    model,
    targetLang: 'English',
    autoTranslate: false,
    showProgress: true,
    localBackend: {
      url: backendUrl,
      authToken,
      detector: 'rtdetr',
      ocr: 'manga-ocr',
      inpainter: 'lama_large',
      translator: translator as Translator,
    },
  });
  const { syncSettings, apiKeys } = splitSettingsForStorage(settings);

  fs.mkdirSync(profileDir, { recursive: true });
  const executablePath = resolveNativeChrome();
  const context = await chromium.launchPersistentContext(profileDir, {
    headless: false,
    executablePath,
    args: [
      ...extensionLaunchArgs(extensionPath),
      '--force-renderer-accessibility',
      '--no-first-run',
      '--window-position=-32000,-32000',
    ],
  });

  try {
    const worker = await getExtensionWorker(context);
    const extensionId = extensionIdFromWorker(worker);
    const page = await context.newPage();
    await page.goto(`chrome-extension://${extensionId}/popup.html`);
    let cache: { count: number; metadataCount: number; keys: string[] } | null = null;
    let viewerUrl: string | null = null;
    if (inspectOnly) {
      cache = await page.evaluate(async () => {
        const local = await chrome.storage.local.get(null);
        const keys = Object.keys(local).filter(
          (key) => key.startsWith('manga_cache_') && key !== 'manga_cache_metadata',
        );
        return {
          count: keys.length,
          metadataCount: Array.isArray(local.manga_cache_metadata)
            ? local.manga_cache_metadata.length
            : 0,
          keys,
        };
      });
    } else {
      await page.evaluate(
        async ({ syncValue, keysValue }) => {
          await chrome.storage.sync.clear();
          await chrome.storage.local.clear();
          await chrome.storage.session.clear();
          await chrome.storage.sync.set({ settings: syncValue });
          await chrome.storage.local.set({ apiKeys: keysValue });
        },
        { syncValue: syncSettings, keysValue: apiKeys },
      );
      viewerUrl = await establishProductSession(context, productUrl);
    }
    fs.mkdirSync(path.dirname(resultPath), { recursive: true });
    fs.writeFileSync(
      resultPath,
      JSON.stringify(
        {
          extensionId,
          executablePath,
          extensionPath,
          productSessionEstablished: !inspectOnly,
          viewerUrl,
          cache,
        },
        null,
        2,
      ),
    );
  } finally {
    await context.close();
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error: unknown) => {
    console.error(
      `BookWalker profile bootstrap failed: ${error instanceof Error ? error.message : error}`,
    );
    process.exit(1);
  });
}
