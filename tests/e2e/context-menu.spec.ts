import http from 'node:http';
import type { AddressInfo, Socket } from 'node:net';
import { expect, type Page, test } from '@playwright/test';
import { injectManifestContentScripts } from './fixtures.ts';

interface TranslateCall {
  imageId?: string;
  base64Length: number;
  targetLang?: string;
  apiKey: string;
  forceRefresh: boolean;
}

type StoredSettingsInput = Record<string, unknown>;

/** Test hooks installed by installContentScriptChromeMock (runs in the page). */
type TestWindow = Window &
  typeof globalThis & {
    __translateCalls: TranslateCall[];
    __translationDelayMs: number;
    __runtimeListenerCount: () => number;
    __sendRuntimeMessage: (message: { type: string; [key: string]: unknown }) => Promise<unknown>;
    __setStoredSettings: (nextSettings: StoredSettingsInput) => void;
    __setStoredApiKeys: (nextApiKeys: Record<string, unknown> | undefined) => void;
    chrome: unknown;
  };

async function createImageFixtureServer() {
  const sockets = new Set<Socket>();
  const server = http.createServer((_req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(`<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Context Menu Image Fixture</title>
  <style>
    body { margin: 0; padding: 32px; background: #f5f2ec; }
    .reader { display: flex; gap: 24px; align-items: flex-start; }
    .panel-frame { position: relative; width: 320px; height: 320px; }
    .panel-frame img { display: block; width: 320px; height: 320px; object-fit: cover; }
  </style>
</head>
<body>
  <main class="reader">
    <div class="panel-frame"><img id="page-one" alt="Page one"></div>
    <div class="panel-frame"><img id="page-two" alt="Page two"></div>
  </main>
  <script>
    function makePanel(color, label) {
      const canvas = document.createElement('canvas');
      canvas.width = 320;
      canvas.height = 320;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = color;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(42, 112, 236, 84);
      ctx.fillStyle = '#111111';
      ctx.font = 'bold 38px sans-serif';
      ctx.fillText(label, 86, 166);
      return canvas.toDataURL('image/png');
    }

    document.getElementById('page-one').src = makePanel('#d95f5f', 'one');
    document.getElementById('page-two').src = makePanel('#5f7ed9', 'two');
  </script>
</body>
</html>`);
  });

  server.on('connection', (socket) => {
    sockets.add(socket);
    socket.on('close', () => sockets.delete(socket));
  });

  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });

  const address = server.address() as AddressInfo;
  return {
    url: `http://127.0.0.1:${address.port}/`,
    close: async () => {
      for (const socket of sockets) socket.destroy();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

async function installContentScriptChromeMock(page: Page) {
  await page.addInitScript(() => {
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
    type StorageListener = (changes: Record<string, unknown>, areaName: string) => void;
    const listeners: RuntimeListener[] = [];
    const storageChangeListeners: StorageListener[] = [];
    const defaultSettings = {
      provider: 'openrouter',
      apiKey: '',
      model: '',
      targetLang: 'English',
      autoTranslate: false,
      showProgress: false,
      pipelineMode: 'local',
      localBackend: {
        url: 'http://127.0.0.1:8001',
        detector: 'default',
        ocr: 'manga-ocr',
        inpainter: 'lama_large',
        translator: 'none',
      },
    };
    const storageState: { settings: Record<string, unknown>; apiKeys: unknown } = {
      settings: defaultSettings,
      apiKeys: undefined,
    };

    function emitStorageChange(changes: Record<string, unknown>, areaName: string) {
      for (const listener of storageChangeListeners) listener(changes, areaName);
    }

    function makeTranslatedBase64() {
      const canvas = document.createElement('canvas');
      canvas.width = 320;
      canvas.height = 320;
      const ctx = canvas.getContext('2d') as CanvasRenderingContext2D;
      ctx.fillStyle = '#4ade80';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = '#052e16';
      ctx.font = 'bold 34px sans-serif';
      ctx.fillText('translated', 70, 165);
      return canvas.toDataURL('image/png').split(',')[1];
    }

    (window as TestWindow).__translateCalls = [];
    (window as TestWindow).__translationDelayMs = 0;
    (window as TestWindow).__runtimeListenerCount = () => listeners.length;
    (window as TestWindow).__sendRuntimeMessage = (message) =>
      new Promise((resolve) => {
        listeners[0]?.(message, {}, resolve);
      });
    (window as TestWindow).__setStoredSettings = (nextSettings) => {
      const oldValue = storageState.settings;
      storageState.settings = {
        ...defaultSettings,
        ...nextSettings,
        localBackend: {
          ...defaultSettings.localBackend,
          ...(nextSettings?.localBackend || {}),
        },
      };
      emitStorageChange({ settings: { oldValue, newValue: storageState.settings } }, 'sync');
    };
    (window as TestWindow).__setStoredApiKeys = (nextApiKeys) => {
      const oldValue = storageState.apiKeys;
      storageState.apiKeys = nextApiKeys;
      emitStorageChange({ apiKeys: { oldValue, newValue: nextApiKeys } }, 'local');
    };

    (window as TestWindow).chrome = {
      runtime: {
        onMessage: {
          addListener: (listener: RuntimeListener) => listeners.push(listener),
        },
        sendMessage: async (message: TranslateRequestMessage) => {
          if (message.type === 'CHECK_BACKEND') {
            return { success: true };
          }

          if (message.type === 'TRANSLATE_IMAGE') {
            (window as TestWindow).__translateCalls.push({
              imageId: message.imageId,
              base64Length: message.base64.length,
              targetLang: message.settings?.targetLang,
              apiKey: message.settings?.apiKey || '',
              forceRefresh: Boolean(message.forceRefresh),
            });

            if ((window as TestWindow).__translationDelayMs > 0) {
              await new Promise((resolve) =>
                setTimeout(resolve, (window as TestWindow).__translationDelayMs),
              );
            }

            return {
              success: true,
              result: {
                translatedImage: makeTranslatedBase64(),
                mimeType: 'image/png',
                mode: 'local',
              },
            };
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
  });
}

async function loadFixtureWithContentScript(page: Page) {
  const server = await createImageFixtureServer();
  await installContentScriptChromeMock(page);
  await page.goto(server.url);
  await page.waitForFunction(() =>
    Array.from(document.images).every((img) => img.complete && img.naturalWidth > 0),
  );
  await injectManifestContentScripts(page);
  await page.waitForFunction(() => (window as TestWindow).__runtimeListenerCount?.() > 0);
  return server;
}

test('context-menu image translation processes only the selected image source', async ({
  page,
}) => {
  const server = await loadFixtureWithContentScript(page);

  try {
    const selectedSrc = await page
      .locator('#page-two')
      .evaluate((img) => (img as HTMLImageElement).currentSrc || (img as HTMLImageElement).src);
    const response = await page.evaluate((src) => {
      return (window as TestWindow).__sendRuntimeMessage({ type: 'TRANSLATE_IMAGE_BY_SRC', src });
    }, selectedSrc);

    expect(response).toEqual({ success: true });

    await expect
      .poll(() =>
        page.evaluate(() => ({
          calls: (window as TestWindow).__translateCalls.length,
          pageOneTranslated: document
            .getElementById('page-one')!
            .hasAttribute('data-manga-translated'),
          pageTwoTranslated: document
            .getElementById('page-two')!
            .hasAttribute('data-manga-translated'),
          overlays: document.querySelectorAll('.manga-translated-img-overlay').length,
        })),
      )
      .toEqual({
        calls: 1,
        pageOneTranslated: false,
        pageTwoTranslated: true,
        overlays: 1,
      });
  } finally {
    await server.close();
  }
});

test('content settings changes preserve and clear local API keys for translation requests', async ({
  page,
}) => {
  const server = await loadFixtureWithContentScript(page);

  try {
    const pageOneSrc = await page
      .locator('#page-one')
      .evaluate((img) => (img as HTMLImageElement).currentSrc || (img as HTMLImageElement).src);
    const pageTwoSrc = await page
      .locator('#page-two')
      .evaluate((img) => (img as HTMLImageElement).currentSrc || (img as HTMLImageElement).src);

    await page.evaluate(() => {
      (window as TestWindow).__setStoredSettings({
        pipelineMode: 'cloud',
        provider: 'openrouter',
        targetLang: 'English',
        localBackend: { translator: 'none' },
      });
      (window as TestWindow).__setStoredApiKeys({ apiKey: 'sk-live' });
    });
    await page.waitForTimeout(50);

    await page.evaluate((src) => {
      return (window as TestWindow).__sendRuntimeMessage({ type: 'TRANSLATE_IMAGE_BY_SRC', src });
    }, pageOneSrc);

    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateCalls[0]))
      .toEqual(
        expect.objectContaining({
          targetLang: 'English',
          apiKey: 'sk-live',
        }),
      );

    await page.evaluate(() => {
      (window as TestWindow).__setStoredSettings({
        pipelineMode: 'cloud',
        provider: 'openrouter',
        targetLang: 'Thai',
        localBackend: { translator: 'none' },
      });
    });
    await page.waitForTimeout(50);

    await page.evaluate((src) => {
      return (window as TestWindow).__sendRuntimeMessage({ type: 'TRANSLATE_IMAGE_BY_SRC', src });
    }, pageTwoSrc);

    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateCalls[1]))
      .toEqual(
        expect.objectContaining({
          targetLang: 'Thai',
          apiKey: 'sk-live',
        }),
      );

    await page.evaluate(() =>
      (window as TestWindow).__sendRuntimeMessage({ type: 'CLEAR_PAGE_TRANSLATIONS' }),
    );
    await page.evaluate(() => {
      (window as TestWindow).__setStoredSettings({
        pipelineMode: 'cloud',
        provider: 'openrouter',
        targetLang: 'Korean',
        localBackend: { translator: 'none' },
      });
      (window as TestWindow).__setStoredApiKeys(undefined);
    });
    await page.waitForTimeout(50);

    await page.evaluate((src) => {
      return (window as TestWindow).__sendRuntimeMessage({ type: 'TRANSLATE_IMAGE_BY_SRC', src });
    }, pageOneSrc);

    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateCalls[2]))
      .toEqual(
        expect.objectContaining({
          targetLang: 'Korean',
          apiKey: '',
        }),
      );
  } finally {
    await server.close();
  }
});

