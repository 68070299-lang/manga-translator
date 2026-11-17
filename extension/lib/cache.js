/**
 * Caching utilities for Manga Translator
 * Uses chrome.storage.local and Cache API for storing translations
 */

const CACHE_PREFIX = 'manga_cache_';
const CACHE_TTL_DAYS = 7;
const TARGET_CACHED_PAGE_COUNT = 5;
const TARGET_RENDERED_PAGE_BYTES = 2 * 1024 * 1024;
const BASE64_EXPANSION_NUMERATOR = 4;
const BASE64_EXPANSION_DENOMINATOR = 3;
const CACHE_HEADROOM_BYTES = 2 * 1024 * 1024;
const STORAGE_QUOTA_BYTES = Math.ceil(
  TARGET_CACHED_PAGE_COUNT * TARGET_RENDERED_PAGE_BYTES *
  BASE64_EXPANSION_NUMERATOR / BASE64_EXPANSION_DENOMINATOR
) + CACHE_HEADROOM_BYTES;
const CACHE_METADATA_KEY = 'manga_cache_metadata';
// A cache hit rewrites the whole entry to record access, so limit how often that happens.
const LAST_ACCESSED_WRITE_INTERVAL_MS = 5 * 60 * 1000;
let cacheMetadataLock = Promise.resolve();

const listLocalStorageKeys =
  globalThis.MangaTranslatorSettings?.listLocalStorageKeys ||
  (typeof require === 'function' ? require('./settings').listLocalStorageKeys : null);

async function withCacheMetadataLock(fn) {
  const previous = cacheMetadataLock;
  let release;
  cacheMetadataLock = new Promise(resolve => {
    release = resolve;
  });

  await previous.catch(() => {});
  try {
    return await fn();
  } finally {
    release();
  }
}

function getStorageCacheKeys(storageKeys) {
  return storageKeys.filter(k => k.startsWith(CACHE_PREFIX) && k !== CACHE_METADATA_KEY);
}

async function getCacheKeys() {
  try {
    const result = await chrome.storage.local.get(CACHE_METADATA_KEY);
    const meta = result[CACHE_METADATA_KEY];
    const keys = Array.from(new Set([
      ...(Array.isArray(meta) ? meta : []),
      ...getStorageCacheKeys(await listLocalStorageKeys())
    ]));
    if (!Array.isArray(meta) || keys.length !== meta.length || keys.some((key, index) => meta[index] !== key)) {
      await setCacheKeys(keys);
    }
    return keys;
  } catch {
    return [];
  }
}

async function setCacheKeys(keys) {
  await withCacheMetadataLock(async () => {
    await chrome.storage.local.set({ [CACHE_METADATA_KEY]: Array.from(new Set(keys)) });
  });
}

async function updateCacheMetadata(computeKeys) {
  await withCacheMetadataLock(async () => {
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const result = await chrome.storage.local.get(CACHE_METADATA_KEY);
        const meta = result[CACHE_METADATA_KEY];
        const keys = await computeKeys(Array.isArray(meta) ? meta : []);
        await chrome.storage.local.set({ [CACHE_METADATA_KEY]: keys });
        return;
      } catch (e) {
        if (attempt === 2) throw e;
        await new Promise(r => setTimeout(r, 10 * (attempt + 1)));
      }
    }
  });
}

async function addCacheKey(key) {
  await updateCacheMetadata(async meta => Array.from(new Set([
    ...meta,
    ...getStorageCacheKeys(await listLocalStorageKeys()),
    key
  ])));
}

async function removeCacheKeys(keysToRemove) {
  const toRemoveSet = new Set(keysToRemove);
  await updateCacheMetadata(meta => meta.filter(k => !toRemoveSet.has(k)));
}

const hashObjectFn =
  (typeof globalThis.hashObject === 'function' && globalThis.hashObject) ||
  (typeof require === 'function' ? require('./utils').hashObject : null);

function isValidCacheEntry(entry) {
  return Boolean(
    entry &&
    typeof entry === 'object' &&
    Number.isFinite(entry.timestamp) &&
    Object.prototype.hasOwnProperty.call(entry, 'data') &&
    entry.data !== null
  );
}

function getEntrySize(entry) {
  try {
    return JSON.stringify(entry).length;
  } catch (_error) {
    return 0;
  }
}

function normalizeCachedInpainter(value) {
  const normalized = typeof value === 'string' ? value.trim().toLowerCase() : '';
  if (normalized === 'lama') return 'lama_large';
  if (normalized === 'none' || normalized === 'lama_large') return normalized;
  if (
    normalized === 'flux2_klein_lanpaint' ||
    normalized === 'flux2-klein-lanpaint' ||
    normalized === 'flux_lanpaint' ||
    normalized === 'lanpaint'
  ) return 'flux2_klein_lanpaint';
  return value || '';
}

