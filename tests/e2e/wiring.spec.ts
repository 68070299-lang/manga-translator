import type { BrowserContext, Route } from '@playwright/test';
import type { ChromeApi } from '../../scripts/lib/extension.ts';
import { expect, test } from './fixtures.ts';

declare const chrome: ChromeApi;

interface CapturedTranslateRequest {
  contentType: string;
  postData: Buffer;
}

interface MockBackendOptions {
  onTranslate?: (route: Route) => Promise<void>;
}

const READER_URL = 'https://reader.test/manga';
const BACKEND_URL = 'http://localhost:8001';

const READER_HTML = `<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Reader wiring fixture</title>
  <style>
    body { margin: 0; padding: 32px; background: #f5f2ec; }
    #reader-frame { position: relative; width: 320px; height: 320px; }
    #manga-page { display: block; width: 320px; height: 320px; }
  </style>
</head>
<body>
  <main id="reader-frame">
    <img id="manga-page" alt="Manga page" width="320" height="320">
  </main>
  <script>
    const canvas = document.createElement('canvas');
    canvas.width = 320;
    canvas.height = 320;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#f7d6d6';
    ctx.fillRect(0, 0, 320, 320);
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(45, 100, 230, 110);
    ctx.fillStyle = '#111111';
    ctx.font = 'bold 34px sans-serif';
    ctx.fillText('ORIGINAL', 72, 166);
    document.getElementById('manga-page').src = canvas.toDataURL('image/png');
  </script>
</body>
</html>`;

const TRANSLATED_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320" viewBox="0 0 320 320">
  <rect width="320" height="320" fill="#dcfce7"/>
  <rect x="45" y="100" width="230" height="110" fill="#ffffff"/>
  <text x="160" y="166" text-anchor="middle" font-family="Arial, sans-serif" font-size="34" font-weight="700" fill="#166534">TRANSLATED</text>
</svg>`;

const READY_HEALTH_BODY = JSON.stringify({
  status: 'ready',
  models_loaded: true,
  models: {
    detector: { status: 'loaded' },
    ocr: { status: 'loaded' },
    inpainter: { status: 'loaded' },
  },
});

/**
 * Serve the reader page and a mocked local backend. `onTranslate` decides the
 * /translate response; the default returns the translated SVG.
 */
async function mockReaderAndBackend(
  context: BrowserContext,
  { onTranslate }: MockBackendOptions = {},
) {
  const healthRequests: string[] = [];
  const translateRequests: CapturedTranslateRequest[] = [];

  await context.route('https://reader.test/**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: READER_HTML,
    }),
  );

  await context.route(`${BACKEND_URL}/**`, (route) => {
    const request = route.request();
    const url = new URL(request.url());

    if (request.method() === 'GET' && url.pathname === '/health') {
      healthRequests.push(request.url());
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: READY_HEALTH_BODY,
      });
    }

    if (request.method() === 'POST' && url.pathname === '/translate') {
      translateRequests.push({
        contentType: request.headers()['content-type'] || '',
        postData: request.postDataBuffer() ?? Buffer.alloc(0),
      });
      if (onTranslate) {
        return onTranslate(route);
      }
      return route.fulfill({
        status: 200,
        contentType: 'image/svg+xml',
        headers: { 'x-request-id': 'e2e-wiring-request' },
        body: TRANSLATED_SVG,
      });
    }

    return route.fulfill({ status: 404, body: 'not found' });
  });

  return { healthRequests, translateRequests };
}

/** Ask the real content script to translate the reader tab, like the popup does. */
async function sendTranslatePage(context: BrowserContext) {
  const worker = context.serviceWorkers()[0]!;
  expect(worker).toBeTruthy();
  return worker.evaluate(async (targetUrl: string) => {
    const tabs = await chrome.tabs.query({});
    const readerTab = tabs.find((tab) => tab.url === targetUrl);
    if (!readerTab?.id) {
      throw new Error('Reader tab was not found');
    }
    return chrome.tabs.sendMessage(readerTab.id, { type: 'TRANSLATE_PAGE' });
  }, READER_URL);
}

/** Parse a captured multipart/form-data body into a name -> text value map. */
function parseMultipartFields(contentType: string, postData: Buffer): Record<string, string> {
  const boundaryMatch = /boundary=(?:"([^"]+)"|([^;]+))/i.exec(contentType);
  expect(boundaryMatch, 'multipart boundary').toBeTruthy();
  const boundary = boundaryMatch?.[1] || boundaryMatch?.[2];
  const fields: Record<string, string> = {};

  // latin1 keeps binary bytes (the image part) from being re-encoded; text
  // fields in this request are ASCII.
  for (const part of postData.toString('latin1').split(`--${boundary}`)) {
    const headerEnd = part.indexOf('\r\n\r\n');
    if (headerEnd === -1) continue;
    const nameMatch = /name="([^"]+)"/i.exec(part.slice(0, headerEnd));
    if (!nameMatch) continue;
    fields[nameMatch[1]!] = part.slice(headerEnd + 4).replace(/\r\n$/, '');
  }
  return fields;
}