test('reader controls translate visible images and clear page translations', async ({ page }) => {
  const server = await loadFixtureWithContentScript(page);

  try {
    await page.evaluate(() => {
      const reader = document.querySelector('.reader') as HTMLElement;
      reader.style.display = 'block';
      const secondFrame = document.getElementById('page-two')!.parentElement as HTMLElement;
      secondFrame.style.marginTop = '900px';
    });

    await page.locator('[data-manga-action="visible"]').click();

    await expect
      .poll(() =>
        page.evaluate(() => ({
          calls: (window as TestWindow).__translateCalls.length,
          pageOneTranslated: document
            .getElementById('page-one')!
            .hasAttribute('data-manga-translated'),
          pageTwoTranslated: document
            .getElementById('page-two')!
            .hasAttribute('data-manga-translated'),
          overlays: document.querySelectorAll('.manga-translated-img-overlay').length,
        })),
      )
      .toEqual({
        calls: 1,
        pageOneTranslated: true,
        pageTwoTranslated: false,
        overlays: 1,
      });

    await page.locator('[data-manga-action="clear"]').click();

    await expect
      .poll(() =>
        page.evaluate(() => ({
          pageOneTranslated: document
            .getElementById('page-one')!
            .hasAttribute('data-manga-translated'),
          pageTwoTranslated: document
            .getElementById('page-two')!
            .hasAttribute('data-manga-translated'),
          overlays: document.querySelectorAll(
            '.manga-translated-img-overlay, .manga-translate-overlay, .manga-composite-overlay',
          ).length,
          progressBars: document.querySelectorAll('.manga-image-progress, #manga-global-progress')
            .length,
        })),
      )
      .toEqual({
        pageOneTranslated: false,
        pageTwoTranslated: false,
        overlays: 0,
        progressBars: 0,
      });
  } finally {
    await server.close();
  }
});

