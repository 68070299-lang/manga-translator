import type { Page } from '@playwright/test';
import type { ChromeApi } from '../../scripts/lib/extension.ts';
import { expect, test } from './fixtures.ts';

declare const chrome: ChromeApi;

interface StoredSettings {
  apiKey?: string;
  targetLang?: string;
  googleOcr?: { apiKey?: string };
  localLlm?: { apiKey?: string };
  localBackend?: { translator?: string };
}

async function gotoOptions(page: Page, extensionId: string): Promise<void> {
  await page.goto(`chrome-extension://${extensionId}/options.html`);
}

async function resetExtensionStorage(page: Page, extensionId: string): Promise<void> {
  await gotoOptions(page, extensionId);
  await page.evaluate(async () => {
    await chrome.storage.sync.clear();
    await chrome.storage.local.clear();
    await chrome.storage.session.clear();
  });
}

test('options page migrates legacy synced API keys into private local storage', async ({
  page,
  extensionId,
}) => {
  await resetExtensionStorage(page, extensionId);
  await page.evaluate(async () => {
    await chrome.storage.sync.set({
      settings: {
        provider: 'openrouter',
        apiKey: 'legacy-sync-key',
        googleOcr: { apiKey: 'legacy-vision-key' },
        localLlm: { apiKey: 'legacy-local-key' },
      },
    });
  });

  await gotoOptions(page, extensionId);
  await expect(page.locator('#apiKey')).toHaveValue('legacy-sync-key');
  await expect(page.locator('#googleOcrApiKey')).toHaveValue('legacy-vision-key');
  await expect(page.locator('#localLlmApiKey')).toHaveValue('legacy-local-key');

  await page.locator('#saveBtn').click();

  await expect
    .poll(() =>
      page.evaluate(async () => {
        const syncResult = await chrome.storage.sync.get('settings');
        const localResult = await chrome.storage.local.get('apiKeys');
        const settings = syncResult.settings as StoredSettings;
        return {
          syncApiKey: settings.apiKey,
          syncGoogleApiKey: settings.googleOcr?.apiKey,
          syncLocalLlmApiKey: settings.localLlm?.apiKey,
          localApiKeys: localResult.apiKeys,
        };
      }),
    )
    .toEqual({
      syncApiKey: undefined,
      syncGoogleApiKey: undefined,
      syncLocalLlmApiKey: undefined,
      localApiKeys: {
        apiKey: 'legacy-sync-key',
        googleOcrApiKey: 'legacy-vision-key',
        localLlmApiKey: 'legacy-local-key',
      },
    });
});

test('options page does not resurrect synced API keys after local keys are cleared', async ({
  page,
  extensionId,
}) => {
  await resetExtensionStorage(page, extensionId);
  await page.evaluate(async () => {
    await chrome.storage.sync.set({
      settings: {
        provider: 'openrouter',
        apiKey: 'old-sync-key',
        googleOcr: { apiKey: 'old-vision-key' },
        localLlm: { apiKey: 'old-local-key' },
      },
    });
    await chrome.storage.local.set({
      apiKeys: {
        apiKey: '',
        googleOcrApiKey: '',
        localLlmApiKey: '',
      },
    });
  });

  await gotoOptions(page, extensionId);

  await expect(page.locator('#apiKey')).toHaveValue('');
  await expect(page.locator('#googleOcrApiKey')).toHaveValue('');
  await expect(page.locator('#localLlmApiKey')).toHaveValue('');
});

test('options page only offers backend-supported local pipeline choices', async ({
  page,
  extensionId,
}) => {
  await resetExtensionStorage(page, extensionId);
  await gotoOptions(page, extensionId);

  const choices = await page.evaluate(() => ({
    detectors: Array.from(
      document.querySelectorAll('#localDetector option'),
      (option) => (option as HTMLOptionElement).value,
    ),
    ocr: Array.from(
      document.querySelectorAll('#localOcr option'),
      (option) => (option as HTMLOptionElement).value,
    ),
    inpainters: Array.from(
      document.querySelectorAll('#localInpainter option'),
      (option) => (option as HTMLOptionElement).value,
    ),
    translators: Array.from(
      document.querySelectorAll('#localTranslator option'),
      (option) => (option as HTMLOptionElement).value,
    ),
  }));

  expect(choices).toEqual({
    detectors: ['default', 'rtdetr', 'animetext-yolo'],
    ocr: ['manga-ocr', 'paddle-ocr', 'google-ocr'],
    inpainters: ['flux2_klein_lanpaint', 'flux2_klein_q4', 'lama_large', 'none'],
    translators: ['', 'none', 'gemini', 'openai', 'openrouter', 'local'],
  });
});

test('options page preserves blank local translator inheritance', async ({ page, extensionId }) => {
  await resetExtensionStorage(page, extensionId);
  await page.evaluate(async () => {
    await chrome.storage.sync.set({
      settings: {
        provider: 'openrouter',
        pipelineMode: 'local',
        localBackend: {
          translator: '',
        },
      },
    });
  });

  await gotoOptions(page, extensionId);
  await expect(page.locator('#localTranslator')).toHaveValue('');

  await page.locator('#saveBtn').click();

  await expect
    .poll(() =>
      page.evaluate(async () => {
        const result = await chrome.storage.sync.get('settings');
        return (result.settings as StoredSettings).localBackend?.translator;
      }),
    )
    .toBe('');
});
