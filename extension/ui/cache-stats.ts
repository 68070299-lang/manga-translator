/** Translation cache counting and clearing for the popup and options page. */
import { browser } from 'wxt/browser';
import { CACHE_METADATA_KEY, CACHE_PREFIX } from '../lib/cache';
import { listLocalStorageKeys } from '../lib/settings';

/**
 * Keys of cached translations: entries found in storage plus any listed in the
 * metadata record, so a stale or missing metadata list does not skew the count.
 */
export async function listCachedTranslationKeys(): Promise<string[]> {
  const stored = await browser.storage.local.get(CACHE_METADATA_KEY);
  const metadata: unknown = stored[CACHE_METADATA_KEY];
  const listed = Array.isArray(metadata)
    ? metadata.filter((key): key is string => typeof key === 'string')
    : [];
  const inStorage = (await listLocalStorageKeys()).filter(
    (key) => key.startsWith(CACHE_PREFIX) && key !== CACHE_METADATA_KEY,
  );
  return Array.from(new Set([...listed, ...inStorage]));
}

export async function countCachedTranslations(): Promise<number> {
  return (await listCachedTranslationKeys()).length;
}

/** Remove every cache entry and the metadata record; resolves to the entries removed. */
export async function clearCachedTranslations(): Promise<number> {
  const entries = (await listLocalStorageKeys()).filter(
    (key) => key.startsWith(CACHE_PREFIX) && key !== CACHE_METADATA_KEY,
  );
  if (entries.length > 0) {
    await browser.storage.local.remove(entries);
  }
  await browser.storage.local.remove(CACHE_METADATA_KEY);
  return entries.length;
}
