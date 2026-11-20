import { expect, type Page, test } from '@playwright/test';
import {
  E2E_DEFAULT_SETTINGS,
  injectManifestContentScripts,
  installE2eChromeMock,
  serveHtmlFixture,
} from './fixtures.ts';

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
  return serveHtmlFixture(`<!doctype html>
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
}

async function installContentScriptChromeMock(page: Page) {
  await page.addInitScript(installE2eChromeMock, {
    defaultSettings: {
      ...E2E_DEFAULT_SETTINGS,
      localBackend: { ...E2E_DEFAULT_SETTINGS.localBackend, translator: 'none' },
    },
    tracking: 'records' as const,
    failureModes: false,
    image: { width: 320, height: 320, textX: 70, textY: 165, fontSize: 34 },
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

interface ImageTranslationState {
  calls: number;
  pageOneTranslated: boolean;
  pageTwoTranslated: boolean;
  overlays?: number;
  progressText?: string;
}

/** Poll the reader's per-image translation state until it matches `expected`. */
async function expectImageTranslationState(
  page: Page,
  expected: ImageTranslationState,
  { timeout }: { timeout?: number } = {},
): Promise<void> {
  const hasOverlays = expected.overlays !== undefined;
  const hasProgressText = expected.progressText !== undefined;
  await expect
    .poll(
      () =>
        page.evaluate(
          ({ withOverlays, withProgressText }) => ({
            calls: (window as TestWindow).__translateCalls.length,
            pageOneTranslated: document
              .getElementById('page-one')!
              .hasAttribute('data-manga-translated'),
            pageTwoTranslated: document
              .getElementById('page-two')!
              .hasAttribute('data-manga-translated'),
            ...(withOverlays
              ? { overlays: document.querySelectorAll('.manga-translated-img-overlay').length }
              : {}),
            ...(withProgressText
              ? {
                  progressText:
                    document
                      .querySelector('#manga-global-progress')
                      ?.shadowRoot?.querySelector('.manga-progress-text')?.textContent || '',
                }
              : {}),
          }),
          { withOverlays: hasOverlays, withProgressText: hasProgressText },
        ),
      timeout === undefined ? {} : { timeout },
    )
    .toEqual(expected);
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

    await expectImageTranslationState(page, {
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

    await expectImageTranslationState(page, {
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

    await expectImageTranslationState(
      page,
      {
        calls: 1,
        pageOneTranslated: true,
        pageTwoTranslated: false,
        progressText: 'Stopped after 1/2 images',
      },
      { timeout: 4000 },
    );

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
