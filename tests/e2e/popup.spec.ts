import type { Page } from '@playwright/test';
import type { ChromeApi } from '../../scripts/lib/extension.ts';
import { expect, test } from './fixtures.ts';

declare const chrome: ChromeApi;

interface StoredSettings {
  apiKey?: string;
  targetLang?: string;
  localLlm?: { apiKey?: string };
  localBackend?: { translator?: string };
}

interface StoredApiKeys {
  apiKey?: string;
}

async function gotoPopup(page: Page, extensionId: string): Promise<void> {
  await page.goto(`chrome-extension://${extensionId}/popup.html`);
}

async function resetExtensionStorage(page: Page, extensionId: string): Promise<void> {
  await gotoPopup(page, extensionId);
  await page.evaluate(async () => {
    await chrome.storage.sync.clear();
    await chrome.storage.local.clear();
    await chrome.storage.session.clear();
  });
}

async function openFreshPopup(page: Page, extensionId: string): Promise<void> {
  await resetExtensionStorage(page, extensionId);
  await gotoPopup(page, extensionId);
}

test('popup uses the Ichigo-style visual shell without compact text overflow', async ({
  page,
  extensionId,
}) => {
  await openFreshPopup(page, extensionId);

  await expect(page.locator('[data-testid="brand-mark"]')).toBeVisible();
  await expect(page.locator('.manga-strip')).toBeVisible();
  await expect(page.locator('.header-title')).toHaveText('Manga Translator');

  const colors = await page.evaluate(() => {
    const body = getComputedStyle(document.body);
    const title = getComputedStyle(document.querySelector('.header-title') as Element);
    return {
      bodyBackground: body.backgroundColor,
      titleColor: title.color,
    };
  });

  expect(colors).toEqual({
    bodyBackground: 'rgb(248, 232, 239)',
    titleColor: 'rgb(111, 45, 39)',
  });

  const overflowingText = await page
    .locator('.btn span, .mode-tab span, .checkbox-label span, .status-label')
    .evaluateAll((elements) =>
      elements
        .filter((element) => element.scrollWidth > element.clientWidth + 1)
        .map((element) => element.textContent.trim()),
    );
  expect(overflowingText).toEqual([]);
});

test('pipeline mode toggle reveals local backend status only for local mode', async ({
  page,
  extensionId,
}) => {
  await openFreshPopup(page, extensionId);
  const connectionIndicator = page.locator('#connection-indicator');

  await expect(page.locator('#mode-local')).toBeChecked();
  await expect(connectionIndicator).toBeVisible();

  await page.locator('#mode-cloud').check();
  await expect(connectionIndicator).toBeHidden();

  await page.locator('#mode-local').check();
  await expect(connectionIndicator).toBeVisible();
});

test('API key warning handles missing and stored browser keys', async ({ page, extensionId }) => {
  await resetExtensionStorage(page, extensionId);
  await page.evaluate(async () => {
    await chrome.storage.sync.set({
      settings: {
        pipelineMode: 'local',
        provider: 'openrouter',
        localBackend: { translator: '' },
      },
    });
  });
  await gotoPopup(page, extensionId);

  await expect(page.locator('#api-warning-section')).toBeVisible();
  await expect(page.locator('#api-warning-text')).toContainText(
    'Local backend is using openrouter',
  );

  await page.evaluate(async () => {
    await chrome.storage.local.set({ apiKeys: { apiKey: 'sk-test' } });
  });
  await gotoPopup(page, extensionId);

  await expect(page.locator('#api-warning-section')).toBeHidden();
});

test('cloud mode API key warning ignores OCR-only local backend setting', async ({
  page,
  extensionId,
}) => {
  await resetExtensionStorage(page, extensionId);
  await page.evaluate(async () => {
    await chrome.storage.sync.set({
      settings: {
        pipelineMode: 'cloud',
        provider: 'openrouter',
        localBackend: { translator: 'none' },
      },
    });
  });
  await gotoPopup(page, extensionId);

  await expect(page.locator('#api-warning-section')).toBeVisible();

  await page.evaluate(async () => {
    await chrome.storage.local.set({ apiKeys: { apiKey: 'sk-test' } });
  });
  await gotoPopup(page, extensionId);

  await expect(page.locator('#api-warning-section')).toBeHidden();
});

