const fs = require('fs');
const path = require('path');
const settingsHelpers = require('../../extension/lib/settings');

const optionsHtml = fs.readFileSync(
  path.resolve(__dirname, '../../extension/options/options.html'),
  'utf8'
);

function setupOptionsDom() {
  const parsed = new DOMParser().parseFromString(optionsHtml, 'text/html');
  document.body.innerHTML = parsed.body.innerHTML;
}

function populateOptionsForm(overrides = {}) {
  const defaults = settingsHelpers.mergeSettings({
    pipelineMode: 'cloud',
    provider: 'openrouter',
    model: 'google/gemini-2.0-flash-exp:free',
    apiKey: 'browser-key',
    localBackend: {
      url: 'http://localhost:8001',
      authToken: '',
      detector: 'default',
      ocr: 'manga-ocr',
      inpainter: 'lama_large',
      translator: ''
    },
    localLlm: {
      baseUrl: 'http://localhost:11434',
      model: 'qwen',
      apiKey: 'local-key'
    },
    googleOcr: {
      apiKey: 'vision-key',
      languageHint: 'ja'
    }
  });
  const values = { ...defaults, ...overrides };

  document.getElementById('provider').value = values.provider;
  document.getElementById('model').value = values.model;
  document.getElementById('apiKey').value = values.apiKey;
  document.getElementById('baseUrl').value = values.baseUrl;
  document.getElementById('backendUrl').value = values.localBackend.url;
  document.getElementById('backendAuthToken').value = values.localBackend.authToken || '';
  document.getElementById('localDetector').value = values.localBackend.detector;
  document.getElementById('localOcr').value = values.localBackend.ocr;
  document.getElementById('localInpainter').value = values.localBackend.inpainter;
  document.getElementById('localTranslator').value = values.localBackend.translator;
  document.getElementById('localLlmBaseUrl').value = values.localLlm.baseUrl;
  document.getElementById('localLlmModel').value = values.localLlm.model;
  document.getElementById('localLlmApiKey').value = values.localLlm.apiKey;
  document.getElementById('googleOcrApiKey').value = values.googleOcr.apiKey;
  document.getElementById('googleOcrLanguageHint').value = values.googleOcr.languageHint;
  document.getElementById('targetLang').value = values.targetLang;
  document.getElementById('fontSize').value = String(values.fontSize);
  document.getElementById('fontFamily').value = values.fontFamily;
  document.getElementById('textAlignment').value = values.render.textAlignment;
  document.getElementById('textDirection').value = values.render.textDirection;
  document.getElementById('uppercase').checked = values.render.uppercase;
  document.getElementById('lineSpacing').value = String(values.render.lineSpacing);
  document.getElementById('borderSize').value = String(values.render.borderSize);
  document.getElementById('textColor').value = values.render.textColor;
  document.getElementById('borderColor').value = values.render.borderColor;
  document.getElementById('bgColor').value = values.render.bgColor;
  document.getElementById('bgOpacity').value = String(values.render.bgOpacity);
  document.getElementById('glowEnabled').checked = values.render.glowEnabled;
  document.getElementById('glowSize').value = String(values.render.glowSize);
  document.getElementById('glowColor').value = values.render.glowColor;
  document.getElementById('autoScale').checked = values.render.autoScale;
  document.getElementById('autoTranslate').checked = values.autoTranslate;
  document.getElementById('showProgress').checked = values.showProgress;
}

function loadOptionsModule() {
  jest.resetModules();
  global.MangaTranslatorSettings = require('../../extension/lib/settings');
  setupOptionsDom();
  return require('../../extension/options/options.js');
}

