const {
  SETTINGS_VERSION,
  DEFAULT_SETTINGS,
  migrateStoredSettings,
  mergeSettings,
  applyStoredApiKeys,
  splitSettingsForStorage,
  isValidLocalBackendUrl,
  normalizeLocalBackendBaseUrl,
  buildLocalBackendUrl,
  normalizeDetector,
  normalizeInpainter,
  resolveTranslator,
  translatorNeedsApiKey
} = require('../../extension/lib/settings');

describe('settings shape', () => {

  it('migrates the unversioned LaMa default to Distilled LanPaint once', () => {
    const stored = {
      provider: 'openrouter',
      localBackend: {
        url: 'http://localhost:8001',
        inpainter: 'lama_large',
        requestTimeoutMs: 345000
      },
      render: { borderSize: 3 }
    };
    const original = JSON.parse(JSON.stringify(stored));

    const first = migrateStoredSettings(stored);
    expect(first).toEqual({
      changed: true,
      settings: {
        ...original,
        fontSize: 16,
        settingsVersion: SETTINGS_VERSION,
        localBackend: {
          ...original.localBackend,
          inpainter: 'flux2_klein_lanpaint'
        }
      }
    });
    expect(stored).toEqual(original);

    const second = migrateStoredSettings(first.settings);
    expect(second.changed).toBe(false);
    expect(second.settings).toEqual(first.settings);
  });

  it('preserves current-version LaMa as an explicit user choice', () => {
    const result = migrateStoredSettings({
      settingsVersion: SETTINGS_VERSION,
      localBackend: { inpainter: 'lama_large' }
    });

    expect(result.changed).toBe(false);
    expect(result.settings.localBackend.inpainter).toBe('lama_large');
  });

  it('migrates the old 14px default to 16px but preserves custom sizes', () => {
    expect(migrateStoredSettings({ settingsVersion: 1, fontSize: 14 })).toEqual({
      changed: true,
      settings: { settingsVersion: SETTINGS_VERSION, fontSize: 16 }
    });
    expect(migrateStoredSettings({ settingsVersion: 1, fontSize: 18 })).toEqual({
      changed: true,
      settings: { settingsVersion: SETTINGS_VERSION, fontSize: 18 }
    });
  });

  it.each(['flux2_klein_lanpaint', 'flux2_klein_q4', 'none'])(
    'stamps an unversioned %s setting without changing it',
    inpainter => {
      const result = migrateStoredSettings({ localBackend: { inpainter } });

      expect(result.changed).toBe(true);
      expect(result.settings).toEqual({
        settingsVersion: SETTINGS_VERSION,
        fontSize: 16,
        localBackend: { inpainter }
      });
    }
  );

  it('merges nested defaults without losing omitted objects', () => {
    const settings = mergeSettings({
      targetLang: 'Thai',
      localBackend: { ocr: 'google-ocr' },
      render: { borderSize: 3 }
    });

    expect(settings.targetLang).toBe('Thai');
    expect(settings.localBackend.url).toBe(DEFAULT_SETTINGS.localBackend.url);
    expect(settings.localBackend.ocr).toBe('google-ocr');
    expect(settings.localBackend.requestTimeoutMs).toBe(DEFAULT_SETTINGS.localBackend.requestTimeoutMs);
    expect(settings.render.lineSpacing).toBe(DEFAULT_SETTINGS.render.lineSpacing);
    expect(settings.render.borderSize).toBe(3);
    expect(settings.googleOcr.languageHint).toBe('');
  });

  it('applies private API keys from local storage shape', () => {
    const settings = applyStoredApiKeys(
      { googleOcr: { languageHint: 'ja' } },
      {
        apiKey: 'translate-key',
        googleOcrApiKey: 'vision-key',
        localLlmApiKey: 'local-key'
      }
    );

    expect(settings.apiKey).toBe('translate-key');
    expect(settings.googleOcr.apiKey).toBe('vision-key');
    expect(settings.googleOcr.languageHint).toBe('ja');
    expect(settings.localLlm.apiKey).toBe('local-key');
  });

  it('uses local API key fields as authoritative when they are empty', () => {
    const settings = applyStoredApiKeys(
      {
        apiKey: 'old-sync-key',
        googleOcr: { apiKey: 'old-vision-key' },
        localLlm: { apiKey: 'old-local-key' }
      },
      {
        apiKey: '',
        googleOcrApiKey: '',
        localLlmApiKey: ''
      }
    );

    expect(settings.apiKey).toBe('');
    expect(settings.googleOcr.apiKey).toBe('');
    expect(settings.localLlm.apiKey).toBe('');
  });

  it('preserves migrated sync API keys when local key storage does not exist yet', () => {
    const settings = applyStoredApiKeys(
      {
        apiKey: 'old-sync-key',
        googleOcr: { apiKey: 'old-vision-key' },
        localLlm: { apiKey: 'old-local-key' }
      },
      {}
    );

    expect(settings.apiKey).toBe('old-sync-key');
    expect(settings.googleOcr.apiKey).toBe('old-vision-key');
    expect(settings.localLlm.apiKey).toBe('old-local-key');
  });

  it('splits public settings from private API keys', () => {
    const { syncSettings, apiKeys } = splitSettingsForStorage({
      apiKey: 'translate-key',
      googleOcr: { apiKey: 'vision-key' },
      localLlm: { apiKey: 'local-key' },
      localBackend: { authToken: 'backend-token' }
    });

    expect(syncSettings.apiKey).toBeUndefined();
    expect(syncSettings.googleOcr.apiKey).toBeUndefined();
    expect(syncSettings.localLlm.apiKey).toBeUndefined();
    expect(apiKeys).toEqual({
      apiKey: 'translate-key',
      backendAuthToken: 'backend-token',
      googleOcrApiKey: 'vision-key',
      localLlmApiKey: 'local-key'
    });
  });

  it('resolves translator by pipeline mode', () => {
    expect(resolveTranslator({
      pipelineMode: 'local',
      provider: 'openrouter',
      localBackend: { translator: 'gemini' }
    })).toBe('gemini');
    expect(resolveTranslator({
      pipelineMode: 'local',
      provider: 'openrouter',
      localBackend: { translator: 'none' }
    })).toBe('none');
    expect(resolveTranslator({
      pipelineMode: 'local',
      provider: 'openrouter',
      localBackend: { translator: '' }
    })).toBe('openrouter');
    expect(resolveTranslator({
      pipelineMode: 'cloud',
      provider: 'openrouter',
      localBackend: { translator: 'none' }
    })).toBe('openrouter');
    expect(translatorNeedsApiKey('openrouter')).toBe(true);
    expect(translatorNeedsApiKey('local')).toBe(false);
    expect(translatorNeedsApiKey('none')).toBe(false);
  });

  it('validates and normalizes local backend URLs', () => {
    expect(isValidLocalBackendUrl('http://localhost:8001')).toBe(true);
    expect(isValidLocalBackendUrl('http://127.0.0.1:8001/api/')).toBe(true);
    expect(isValidLocalBackendUrl('https://localhost:8001')).toBe(false);
    expect(isValidLocalBackendUrl('http://example.com:8001')).toBe(false);

    expect(normalizeLocalBackendBaseUrl('http://127.0.0.1:8001/api/')).toBe('http://127.0.0.1:8001/api');
    expect(buildLocalBackendUrl('http://localhost:8001/api/', '/health')).toBe('http://localhost:8001/api/health');
    expect(() => normalizeLocalBackendBaseUrl('https://localhost:8001')).toThrow(/Local backend URL/);
  });

  it('migrates the legacy lama inpainter alias to lama_large', () => {
    expect(normalizeInpainter('lama')).toBe('lama_large');
    expect(mergeSettings({ localBackend: { inpainter: 'LAMA' } }).localBackend.inpainter)
      .toBe('lama_large');
    expect(normalizeInpainter('none')).toBe('none');
  });

  it('uses Distilled LanPaint by default and canonicalizes its aliases', () => {
    expect(DEFAULT_SETTINGS.localBackend.inpainter).toBe('flux2_klein_lanpaint');
    expect(normalizeInpainter('lanpaint')).toBe('flux2_klein_lanpaint');
    expect(normalizeInpainter('flux_lanpaint')).toBe('flux2_klein_lanpaint');
    expect(normalizeInpainter('flux2-klein-lanpaint')).toBe('flux2_klein_lanpaint');
  });

  it('repairs blank, malformed, and non-string inpainter settings', () => {
    for (const value of ['', '   ', 'unknown-runtime', null, 42]) {
      expect(normalizeInpainter(value)).toBe('flux2_klein_lanpaint');
      expect(mergeSettings({ localBackend: { inpainter: value } }).localBackend.inpainter)
        .toBe('flux2_klein_lanpaint');
    }
  });

  it('canonicalizes AnimeText detector aliases', () => {
    for (const value of ['animetext', 'animetext_yolo', 'anime-text-yolo']) {
      expect(normalizeDetector(value)).toBe('animetext-yolo');
      expect(mergeSettings({ localBackend: { detector: value } }).localBackend.detector)
        .toBe('animetext-yolo');
    }
  });

  it('repairs blank, malformed, and non-string detector settings', () => {
    for (const value of ['', '   ', 'removed-detector', null, 42]) {
      expect(normalizeDetector(value)).toBe('default');
      expect(mergeSettings({ localBackend: { detector: value } }).localBackend.detector)
        .toBe('default');
    }
  });
});