function normalizeCachedFontSize(value) {
  const numeric = Number(value);
  return Number.isInteger(numeric)
    ? Math.max(6, Math.min(96, numeric))
    : 16;
}

function normalizeSettingsForCache(settings = {}) {
  const localBackend = settings.localBackend || {};
  const googleOcr = settings.googleOcr || {};
  const render = settings.render || {};
  const localLlm = settings.localLlm || {};

  return {
    pipelineMode: settings.pipelineMode || 'local',
    provider: settings.provider || 'none',
    model: settings.model || '',
    baseUrl: settings.baseUrl || '',
    targetLang: settings.targetLang || 'English',
    fontSize: normalizeCachedFontSize(settings.fontSize),
    localBackend: {
      url: localBackend.url || '',
      detector: localBackend.detector || '',
      ocr: localBackend.ocr || '',
      inpainter: normalizeCachedInpainter(localBackend.inpainter),
      translator: localBackend.translator || ''
    },
    localLlm: {
      baseUrl: localLlm.baseUrl || '',
      model: localLlm.model || ''
    },
    googleOcr: {
      languageHint: googleOcr.languageHint || ''
    },
    render: {
      textAlignment: render.textAlignment || '',
      textDirection: render.textDirection || '',
      uppercase: !!render.uppercase,
      lineSpacing: render.lineSpacing ?? '',
      borderSize: render.borderSize ?? '',
      textColor: render.textColor || '',
      borderColor: render.borderColor || '',
      bgColor: render.bgColor || '',
      bgOpacity: render.bgOpacity ?? '',
      glowEnabled: !!render.glowEnabled,
      glowSize: render.glowSize ?? '',
      glowColor: render.glowColor || '',
      autoScale: render.autoScale !== false
    }
  };
}

/**
 * Generate cache key from image and settings
 * @param {string} imageHash - Hash of the image blob
 * @param {Object} settings - Current settings
 * @returns {string} - Cache key
 */
function generateCacheKey(imageHash, settings) {
  const normalizedSettings = normalizeSettingsForCache(settings);
  const stableSettingsHash = hashObjectFn
    ? hashObjectFn(normalizedSettings)
    : JSON.stringify(normalizedSettings);

  const keyParts = [
    normalizedSettings.provider,
    normalizedSettings.model || 'none',
    normalizedSettings.targetLang,
    stableSettingsHash
  ];

  return `${CACHE_PREFIX}${imageHash}_${keyParts.join('_')}`;
}

/**
 * Get cached translation
 * @param {string} cacheKey - Cache key
 * @returns {Promise<Object|null>} - Cached data or null
 */
async function getFromCache(cacheKey) {
  try {
    const result = await chrome.storage.local.get(cacheKey);
    const cached = result[cacheKey];
    
    if (!cached) return null;

    if (!isValidCacheEntry(cached)) {
      await chrome.storage.local.remove(cacheKey);
      await removeCacheKeys([cacheKey]);
      return null;
    }
    
    const now = Date.now();
    const age = now - cached.timestamp;
    const maxAge = CACHE_TTL_DAYS * 24 * 60 * 60 * 1000;
    
    if (age > maxAge) {
      await chrome.storage.local.remove(cacheKey);
      await removeCacheKeys([cacheKey]);
      return null;
    }
    
    const lastAccessed = Number.isFinite(cached.lastAccessed) ? cached.lastAccessed : cached.timestamp;
    if (now - lastAccessed >= LAST_ACCESSED_WRITE_INTERVAL_MS) {
      cached.lastAccessed = now;
      await chrome.storage.local.set({ [cacheKey]: cached });
    }

    return cached.data;
  } catch (error) {
    console.error('Cache get error:', error);
    return null;
  }
}

/**
 * Save translation to cache
 * @param {string} cacheKey - Cache key
 * @param {Object} data - Translation data to cache
 * @returns {Promise<void>}
 */
async function getStorageUsage() {
  try {
    const keys = await getCacheKeys();
    if (keys.length === 0) return 0;
    if (typeof chrome.storage.local.getBytesInUse === 'function') {
      return await chrome.storage.local.getBytesInUse(keys);
    }
    const all = await chrome.storage.local.get(keys);
    let totalSize = 0;
    for (const key of keys) {
      if (all[key]) totalSize += getEntrySize(all[key]);
    }
    return totalSize;
  } catch (error) {
    console.error('Storage usage error:', error);
    return 0;
  }
}

