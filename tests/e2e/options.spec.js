const { test, expect } = require('./fixtures');

async function gotoOptions(page, extensionId) {
  await page.goto(`chrome-extension://${extensionId}/options/options.html`);
}

async function resetExtensionStorage(page, extensionId) {
  await gotoOptions(page, extensionId);
  await page.evaluate(async () => {
    await chrome.storage.sync.clear();
    await chrome.storage.local.clear();
    await chrome.storage.session.clear();
  });
}

test('options page migrates legacy synced API keys into private local storage', async ({ page, extensionId }) => {
  await resetExtensionStorage(page, extensionId);
  await page.evaluate(async () => {
    await chrome.storage.sync.set({
      settings: {
        provider: 'openrouter',
        apiKey: 'legacy-sync-key',
        googleOcr: { apiKey: 'legacy-vision-key' },
        localLlm: { apiKey: 'legacy-local-key' }
      }
    });
  });

  await gotoOptions(page, extensionId);
  await expect(page.locator('#apiKey')).toHaveValue('legacy-sync-key');
  await expect(page.locator('#googleOcrApiKey')).toHaveValue('legacy-vision-key');
  await expect(page.locator('#localLlmApiKey')).toHaveValue('legacy-local-key');

  await page.locator('#saveBtn').click();

  await expect.poll(() => page.evaluate(async () => {
    const syncResult = await chrome.storage.sync.get('settings');
    const localResult = await chrome.storage.local.get('apiKeys');
    return {
      syncApiKey: syncResult.settings.apiKey,
      syncGoogleApiKey: syncResult.settings.googleOcr?.apiKey,
      syncLocalLlmApiKey: syncResult.settings.localLlm?.apiKey,
      localApiKeys: localResult.apiKeys
    };
  })).toEqual({
    syncApiKey: undefined,
    syncGoogleApiKey: undefined,
    syncLocalLlmApiKey: undefined,
    localApiKeys: {
      apiKey: 'legacy-sync-key',
      googleOcrApiKey: 'legacy-vision-key',
      localLlmApiKey: 'legacy-local-key'
    }
  });
});

test('options page does not resurrect synced API keys after local keys are cleared', async ({ page, extensionId }) => {
  await resetExtensionStorage(page, extensionId);
  await page.evaluate(async () => {
    await chrome.storage.sync.set({
      settings: {
        provider: 'openrouter',
        apiKey: 'old-sync-key',
        googleOcr: { apiKey: 'old-vision-key' },
        localLlm: { apiKey: 'old-local-key' }
      }
    });
    await chrome.storage.local.set({
      apiKeys: {
        apiKey: '',
        googleOcrApiKey: '',
        localLlmApiKey: ''
      }
    });
  });

  await gotoOptions(page, extensionId);

  await expect(page.locator('#apiKey')).toHaveValue('');
  await expect(page.locator('#googleOcrApiKey')).toHaveValue('');
  await expect(page.locator('#localLlmApiKey')).toHaveValue('');
});

test('options page only offers backend-supported local pipeline choices', async ({ page, extensionId }) => {
  await resetExtensionStorage(page, extensionId);
  await gotoOptions(page, extensionId);

  const choices = await page.evaluate(() => ({
    detectors: Array.from(document.querySelectorAll('#localDetector option'), option => option.value),
    ocr: Array.from(document.querySelectorAll('#localOcr option'), option => option.value),
    inpainters: Array.from(document.querySelectorAll('#localInpainter option'), option => option.value),
    translators: Array.from(document.querySelectorAll('#localTranslator option'), option => option.value)
  }));

  expect(choices).toEqual({
    detectors: ['default', 'rtdetr', 'animetext-yolo'],
    ocr: ['manga-ocr', 'paddle-ocr', 'google-ocr'],
    inpainters: ['flux2_klein_lanpaint', 'flux2_klein_q4', 'lama_large', 'none'],
    translators: ['', 'none', 'gemini', 'openai', 'openrouter', 'local']
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
          translator: ''
        }
      }
    });
  });

  await gotoOptions(page, extensionId);
  await expect(page.locator('#localTranslator')).toHaveValue('');

  await page.locator('#saveBtn').click();

  await expect.poll(() => page.evaluate(async () => {
    const result = await chrome.storage.sync.get('settings');
    return result.settings.localBackend.translator;
  })).toBe('');
});
