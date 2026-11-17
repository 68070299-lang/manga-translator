const fs = require('fs');
const os = require('os');
const path = require('path');

const extensionPath = path.resolve(__dirname, '..', 'extension');

function getChromium() {
  return require('@playwright/test').chromium;
}

function requiredEnv(name) {
  const value = String(process.env[name] || '').trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function resolveNativeChrome() {
  const override = String(process.env.BOOKWALKER_CHROME_PATH || '').trim();
  if (override) {
    const resolved = path.resolve(override);
    if (!fs.existsSync(resolved)) throw new Error('BOOKWALKER_CHROME_PATH does not exist');
    return resolved;
  }

  const browsersDir = path.join(os.homedir(), '.agent-browser', 'browsers');
  if (fs.existsSync(browsersDir)) {
    const candidates = fs.readdirSync(browsersDir)
      .filter(name => /^chrome-\d+(?:\.\d+)*$/.test(name))
      .sort((left, right) => right.localeCompare(left, undefined, { numeric: true }))
      .map(name => path.join(browsersDir, name, 'chrome.exe'))
      .filter(candidate => fs.existsSync(candidate));
    if (candidates[0]) return candidates[0];
  }
  return getChromium().executablePath();
}

async function getExtensionWorker(context) {
  return context.serviceWorkers()[0] || context.waitForEvent('serviceworker', { timeout: 30000 });
}

async function establishProductSession(context, productUrl) {
  const product = await context.newPage();
  await product.goto(productUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });

  for (const name of ['Allow only essential cookies', 'Allow all cookies']) {
    const button = product.getByRole('button', { name, exact: true });
    if (await button.count() && await button.first().isVisible()) {
      await button.first().click({ timeout: 10000 });
      break;
    }
  }

  const freeRead = product.getByRole('link', { name: '\u7121\u6599\u3067\u8aad\u3080', exact: true });
  await freeRead.waitFor({ state: 'visible', timeout: 30000 });
  await freeRead.click({ noWaitAfter: true, timeout: 30000 });
  await new Promise(resolve => setTimeout(resolve, 2500));
  return context.pages().find(page => page.url().includes('viewer.bookwalker.jp'))?.url() || null;
}

async function main() {
  const chromium = getChromium();
  const profileDir = path.resolve(requiredEnv('BOOKWALKER_PROFILE_DIR'));
  const backendUrl = requiredEnv('BOOKWALKER_BACKEND_URL');
  const authToken = requiredEnv('BACKEND_AUTH_TOKEN');
  const translator = String(process.env.BOOKWALKER_TRANSLATOR || 'none').trim() || 'none';
  const browserApiKey = String(process.env.BOOKWALKER_API_KEY || process.env.GEMINI_API_KEY || '').trim();
  const model = String(process.env.BOOKWALKER_MODEL || process.env.GEMINI_MODEL || '').trim();
  const resultPath = path.resolve(requiredEnv('BOOKWALKER_BOOTSTRAP_FILE'));
  const inspectOnly = process.env.BOOKWALKER_INSPECT_ONLY === '1';
  const productUrl = inspectOnly ? '' : requiredEnv('BOOKWALKER_PRODUCT_URL');

  fs.mkdirSync(profileDir, { recursive: true });
  const executablePath = resolveNativeChrome();
  const context = await chromium.launchPersistentContext(profileDir, {
    headless: false,
    executablePath,
    args: [
      `--disable-extensions-except=${extensionPath}`,
      `--load-extension=${extensionPath}`,
      '--force-renderer-accessibility',
      '--no-first-run',
      '--window-position=-32000,-32000'
    ]
  });

  try {
    const worker = await getExtensionWorker(context);
    const extensionId = worker.url().split('/')[2];
    const page = await context.newPage();
    await page.goto(`chrome-extension://${extensionId}/popup/popup.html`);
    let cache = null;
    let viewerUrl = null;
    if (inspectOnly) {
      cache = await page.evaluate(async () => {
        const local = await chrome.storage.local.get(null);
        const keys = Object.keys(local).filter(key => key.startsWith('manga_cache_') && key !== 'manga_cache_metadata');
        return {
          count: keys.length,
          metadataCount: Array.isArray(local.manga_cache_metadata) ? local.manga_cache_metadata.length : 0,
          keys
        };
      });
    } else {
      await page.evaluate(async ({ backendUrl, authToken, translator, browserApiKey, model }) => {
        const api = globalThis.MangaTranslatorSettings;
        if (!api) throw new Error('Settings API is unavailable');
        const settings = api.mergeSettings({
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
            translator
          }
        });
        const { syncSettings, apiKeys } = api.splitSettingsForStorage(settings);
        await chrome.storage.sync.clear();
        await chrome.storage.local.clear();
        await chrome.storage.session.clear();
      await chrome.storage.sync.set({ settings: syncSettings });
      await chrome.storage.local.set({ apiKeys });
      }, { backendUrl, authToken, translator, browserApiKey, model });
      viewerUrl = await establishProductSession(context, productUrl);
    }
    fs.mkdirSync(path.dirname(resultPath), { recursive: true });
    fs.writeFileSync(resultPath, JSON.stringify({
      extensionId,
      executablePath,
      extensionPath,
      productSessionEstablished: !inspectOnly,
      viewerUrl,
      cache
    }, null, 2));
  } finally {
    await context.close();
  }
}

if (require.main === module) {
  main().catch(error => {
    console.error(`BookWalker profile bootstrap failed: ${error.message}`);
    process.exit(1);
  });
}

module.exports = { establishProductSession, requiredEnv, resolveNativeChrome };