describe('options page settings persistence', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
    delete global.MangaTranslatorSettings;
    delete global.fetch;
    jest.resetModules();
  });

  it('preserves popup-selected pipeline mode when saving options-only fields', async () => {
    const options = loadOptionsModule();
    chrome.storage.sync._setData({
      settings: {
        pipelineMode: 'cloud',
        localBackend: {
          url: 'http://localhost:8001',
          requestTimeoutMs: 345000
        }
      }
    });
    chrome.storage.local._setData({
      apiKeys: {
        apiKey: 'existing-key',
        googleOcrApiKey: '',
        localLlmApiKey: ''
      }
    });
    populateOptionsForm({
      localBackend: {
        ...settingsHelpers.DEFAULT_SETTINGS.localBackend,
        url: 'http://127.0.0.1:8002/api/'
      }
    });

    await options.saveSettings();

    const result = await chrome.storage.sync.get('settings');
    expect(result.settings.pipelineMode).toBe('cloud');
    expect(result.settings.localBackend.url).toBe('http://127.0.0.1:8002/api');
    expect(result.settings.localBackend.requestTimeoutMs).toBe(345000);
  });

  it('stamps the current settings version on a fresh save', async () => {
    const options = loadOptionsModule();
    chrome.storage.sync._setData({});
    chrome.storage.local._setData({ apiKeys: {} });
    populateOptionsForm();

    await options.saveSettings();

    const result = await chrome.storage.sync.get('settings');
    expect(result.settings.settingsVersion).toBe(settingsHelpers.SETTINGS_VERSION);
  });

  it('trims pasted whitespace from model ids before saving', async () => {
    const options = loadOptionsModule();
    chrome.storage.sync._setData({});
    chrome.storage.local._setData({ apiKeys: {} });
    populateOptionsForm();
    document.getElementById('model').value = '  google/gemini-2.0-flash \n';
    document.getElementById('localLlmModel').value = ' qwen2.5-7b-instruct\n';

    await options.saveSettings();

    const result = await chrome.storage.sync.get('settings');
    expect(result.settings.model).toBe('google/gemini-2.0-flash');
    expect(result.settings.localLlm.model).toBe('qwen2.5-7b-instruct');
  });

  it('migrates legacy LaMa storage while loading the options page', async () => {
    const options = loadOptionsModule();
    chrome.storage.sync._setData({
      settings: {
        pipelineMode: 'local',
        localBackend: {
          detector: 'animetext-yolo',
          inpainter: 'lama_large'
        }
      }
    });

    await options.loadSettings();

    const result = await chrome.storage.sync.get('settings');
    expect(result.settings).toEqual({
      settingsVersion: settingsHelpers.SETTINGS_VERSION,
      fontSize: 16,
      pipelineMode: 'local',
      localBackend: {
        detector: 'animetext-yolo',
        inpainter: 'flux2_klein_lanpaint'
      }
    });
    expect(document.getElementById('localInpainter').value)
      .toBe('flux2_klein_lanpaint');
  });

  it('rejects non-loopback backend URLs during save', async () => {
    const options = loadOptionsModule();
    chrome.storage.sync._setData({
      settings: {
        pipelineMode: 'local',
        localBackend: { url: 'http://localhost:8001' }
      }
    });
    populateOptionsForm({
      localBackend: {
        ...settingsHelpers.DEFAULT_SETTINGS.localBackend,
        url: 'https://example.com'
      }
    });

    await options.saveSettings();

    expect(chrome.storage.sync.set).not.toHaveBeenCalled();
    expect(chrome.storage.local.set).not.toHaveBeenCalled();
    expect(document.querySelector('.toast').textContent).toContain('Only http://localhost');
  });

  it('accepts an IPv6 loopback backend URL during save', async () => {
    const options = loadOptionsModule();
    chrome.storage.sync._setData({
      settings: {
        pipelineMode: 'local',
        localBackend: { url: 'http://localhost:8001' }
      }
    });
    chrome.storage.local._setData({ apiKeys: {} });
    populateOptionsForm({
      localBackend: {
        ...settingsHelpers.DEFAULT_SETTINGS.localBackend,
        url: 'http://[::1]:8001'
      }
    });

    await options.saveSettings();

    const result = await chrome.storage.sync.get('settings');
    expect(result.settings.localBackend.url).toBe('http://[::1]:8001');
  });

  it('persists backend auth token privately when saving options', async () => {
    const options = loadOptionsModule();
    chrome.storage.sync._setData({
      settings: {
        pipelineMode: 'local',
        localBackend: { url: 'http://localhost:8001' }
      }
    });
    chrome.storage.local._setData({ apiKeys: {} });
    populateOptionsForm({
      localBackend: {
        ...settingsHelpers.DEFAULT_SETTINGS.localBackend,
        url: 'http://127.0.0.1:8001',
        authToken: 'private-backend-token'
      }
    });

    await options.saveSettings();

    const syncResult = await chrome.storage.sync.get('settings');
    const localResult = await chrome.storage.local.get('apiKeys');
    expect(syncResult.settings.localBackend.authToken).toBeUndefined();
    expect(localResult.apiKeys.backendAuthToken).toBe('private-backend-token');
  });

  it('loads the private backend auth token into the options form', async () => {
    const options = loadOptionsModule();
    chrome.storage.sync._setData({
      settings: {
        localBackend: { url: 'http://localhost:8001' }
      }
    });
    chrome.storage.local._setData({
      apiKeys: { backendAuthToken: 'private-backend-token' }
    });

    await options.loadSettings();

    expect(document.getElementById('backendAuthToken').value).toBe('private-backend-token');
  });

  it('shows Distilled LanPaint instead of a blank selector for malformed storage', async () => {
    const options = loadOptionsModule();
    chrome.storage.sync._setData({
      settings: { localBackend: { inpainter: 'removed-model' } }
    });

    await options.loadSettings();

    expect(document.getElementById('localInpainter').value)
      .toBe('flux2_klein_lanpaint');
  });

  it('offers the plain FLUX.2 Klein Q4 inpainter and keeps it selected', async () => {
    const options = loadOptionsModule();
    const choices = Array.from(
      document.querySelectorAll('#localInpainter option'),
      option => option.value
    );
    expect(choices).toContain('flux2_klein_q4');

    chrome.storage.sync._setData({
      settings: {
        settingsVersion: settingsHelpers.SETTINGS_VERSION,
        localBackend: { inpainter: 'flux2_klein_q4' }
      }
    });
    await options.loadSettings();
    expect(document.getElementById('localInpainter').value).toBe('flux2_klein_q4');

    await options.saveSettings();

    const result = await chrome.storage.sync.get('settings');
    expect(result.settings.localBackend.inpainter).toBe('flux2_klein_q4');
  });

  it('shows the default OCR instead of a blank selector for malformed storage', async () => {
    const options = loadOptionsModule();
    chrome.storage.sync._setData({
      settings: { localBackend: { ocr: 'removed-ocr' } }
    });

    await options.loadSettings();

    expect(document.getElementById('localOcr').value).toBe('manga-ocr');
  });

  it('keeps a stored paddle-ocr selection when saving', async () => {
    const options = loadOptionsModule();
    chrome.storage.sync._setData({
      settings: { localBackend: { ocr: 'paddle-ocr' } }
    });
    await options.loadSettings();
    expect(document.getElementById('localOcr').value).toBe('paddle-ocr');

    await options.saveSettings();

    const result = await chrome.storage.sync.get('settings');
    expect(result.settings.localBackend.ocr).toBe('paddle-ocr');
  });

  it('loads legacy AnimeText aliases as a visible detector selection', async () => {
    const options = loadOptionsModule();
    chrome.storage.sync._setData({
      settings: { localBackend: { detector: 'anime-text-yolo' } }
    });

    await options.loadSettings();

    expect(document.getElementById('localDetector').value)
      .toBe('animetext-yolo');
  });

  it('shows the default detector instead of a blank selector for malformed storage', async () => {
    const options = loadOptionsModule();
    chrome.storage.sync._setData({
      settings: { localBackend: { detector: 'removed-detector' } }
    });

    await options.loadSettings();

    expect(document.getElementById('localDetector').value).toBe('default');
  });

  it('counts cache entries even when metadata is missing or stale', async () => {
    const options = loadOptionsModule();
    chrome.storage.local._setData({
      manga_cache_metadata: ['manga_cache_a'],

      manga_cache_b: { value: 'cached' },
      apiKeys: { apiKey: 'not-a-cache-entry' }
    });

    await options.updateCacheStats();

    expect(document.getElementById('cacheCount').textContent).toBe('2');
  });

  it('reports a degraded backend as not ready', async () => {
    const options = loadOptionsModule();
    document.getElementById('backendUrl').value = 'http://localhost:8001';
    global.fetch = jest.fn(async () => ({
      ok: true,
      json: async () => ({
        status: 'degraded',
        models_loaded: false,
        device: 'cpu'
      })
    }));

    await options.testConnection();

    expect(document.getElementById('connectionStatus').textContent)
      .toBe('Backend models are not ready');
    expect(document.getElementById('connectionStatus').className).toContain('error');
  });

  it('reports a loading backend as still loading', async () => {
    const options = loadOptionsModule();
    document.getElementById('backendUrl').value = 'http://localhost:8001';
    global.fetch = jest.fn(async () => ({
      ok: true,
      json: async () => ({ status: 'loading', models_loaded: false })
    }));

    await options.testConnection();

    expect(document.getElementById('connectionStatus').textContent).toContain('still loading');
  });

  it.each([
    [401, 'rejected the access token'],
    [503, 'no access token configured']
  ])('explains a %i health response as a token problem', async (status, expected) => {
    const options = loadOptionsModule();
    document.getElementById('backendUrl').value = 'http://localhost:8001';
    global.fetch = jest.fn(async () => ({ ok: false, status }));

    await options.testConnection();

    expect(document.getElementById('connectionStatus').textContent).toContain(expected);
    expect(document.getElementById('connectionStatus').className).toContain('error');
  });

  it('does not send the auth token to a non-loopback backend when testing', async () => {
    const options = loadOptionsModule();
    document.getElementById('backendUrl').value = 'http://example.com:8001';
    document.getElementById('backendAuthToken').value = 'private-backend-token';
    global.fetch = jest.fn();

    await options.testConnection();

    expect(global.fetch).not.toHaveBeenCalled();
    expect(document.getElementById('connectionStatus').className).toContain('error');
  });

  it('uses the private backend auth token when testing a protected backend', async () => {
    const options = loadOptionsModule();
    document.getElementById('backendUrl').value = 'http://localhost:8001';
    document.getElementById('backendAuthToken').value = 'private-backend-token';
    global.fetch = jest.fn(async () => ({
      ok: true,
      json: async () => ({ status: 'ok', models_loaded: true, device: 'cpu' })
    }));

    await options.testConnection();

    expect(global.fetch).toHaveBeenCalledWith(
      'http://localhost:8001/health',
      expect.objectContaining({
        method: 'GET',
        headers: { 'X-Manga-Translator-Token': 'private-backend-token' }
      })
    );
    expect(document.getElementById('connectionStatus').className).toContain('success');
  });
});

