import http from 'node:http';
import type { AddressInfo, Socket } from 'node:net';
import { expect, type Locator, type Page, test } from '@playwright/test';
import { injectManifestContentScripts } from './fixtures.ts';

type TranslationMode = 'success' | 'black' | 'error' | 'timeout-error' | 'broken-data-url';

interface MockOverrides {
  translationDelayMs?: number;
}

type SettingsOverrides = Record<string, unknown> & { localBackend?: Record<string, unknown> };

/** Test hooks installed by installChromeMock (runs in the page). */
type TestWindow = Window &
  typeof globalThis & {
    __translationMode: string;
    __translationDelayMs: number;
    __translateCalls: number;
    __translateResponses: number;
    __activeTranslateRequests: number;
    __maxActiveTranslateRequests: number;
    __sentImageSizes: number[];
    __runtimeListenerCount: () => number;
    __setTranslationMode: (mode: string) => void;
    __setTranslationDelayMs: (delayMs: number) => void;
    __sendRuntimeMessage: (message: { type: string; [key: string]: unknown }) => Promise<unknown>;
    drawBookWalkerPage: (color: string, label: string) => void;
    drawBookWalkerCanvas: (canvasId: string, color: string, label: string) => void;
    __dumpState: () => unknown;
    __startCanvasChurn: () => void;
    __stopCanvasChurn: () => void;
    __canvasChurnTimer?: ReturnType<typeof setInterval> | null;
    chrome: unknown;
  };