async function evictLRUEntries(requiredBytes) {
  try {
    const cacheKeys = await getCacheKeys();
    if (cacheKeys.length === 0) return 0;
    
    const all = await chrome.storage.local.get(cacheKeys);
    const malformedKeys = [];
    const entries = [];

    cacheKeys.forEach(key => {
      const entry = all[key];
      if (!isValidCacheEntry(entry)) {
        malformedKeys.push(key);
        return;
      }

      entries.push({
        key: key,
        lastAccessed: Number.isFinite(entry.lastAccessed) ? entry.lastAccessed : entry.timestamp,
        size: getEntrySize(entry)
      });
    });
    
    entries.sort((a, b) => a.lastAccessed - b.lastAccessed);
    
    let freedBytes = 0;
    const keysToRemove = [];

    for (const key of malformedKeys) {
      keysToRemove.push(key);
      freedBytes += getEntrySize(all[key]);
    }
    
    for (const entry of entries) {
      if (freedBytes >= requiredBytes) break;
      keysToRemove.push(entry.key);
      freedBytes += entry.size;
    }
    
    if (keysToRemove.length > 0) {
      await chrome.storage.local.remove(keysToRemove);
      await removeCacheKeys(keysToRemove);
    }
    
    return freedBytes;
  } catch (error) {
    console.error('LRU eviction error:', error);
    return 0;
  }
}

async function saveToCache(cacheKey, data) {
  const cacheEntry = {
    timestamp: Date.now(),
    lastAccessed: Date.now(),
    data: data
  };

  try {
    const entrySize = JSON.stringify(cacheEntry).length;
    const currentUsage = await getStorageUsage();
    
    if (currentUsage + entrySize > STORAGE_QUOTA_BYTES) {
      const needed = (currentUsage + entrySize) - STORAGE_QUOTA_BYTES;
      await evictLRUEntries(needed);
    }
    
    await chrome.storage.local.set({ [cacheKey]: cacheEntry });
    await addCacheKey(cacheKey);
  } catch (error) {
    console.error('Cache save error:', error);
    if (error.message && error.message.includes('QUOTA')) {
      try {
        await evictLRUEntries(STORAGE_QUOTA_BYTES * 0.2);
        await chrome.storage.local.set({ [cacheKey]: cacheEntry });
        await addCacheKey(cacheKey);
      } catch (retryError) {
        console.error('Cache save retry error:', retryError);
      }
    }
  }
}

/**
 * Clear all cache entries
 * @returns {Promise<number>} - Number of entries cleared
 */
async function clearAllCache() {
  try {
    const cacheKeys = await getCacheKeys();
    if (cacheKeys.length > 0) {
      await chrome.storage.local.remove(cacheKeys);
      await setCacheKeys([]);
    }
    return cacheKeys.length;
  } catch (error) {
    console.error('Cache clear error:', error);
    return 0;
  }
}

/**
 * Get cache statistics
 * @returns {Promise<Object>} - Cache stats
 */
async function getCacheStats() {
  try {
    const cacheKeys = await getCacheKeys();
    if (cacheKeys.length === 0) {
      return { count: 0, sizeBytes: 0, sizeKB: '0', oldestEntry: null, newestEntry: null };
    }
    
    const all = await chrome.storage.local.get(cacheKeys);
    let validCount = 0;
    let totalSize = 0;
    let oldestTimestamp = Date.now();
    let newestTimestamp = 0;
    
    for (const key of cacheKeys) {
      const entry = all[key];
      if (!entry || !isValidCacheEntry(entry)) continue;
      validCount++;
      totalSize += getEntrySize(entry);
      if (entry.timestamp < oldestTimestamp) oldestTimestamp = entry.timestamp;
      if (entry.timestamp > newestTimestamp) newestTimestamp = entry.timestamp;
    }
    
    return {
      count: validCount,
      sizeBytes: totalSize,
      sizeKB: (totalSize / 1024).toFixed(2),
      oldestEntry: validCount > 0 ? new Date(oldestTimestamp).toISOString() : null,
      newestEntry: validCount > 0 ? new Date(newestTimestamp).toISOString() : null
    };
  } catch (error) {
    console.error('Cache stats error:', error);
    return { count: 0, sizeBytes: 0, sizeKB: '0' };
  }
}

/**
 * Prune expired cache entries
 * @returns {Promise<number>} - Number of entries pruned
 */
async function pruneExpiredCache() {
  try {
    const cacheKeys = await getCacheKeys();
    if (cacheKeys.length === 0) return 0;
    
    const all = await chrome.storage.local.get(cacheKeys);
    const now = Date.now();
    const maxAge = CACHE_TTL_DAYS * 24 * 60 * 60 * 1000;
    const expiredKeys = [];
    
    for (const key of cacheKeys) {
      const entry = all[key];
      if (!entry || !isValidCacheEntry(entry) || now - entry.timestamp > maxAge) {
        expiredKeys.push(key);
      }
    }
    
    if (expiredKeys.length > 0) {
      await chrome.storage.local.remove(expiredKeys);
      await removeCacheKeys(expiredKeys);
    }
    
    return expiredKeys.length;
  } catch (error) {
    console.error('Cache prune error:', error);
    return 0;
  }
}

// Export for module use
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    generateCacheKey,
    getFromCache,
    saveToCache,
    clearAllCache,
    getCacheStats,
    pruneExpiredCache,
    getStorageUsage,
    evictLRUEntries,
    isValidCacheEntry,
    getCacheKeys
  };
}
