/**
 * Translation cache stored in `chrome.storage.local`.
 *
 * Keys start with `manga_cache_`; `manga_cache_metadata` lists them so storage
 * does not have to be read in full. Entries expire after seven days and the
 * total size is bounded (least-recently-used entries are evicted first).
 */
import { browser } from 'wxt/browser';
import type { CacheEntry, CacheStats, SettingsInput, TranslationResult } from '../shared/types';
import { listLocalStorageKeys } from './settings';
import { hashObject } from './utils';

export const CACHE_PREFIX = 'manga_cache_';
export const CACHE_METADATA_KEY = 'manga_cache_metadata';
const CACHE_TTL_DAYS = 7;
const CACHE_TTL_MS = CACHE_TTL_DAYS * 24 * 60 * 60 * 1000;
const TARGET_CACHED_PAGE_COUNT = 5;
const TARGET_RENDERED_PAGE_BYTES = 2 * 1024 * 1024;
const BASE64_EXPANSION_NUMERATOR = 4;
const BASE64_EXPANSION_DENOMINATOR = 3;
const CACHE_HEADROOM_BYTES = 2 * 1024 * 1024;
const STORAGE_QUOTA_BYTES =
  Math.ceil(
    (TARGET_CACHED_PAGE_COUNT * TARGET_RENDERED_PAGE_BYTES * BASE64_EXPANSION_NUMERATOR) /
      BASE64_EXPANSION_DENOMINATOR,
  ) + CACHE_HEADROOM_BYTES;
// A cache hit rewrites the whole entry to record access, so limit how often that happens.
const LAST_ACCESSED_WRITE_INTERVAL_MS = 5 * 60 * 1000;

let cacheMetadataLock: Promise<unknown> = Promise.resolve();

async function withCacheMetadataLock<T>(fn: () => Promise<T>): Promise<T> {
  const previous = cacheMetadataLock;
  // Not Promise.withResolvers: that needs Chrome 119 and the floor is 110.
  let release!: () => void;
  cacheMetadataLock = new Promise<void>((resolve) => {
    release = resolve;
  });

  await previous.catch(() => {});
  try {
    return await fn();
  } finally {
    release();
  }
}

function getStorageCacheKeys(storageKeys: string[]): string[] {
  return storageKeys.filter((k) => k.startsWith(CACHE_PREFIX) && k !== CACHE_METADATA_KEY);
}

/** All cache keys: the metadata list merged with any entries found in storage. */
export async function getCacheKeys(): Promise<string[]> {
  try {
    const result = await browser.storage.local.get(CACHE_METADATA_KEY);
    const meta: unknown = result[CACHE_METADATA_KEY];
    const keys = Array.from(
      new Set([
        ...(Array.isArray(meta) ? (meta as string[]) : []),
        ...getStorageCacheKeys(await listLocalStorageKeys()),
      ]),
    );
    if (
      !Array.isArray(meta) ||
      keys.length !== meta.length ||
      keys.some((key, index) => meta[index] !== key)
    ) {
      await setCacheKeys(keys);
    }
    return keys;
  } catch {
    return [];
  }
}

async function setCacheKeys(keys: string[]): Promise<void> {
  await withCacheMetadataLock(async () => {
    await browser.storage.local.set({ [CACHE_METADATA_KEY]: Array.from(new Set(keys)) });
  });
}

async function updateCacheMetadata(
  computeKeys: (meta: string[]) => string[] | Promise<string[]>,
): Promise<void> {
  await withCacheMetadataLock(async () => {
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const result = await browser.storage.local.get(CACHE_METADATA_KEY);
        const meta: unknown = result[CACHE_METADATA_KEY];
        const keys = await computeKeys(Array.isArray(meta) ? (meta as string[]) : []);
        await browser.storage.local.set({ [CACHE_METADATA_KEY]: keys });
        return;
      } catch (e) {
        if (attempt === 2) throw e;
        await new Promise((r) => setTimeout(r, 10 * (attempt + 1)));
      }
    }
  });
}

async function addCacheKey(key: string): Promise<void> {
  await updateCacheMetadata(async (meta) =>
    Array.from(new Set([...meta, ...getStorageCacheKeys(await listLocalStorageKeys()), key])),
  );
}