async function createBookWalkerFixtureServer() {
  const sockets = new Set<Socket>();
  const server = http.createServer((_req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(`<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>BookWalker Canvas Fixture</title>
  <style>
    html, body { margin: 0; width: 100%; height: 100%; overflow: hidden; background: #202124; }
    #renderer { position: relative; width: 100vw; height: 100vh; }
    #viewport0.currentScreen {
      position: absolute;
      left: 180px;
      top: 40px;
      width: 480px;
      height: 720px;
    }
    #viewport0 canvas {
      width: 480px;
      height: 720px;
      display: block;
    }
  </style>
</head>
<body>
  <div id="renderer">
    <div id="viewport0" class="currentScreen" style="visibility: visible;">
      <canvas id="bookwalker-page" data-shared-src="bookwalker-shared-link" width="480" height="720"></canvas>
    </div>
    <div id="viewport1" style="position: absolute; left: 1500px; top: 40px; width: 480px; height: 720px; visibility: visible;">
      <canvas id="bookwalker-page-2" data-shared-src="bookwalker-shared-link" width="480" height="720"></canvas>
    </div>
    <div id="viewport2" style="position: absolute; left: 2500px; top: 40px; width: 480px; height: 720px; visibility: visible;">
      <canvas id="bookwalker-page-3" data-shared-src="bookwalker-shared-link" width="480" height="720"></canvas>
    </div>
  </div>
  <script>
    window.drawBookWalkerCanvas = function(canvasId, color, label) {
      const canvas = document.getElementById(canvasId);
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = color;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = '#111111';
      ctx.fillRect(120, 180, 240, 120);
      ctx.fillStyle = '#ffffff';
      ctx.font = 'bold 44px sans-serif';
      ctx.fillText(label, 145, 250);
    };
    window.drawBookWalkerPage = function(color, label) {
      window.drawBookWalkerCanvas('bookwalker-page', color, label);
    };
    window.drawBookWalkerCanvas('bookwalker-page', '#f5f5f5', 'page 1');
    window.drawBookWalkerCanvas('bookwalker-page-2', '#fde68a', 'page 2');
    window.drawBookWalkerCanvas('bookwalker-page-3', '#bfdbfe', 'page 3');
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

async function installChromeMock(
  page: Page,
  initialMode: TranslationMode = 'success',
  settingsOverrides: SettingsOverrides = {},
  mockOverrides: MockOverrides = {},
) {
  await page.addInitScript(
    ({
      mode,
      settingsOverrides,
      mockOverrides,
    }: {
      mode: TranslationMode;
      settingsOverrides: SettingsOverrides;
      mockOverrides: MockOverrides;
    }) => {
      type RuntimeListener = (
        message: unknown,
        sender: unknown,
        sendResponse: (response: unknown) => void,
      ) => void;
      interface TranslateRequestMessage {
        type: string;
        base64: string;
      }
      interface MockResponse {
        success: boolean;
        error?: string;
        result?: unknown;
      }
      const listeners: RuntimeListener[] = [];

      function makeTranslatedBase64(width: number, height: number, renderMode: string) {
        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext('2d') as CanvasRenderingContext2D;

        if (renderMode === 'black') {
          ctx.fillStyle = '#000000';
          ctx.fillRect(0, 0, width, height);
        } else {
          ctx.fillStyle = '#4ade80';
          ctx.fillRect(0, 0, width, height);
          ctx.fillStyle = '#052e16';
          ctx.font = 'bold 40px sans-serif';
          ctx.fillText('translated', 110, 250);
        }

        return canvas.toDataURL('image/png').split(',')[1] as string;
      }

      (window as TestWindow).__translationMode = mode;
      (window as TestWindow).__translationDelayMs = mockOverrides.translationDelayMs || 0;
      (window as TestWindow).__translateCalls = 0;
      (window as TestWindow).__translateResponses = 0;
      (window as TestWindow).__activeTranslateRequests = 0;
      (window as TestWindow).__maxActiveTranslateRequests = 0;
      (window as TestWindow).__sentImageSizes = [];
      (window as TestWindow).__runtimeListenerCount = () => listeners.length;
      (window as TestWindow).__setTranslationMode = (nextMode: string) => {
        (window as TestWindow).__translationMode = nextMode;
      };
      (window as TestWindow).__setTranslationDelayMs = (delayMs: number) => {
        (window as TestWindow).__translationDelayMs = delayMs;
      };
      (window as TestWindow).__sendRuntimeMessage = (message) =>
        new Promise((resolve) => {
          listeners[0]?.(message, {}, resolve);
        });

      function getMockSettings() {
        const defaultSettings = {
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

        return {
          ...defaultSettings,
          ...settingsOverrides,
          localBackend: {
            ...defaultSettings.localBackend,
            ...(settingsOverrides.localBackend || {}),
          },
        };
      }

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
              (window as TestWindow).__translateCalls += 1;
              (window as TestWindow).__activeTranslateRequests += 1;
              (window as TestWindow).__maxActiveTranslateRequests = Math.max(
                (window as TestWindow).__maxActiveTranslateRequests,
                (window as TestWindow).__activeTranslateRequests,
              );
              (window as TestWindow).__sentImageSizes.push(message.base64.length);

              const finishResponse = (response: MockResponse) => {
                (window as TestWindow).__translateResponses += 1;
                (window as TestWindow).__activeTranslateRequests -= 1;
                return response;
              };

              if ((window as TestWindow).__translationDelayMs > 0) {
                await new Promise((resolve) =>
                  setTimeout(resolve, (window as TestWindow).__translationDelayMs),
                );
              }

              if ((window as TestWindow).__translationMode === 'error') {
                return finishResponse({ success: false, error: 'backend failed' });
              }

              if ((window as TestWindow).__translationMode === 'timeout-error') {
                return finishResponse({
                  success: false,
                  error:
                    'Local backend request timed out after 180s. The image may be too large or the server is busy.',
                });
              }

              if ((window as TestWindow).__translationMode === 'broken-data-url') {
                return finishResponse({
                  success: true,
                  result: {
                    translatedImage: 'data:image/png;base64,not-a-real-image',
                    mimeType: 'image/png',
                    mode: 'local',
                  },
                });
              }

              const translatedImage = makeTranslatedBase64(
                480,
                720,
                (window as TestWindow).__translationMode,
              );
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
              settings: getMockSettings(),
            }),
          },
          local: {
            get: async () => ({}),
          },
          onChanged: {
            addListener: () => {},
          },
        },
      };
    },
    { mode: initialMode, settingsOverrides, mockOverrides },
  );
}

async function loadFixtureWithContentScript(
  page: Page,
  initialMode: TranslationMode = 'success',
  settingsOverrides: SettingsOverrides = {},
  mockOverrides: MockOverrides = {},
) {
  const server = await createBookWalkerFixtureServer();
  await installChromeMock(page, initialMode, settingsOverrides, mockOverrides);
  await page.goto(server.url);
  await injectManifestContentScripts(page, { css: false });
  await page.waitForFunction(() => (window as TestWindow).__runtimeListenerCount?.() > 0);
  return server;
}

async function requestTranslatePage(page: Page) {
  await page.evaluate(() =>
    (window as TestWindow).__sendRuntimeMessage({ type: 'TRANSLATE_PAGE' }),
  );
}

async function showBookWalkerViewport(page: Page, viewportIndex: number) {
  await page.evaluate((index: number) => {
    for (const viewport of document.querySelectorAll<HTMLElement>('[id^="viewport"]')) {
      viewport.classList.remove('currentScreen');
      Object.assign(viewport.style, {
        left: '1500px',
        zIndex: '1',
      });
    }

    const viewport = document.getElementById(`viewport${index}`) as HTMLElement;
    viewport.classList.add('currentScreen');
    Object.assign(viewport.style, {
      position: 'absolute',
      left: '180px',
      top: '40px',
      width: '480px',
      height: '720px',
      visibility: 'visible',
      zIndex: '2',
    });

    window.dispatchEvent(new Event('scroll'));
  }, viewportIndex);
}

async function getBookWalkerProgressText(page: Page, viewportIndex: number) {
  return page.evaluate(
    (index: number) =>
      document.querySelector(`#viewport${index} .manga-image-progress`)?.shadowRoot?.textContent ||
      '',
    viewportIndex,
  );
}

async function getBookWalkerOverlayState(page: Page, viewportIndex: number) {
  return page.evaluate((index: number) => {
    const canvas = document.querySelector(`#viewport${index} canvas`);
    return {
      translated: Boolean(canvas?.hasAttribute('data-manga-translated')),
      processing: Boolean(canvas?.hasAttribute('data-manga-processing')),
      queueState: canvas?.getAttribute('data-manga-queue-state') || '',
      queueAhead: canvas?.getAttribute('data-manga-queue-ahead') || '',
      overlayCount: document.querySelectorAll(`#viewport${index} .manga-canvas-overlay`).length,
      progressText:
        document.querySelector(`#viewport${index} .manga-image-progress`)?.shadowRoot
          ?.textContent || '',
    };
  }, viewportIndex);
}

async function expectSingleVisibleCanvasOverlay(page: Page): Promise<Locator> {
  const overlay = page.locator('.manga-canvas-overlay');
  await expect(overlay).toHaveCount(1);
  await expect(overlay).toBeVisible();
  return overlay;
}

async function getCanvasOverlayState(page: Page) {
  return page.evaluate(() => {
    const canvas = document.getElementById('bookwalker-page') as HTMLCanvasElement;
    const overlayHost = document.querySelector('.manga-canvas-overlay') as HTMLElement;
    const overlayContent = overlayHost?.shadowRoot?.querySelector<HTMLImageElement | SVGSVGElement>(
      'img, svg',
    );
    const canvasRect = canvas.getBoundingClientRect();
    const overlayRect = overlayHost?.getBoundingClientRect();

    return {
      translated: canvas.hasAttribute('data-manga-translated'),
      processing: canvas.hasAttribute('data-manga-processing'),
      sourceSignature: canvas.getAttribute('data-manga-source-signature'),
      translationKey: canvas.dataset.mangaCanvasTranslationKey || '',
      translatedSrc: canvas.dataset.mangaTranslatedSrc || '',
      translatedUrl: canvas.dataset.mangaTranslatedUrl || '',
      overlayCount: document.querySelectorAll('.manga-canvas-overlay').length,
      wrapperCount: document.querySelectorAll('.manga-translate-wrapper').length,
      canvasParentId: canvas.parentElement?.id || '',
      overlayParentId: overlayHost?.parentElement?.id || '',
      overlayTag: overlayContent?.tagName || '',
      overlayComplete: Boolean(
        overlayContent && 'complete' in overlayContent && overlayContent.complete,
      ),
      overlayDisplay: overlayHost ? getComputedStyle(overlayHost).display : null,
      naturalWidth: (overlayContent as HTMLImageElement | null)?.naturalWidth || 0,
      naturalHeight: (overlayContent as HTMLImageElement | null)?.naturalHeight || 0,
      canvasRect: {
        left: canvasRect.left,
        top: canvasRect.top,
        width: canvasRect.width,
        height: canvasRect.height,
      },
      overlayRect: overlayRect
        ? {
            left: overlayRect.left,
            top: overlayRect.top,
            width: overlayRect.width,
            height: overlayRect.height,
          }
        : null,
      calls: (window as TestWindow).__translateCalls,
      progressText: document.querySelector('.manga-image-progress')?.shadowRoot?.textContent || '',
    };
  });
}

async function sampleOverlayPixel(
  page: Page,
  x = 10,
  y = 10,
): Promise<[number, number, number, number]> {
  return page.evaluate(
    ({ x, y }: { x: number; y: number }) => {
      const overlayImg = document
        .querySelector('.manga-canvas-overlay')
        ?.shadowRoot?.querySelector('img') as HTMLImageElement;
      const sample = document.createElement('canvas');
      sample.width = 1;
      sample.height = 1;
      const ctx = sample.getContext('2d', { willReadFrequently: true }) as CanvasRenderingContext2D;
      ctx.drawImage(overlayImg, x, y, 1, 1, 0, 0, 1, 1);
      return Array.from(ctx.getImageData(0, 0, 1, 1).data) as [number, number, number, number];
    },
    { x, y },
  );
}

type OverlayState = Awaited<ReturnType<typeof getCanvasOverlayState>>;

function expectOverlayAligned(state: OverlayState) {
  expect(Math.abs(state.canvasRect.left - state.overlayRect!.left)).toBeLessThan(1);
  expect(Math.abs(state.canvasRect.top - state.overlayRect!.top)).toBeLessThan(1);
  expect(Math.abs(state.canvasRect.width - state.overlayRect!.width)).toBeLessThan(1);
  expect(Math.abs(state.canvasRect.height - state.overlayRect!.height)).toBeLessThan(1);
}

test('canvas translation applies backend image output and does not stack overlays', async ({
  page,
}) => {
  const server = await loadFixtureWithContentScript(page);

  try {
    await requestTranslatePage(page);
    await expectSingleVisibleCanvasOverlay(page);

    const firstState = await getCanvasOverlayState(page);
    expect(firstState.translated).toBe(true);
    expect(firstState.processing).toBe(false);
    expect(firstState.sourceSignature).toBeTruthy();
    expect(firstState.translatedSrc).toMatch(/^blob:/);
    expect(firstState.translatedUrl).toMatch(/^blob:/);
    expect(firstState.overlayComplete).toBe(true);
    expect(firstState.naturalWidth).toBe(480);
    expect(firstState.naturalHeight).toBe(720);
    expect(firstState.calls).toBe(1);
    expectOverlayAligned(firstState);

    const [r, g, b, a] = await sampleOverlayPixel(page);
    expect(g).toBeGreaterThan(r);
    expect(g).toBeGreaterThan(b);
    expect(a).toBe(255);

    await requestTranslatePage(page);
    await page.waitForTimeout(250);
    await expect(page.locator('.manga-canvas-overlay')).toHaveCount(1);
    await expect.poll(() => page.evaluate(() => (window as TestWindow).__translateCalls)).toBe(1);
  } finally {
    await server.close();
  }
});

test('canvas translation clears stale output when the source canvas changes', async ({ page }) => {
  const server = await loadFixtureWithContentScript(page);

  try {
    await requestTranslatePage(page);
    await expectSingleVisibleCanvasOverlay(page);

    await page.evaluate(() => (window as TestWindow).drawBookWalkerPage('#fde68a', 'page 2'));
    await page.waitForFunction(
      () => {
        const canvas = document.getElementById('bookwalker-page') as HTMLCanvasElement;
        return (
          !canvas.hasAttribute('data-manga-translated') &&
          !canvas.hasAttribute('data-manga-processing') &&
          document.querySelectorAll('.manga-canvas-overlay').length === 0
        );
      },
      null,
      { timeout: 3000 },
    );

    await requestTranslatePage(page);
    await expectSingleVisibleCanvasOverlay(page);
    await expect.poll(() => page.evaluate(() => (window as TestWindow).__translateCalls)).toBe(2);
  } finally {
    await server.close();
  }
});

test('canvas translation is restored when returning to a previously translated BookWalker page', async ({
  page,
}) => {
  const server = await loadFixtureWithContentScript(page);

  try {
    await requestTranslatePage(page);
    await expectSingleVisibleCanvasOverlay(page);
    await expect.poll(() => page.evaluate(() => (window as TestWindow).__translateCalls)).toBe(1);

    await page.evaluate(() => (window as TestWindow).drawBookWalkerPage('#fde68a', 'page 2'));
    await page.waitForFunction(
      () => {
        const canvas = document.getElementById('bookwalker-page') as HTMLCanvasElement;
        return (
          !canvas.hasAttribute('data-manga-translated') &&
          !canvas.hasAttribute('data-manga-processing') &&
          document.querySelectorAll('.manga-canvas-overlay').length === 0
        );
      },
      null,
      { timeout: 3000 },
    );

    await page.evaluate(() => (window as TestWindow).drawBookWalkerPage('#f5f5f5', 'page 1'));
    await expectSingleVisibleCanvasOverlay(page);

    const state = await getCanvasOverlayState(page);
    expect(state.translated).toBe(true);
    expect(state.calls).toBe(1);
    expectOverlayAligned(state);
  } finally {
    await server.close();
  }
});

test('five translated pages restore in reverse from exact source memory with only five requests', async ({
  page,
}) => {
  const server = await loadFixtureWithContentScript(page);
  const pages = [
    { number: 1, color: '#f5f5f5' },
    { number: 2, color: '#fde68a' },
    { number: 3, color: '#bfdbfe' },
    { number: 4, color: '#c4b5fd' },
    { number: 5, color: '#fbcfe8' },
  ];

  try {
    for (const pageData of pages) {
      if (pageData.number > 1) {
        await page.evaluate(({ color, number }) => {
          (window as TestWindow).drawBookWalkerPage(color, `page ${number}`);
        }, pageData);
        await expect
          .poll(
            async () => {
              const state = await getCanvasOverlayState(page);
              return {
                translated: state.translated,
                processing: state.processing,
                overlayCount: state.overlayCount,
              };
            },
            { timeout: 3000 },
          )
          .toEqual({
            translated: false,
            processing: false,
            overlayCount: 0,
          });
      }

      await requestTranslatePage(page);
      await expectSingleVisibleCanvasOverlay(page);
      await expect
        .poll(() => page.evaluate(() => (window as TestWindow).__translateCalls))
        .toBe(pageData.number);
    }

    for (const pageData of [...pages].reverse()) {
      await page.evaluate(({ color, number }) => {
        (window as TestWindow).drawBookWalkerPage(color, `page ${number}`);
      }, pageData);
      await expect
        .poll(
          async () => {
            const state = await getCanvasOverlayState(page);
            return {
              translated: state.translated,
              processing: state.processing,
              overlayCount: state.overlayCount,
              calls: state.calls,
            };
          },
          { timeout: 3000 },
        )
        .toEqual({
          translated: true,
          processing: false,
          overlayCount: 1,
          calls: 5,
        });
    }
  } finally {
    await server.close();
  }
});

test('canvas translation result is ignored if BookWalker reuses the canvas before it returns', async ({
  page,
}) => {
  const server = await loadFixtureWithContentScript(page);

  try {
    await page.evaluate(() => (window as TestWindow).__setTranslationDelayMs(300));
    await requestTranslatePage(page);

    await expect.poll(() => page.evaluate(() => (window as TestWindow).__translateCalls)).toBe(1);
    await page.evaluate(() => (window as TestWindow).drawBookWalkerPage('#fde68a', 'page 2'));

    await expect
      .poll(
        async () => {
          const state = await getCanvasOverlayState(page);
          return {
            translated: state.translated,
            processing: state.processing,
            overlayCount: state.overlayCount,
            calls: state.calls,
          };
        },
        { timeout: 3000 },
      )
      .toEqual({
        translated: false,
        processing: false,
        overlayCount: 0,
        calls: 1,
      });

    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateResponses))
      .toBe(1);
    await page.evaluate(() => (window as TestWindow).__setTranslationDelayMs(0));
    await requestTranslatePage(page);
    await expectSingleVisibleCanvasOverlay(page);
    await expect.poll(() => page.evaluate(() => (window as TestWindow).__translateCalls)).toBe(2);
  } finally {
    await server.close();
  }
});

