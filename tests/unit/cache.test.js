const {
  generateCacheKey,
  getFromCache,
  saveToCache,
  clearAllCache,
  getCacheStats,
  pruneExpiredCache,
  evictLRUEntries,
  isValidCacheEntry,
  getCacheKeys
} = require('../../extension/lib/cache');

function makeCacheSettings() {
  return {
    pipelineMode: 'local',
    provider: 'openrouter',
    model: 'provider-model',
    baseUrl: 'https://provider.example/v1',
    targetLang: 'English',
    apiKey: 'translation-secret',
    localBackend: {
      url: 'http://localhost:8001',
      detector: 'default',
      ocr: 'manga-ocr',
      inpainter: 'lama_large',
      translator: 'openrouter'
    },
    localLlm: {
      baseUrl: 'http://localhost:8000/v1',
      model: 'llama',
      apiKey: 'local-secret'
    },
    googleOcr: {
      languageHint: 'ja',
      apiKey: 'vision-secret'
    },
    render: {
      textAlignment: 'center',
      textDirection: 'auto',
      uppercase: false,
      lineSpacing: 1.2,
      borderSize: 2,
      textColor: '#111111',
      borderColor: '#222222',
      bgColor: '#ffffff',
      bgOpacity: 0.8,
      glowEnabled: false,
      glowSize: 2,
      glowColor: '#333333',
      autoScale: true
    }
  };
}

const CACHE_KEY_MUTATIONS = [
  ['pipeline mode', settings => { settings.pipelineMode = 'cloud'; }],
  ['provider', settings => { settings.provider = 'gemini'; }],
  ['provider model', settings => { settings.model = 'provider-model-2'; }],
  ['provider base URL', settings => { settings.baseUrl = 'https://other.example/v1'; }],
  ['target language', settings => { settings.targetLang = 'Thai'; }],
  ['font size', settings => { settings.fontSize = 48; }],
  ['backend URL', settings => { settings.localBackend.url = 'http://localhost:8002'; }],
  ['detector', settings => { settings.localBackend.detector = 'rtdetr'; }],
  ['OCR engine', settings => { settings.localBackend.ocr = 'paddle-ocr'; }],
  ['inpainter', settings => { settings.localBackend.inpainter = 'opencv'; }],
  ['translator', settings => { settings.localBackend.translator = 'gemini'; }],
  ['local LLM base URL', settings => { settings.localLlm.baseUrl = 'http://localhost:11434/v1'; }],
  ['local LLM model', settings => { settings.localLlm.model = 'qwen'; }],
  ['Google OCR language hint', settings => { settings.googleOcr.languageHint = 'ko'; }],
  ['text alignment', settings => { settings.render.textAlignment = 'left'; }],
  ['text direction', settings => { settings.render.textDirection = 'rtl'; }],
  ['uppercase', settings => { settings.render.uppercase = true; }],
  ['line spacing', settings => { settings.render.lineSpacing = 1.4; }],
  ['border size', settings => { settings.render.borderSize = 3; }],
  ['text color', settings => { settings.render.textColor = '#444444'; }],
  ['border color', settings => { settings.render.borderColor = '#555555'; }],
  ['background color', settings => { settings.render.bgColor = '#eeeeee'; }],
  ['background opacity', settings => { settings.render.bgOpacity = 0.5; }],
  ['glow enabled', settings => { settings.render.glowEnabled = true; }],
  ['glow size', settings => { settings.render.glowSize = 4; }],
  ['glow color', settings => { settings.render.glowColor = '#666666'; }],
  ['auto scale', settings => { settings.render.autoScale = false; }]
];

