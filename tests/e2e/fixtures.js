const fs = require('fs');
const path = require('path');
const { chromium, test: base, expect } = require('@playwright/test');

const extensionPath = path.join(__dirname, '..', '..', 'extension');
let cachedExtensionId = null;
const EXTENSION_WORKER_TIMEOUT_MS = 10000;
const ALLOWED_CONSOLE_ERRORS = [
  {
    message: /^Failed to load resource: net::ERR_CONNECTION_REFUSED$/,
    url: /^http:\/\/(?:localhost|127\.0\.0\.1):8001\/health(?:$|[?#])/
  },
  {
    message: /^Failed to load resource: net::ERR_TIMED_OUT$/,
    url: /^http:\/\/(?:localhost|127\.0\.0\.1):8001\/health(?:$|[?#])/
  }
];

function isAllowedConsoleError(consoleMessage) {
  const message = consoleMessage.text();
  const url = consoleMessage.location().url;
  return ALLOWED_CONSOLE_ERRORS.some(expected =>
    expected.message.test(message) && expected.url.test(url)
  );
}


async function getExtensionWorker(context) {
  const [worker] = context.serviceWorkers();
  if (worker) {
    return worker;
  }

  const startedWorker = await context.waitForEvent('serviceworker', {
    timeout: EXTENSION_WORKER_TIMEOUT_MS
  }).catch(() => null);

  if (startedWorker) {
    return startedWorker;
  }

  const [lateWorker] = context.serviceWorkers();
  if (lateWorker) {
    return lateWorker;
  }

  throw new Error(`Extension service worker did not start within ${EXTENSION_WORKER_TIMEOUT_MS}ms`);
}

const HEADLESS = true;

async function launchExtensionContext() {
  return chromium.launchPersistentContext('', {
    channel: 'chromium',
    headless: HEADLESS,
    args: [
      `--disable-extensions-except=${extensionPath}`,
      `--load-extension=${extensionPath}`
    ]
  });
}

const test = base.extend({
  isHeadless: HEADLESS,

  // eslint-disable-next-line no-empty-pattern
  context: async ({}, use) => {
    let context = await launchExtensionContext();

    try {
      await getExtensionWorker(context);
    } catch (_error) {
      cachedExtensionId = null;
      await context.close();
      context = await launchExtensionContext();
      await getExtensionWorker(context);
    }

    try {
      await use(context);
    } finally {
      await context.close();
    }
  },

  page: async ({ context }, use) => {
    const page = await context.newPage();
    const browserErrors = [];

    page.on('pageerror', error => {
      browserErrors.push(`pageerror: ${error.message}`);
    });
    page.on('console', message => {
      if (message.type() === 'error' && !isAllowedConsoleError(message)) {
        browserErrors.push(`console.error: ${message.text()}`);
      }
    });

    try {
      await use(page);
      expect(browserErrors, 'Unexpected browser errors').toEqual([]);
    } finally {
      await page.close();
    }
  },

  extensionId: async ({ context }, use) => {
    if (!cachedExtensionId) {
      const worker = await getExtensionWorker(context);
      cachedExtensionId = worker.url().split('/')[2];
    }
    await use(cachedExtensionId);
  }
});

/**
 * Read the first content_scripts entry from the real manifest so specs that
 * inject the content script by hand cannot drift from what Chrome injects.
 */
function readManifestContentScripts() {
  const manifest = JSON.parse(
    fs.readFileSync(path.join(extensionPath, 'manifest.json'), 'utf8')
  );
  const entry = manifest.content_scripts[0];
  return {
    js: entry.js.map(file => path.join(extensionPath, file)),
    css: (entry.css || []).map(file => path.join(extensionPath, file))
  };
}

/**
 * Inject the manifest content script list into a page, in manifest order.
 * Stylesheets go first, like Chrome. Pass { css: false } for specs whose
 * layout assertions were written without content.css.
 */
async function injectManifestContentScripts(page, { css = true } = {}) {
  const scripts = readManifestContentScripts();
  if (css) {
    for (const stylePath of scripts.css) {
      await page.addStyleTag({ path: stylePath });
    }
  }
  for (const scriptPath of scripts.js) {
    await page.addScriptTag({ path: scriptPath });
  }
}

module.exports = { test, expect, readManifestContentScripts, injectManifestContentScripts };