async function configureLocalMode(
  context: BrowserContext,
  extensionId: string,
  {
    detector = 'default',
    inpainter = 'lama_large',
  }: { detector?: string; inpainter?: string } = {},
) {
  const settingsPage = await context.newPage();
  try {
    await settingsPage.goto(`chrome-extension://${extensionId}/popup.html`);
    await settingsPage.evaluate(
      async ({ backendUrl, detector, inpainter }) => {
        await chrome.storage.sync.clear();
        await chrome.storage.local.clear();
        await chrome.storage.session.clear();
        await chrome.storage.sync.set({
          settings: {
            provider: 'openrouter',
            model: '',
            targetLang: 'English',
            autoTranslate: false,
            showProgress: true,
            localBackend: {
              url: backendUrl,
              detector,
              ocr: 'manga-ocr',
              inpainter,
              translator: 'none',
            },
          },
        });
      },
      { backendUrl: BACKEND_URL, detector, inpainter },
    );
  } finally {
    await settingsPage.close();
  }
}

test('installed extension routes a reader image through the local backend and renders its result', async ({
  context,
  page,
  extensionId,
}) => {
  const { healthRequests, translateRequests } = await mockReaderAndBackend(context);

  await configureLocalMode(context, extensionId);
  healthRequests.length = 0;
  await page.goto(READER_URL, { waitUntil: 'load' });

  await expect(page.locator('[data-manga-action="all"]')).toBeVisible();

  const messageResponse = await sendTranslatePage(context);
  expect(messageResponse).toEqual({ success: true });

  const original = page.locator('#manga-page');
  const overlay = page.locator('#manga-page + .manga-translated-img-overlay');
  await expect(overlay).toBeVisible();
  await expect(overlay.locator('img')).toHaveAttribute('src', /^blob:/);
  await expect(original).toHaveAttribute('data-manga-translated', 'true');
  await expect(original).toHaveCSS('visibility', 'hidden');

  expect(healthRequests.length).toBeGreaterThanOrEqual(1);
  expect(translateRequests).toHaveLength(1);
  expect(translateRequests[0]!.contentType).toContain('multipart/form-data; boundary=');
  expect(translateRequests[0]!.postData.toString('utf8')).toContain('name="image"');
  expect(translateRequests[0]!.postData.length).toBeGreaterThan(100);
});

test('experimental detector and inpainter choices reach the local backend request', async ({
  context,
  page,
  extensionId,
}) => {
  const { translateRequests } = await mockReaderAndBackend(context);

  await configureLocalMode(context, extensionId, {
    detector: 'animetext-yolo',
    inpainter: 'flux2_klein_lanpaint',
  });
  await page.goto(READER_URL, { waitUntil: 'load' });
  await expect(page.locator('[data-manga-action="all"]')).toBeVisible();

  expect(await sendTranslatePage(context)).toEqual({ success: true });

  await expect(page.locator('#manga-page + .manga-translated-img-overlay')).toBeVisible();
  expect(translateRequests).toHaveLength(1);

  const fields = parseMultipartFields(
    translateRequests[0]!.contentType,
    translateRequests[0]!.postData,
  );
  expect(fields.detector).toBe('animetext-yolo');
  expect(fields.inpainter).toBe('flux2_klein_lanpaint');
  expect(fields).toHaveProperty('image');
});

test('translating again after clearing the page is served from the service-worker cache', async ({
  context,
  page,
  extensionId,
}) => {
  const { translateRequests } = await mockReaderAndBackend(context);

  await configureLocalMode(context, extensionId);
  await page.goto(READER_URL, { waitUntil: 'load' });
  await expect(page.locator('[data-manga-action="clear"]')).toBeVisible();

  const original = page.locator('#manga-page');
  const overlay = page.locator('#manga-page + .manga-translated-img-overlay');

  expect(await sendTranslatePage(context)).toEqual({ success: true });
  await expect(overlay).toBeVisible();
  await expect(original).toHaveAttribute('data-manga-translated', 'true');
  expect(translateRequests).toHaveLength(1);

  // Clearing only resets the page state; it does not touch the stored cache.
  await page.locator('[data-manga-action="clear"]').click();
  await expect(overlay).toHaveCount(0);
  await expect(original).not.toHaveAttribute('data-manga-translated', /.*/);
  await expect(original).not.toHaveCSS('visibility', 'hidden');

  // The image is untranslated again, so this request reaches the service
  // worker and must be answered from cache instead of the backend.
  expect(await sendTranslatePage(context)).toEqual({ success: true });
  await expect(overlay).toBeVisible();
  await expect(original).toHaveAttribute('data-manga-translated', 'true');
  expect(translateRequests).toHaveLength(1);
});

