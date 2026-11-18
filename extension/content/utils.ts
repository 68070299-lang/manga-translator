/** Small, dependency-free helpers shared by the content script modules. */
import { LOG_PREFIX } from './constants';
import { state } from './state';

/** Debug logging gated by `settings.debug`. */
export function debugLog(...args: unknown[]): void {
  if (state.settings?.debug) {
    console.log(LOG_PREFIX, ...args);
  }
}

export function getErrorMessage(error: unknown): string {
  const message = (error as { message?: unknown } | null | undefined)?.message;
  return String(message || error || '');
}

export function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Resolve after `count` animation frames. */
export function waitForFrames(count: number): Promise<void> {
  return new Promise((resolve) => {
    const step = (remaining: number) => {
      if (remaining <= 0) {
        resolve();
        return;
      }
      requestAnimationFrame(() => step(remaining - 1));
    };
    step(count);
  });
}

/** Run `fn` over `items` with at most `concurrency` calls in flight; results keep input order. */
export async function processWithConcurrency<T, R>(
  items: readonly T[],
  concurrency: number,
  fn: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let index = 0;
  async function worker(): Promise<void> {
    while (index < items.length) {
      const i = index++;
      results[i] = await fn(items[i] as T, i);
    }
  }
  const workers = Array.from({ length: Math.min(concurrency, items.length) }, () => worker());
  await Promise.all(workers);
  return results;
}

/** Insert into a Map, evicting the oldest entry once `maxSize` is reached. */
export function lruSet<K, V>(
  map: Map<K, V>,
  key: K,
  value: V,
  maxSize: number,
  onEvict?: (evictedKey: K) => void,
): void {
  if (map.has(key)) {
    map.delete(key);
  } else if (map.size >= maxSize) {
    const oldestKey = map.keys().next().value as K;
    map.delete(oldestKey);
    onEvict?.(oldestKey);
  }
  map.set(key, value);
}

export function makeElement<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props?: Partial<HTMLElementTagNameMap[K]>,
): HTMLElementTagNameMap[K] {
  return Object.assign(document.createElement(tag), props);
}

export function generateImageId(): string {
  return `img_${Date.now().toString(36)}_${Math.random().toString(36).substring(2, 11)}${Math.random().toString(36).substring(2, 6)}`;
}

/** 32-bit FNV-1a hash of a string. */
export function fnv1aHash(text: string): number {
  let hash = 2166136261;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

/** SHA-256 hex digest, or an FNV-1a hex fallback where SubtleCrypto is unavailable. */
export async function hashString(value: string): Promise<string> {
  if (globalThis.crypto?.subtle && typeof TextEncoder !== 'undefined') {
    const data = new TextEncoder().encode(value);
    const digest = await globalThis.crypto.subtle.digest('SHA-256', data);
    return Array.from(new Uint8Array(digest))
      .map((byte) => byte.toString(16).padStart(2, '0'))
      .join('');
  }

  return fnv1aHash(value).toString(16);
}