test('BookWalker auto translate queues three same-source pages with exact visible status labels', async ({
  page,
}) => {
  const server = await loadFixtureWithContentScript(
    page,
    'success',
    { autoTranslate: true, showProgress: true },
    { translationDelayMs: 3000 },
  );

  try {
    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateCalls), { timeout: 3000 })
      .toBe(1);
    await expect
      .poll(() => getBookWalkerProgressText(page, 0), { timeout: 3000 })
      .toBe('wait_for translate');
    await showBookWalkerViewport(page, 1);
    await expect
      .poll(() => getBookWalkerProgressText(page, 1), { timeout: 3000 })
      .toBe('load_img in queue1');
    await showBookWalkerViewport(page, 2);
    await expect
      .poll(() => getBookWalkerProgressText(page, 2), { timeout: 3000 })
      .toBe('load_img in queue2');
    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateResponses), {
        timeout: 4000,
      })
      .toBe(1);
    await expect
      .poll(() => getBookWalkerProgressText(page, 2), { timeout: 3000 })
      .toBe('load_img in queue1');
    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateResponses), {
        timeout: 12000,
      })
      .toBe(3);
    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__maxActiveTranslateRequests))
      .toBe(1);

    for (const viewportIndex of [0, 1, 2]) {
      await showBookWalkerViewport(page, viewportIndex);
      await expect
        .poll(() => getBookWalkerOverlayState(page, viewportIndex), { timeout: 3000 })
        .toMatchObject({
          translated: true,
          overlayCount: 1,
        });
    }
  } finally {
    await server.close();
  }
});

