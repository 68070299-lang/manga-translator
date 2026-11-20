/** Shared helpers: cache-key hashing and base64/Blob conversion. */

/**
 * 32-bit string hash of the JSON form of a value, as lowercase hex.
 * Part of the cache key; the algorithm must not change or existing cache
 * entries stop hitting.
 */
export function hashObject(obj: unknown): string {
  const str = JSON.stringify(obj);
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = (hash << 5) - hash + char;
    hash = hash & hash;
  }
  return Math.abs(hash).toString(16);
}

/** Base64 (without the `data:` prefix) of a Blob. */
export function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      resolve(String(reader.result).split(',')[1] ?? '');
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

/**
 * Decode base64 (or a `data:` URL) to a Blob. Throws on empty or malformed input.
 */
export function base64ToBlob(base64: string, mimeType = 'image/png'): Blob {
  if (!base64 || typeof base64 !== 'string') {
    throw new Error('Invalid base64 input: empty or not a string');
  }
  const base64Data = base64.replace(/^data:[^,]*;base64,/, '');
  if (base64Data.length === 0) {
    throw new Error('Invalid base64 input: empty after prefix removal');
  }
  if (!/^[A-Za-z0-9+/=]+$/.test(base64Data.substring(0, Math.min(100, base64Data.length)))) {
    throw new Error('Invalid base64 encoding: contains invalid characters');
  }
  try {
    const byteCharacters = atob(base64Data);
    const bytes = new Uint8Array(byteCharacters.length);
    for (let i = 0; i < byteCharacters.length; i++) {
      bytes[i] = byteCharacters.charCodeAt(i);
    }
    return new Blob([bytes], { type: mimeType });
  } catch (e) {
    throw new Error(`Base64 decode failed: ${e instanceof Error ? e.message : String(e)}`);
  }
}

/** Human-readable message for anything that was thrown or rejected. */
export function errorMessage(error: unknown): string {
  const message = (error as { message?: unknown } | null | undefined)?.message;
  return typeof message === 'string' && message ? message : String(error);
}
