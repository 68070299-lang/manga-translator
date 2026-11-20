import fs from 'node:fs';
import http from 'node:http';
import type { AddressInfo, Socket } from 'node:net';
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

export interface FixtureServer {
  url: string;
  close: () => Promise<void>;
}

/**
 * Serve one HTML fixture on an ephemeral local port and track its sockets so
 * the server can be closed cleanly between tests.
 */
export async function serveHtmlFixture(
  html: string,
  { host = '127.0.0.1', urlHost = host }: { host?: string; urlHost?: string } = {},
): Promise<FixtureServer> {
  const sockets = new Set<Socket>();
  const server = http.createServer((_request, response) => {
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    response.end(html);
  });

  server.on('connection', (socket) => {
    sockets.add(socket);
    socket.on('close', () => sockets.delete(socket));
  });

  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, host, resolve);
  });

  const address = server.address() as AddressInfo;
  return {
    url: `http://${urlHost}:${address.port}/`,
    close: async () => {
      for (const socket of sockets) socket.destroy();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

export interface E2eDefaultSettings {
  provider: string;
  apiKey: string;
  model: string;
  targetLang: string;
  autoTranslate: boolean;
  showProgress: boolean;
  localBackend: {
    url: string;
    detector: string;
    ocr: string;
    inpainter: string;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

/** Default extension settings shared by the specs that mock the worker. */
export const E2E_DEFAULT_SETTINGS: E2eDefaultSettings = {
  provider: 'openrouter',
  apiKey: '',
  model: '',
  targetLang: 'English',
  autoTranslate: false,
  showProgress: false,
  localBackend: {
    url: 'http://127.0.0.1:8001',
    detector: 'default',
    ocr: 'manga-ocr',
    inpainter: 'lama_large',
  },
};

export interface E2eChromeMockInit {
  defaultSettings: E2eDefaultSettings;
  settingsOverrides?: Record<string, unknown> & { localBackend?: Record<string, unknown> };
  translationDelayMs?: number;
  initialMode?: string;
  /** `counters` tracks numeric call/response counters; `records` keeps request records. */
  tracking: 'counters' | 'records';
  /** Include the BookWalker failure modes (error, timeout-error, broken-data-url). */
  failureModes?: boolean;
  /** Geometry of the generated translated image. */
  image: { width: number; height: number; textX: number; textY: number; fontSize: number };
}

/**
 * Page-side chrome/worker mock shared by the specs that inject the content
 * script by hand. Pass the serializable init to `page.addInitScript`.
 */
export function installE2eChromeMock(init: E2eChromeMockInit): void {
  type RuntimeListener = (
    message: unknown,
    sender: unknown,
    sendResponse: (response: unknown) => void,
  ) => void;
  interface TranslateRequestMessage {
    type: string;
    imageId?: string;
    base64: string;
    settings?: { targetLang?: string; apiKey?: string };
    forceRefresh?: boolean;
  }
  interface MockResponse {
    success: boolean;
    error?: string;
    result?: unknown;
  }
  type StorageListener = (changes: Record<string, unknown>, areaName: string) => void;
  interface TestWindow {
    __translationMode: string;
    __translationDelayMs: number;
    __translateCalls: number | Array<Record<string, unknown>>;
    __translateResponses: number;
    __activeTranslateRequests: number;
    __maxActiveTranslateRequests: number;
    __sentImageSizes: number[];
    __runtimeListenerCount: () => number;
    __setTranslationMode: (nextMode: string) => void;
    __setTranslationDelayMs: (delayMs: number) => void;
    __sendRuntimeMessage: (message: { type: string; [key: string]: unknown }) => Promise<unknown>;
    __setStoredSettings: (nextSettings: Record<string, unknown>) => void;
    __setStoredApiKeys: (nextApiKeys: Record<string, unknown> | undefined) => void;
    chrome: unknown;
  }

  const testWindow = window as unknown as TestWindow;
  const listeners: RuntimeListener[] = [];
  const storageChangeListeners: StorageListener[] = [];
  const defaultSettings = init.defaultSettings;
  const counters = init.tracking === 'counters';
  const storageState: { settings: Record<string, unknown>; apiKeys: unknown } = {
    settings: {
      ...defaultSettings,
      ...(init.settingsOverrides || {}),
      localBackend: {
        ...defaultSettings.localBackend,
        ...(init.settingsOverrides?.localBackend || {}),
      },
    },
    apiKeys: undefined,
  };

  function emitStorageChange(changes: Record<string, unknown>, areaName: string) {
    for (const listener of storageChangeListeners) listener(changes, areaName);
  }

  function makeTranslatedBase64(renderMode: string): string {
    const canvas = document.createElement('canvas');
    canvas.width = init.image.width;
    canvas.height = init.image.height;
    const context = canvas.getContext('2d') as CanvasRenderingContext2D;
    if (renderMode === 'black') {
      context.fillStyle = '#000000';
      context.fillRect(0, 0, canvas.width, canvas.height);
    } else {
      context.fillStyle = '#4ade80';
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.fillStyle = '#052e16';
      context.font = `bold ${init.image.fontSize}px sans-serif`;
      context.fillText('translated', init.image.textX, init.image.textY);
    }
    return canvas.toDataURL('image/png').split(',')[1] as string;
  }

  testWindow.__translationMode = init.initialMode ?? 'success';
  testWindow.__translationDelayMs = init.translationDelayMs ?? 0;
  testWindow.__translateCalls = counters ? 0 : [];
  testWindow.__translateResponses = 0;
  testWindow.__activeTranslateRequests = 0;
  testWindow.__maxActiveTranslateRequests = 0;
  testWindow.__sentImageSizes = [];
  testWindow.__runtimeListenerCount = () => listeners.length;
  testWindow.__setTranslationMode = (nextMode) => {
    testWindow.__translationMode = nextMode;
  };
  testWindow.__setTranslationDelayMs = (delayMs) => {
    testWindow.__translationDelayMs = delayMs;
  };
  testWindow.__sendRuntimeMessage = (message) =>
    new Promise((resolve) => {
      listeners[0]?.(message, {}, resolve);
    });
  testWindow.__setStoredSettings = (nextSettings) => {
    const oldValue = storageState.settings;
    storageState.settings = {
      ...defaultSettings,
      ...nextSettings,
      localBackend: {
        ...defaultSettings.localBackend,
        ...((nextSettings.localBackend as Record<string, unknown> | undefined) || {}),
      },
    };
    emitStorageChange({ settings: { oldValue, newValue: storageState.settings } }, 'sync');
  };
  testWindow.__setStoredApiKeys = (nextApiKeys) => {
    const oldValue = storageState.apiKeys;
    storageState.apiKeys = nextApiKeys;
    emitStorageChange({ apiKeys: { oldValue, newValue: nextApiKeys } }, 'local');
  };

  testWindow.chrome = {
    runtime: {
      onMessage: {
        addListener: (listener: RuntimeListener) => listeners.push(listener),
      },
      sendMessage: async (message: TranslateRequestMessage) => {
        if (message.type === 'CHECK_BACKEND') {
          return { success: true };
        }

        if (message.type === 'TRANSLATE_IMAGE') {
          if (counters) {
            testWindow.__translateCalls = (testWindow.__translateCalls as number) + 1;
            testWindow.__activeTranslateRequests += 1;
            testWindow.__maxActiveTranslateRequests = Math.max(
              testWindow.__maxActiveTranslateRequests,
              testWindow.__activeTranslateRequests,
            );
            testWindow.__sentImageSizes.push(message.base64.length);
          } else {
            (testWindow.__translateCalls as Array<Record<string, unknown>>).push({
              imageId: message.imageId,
              base64Length: message.base64.length,
              targetLang: message.settings?.targetLang,
              apiKey: message.settings?.apiKey || '',
              forceRefresh: Boolean(message.forceRefresh),
            });
          }

          const finishResponse = (response: MockResponse) => {
            if (counters) {
              testWindow.__translateResponses += 1;
              testWindow.__activeTranslateRequests -= 1;
            }
            return response;
          };

          if (testWindow.__translationDelayMs > 0) {
            await new Promise((resolve) => setTimeout(resolve, testWindow.__translationDelayMs));
          }

          if (init.failureModes) {
            if (testWindow.__translationMode === 'error') {
              return finishResponse({ success: false, error: 'backend failed' });
            }

            if (testWindow.__translationMode === 'timeout-error') {
              return finishResponse({
                success: false,
                error:
                  'Local backend request timed out after 180s. The image may be too large or the server is busy.',
              });
            }

            if (testWindow.__translationMode === 'broken-data-url') {
              return finishResponse({
                success: true,
                result: {
                  translatedImage: 'data:image/png;base64,not-a-real-image',
                  mimeType: 'image/png',
                  mode: 'local',
                },
              });
            }
          }

          const translatedImage = makeTranslatedBase64(testWindow.__translationMode);
          return finishResponse({
            success: true,
            result: {
              translatedImage,
              mimeType: 'image/png',
              mode: 'local',
            },
          });
        }

        return { success: true };
      },
    },
    storage: {
      sync: {
        get: async () => ({
          settings: storageState.settings,
        }),
      },
      local: {
        get: async (key: string) => {
          if (key === 'apiKeys') {
            return storageState.apiKeys === undefined ? {} : { apiKeys: storageState.apiKeys };
          }
          return {};
        },
      },
      onChanged: {
        addListener: (listener: StorageListener) => storageChangeListeners.push(listener),
      },
    },
  };
}