test('popup quick settings preserve private API keys outside sync storage', async ({
  page,
  extensionId,
}) => {
  await resetExtensionStorage(page, extensionId);
  await page.evaluate(async () => {
    await chrome.storage.sync.set({
      settings: {
        pipelineMode: 'cloud',
        provider: 'openrouter',
        apiKey: 'legacy-sync-key',
        targetLang: 'English',
        localBackend: { translator: 'none' },
      },
    });
  });
  await gotoPopup(page, extensionId);

  await page.locator('#target-lang').selectOption('Thai');

  await expect
    .poll(() =>
      page.evaluate(async () => {
        const syncResult = await chrome.storage.sync.get('settings');
        const localResult = await chrome.storage.local.get('apiKeys');
        const settings = syncResult.settings as StoredSettings;
        return {
          targetLang: settings.targetLang,
          syncApiKey: settings.apiKey,
          localApiKey: (localResult.apiKeys as StoredApiKeys | undefined)?.apiKey,
        };
      }),
    )
    .toEqual({
      targetLang: 'Thai',
      syncApiKey: undefined,
      localApiKey: 'legacy-sync-key',
    });
});

test('local backend health renders connected model states', async ({ page, extensionId }) => {
  await page.route('http://localhost:8001/health', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        models: {
          detector: { status: 'loaded' },
          ocr: { status: 'loading' },
          paddle_ocr: { status: 'not_loaded' },
          inpainter: { status: 'error' },
        },
      }),
    }),
  );

  await openFreshPopup(page, extensionId);

  await expect(page.locator('.status-label')).toHaveText('Backend connected');
  await expect(page.locator('.model-status-list')).toBeVisible();
  await expect(page.locator('.model-status-item.loaded')).toContainText('Detector');
  await expect(page.locator('.model-status-item.loading')).toContainText('OCR');
  await expect(page.locator('.model-status-item.error')).toContainText('Inpainter');
});

test('local backend health network timeout renders offline state', async ({
  page,
  extensionId,
}) => {
  await page.route('http://localhost:8001/health', (route) => route.abort('timedout'));

  await openFreshPopup(page, extensionId);

  await expect(page.locator('.status-label')).toHaveText('Backend offline', { timeout: 3000 });
  await expect(page.locator('.model-status-list')).toHaveCount(0);
});

test('progress restore keeps fresh work and drops stale session state', async ({
  page,
  extensionId,
}) => {
  await resetExtensionStorage(page, extensionId);
  await page.evaluate(async () => {
    await chrome.storage.session.set({
      translationProgress: {
        status: 'Rendering...',
        percent: 70,
        tabId: 42,
        timestamp: Date.now(),
      },
    });
  });
  await gotoPopup(page, extensionId);

  await expect(page.locator('#status-section')).toBeVisible();
  await expect(page.locator('#status-text')).toHaveText('Rendering...');
  await expect(page.locator('#progress-fill')).toHaveAttribute('style', /70%/);

  await page.evaluate(async () => {
    await chrome.storage.session.set({
      translationProgress: {
        status: 'Old work',
        percent: 50,
        tabId: 42,
        timestamp: Date.now() - 61000,
      },
    });
  });
  await gotoPopup(page, extensionId);

  await expect(page.locator('#status-section')).toBeHidden();
  const staleState = await page.evaluate(async () =>
    chrome.storage.session.get('translationProgress'),
  );
  expect(staleState.translationProgress).toBeUndefined();
});

test('cache count handles multiple cached translations', async ({ page, extensionId }) => {
  await resetExtensionStorage(page, extensionId);
  await page.evaluate(async () => {
    await chrome.storage.local.set({
      manga_cache_a: { timestamp: Date.now() },
      manga_cache_b: { timestamp: Date.now() },
      manga_cache_c: { timestamp: Date.now() },
      unrelated: { timestamp: Date.now() },
    });
  });
  await gotoPopup(page, extensionId);

  await expect(page.locator('#cache-count')).toHaveText('3 cached translations');
});