async function removeCacheKeys(keysToRemove: string[]): Promise<void> {
  const toRemoveSet = new Set(keysToRemove);
  await updateCacheMetadata((meta) => meta.filter((k) => !toRemoveSet.has(k)));
}

/** Whether a stored value has the `{timestamp, data}` shape of a cache entry. */
export function isValidCacheEntry(entry: unknown): entry is CacheEntry {
  return Boolean(
    entry &&
      typeof entry === 'object' &&
      Number.isFinite((entry as CacheEntry).timestamp) &&
      Object.hasOwn(entry, 'data') &&
      (entry as CacheEntry).data !== null,
  );
}

function getEntrySize(entry: unknown): number {
  try {
    return JSON.stringify(entry).length;
  } catch {
    return 0;
  }
}

function lastAccessedOf(entry: CacheEntry): number {
  return Number.isFinite(entry.lastAccessed) ? (entry.lastAccessed as number) : entry.timestamp;
}

function normalizeCachedInpainter(value: unknown): string {
  const normalized = typeof value === 'string' ? value.trim().toLowerCase() : '';
  if (normalized === 'lama') return 'lama_large';
  if (normalized === 'none' || normalized === 'lama_large') return normalized;
  if (
    normalized === 'flux2_klein_lanpaint' ||
    normalized === 'flux2-klein-lanpaint' ||
    normalized === 'flux_lanpaint' ||
    normalized === 'lanpaint'
  ) {
    return 'flux2_klein_lanpaint';
  }
  return (value as string) || '';
}

function normalizeCachedFontSize(value: unknown): number {
  const numeric = Number(value);
  return Number.isInteger(numeric) ? Math.max(6, Math.min(96, numeric)) : 16;
}

/**
 * The subset of settings that affects translation output, in a stable shape.
 * API keys and the backend token are deliberately excluded.
 */
function normalizeSettingsForCache(settings: SettingsInput = {}) {
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
      translator: localBackend.translator || '',
    },
    localLlm: {
      baseUrl: localLlm.baseUrl || '',
      model: localLlm.model || '',
    },
    googleOcr: {
      languageHint: googleOcr.languageHint || '',
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
      autoScale: render.autoScale !== false,
    },
  };
}

/**
 * Cache key from an image hash (or canvas signature) and the settings that
 * affect output. The derivation is stable: existing entries must keep hitting.
 */
export function generateCacheKey(imageHash: string, settings: SettingsInput): string {
  const normalizedSettings = normalizeSettingsForCache(settings);
  const keyParts = [
    normalizedSettings.provider,
    normalizedSettings.model || 'none',
    normalizedSettings.targetLang,
    hashObject(normalizedSettings),
  ];

  return `${CACHE_PREFIX}${imageHash}_${keyParts.join('_')}`;
}

/**
 * Read a cached translation. Malformed and expired entries are removed and
 * reported as a miss. A hit refreshes `lastAccessed` at most every five minutes.
 */
export async function getFromCache<T = TranslationResult>(cacheKey: string): Promise<T | null> {
  try {
    const result = await browser.storage.local.get(cacheKey);
    const cached: unknown = result[cacheKey];

    if (!cached) return null;

    if (!isValidCacheEntry(cached)) {
      await browser.storage.local.remove(cacheKey);
      await removeCacheKeys([cacheKey]);
      return null;
    }

    const now = Date.now();
    if (now - cached.timestamp > CACHE_TTL_MS) {
      await browser.storage.local.remove(cacheKey);
      await removeCacheKeys([cacheKey]);
      return null;
    }

    if (now - lastAccessedOf(cached) >= LAST_ACCESSED_WRITE_INTERVAL_MS) {
      await browser.storage.local.set({ [cacheKey]: { ...cached, lastAccessed: now } });
    }

    return cached.data as T;
  } catch (error) {
    console.error('Cache get error:', error);
    return null;
  }
}

