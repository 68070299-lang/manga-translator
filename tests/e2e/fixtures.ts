import fs from 'node:fs';
import path from 'node:path';
import {
  type BrowserContext,
  test as base,
  type ConsoleMessage,
  chromium,
  expect,
  type Page,
  type Worker,
} from '@playwright/test';

/** Unpacked extension built by `wxt build`. */
export const extensionPath = path.join(import.meta.dirname, '..', '..', 'dist', 'chrome-mv3');

let cachedExtensionId: string | null = null;
const EXTENSION_WORKER_TIMEOUT_MS = 10000;
const ALLOWED_CONSOLE_ERRORS = [
  {
    message: /^Failed to load resource: net::ERR_CONNECTION_REFUSED$/,
    url: /^http:\/\/(?:localhost|127\.0\.0\.1):8001\/health(?:$|[?#])/,
  },
  {
    message: /^Failed to load resource: net::ERR_TIMED_OUT$/,
    url: /^http:\/\/(?:localhost|127\.0\.0\.1):8001\/health(?:$|[?#])/,
  },
];

function isAllowedConsoleError(consoleMessage: ConsoleMessage): boolean {
  const message = consoleMessage.text();
  const url = consoleMessage.location().url;
  return ALLOWED_CONSOLE_ERRORS.some(
    (expected) => expected.message.test(message) && expected.url.test(url),
  );
}

async function getExtensionWorker(context: BrowserContext): Promise<Worker> {
  const [worker] = context.serviceWorkers();
  if (worker) {
    return worker;
  }

  const startedWorker = await context
    .waitForEvent('serviceworker', {
      timeout: EXTENSION_WORKER_TIMEOUT_MS,
    })
    .catch(() => null);

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

async function launchExtensionContext(): Promise<BrowserContext> {
  if (!fs.existsSync(path.join(extensionPath, 'manifest.json'))) {
    throw new Error(`Built extension not found at ${extensionPath}. Run \`npm run build\` first.`);
  }
  return chromium.launchPersistentContext('', {
    channel: 'chromium',
    headless: HEADLESS,
    args: [`--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`],
  });
}

interface Fixtures {
  isHeadless: boolean;
  extensionId: string;
}

export const test = base.extend<Fixtures>({
  isHeadless: HEADLESS,

  // biome-ignore lint/correctness/noEmptyPattern: Playwright requires a destructured first argument
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
    const browserErrors: string[] = [];

    page.on('pageerror', (error) => {
      browserErrors.push(`pageerror: ${error.message}`);
    });
    page.on('console', (message) => {
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
      const id = worker.url().split('/')[2];
      if (!id) throw new Error(`Cannot read extension id from worker URL: ${worker.url()}`);
      cachedExtensionId = id;
    }
    await use(cachedExtensionId);
  },
});

export { expect };

interface ManifestContentScript {
  js?: string[];
  css?: string[];
}

/**
 * Read the first content_scripts entry from the real (generated) manifest so
 * specs that inject the content script by hand cannot drift from what Chrome
 * injects.
 */
export function readManifestContentScripts(): { js: string[]; css: string[] } {
  const manifest = JSON.parse(
    fs.readFileSync(path.join(extensionPath, 'manifest.json'), 'utf8'),
  ) as {
    content_scripts: ManifestContentScript[];
  };
  const entry = manifest.content_scripts[0];
  if (!entry) throw new Error('Built manifest declares no content_scripts');
  return {
    js: (entry.js ?? []).map((file) => path.join(extensionPath, file)),
    css: (entry.css ?? []).map((file) => path.join(extensionPath, file)),
  };
}

/**
 * Inject the manifest content script list into a page, in manifest order.
 * Stylesheets go first, like Chrome. Pass { css: false } for specs whose
 * layout assertions were written without the content stylesheet.
 */
export async function injectManifestContentScripts(
  page: Page,
  { css = true }: { css?: boolean } = {},
): Promise<void> {
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