describe('options page API key warnings', () => {
  async function loadWarningPage({ settings, apiKeys = {} }) {
    const options = loadOptionsModule();
    chrome.storage.sync._setData({ settings });
    chrome.storage.local._setData({ apiKeys });
    await options.init();
    return options;
  }

  function isVisible(id) {
    return document.getElementById(id).style.display !== 'none';
  }

  function change(id, value, eventName = 'change') {
    const element = document.getElementById(id);
    element.value = value;
    element.dispatchEvent(new Event(eventName));
  }

  afterEach(() => {
    delete global.MangaTranslatorSettings;
    jest.resetModules();
  });

  it('warns when the provider needs a key and none is configured', async () => {
    await loadWarningPage({
      settings: { pipelineMode: 'local', provider: 'openrouter', localBackend: { translator: '' } }
    });

    expect(isVisible('apiKeyWarning')).toBe(true);
  });

  it('hides the translator warning when the local translator is none or local', async () => {
    await loadWarningPage({
      settings: { pipelineMode: 'local', provider: 'openrouter', localBackend: { translator: '' } }
    });
    expect(isVisible('apiKeyWarning')).toBe(true);

    change('localTranslator', 'none');
    expect(isVisible('apiKeyWarning')).toBe(false);

    change('localTranslator', 'gemini');
    expect(isVisible('apiKeyWarning')).toBe(true);

    change('localTranslator', 'local');
    expect(isVisible('apiKeyWarning')).toBe(false);

    change('localTranslator', '');
    expect(isVisible('apiKeyWarning')).toBe(true);
  });

  it('hides the translator warning once an API key is typed', async () => {
    await loadWarningPage({
      settings: { pipelineMode: 'local', provider: 'openrouter' }
    });
    expect(isVisible('apiKeyWarning')).toBe(true);

    change('apiKey', 'typed-key', 'input');

    expect(isVisible('apiKeyWarning')).toBe(false);
  });

  it('ignores the local translator override in cloud mode', async () => {
    await loadWarningPage({
      settings: { pipelineMode: 'cloud', provider: 'openrouter', localBackend: { translator: 'none' } }
    });

    expect(isVisible('apiKeyWarning')).toBe(true);
  });

  it('warns when google-ocr is selected without a Google Vision key', async () => {
    await loadWarningPage({
      settings: { pipelineMode: 'local', localBackend: { ocr: 'google-ocr', translator: 'none' } }
    });
    expect(isVisible('googleOcrKeyWarning')).toBe(true);

    change('googleOcrApiKey', 'vision-key', 'input');
    expect(isVisible('googleOcrKeyWarning')).toBe(false);

    change('googleOcrApiKey', '', 'input');
    expect(isVisible('googleOcrKeyWarning')).toBe(true);

    change('localOcr', 'manga-ocr');
    expect(isVisible('googleOcrKeyWarning')).toBe(false);

    change('localOcr', 'google-ocr');
    expect(isVisible('googleOcrKeyWarning')).toBe(true);
  });

  it('does not warn about google-ocr when a Google Vision key is stored', async () => {
    await loadWarningPage({
      settings: { pipelineMode: 'local', localBackend: { ocr: 'google-ocr', translator: 'none' } },
      apiKeys: { googleOcrApiKey: 'vision-key' }
    });

    expect(isVisible('googleOcrKeyWarning')).toBe(false);
  });

  it('does not warn about google-ocr in cloud mode', async () => {
    await loadWarningPage({
      settings: { pipelineMode: 'cloud', localBackend: { ocr: 'google-ocr' } }
    });

    expect(isVisible('googleOcrKeyWarning')).toBe(false);
  });
});