describe('cache utilities', () => {
  it('generates cache key from settings', () => {
    const key = generateCacheKey('hash123', {
      provider: 'gemini',
      model: 'gemini-2.0-flash',
      targetLang: 'English'
    });

    expect(key).toContain('hash123');
    expect(key).toContain('gemini');
  });

  it.each(CACHE_KEY_MUTATIONS)('changes when %s changes', (_name, mutate) => {
    const baselineSettings = makeCacheSettings();
    const changedSettings = makeCacheSettings();
    mutate(changedSettings);

    expect(generateCacheKey('hash123', changedSettings))
      .not.toBe(generateCacheKey('hash123', baselineSettings));
  });

  it('uses the normalized font size in the cache identity', () => {
    const defaultSize = makeCacheSettings();
    const explicitDefault = makeCacheSettings();
    const clampedLow = makeCacheSettings();
    const clampedHigh = makeCacheSettings();
    const maximum = makeCacheSettings();

    explicitDefault.fontSize = '16';
    clampedLow.fontSize = 4;
    clampedHigh.fontSize = 120;
    maximum.fontSize = 96;

    expect(generateCacheKey('hash123', explicitDefault))
      .toBe(generateCacheKey('hash123', defaultSize));
    expect(generateCacheKey('hash123', clampedLow))
      .toBe(generateCacheKey('hash123', { ...defaultSize, fontSize: 6 }));
    expect(generateCacheKey('hash123', clampedHigh))
      .toBe(generateCacheKey('hash123', maximum));
  });

  it('uses one cache identity for the legacy lama inpainter alias', () => {
    const canonical = makeCacheSettings();
    const legacy = makeCacheSettings();
    canonical.localBackend.inpainter = 'lama_large';
    legacy.localBackend.inpainter = 'lama';

    expect(generateCacheKey('hash123', legacy)).toBe(generateCacheKey('hash123', canonical));
  });

  it('uses one cache identity for LanPaint aliases', () => {
    const canonical = makeCacheSettings();
    const alias = makeCacheSettings();
    canonical.localBackend.inpainter = 'flux2_klein_lanpaint';
    alias.localBackend.inpainter = 'lanpaint';

    expect(generateCacheKey('hash123', alias)).toBe(generateCacheKey('hash123', canonical));
  });

  it('does not include private API keys in cache identity', () => {
    const baselineSettings = makeCacheSettings();
    const changedSecrets = makeCacheSettings();
    changedSecrets.apiKey = 'different-translation-secret';
    changedSecrets.localLlm.apiKey = 'different-local-secret';
    changedSecrets.googleOcr.apiKey = 'different-vision-secret';

    expect(generateCacheKey('hash123', changedSecrets))
      .toBe(generateCacheKey('hash123', baselineSettings));
  });

  it('saves and retrieves cached data', async () => {
    const key = generateCacheKey('hashabc', {
      provider: 'gemini',
      model: 'm1',
      targetLang: 'English'
    });

    await saveToCache(key, { foo: 'bar' });
    const data = await getFromCache(key);

    expect(data).toEqual({ foo: 'bar' });
  });

  it('recovers cache keys that are missing from stale metadata', async () => {
    await chrome.storage.local.set({
      manga_cache_metadata: ['manga_cache_existing'],
      manga_cache_existing: {
        timestamp: Date.now(),
        data: { existing: true }
      },
      manga_cache_missing_from_meta: {
        timestamp: Date.now(),
        data: { recovered: true }
      }
    });

    const keys = await getCacheKeys();

    expect(keys).toEqual(expect.arrayContaining([
      'manga_cache_existing',
      'manga_cache_missing_from_meta'
    ]));
  });

  it('keeps metadata complete after concurrent cache saves', async () => {
    const key1 = 'manga_cache_concurrent_1';
    const key2 = 'manga_cache_concurrent_2';

    await Promise.all([
      saveToCache(key1, { a: 1 }),
      saveToCache(key2, { b: 2 })
    ]);

    const result = await chrome.storage.local.get('manga_cache_metadata');
    expect(result.manga_cache_metadata).toEqual(expect.arrayContaining([key1, key2]));
  });

  it('retains five realistic rendered pages without evicting earlier entries', async () => {
    const renderedPageBytes = 2 * 1024 * 1024;
    const encodedPageLength = Math.ceil(renderedPageBytes / 3) * 4;
    const keys = Array.from({ length: 5 }, (_value, index) => `manga_cache_realistic_page_${index + 1}`);

    for (const [index, key] of keys.entries()) {
      await saveToCache(key, {
        translatedImage: String(index).repeat(encodedPageLength),
        mimeType: 'image/png',
        mode: 'local'
      });
    }

    const metadata = await chrome.storage.local.get('manga_cache_metadata');
    const cachedPages = await Promise.all(keys.map(key => getFromCache(key)));

    expect(metadata.manga_cache_metadata).toHaveLength(5);
    expect(new Set(metadata.manga_cache_metadata)).toEqual(new Set(keys));
    expect(cachedPages).toHaveLength(5);
    expect(cachedPages.every(page => page?.translatedImage.length === encodedPageLength)).toBe(true);
  });

  it('removes malformed cache entries instead of returning undefined data', async () => {
    const key = 'manga_cache_malformed';
    await chrome.storage.local.set({
      [key]: {
        timestamp: Date.now(),
        staleShape: true
      }
    });

    const data = await getFromCache(key);
    const stored = await chrome.storage.local.get(key);

    expect(data).toBeNull();
    expect(stored[key]).toBeUndefined();
  });

  it('validates cache entry shape', () => {
    expect(isValidCacheEntry({ timestamp: Date.now(), data: { ok: true } })).toBe(true);
    expect(isValidCacheEntry({ timestamp: Date.now() })).toBe(false);
    expect(isValidCacheEntry({ timestamp: 'now', data: { ok: true } })).toBe(false);
    expect(isValidCacheEntry(null)).toBe(false);
  });

  it('clears cache entries', async () => {
    const key = generateCacheKey('hashclear', {
      provider: 'gemini',
      model: 'm1',
      targetLang: 'English'
    });

    await saveToCache(key, { foo: 'bar' });
    const cleared = await clearAllCache();

    expect(cleared).toBeGreaterThanOrEqual(1);
  });

  it('getCacheStats returns stats object', async () => {
    const stats = await getCacheStats();
    expect(stats).toHaveProperty('count');
    expect(stats).toHaveProperty('sizeBytes');
    expect(stats).toHaveProperty('sizeKB');
    expect(typeof stats.count).toBe('number');
  });

  it('getCacheStats reflects cached entries', async () => {
    const key1 = generateCacheKey('stat1', { provider: 'gemini', model: 'm1', targetLang: 'English' });
    const key2 = generateCacheKey('stat2', { provider: 'gemini', model: 'm1', targetLang: 'English' });

    await saveToCache(key1, { a: 1 });
    await saveToCache(key2, { b: 2 });

    const stats = await getCacheStats();
    expect(stats.count).toBeGreaterThanOrEqual(2);
    expect(stats.sizeBytes).toBeGreaterThan(0);
    expect(stats.oldestEntry).not.toBeNull();
    expect(stats.newestEntry).not.toBeNull();
  });

  it('getCacheStats ignores malformed legacy cache entries', async () => {
    await chrome.storage.local.set({
      manga_cache_good: {
        timestamp: Date.now(),
        lastAccessed: Date.now(),
        data: { ok: true }
      },
      manga_cache_bad: {
        timestamp: Date.now(),
        staleShape: true
      }
    });

    const stats = await getCacheStats();

    expect(stats.count).toBe(1);
    expect(stats.sizeBytes).toBeGreaterThan(0);
  });

  it('getCacheStats returns zero counts on empty cache', async () => {
    await clearAllCache();
    const stats = await getCacheStats();
    expect(stats.count).toBe(0);
    expect(stats.sizeBytes).toBe(0);
  });

  it('pruneExpiredCache returns 0 when nothing is expired', async () => {
    const key = generateCacheKey('fresh', { provider: 'gemini', model: 'm1', targetLang: 'English' });
    await saveToCache(key, { data: 'fresh' });

    const pruned = await pruneExpiredCache();
    expect(pruned).toBe(0);
  });

  it('pruneExpiredCache removes expired entries', async () => {
    const expiredKey = 'manga_cache_expired_test';
    const expiredEntry = {
      timestamp: Date.now() - (8 * 24 * 60 * 60 * 1000),
      data: { old: true }
    };
    await chrome.storage.local.set({ [expiredKey]: expiredEntry });

    const pruned = await pruneExpiredCache();
    expect(pruned).toBeGreaterThanOrEqual(1);

    const result = await chrome.storage.local.get(expiredKey);
    expect(result[expiredKey]).toBeUndefined();
  });

  it('pruneExpiredCache removes malformed cache entries', async () => {
    await chrome.storage.local.set({
      manga_cache_bad_shape: {
        timestamp: Date.now(),
        noData: true
      }
    });

    const pruned = await pruneExpiredCache();
    const result = await chrome.storage.local.get('manga_cache_bad_shape');

    expect(pruned).toBe(1);
    expect(result.manga_cache_bad_shape).toBeUndefined();
  });

  it('evictLRUEntries removes malformed entries and then least recently used entries', async () => {
    await chrome.storage.local.set({
      manga_cache_malformed: {
        timestamp: Date.now(),
        oldShape: true
      },
      manga_cache_old: {
        timestamp: 1000,
        lastAccessed: 1000,
        data: { value: 'old' }
      },
      manga_cache_new: {
        timestamp: 3000,
        lastAccessed: 3000,
        data: { value: 'new' }
      }
    });

    const freed = await evictLRUEntries(80);
    const remaining = await chrome.storage.local.get(null);

    expect(freed).toBeGreaterThan(0);
    expect(remaining.manga_cache_malformed).toBeUndefined();
    expect(remaining.manga_cache_old).toBeUndefined();
    expect(remaining.manga_cache_new).toBeDefined();
  });

  describe('large cache performance', () => {
    const storage = () => chrome.storage.local;

    afterEach(() => {
      delete storage().getKeys;
      delete storage().getBytesInUse;
    });

    it('saves without reading all stored values when the browser can list keys and sizes', async () => {
      storage()._setData({
        manga_cache_existing: { timestamp: Date.now(), lastAccessed: Date.now(), data: { image: 'x' } }
      });
      storage().getKeys = jest.fn(async () => Object.keys(storage()._getData()));
      storage().getBytesInUse = jest.fn(async () => 100);
      storage().get.mockClear();

      await saveToCache('manga_cache_new', { image: 'y' });

      expect(storage().get).not.toHaveBeenCalledWith(null);
      expect(storage().getBytesInUse).toHaveBeenCalledWith(['manga_cache_existing']);
      for (const [keys] of storage().get.mock.calls) {
        expect(keys).toBe('manga_cache_metadata');
      }
      expect(await getCacheKeys()).toEqual(expect.arrayContaining(['manga_cache_existing', 'manga_cache_new']));
    });

    it('still finds unindexed entries on browsers without storage.getKeys', async () => {
      storage()._setData({
        manga_cache_unindexed: { timestamp: Date.now(), lastAccessed: Date.now(), data: { image: 'x' } }
      });

      expect(await getCacheKeys()).toEqual(['manga_cache_unindexed']);
    });

    it('does not rewrite a recently accessed entry on a cache hit', async () => {
      const now = Date.now();
      storage()._setData({ manga_cache_hit: { timestamp: now, lastAccessed: now - 1000, data: { image: 'x' } } });
      storage().set.mockClear();

      await expect(getFromCache('manga_cache_hit')).resolves.toEqual({ image: 'x' });

      expect(storage().set).not.toHaveBeenCalled();
    });

    it('records access on a cache hit once the last write is old enough to affect eviction', async () => {
      const now = Date.now();
      const stale = now - 6 * 60 * 1000;
      storage()._setData({ manga_cache_hit: { timestamp: stale, lastAccessed: stale, data: { image: 'x' } } });

      await getFromCache('manga_cache_hit');

      expect(storage()._getData().manga_cache_hit.lastAccessed).toBeGreaterThanOrEqual(now);
    });
  });
});