test('BookWalker page switch clears translating badge and does not retry stale canvas', async ({
  page,
}) => {
  const server = await loadFixtureWithContentScript(page, 'success', { showProgress: true });

  try {
    await page.evaluate(() => (window as TestWindow).__setTranslationDelayMs(1200));
    await requestTranslatePage(page);

    await expect.poll(() => page.evaluate(() => (window as TestWindow).__translateCalls)).toBe(1);
    await expect
      .poll(
        () =>
          page.evaluate(
            () => document.querySelector('.manga-image-progress')?.shadowRoot?.textContent || '',
          ),
        { timeout: 3000 },
      )
      .toContain('wait_for translate');

    await page.evaluate(() => {
      const firstViewport = document.getElementById('viewport0') as HTMLElement;
      Object.assign(firstViewport.style, {
        position: 'absolute',
        left: '180px',
        top: '40px',
        width: '480px',
        height: '720px',
        visibility: 'visible',
        zIndex: '1',
      });
      firstViewport.classList.remove('currentScreen');

      const secondViewport = document.createElement('div');
      secondViewport.id = 'viewport1';
      secondViewport.className = 'currentScreen';
      Object.assign(secondViewport.style, {
        position: 'absolute',
        left: '180px',
        top: '40px',
        width: '480px',
        height: '720px',
        visibility: 'visible',
        zIndex: '2',
      });

      const secondCanvas = document.createElement('canvas');
      secondCanvas.id = 'bookwalker-page-2';
      secondCanvas.width = 480;
      secondCanvas.height = 720;
      secondCanvas.style.cssText = 'width: 480px; height: 720px; display: block;';
      secondViewport.appendChild(secondCanvas);
      document.getElementById('renderer')!.appendChild(secondViewport);

      const ctx = secondCanvas.getContext('2d') as CanvasRenderingContext2D;
      ctx.fillStyle = '#fde68a';
      ctx.fillRect(0, 0, secondCanvas.width, secondCanvas.height);
      ctx.fillStyle = '#111111';
      ctx.fillRect(120, 180, 240, 120);
      ctx.fillStyle = '#ffffff';
      ctx.font = 'bold 44px sans-serif';
      ctx.fillText('page 2', 145, 250);
    });

    await expect
      .poll(
        async () =>
          page.evaluate(() => ({
            firstProcessing: document
              .getElementById('bookwalker-page')!
              .hasAttribute('data-manga-processing'),
            firstProgressCount: document.querySelectorAll('#viewport0 .manga-image-progress')
              .length,
            totalProgressCount: document.querySelectorAll('.manga-image-progress').length,
          })),
        { timeout: 3000 },
      )
      .toEqual({
        firstProcessing: false,
        firstProgressCount: 0,
        totalProgressCount: 0,
      });

    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateResponses), {
        timeout: 3000,
      })
      .toBe(1);
    await page.waitForTimeout(1300);
    await expect.poll(() => page.evaluate(() => (window as TestWindow).__translateCalls)).toBe(1);
  } finally {
    await server.close();
  }
});