test('reader stop control stops queued page translation after the current image', async ({
  page,
}) => {
  const server = await loadFixtureWithContentScript(page);

  try {
    await page.evaluate(() => {
      (window as TestWindow).__translationDelayMs = 500;
    });

    await page.locator('[data-manga-action="all"]').click();
    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateCalls.length))
      .toBe(1);

    await page.locator('[data-manga-action="stop"]').click();

    await expect
      .poll(
        () =>
          page.evaluate(() => ({
            calls: (window as TestWindow).__translateCalls.length,
            pageOneTranslated: document
              .getElementById('page-one')!
              .hasAttribute('data-manga-translated'),
            pageTwoTranslated: document
              .getElementById('page-two')!
              .hasAttribute('data-manga-translated'),
            progressText:
              document
                .querySelector('#manga-global-progress')
                ?.shadowRoot?.querySelector('.manga-progress-text')?.textContent || '',
          })),
        { timeout: 4000 },
      )
      .toEqual({
        calls: 1,
        pageOneTranslated: true,
        pageTwoTranslated: false,
        progressText: 'Stopped after 1/2 images',
      });

    await page.waitForTimeout(800);
    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateCalls.length))
      .toBe(1);
  } finally {
    await server.close();
  }
});

test('hovering an image does not create a floating panel toolbar', async ({ page }) => {
  const server = await loadFixtureWithContentScript(page);

  try {
    await page.locator('#page-two').hover();
    await expect(page.locator('#manga-reader-controls')).toBeVisible();
    await expect(page.locator('#manga-panel-controls')).toHaveCount(0);
    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateCalls.length))
      .toBe(0);
  } finally {
    await server.close();
  }
});