/** Bytes used by cache entries (0 on error). */
export async function getStorageUsage(): Promise<number> {
  try {
    const keys = await getCacheKeys();
    if (keys.length === 0) return 0;
    if (typeof browser.storage.local.getBytesInUse === 'function') {
      return await browser.storage.local.getBytesInUse(keys);
    }
    const all = await browser.storage.local.get(keys);
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

/**
 * Remove malformed entries, then least-recently-used entries, until at least
 * `requiredBytes` are freed. Returns the bytes freed.
 */
export async function evictLRUEntries(requiredBytes: number): Promise<number> {
  try {
    const cacheKeys = await getCacheKeys();
    if (cacheKeys.length === 0) return 0;

    const all = await browser.storage.local.get(cacheKeys);
    const malformedKeys: string[] = [];
    const entries: Array<{ key: string; lastAccessed: number; size: number }> = [];

    for (const key of cacheKeys) {
      const entry: unknown = all[key];
      if (!isValidCacheEntry(entry)) {
        malformedKeys.push(key);
        continue;
      }
      entries.push({ key, lastAccessed: lastAccessedOf(entry), size: getEntrySize(entry) });
    }

    entries.sort((a, b) => a.lastAccessed - b.lastAccessed);

    let freedBytes = 0;
    const keysToRemove: string[] = [];

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
      await browser.storage.local.remove(keysToRemove);
      await removeCacheKeys(keysToRemove);
    }

    return freedBytes;
  } catch (error) {
    console.error('LRU eviction error:', error);
    return 0;
  }
}

/** Store a translation result, evicting old entries when the size bound would be exceeded. */
export async function saveToCache(cacheKey: string, data: unknown): Promise<void> {
  const cacheEntry: CacheEntry = {
    timestamp: Date.now(),
    lastAccessed: Date.now(),
    data,
  };

  try {
    const entrySize = JSON.stringify(cacheEntry).length;
    const currentUsage = await getStorageUsage();

    if (currentUsage + entrySize > STORAGE_QUOTA_BYTES) {
      await evictLRUEntries(currentUsage + entrySize - STORAGE_QUOTA_BYTES);
    }

    await browser.storage.local.set({ [cacheKey]: cacheEntry });
    await addCacheKey(cacheKey);
  } catch (error) {
    console.error('Cache save error:', error);
    if (error instanceof Error && error.message.includes('QUOTA')) {
      try {
        await evictLRUEntries(STORAGE_QUOTA_BYTES * 0.2);
        await browser.storage.local.set({ [cacheKey]: cacheEntry });
        await addCacheKey(cacheKey);
      } catch (retryError) {
        console.error('Cache save retry error:', retryError);
      }
    }
  }
}

/** Remove every cache entry; resolves to the number cleared. */
export async function clearAllCache(): Promise<number> {
  try {
    const cacheKeys = await getCacheKeys();
    if (cacheKeys.length > 0) {
      await browser.storage.local.remove(cacheKeys);
      await setCacheKeys([]);
    }
    return cacheKeys.length;
  } catch (error) {
    console.error('Cache clear error:', error);
    return 0;
  }
}

/** Count, size and age range of the valid cache entries. */
export async function getCacheStats(): Promise<CacheStats> {
  try {
    const cacheKeys = await getCacheKeys();
    if (cacheKeys.length === 0) {
      return { count: 0, sizeBytes: 0, sizeKB: '0', oldestEntry: null, newestEntry: null };
    }

    const all = await browser.storage.local.get(cacheKeys);
    let validCount = 0;
    let totalSize = 0;
    let oldestTimestamp = Date.now();
    let newestTimestamp = 0;

    for (const key of cacheKeys) {
      const entry: unknown = all[key];
      if (!isValidCacheEntry(entry)) continue;
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
      newestEntry: validCount > 0 ? new Date(newestTimestamp).toISOString() : null,
    };
  } catch (error) {
    console.error('Cache stats error:', error);
    return { count: 0, sizeBytes: 0, sizeKB: '0' };
  }
}

/** Remove expired and malformed entries; resolves to the number pruned. */
export async function pruneExpiredCache(): Promise<number> {
  try {
    const cacheKeys = await getCacheKeys();
    if (cacheKeys.length === 0) return 0;

    const all = await browser.storage.local.get(cacheKeys);
    const now = Date.now();
    const expiredKeys: string[] = [];

    for (const key of cacheKeys) {
      const entry: unknown = all[key];
      if (!isValidCacheEntry(entry) || now - entry.timestamp > CACHE_TTL_MS) {
        expiredKeys.push(key);
      }
    }

    if (expiredKeys.length > 0) {
      await browser.storage.local.remove(expiredKeys);
      await removeCacheKeys(expiredKeys);
    }

    return expiredKeys.length;
  } catch (error) {
    console.error('Cache prune error:', error);
    return 0;
  }
}