test('canvas overlay is restored if the reader removes translated output', async ({ page }) => {
  const server = await loadFixtureWithContentScript(page);

  try {
    await requestTranslatePage(page);
    await expectSingleVisibleCanvasOverlay(page);

    await page.evaluate(() => document.querySelector('.manga-canvas-overlay')?.remove());
    await expect(page.locator('.manga-canvas-overlay')).toHaveCount(0);

    await expect
      .poll(() => page.evaluate(() => document.querySelectorAll('.manga-canvas-overlay').length), {
        timeout: 3000,
      })
      .toBe(1);

    const state = await getCanvasOverlayState(page);
    expect(state.translated).toBe(true);
    expect(state.calls).toBe(1);
    expectOverlayAligned(state);
  } finally {
    await server.close();
  }
});

test('canvas overlay follows a translated BookWalker canvas when the reader reparents it', async ({
  page,
}) => {
  const server = await loadFixtureWithContentScript(page);

  try {
    await requestTranslatePage(page);
    await expectSingleVisibleCanvasOverlay(page);

    await page.evaluate(() => {
      const renderer = document.getElementById('renderer') as HTMLElement;
      const currentViewport = document.getElementById('viewport0') as HTMLElement;
      currentViewport.classList.remove('currentScreen');

      const nextViewport = document.createElement('div');
      nextViewport.id = 'viewport1';
      nextViewport.className = 'currentScreen';
      Object.assign(nextViewport.style, {
        position: 'absolute',
        left: '680px',
        top: '40px',
        width: '480px',
        height: '720px',
        visibility: 'visible',
      });

      nextViewport.appendChild(document.getElementById('bookwalker-page') as HTMLElement);
      renderer.appendChild(nextViewport);
    });

    await expect
      .poll(
        async () => {
          const state = await getCanvasOverlayState(page);
          return {
            canvasParentId: state.canvasParentId,
            overlayParentId: state.overlayParentId,
            translated: state.translated,
            overlayCount: state.overlayCount,
            overlayDisplay: state.overlayDisplay,
          };
        },
        { timeout: 3000 },
      )
      .toEqual({
        canvasParentId: 'viewport1',
        overlayParentId: 'viewport1',
        translated: true,
        overlayCount: 1,
        overlayDisplay: 'block',
      });

    const state = await getCanvasOverlayState(page);
    expectOverlayAligned(state);
  } finally {
    await server.close();
  }
});

test('canvas overlay survives a BookWalker responsive canvas replacement', async ({ page }) => {
  const server = await loadFixtureWithContentScript(page);

  try {
    await requestTranslatePage(page);
    await expectSingleVisibleCanvasOverlay(page);

    const initialState = await getCanvasOverlayState(page);
    expect(initialState.translationKey).toBe('bookwalker:bookwalker-page');

    await page.evaluate(() => {
      const viewport = document.getElementById('viewport0') as HTMLElement;
      viewport.style.width = '360px';
      viewport.style.height = '540px';

      const replacementCanvas = document.createElement('canvas');
      replacementCanvas.id = 'bookwalker-page';
      replacementCanvas.width = 360;
      replacementCanvas.height = 540;
      replacementCanvas.style.cssText = 'width: 360px; height: 540px; display: block;';

      viewport.replaceChildren(replacementCanvas);
      (window as TestWindow).drawBookWalkerPage('#f5f5f5', 'page 1');
    });

    await expect
      .poll(
        async () => {
          const state = await getCanvasOverlayState(page);
          return {
            translated: state.translated,
            processing: state.processing,
            overlayCount: state.overlayCount,
            overlayDisplay: state.overlayDisplay,
            canvasParentId: state.canvasParentId,
            overlayParentId: state.overlayParentId,
            translationKey: state.translationKey,
          };
        },
        { timeout: 3000 },
      )
      .toEqual({
        translated: true,
        processing: false,
        overlayCount: 1,
        overlayDisplay: 'block',
        canvasParentId: 'viewport0',
        overlayParentId: 'viewport0',
        translationKey: 'bookwalker:bookwalker-page',
      });

    const state = await getCanvasOverlayState(page);
    expectOverlayAligned(state);
  } finally {
    await server.close();
  }
});

test('canvas translation failure clears processing state and can be retried', async ({ page }) => {
  const server = await loadFixtureWithContentScript(page, 'error');

  try {
    await requestTranslatePage(page);
    await expect
      .poll(async () => {
        const state = await getCanvasOverlayState(page);
        return {
          translated: state.translated,
          processing: state.processing,
          overlayCount: state.overlayCount,
          calls: state.calls,
        };
      })
      .toEqual({
        translated: false,
        processing: false,
        overlayCount: 0,
        calls: 1,
      });

    let state = await getCanvasOverlayState(page);
    expect(state.progressText).toContain('backend failed');

    await page.evaluate(() => (window as TestWindow).__setTranslationMode('success'));
    await requestTranslatePage(page);
    await expectSingleVisibleCanvasOverlay(page);

    state = await getCanvasOverlayState(page);
    expect(state.translated).toBe(true);
    expect(state.calls).toBe(2);
  } finally {
    await server.close();
  }
});