test('a backend 500 shows an error and leaves the original image visible', async ({
  context,
  extensionId,
}) => {
  const { translateRequests } = await mockReaderAndBackend(context, {
    onTranslate: (route) =>
      route.fulfill({
        status: 500,
        contentType: 'text/plain',
        body: 'inpainter exploded',
      }),
  });

  // The shared page fixture fails on any console.error, but the content script
  // logs failed translations that way on purpose, so collect errors manually.
  const page = await context.newPage();
  const unexpectedErrors: string[] = [];
  page.on('pageerror', (error) => unexpectedErrors.push(`pageerror: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() === 'error' && !message.text().startsWith('[Manga Translator]')) {
      unexpectedErrors.push(`console.error: ${message.text()}`);
    }
  });

  try {
    await configureLocalMode(context, extensionId);
    await page.goto(READER_URL, { waitUntil: 'load' });
    await expect(page.locator('[data-manga-action="all"]')).toBeVisible();

    expect(await sendTranslatePage(context)).toEqual({ success: true });

    const imageError = page.locator('.manga-image-progress');
    await expect(imageError).toContainText('Local backend error: 500');
    await expect(imageError).toContainText('inpainter exploded');

    const original = page.locator('#manga-page');
    await expect(original).not.toHaveAttribute('data-manga-translated', /.*/);
    await expect(original).not.toHaveCSS('visibility', 'hidden');
    await expect(page.locator('.manga-translated-img-overlay')).toHaveCount(0);
    expect(translateRequests.length).toBeGreaterThanOrEqual(1);
    expect(unexpectedErrors).toEqual([]);
  } finally {
    await page.close();
  }
});

function readPngSize(postData: Buffer) {
  const start = postData.indexOf(Buffer.from([0x89, 0x50, 0x4e, 0x47]));
  expect(start, 'PNG in multipart body').toBeGreaterThanOrEqual(0);
  return { width: postData.readUInt32BE(start + 16), height: postData.readUInt32BE(start + 20) };
}

test('dragged screenshot region is cropped, translated and cleared', async ({
  context,
  page,
  extensionId,
}) => {
  const { translateRequests } = await mockReaderAndBackend(context);
  await configureLocalMode(context, extensionId);
  await page.goto(READER_URL, { waitUntil: 'load' });

  await page.locator('[data-manga-action="region"]').click();
  await expect(page.locator('#manga-region-selector')).toBeVisible();

  await page.mouse.move(40, 50);
  await page.mouse.down();
  await page.mouse.move(240, 210, { steps: 4 });
  await page.mouse.up();

  const translatedRegion = page.locator('.manga-region-translation .manga-translated-img-overlay');
  await expect(translatedRegion.locator('img')).toHaveAttribute('src', /^blob:/);
  await expect(translatedRegion).toBeVisible();
  await expect(page.locator('#manga-region-selector')).toHaveCount(0);

  const scale = await page.evaluate(() => window.devicePixelRatio);
  expect(translateRequests).toHaveLength(1);
  expect(readPngSize(translateRequests[0]!.postData)).toEqual({
    width: Math.round(200 * scale),
    height: Math.round(160 * scale),
  });

  const box = await page.locator('.manga-region-translation').boundingBox();
  expect(box).toEqual({ x: 40, y: 50, width: 200, height: 160 });

  // The page image itself must not have been treated as translated.
  await expect(page.locator('#manga-page')).not.toHaveAttribute('data-manga-translated', 'true');

  await page.locator('[data-manga-action="clear"]').click();
  await expect(page.locator('.manga-region-translation')).toHaveCount(0);
});

test('pressing Escape during region selection sends nothing to the backend', async ({
  context,
  page,
  extensionId,
}) => {
  const { translateRequests } = await mockReaderAndBackend(context);
  await configureLocalMode(context, extensionId);
  await page.goto(READER_URL, { waitUntil: 'load' });

  await page.locator('[data-manga-action="region"]').click();
  await expect(page.locator('#manga-region-selector')).toBeVisible();
  await page.keyboard.press('Escape');

  await expect(page.locator('#manga-region-selector')).toHaveCount(0);
  await expect(page.locator('.manga-region-translation')).toHaveCount(0);
  expect(translateRequests).toHaveLength(0);
});
