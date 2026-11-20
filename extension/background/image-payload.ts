/**
 * Size limits and identity for image payloads sent by content scripts.
 */
import { base64ToBlob } from '../lib/utils';

/** Maximum decoded image size accepted by TRANSLATE_IMAGE and FETCH_IMAGE (20 MB). */
export const MAX_DECODED_IMAGE_BYTES = 20 * 1024 * 1024;
/** Maximum image area accepted by TRANSLATE_IMAGE (25 megapixels). */
export const MAX_IMAGE_PIXELS = 25_000_000;

/** Strip a `data:...;base64,` prefix and whitespace; non-strings become `''`. */
export function getBase64Payload(value: unknown): string {
  return typeof value === 'string'
    ? value.replace(/^data:[^,]*;base64,/i, '').replace(/\s+/g, '')
    : '';
}

/** Decoded byte size of a base64 payload, without decoding it. */
export function estimateDecodedBase64Bytes(value: unknown): number {
  const payload = getBase64Payload(value);
  if (!payload) return 0;
  const padding = payload.endsWith('==') ? 2 : payload.endsWith('=') ? 1 : 0;
  return Math.floor((payload.length * 3) / 4) - padding;
}

/**
 * Reject payloads over the byte or pixel limits. Service workers can decode an
 * image without allocating a canvas; where `createImageBitmap` is unavailable
 * the byte guard remains the fallback.
 */
export async function validateImagePayload(base64: string): Promise<void> {
  if (estimateDecodedBase64Bytes(base64) > MAX_DECODED_IMAGE_BYTES) {
    throw new Error('Image exceeds 20 MB decoded image limit');
  }

  if (typeof globalThis.createImageBitmap === 'function') {
    const bitmap = await globalThis.createImageBitmap(base64ToBlob(base64));
    try {
      const pixels = Number(bitmap.width || 0) * Number(bitmap.height || 0);
      if (pixels > MAX_IMAGE_PIXELS) {
        throw new Error('Image exceeds 25 MP pixel limit');
      }
    } finally {
      bitmap.close?.();
    }
  }
}

/** SHA-256 (hex) of the base64 payload; part of the cache key for non-canvas images. */
export async function generateImageHash(base64: string): Promise<string> {
  // Not whitespace-stripped: the hash input is part of existing cache keys.
  const normalizedBase64 =
    typeof base64 === 'string' ? base64.replace(/^data:[^,]*;base64,/, '') : '';

  if (!normalizedBase64) {
    throw new Error('Cannot hash empty image data');
  }

  const data = new TextEncoder().encode(normalizedBase64);
  const hashBuffer = await globalThis.crypto.subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(hashBuffer), (b) => b.toString(16).padStart(2, '0')).join('');
}