test('BookWalker auto translate does not loop after backend timeout', async ({ page }) => {
  const server = await loadFixtureWithContentScript(page, 'timeout-error', {
    autoTranslate: true,
    showProgress: true,
  });

  try {
    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateCalls), { timeout: 1200 })
      .toBe(1);
    await expect
      .poll(async () => {
        const state = await getCanvasOverlayState(page);
        return {
          translated: state.translated,
          processing: state.processing,
          overlayCount: state.overlayCount,
          calls: state.calls,
        };
      })
      .toEqual({
        translated: false,
        processing: false,
        overlayCount: 0,
        calls: 1,
      });

    expect((await getCanvasOverlayState(page)).progressText).toContain('timed out');

    await page.waitForTimeout(2200);
    await expect.poll(() => page.evaluate(() => (window as TestWindow).__translateCalls)).toBe(1);
  } finally {
    await server.close();
  }
});

test('BookWalker auto translate does not restart a queued canvas while an earlier page is still processing', async ({
  page,
}) => {
  const server = await createBookWalkerFixtureServer();

  try {
    await installChromeMock(page, 'success', {
      autoTranslate: true,
      showProgress: true,
    });
    await page.goto(server.url);

    await page.evaluate(() => {
      (window as TestWindow).__setTranslationDelayMs(2500);

      const drawPage = (canvasId: string, color: string, label: string) => {
        const canvas = document.getElementById(canvasId) as HTMLCanvasElement;
        const ctx = canvas.getContext('2d') as CanvasRenderingContext2D;
        ctx.fillStyle = color;
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#111111';
        ctx.fillRect(120, 180, 240, 120);
        ctx.fillStyle = '#ffffff';
        ctx.font = 'bold 44px sans-serif';
        ctx.fillText(label, 145, 250);
      };

      drawPage('bookwalker-page-2', '#a7f3d0', 'page 2');
      drawPage('bookwalker-page-3', '#fbcfe8', 'page 3');

      const viewport1 = document.getElementById('viewport1') as HTMLElement;
      const viewport2 = document.getElementById('viewport2') as HTMLElement;
      viewport1.classList.add('currentScreen');
      viewport1.style.left = '680px';
      viewport2.style.left = '1500px';
    });

    await injectManifestContentScripts(page, { css: false });
    await page.waitForFunction(() => (window as TestWindow).__runtimeListenerCount?.() > 0);

    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateCalls), { timeout: 1200 })
      .toBe(1);

    await page.evaluate(() => {
      const viewport0 = document.getElementById('viewport0') as HTMLElement;
      const viewport1 = document.getElementById('viewport1') as HTMLElement;
      const viewport2 = document.getElementById('viewport2') as HTMLElement;

      viewport0.classList.remove('currentScreen');
      viewport0.style.left = '-900px';

      viewport1.classList.remove('currentScreen');
      viewport1.style.left = '-300px';

      viewport2.classList.add('currentScreen');
      viewport2.style.left = '180px';
    });

    await page.evaluate(() => new Promise((resolve) => setTimeout(resolve, 250)));

    await page.evaluate(() => {
      const viewport1 = document.getElementById('viewport1') as HTMLElement;
      const viewport2 = document.getElementById('viewport2') as HTMLElement;

      viewport2.classList.remove('currentScreen');
      viewport2.style.left = '1500px';

      viewport1.classList.add('currentScreen');
      viewport1.style.left = '680px';
    });

    await page.waitForTimeout(1000);

    const inFlightState = await page.evaluate(() => ({
      calls: (window as TestWindow).__translateCalls,
      responses: (window as TestWindow).__translateResponses,
    }));
    expect(inFlightState.calls).toBe(1);
    expect(inFlightState.responses).toBe(0);

    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateResponses), {
        timeout: 6000,
      })
      .toBe(1);
    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateCalls), { timeout: 6000 })
      .toBe(2);
    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateResponses), {
        timeout: 7000,
      })
      .toBe(2);

    const finalState = await page.evaluate(() => ({
      calls: (window as TestWindow).__translateCalls,
      responses: (window as TestWindow).__translateResponses,
      translatedPages: Array.from(document.querySelectorAll('canvas[data-manga-translated]')).map(
        (c) => c.id,
      ),
    }));

    expect(finalState.translatedPages).toContain('bookwalker-page-2');
    expect(finalState.calls).toBe(2);
  } finally {
    await server.close();
  }
});

test('BookWalker auto translate keeps earlier canvases in flight before starting later views', async ({
  page,
}) => {
  const server = await createBookWalkerFixtureServer();

  try {
    await installChromeMock(page, 'success', {
      autoTranslate: true,
      showProgress: true,
    });
    await page.goto(server.url);

    await page.evaluate(() => {
      (window as TestWindow).__setTranslationDelayMs(2500);

      const drawPage = (canvasId: string, color: string, label: string) => {
        const canvas = document.getElementById(canvasId) as HTMLCanvasElement;
        const ctx = canvas.getContext('2d') as CanvasRenderingContext2D;
        ctx.fillStyle = color;
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#111111';
        ctx.fillRect(120, 180, 240, 120);
        ctx.fillStyle = '#ffffff';
        ctx.font = 'bold 44px sans-serif';
        ctx.fillText(label, 145, 250);
      };

      drawPage('bookwalker-page-2', '#a7f3d0', 'page 2');
      drawPage('bookwalker-page-3', '#fbcfe8', 'page 3');
    });

    await injectManifestContentScripts(page, { css: false });
    await page.waitForFunction(() => (window as TestWindow).__runtimeListenerCount?.() > 0);

    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateCalls), { timeout: 1200 })
      .toBe(1);

    await page.evaluate(() => {
      const viewport0 = document.getElementById('viewport0') as HTMLElement;
      const viewport1 = document.getElementById('viewport1') as HTMLElement;

      viewport0.classList.remove('currentScreen');
      viewport0.style.left = '-900px';
      viewport1.classList.add('currentScreen');
      viewport1.style.left = '180px';
    });

    await page.waitForTimeout(200);

    await page.evaluate(() => {
      const viewport1 = document.getElementById('viewport1') as HTMLElement;
      const viewport2 = document.getElementById('viewport2') as HTMLElement;

      viewport1.classList.remove('currentScreen');
      viewport1.style.left = '-300px';
      viewport2.classList.add('currentScreen');
      viewport2.style.left = '180px';
    });

    await page.waitForTimeout(1300);

    const inFlightState = await page.evaluate(() => ({
      calls: (window as TestWindow).__translateCalls,
      responses: (window as TestWindow).__translateResponses,
      processingCanvasIds: Array.from(
        document.querySelectorAll('canvas[data-manga-processing]'),
      ).map((c) => c.id),
      translatedCanvasIds: Array.from(
        document.querySelectorAll('canvas[data-manga-translated]'),
      ).map((c) => c.id),
    }));

    expect(inFlightState.calls).toBe(1);
    expect(inFlightState.responses).toBe(0);
    expect(inFlightState.processingCanvasIds).toEqual(['bookwalker-page']);
    expect(inFlightState.translatedCanvasIds).toEqual([]);

    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateResponses), {
        timeout: 6000,
      })
      .toBe(1);
    await expect
      .poll(
        () =>
          page.evaluate(() => ({
            calls: (window as TestWindow).__translateCalls,
            processingCanvasIds: Array.from(
              document.querySelectorAll('canvas[data-manga-processing]'),
            ).map((c) => c.id),
          })),
        { timeout: 3000 },
      )
      .toEqual({
        calls: 2,
        processingCanvasIds: ['bookwalker-page-2'],
      });
    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateResponses), {
        timeout: 10000,
      })
      .toBe(3);

    const finalState = await page.evaluate(() => ({
      calls: (window as TestWindow).__translateCalls,
      responses: (window as TestWindow).__translateResponses,
      processingPages: Array.from(document.querySelectorAll('canvas[data-manga-processing]')).map(
        (c) => c.id,
      ),
      translatedPages: Array.from(document.querySelectorAll('canvas[data-manga-translated]')).map(
        (c) => c.id,
      ),
    }));

    expect(finalState.calls).toBe(3);
    expect(finalState.responses).toBe(3);
    expect(finalState.processingPages).toEqual([]);
    expect(finalState.translatedPages).toEqual(['bookwalker-page-3']);

    await page.evaluate(() => {
      document.getElementById('viewport2')!.classList.remove('currentScreen');
      document.getElementById('viewport2')!.style.left = '1500px';
      document.getElementById('viewport0')!.classList.add('currentScreen');
      document.getElementById('viewport0')!.style.left = '180px';
    });
    await expect
      .poll(
        () =>
          page.evaluate(() =>
            document.getElementById('bookwalker-page')!.hasAttribute('data-manga-translated'),
          ),
        { timeout: 3000 },
      )
      .toBe(true);

    await page.evaluate(() => {
      document.getElementById('viewport0')!.classList.remove('currentScreen');
      document.getElementById('viewport0')!.style.left = '-900px';
      document.getElementById('viewport1')!.classList.add('currentScreen');
      document.getElementById('viewport1')!.style.left = '180px';
    });
    await expect
      .poll(
        () =>
          page.evaluate(() =>
            document.getElementById('bookwalker-page-2')!.hasAttribute('data-manga-translated'),
          ),
        { timeout: 3000 },
      )
      .toBe(true);
  } finally {
    await server.close();
  }
});

test('BookWalker queued page is not re-translated when scrolling back during pending backend call', async ({
  page,
}) => {
  const server = await loadFixtureWithContentScript(page, 'success');

  try {
    await page.evaluate(() => {
      const renderer = document.getElementById('renderer') as HTMLElement;
      const createPage = (id: string, label: string, color: string, leftPx: number) => {
        const viewport = document.createElement('div');
        viewport.id = id;
        viewport.className = 'currentScreen';
        Object.assign(viewport.style, {
          position: 'absolute',
          left: `${leftPx}px`,
          top: '40px',
          width: '480px',
          height: '720px',
          visibility: 'visible',
          zIndex: '1',
        });
        const canvas = document.createElement('canvas');
        canvas.id = `bookwalker-${id}`;
        canvas.width = 480;
        canvas.height = 720;
        canvas.style.cssText = 'width: 480px; height: 720px; display: block;';
        viewport.appendChild(canvas);
        renderer.appendChild(viewport);
        const ctx = canvas.getContext('2d') as CanvasRenderingContext2D;
        ctx.fillStyle = color;
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#111111';
        ctx.fillRect(120, 180, 240, 120);
        ctx.fillStyle = '#ffffff';
        ctx.font = 'bold 44px sans-serif';
        ctx.fillText(label, 145, 250);
      };
      createPage('viewport1', 'page 2', '#a7f3d0', 680);
      createPage('viewport2', 'page 3', '#fbcfe8', 1500);

      (window as TestWindow).__dumpState = () => ({
        calls: (window as TestWindow).__translateCalls,
        responses: (window as TestWindow).__translateResponses,
        canvases: Array.from(document.querySelectorAll('canvas')).map((c) => ({
          id: c.id,
          translated: c.hasAttribute('data-manga-translated'),
          processing: c.hasAttribute('data-manga-processing'),
          sourceSignature: c.getAttribute('data-manga-source-signature'),
          closestCurrentScreen: Boolean(c.closest('.currentScreen')),
        })),
      });
    });

    await page.evaluate(() => (window as TestWindow).__setTranslationDelayMs(500));
    await requestTranslatePage(page);

    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateCalls))
      .toBeGreaterThanOrEqual(1);

    await page.evaluate(() => {
      document.getElementById('viewport0')!.classList.remove('currentScreen');
      document.getElementById('viewport1')!.classList.remove('currentScreen');
      document.getElementById('viewport2')!.classList.add('currentScreen');
      document.getElementById('viewport2')!.style.left = '180px';
    });

    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateCalls), { timeout: 3000 })
      .toBe(2);

    await page.evaluate(() => {
      document.getElementById('viewport2')!.classList.remove('currentScreen');
      document.getElementById('viewport2')!.style.left = '1500px';
      document.getElementById('viewport1')!.classList.add('currentScreen');
      document.getElementById('viewport1')!.style.left = '180px';
    });

    await expect
      .poll(
        () =>
          page.evaluate(() => ({
            calls: (window as TestWindow).__translateCalls,
            processingPages: Array.from(
              document.querySelectorAll('canvas[data-manga-processing]'),
            ).map((c) => c.id),
            responses: (window as TestWindow).__translateResponses,
            translatedPages: Array.from(
              document.querySelectorAll('canvas[data-manga-translated]'),
            ).map((c) => c.id),
          })),
        { timeout: 5000 },
      )
      .toEqual(
        expect.objectContaining({
          calls: 2,
          responses: 2,
          processingPages: [],
        }),
      );

    const finalState = await page.evaluate(() => ({
      calls: (window as TestWindow).__translateCalls,
      processingPages: Array.from(document.querySelectorAll('canvas[data-manga-processing]')).map(
        (c) => c.id,
      ),
      responses: (window as TestWindow).__translateResponses,
      translatedPages: Array.from(document.querySelectorAll('canvas[data-manga-translated]')).map(
        (c) => c.id,
      ),
    }));

    expect(finalState.translatedPages).toContain('bookwalker-viewport1');
    expect(new Set(finalState.translatedPages).size).toBe(finalState.translatedPages.length);
  } finally {
    await server.close();
  }
});

test('auto translate does not loop when canvas changes while backend is processing', async ({
  page,
}) => {
  const server = await loadFixtureWithContentScript(page, 'success', {
    autoTranslate: true,
    showProgress: true,
  });

  try {
    const initialState = await page.evaluate(() => ({
      calls: (window as TestWindow).__translateCalls,
      delay: (window as TestWindow).__translationDelayMs,
    }));

    if (initialState.calls > 0) {
      await expect
        .poll(() => page.evaluate(() => (window as TestWindow).__translateResponses), {
          timeout: 3000,
        })
        .toBeGreaterThanOrEqual(initialState.calls);
    }

    await page.evaluate(() => {
      (window as TestWindow).__translateCalls = 0;
      (window as TestWindow).__translateResponses = 0;
      (window as TestWindow).__setTranslationDelayMs(800);
      (window as TestWindow).__startCanvasChurn = () => {
        if ((window as TestWindow).__canvasChurnTimer) return;
        let i = 0;
        const colors = ['#f5f5f5', '#fde68a', '#a7f3d0', '#fbcfe8'];
        const churn = () => {
          const color = colors[i % colors.length] as string;
          const label = `page ${i + 1}`;
          (window as TestWindow).drawBookWalkerPage(color, label);
          i++;
        };
        churn();
        (window as TestWindow).__canvasChurnTimer = setInterval(churn, 100);
      };
      (window as TestWindow).__stopCanvasChurn = () => {
        if ((window as TestWindow).__canvasChurnTimer) {
          clearInterval(
            (window as TestWindow).__canvasChurnTimer as ReturnType<typeof setInterval>,
          );
          (window as TestWindow).__canvasChurnTimer = null;
        }
      };
    });

    await page.evaluate(
      () =>
        new Promise((resolve) => {
          (window as TestWindow).__sendRuntimeMessage({ type: 'TRANSLATE_PAGE' });
          setTimeout(resolve, 50);
        }),
    );

    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateCalls), { timeout: 3000 })
      .toBeGreaterThanOrEqual(1);
    await page.evaluate(() => (window as TestWindow).__startCanvasChurn());

    await expect
      .poll(() => page.evaluate(() => (window as TestWindow).__translateResponses), {
        timeout: 3000,
      })
      .toBeGreaterThanOrEqual(1);

    await page.evaluate(() => {
      (window as TestWindow).__setTranslationDelayMs(0);
      (window as TestWindow).__stopCanvasChurn();
    });

    await expect
      .poll(
        () =>
          page.evaluate(() => {
            const calls = (window as TestWindow).__translateCalls;
            const responses = (window as TestWindow).__translateResponses;
            const processing = document
              .getElementById('bookwalker-page')
              ?.hasAttribute('data-manga-processing');
            return {
              responses,
              calls,
              processing,
              idle: responses === calls && !processing,
            };
          }),
        { timeout: 5000 },
      )
      .toEqual(
        expect.objectContaining({
          idle: true,
        }),
      );

    const finalState = await page.evaluate(() => ({
      calls: (window as TestWindow).__translateCalls,
      responses: (window as TestWindow).__translateResponses,
      translated: document.getElementById('bookwalker-page')?.hasAttribute('data-manga-translated'),
      processing: document.getElementById('bookwalker-page')?.hasAttribute('data-manga-processing'),
      overlayCount: document.querySelectorAll('.manga-canvas-overlay').length,
    }));
    expect(finalState.calls).toBeLessThanOrEqual(2);
    expect(finalState.calls).toBeGreaterThanOrEqual(1);
    expect(finalState.responses).toBe(finalState.calls);
    expect(finalState.processing).toBe(false);
    expect(finalState.overlayCount).toBe(finalState.translated ? 1 : 0);
  } finally {
    await server.close();
  }
});

test('bad translated canvas output is rejected and does not block retry', async ({ page }) => {
  for (const badMode of ['black', 'broken-data-url'] as const) {
    const server = await loadFixtureWithContentScript(page, badMode);

    try {
      await requestTranslatePage(page);
      await expect
        .poll(async () => {
          const state = await getCanvasOverlayState(page);
          return {
            translated: state.translated,
            processing: state.processing,
            overlayCount: state.overlayCount,
            calls: state.calls,
          };
        })
        .toEqual({
          translated: false,
          processing: false,
          overlayCount: 0,
          calls: 1,
        });

      await page.evaluate(() => (window as TestWindow).__setTranslationMode('success'));
      await requestTranslatePage(page);
      await expectSingleVisibleCanvasOverlay(page);

      const state = await getCanvasOverlayState(page);
      expect(state.translated).toBe(true);
      expect(state.calls).toBe(2);
    } finally {
      await server.close();
    }
  }
});
