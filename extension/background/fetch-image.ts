/**
 * FETCH_IMAGE: download an http(s) image through the extension context
 * (extension host permissions bypass page CORS), adding the Referer session
 * rule for allowlisted CDNs.
 */
import { blobToBase64, errorMessage } from '../lib/utils';
import type { WorkerResponseMap } from '../shared/messages';
import { withDnrReferrerRule } from './dnr-rules';
import { MAX_DECODED_IMAGE_BYTES } from './image-payload';

export async function handleFetchImage(url: unknown): Promise<WorkerResponseMap['FETCH_IMAGE']> {
  try {
    if (!url || typeof url !== 'string') {
      return { success: false, error: 'Invalid URL for FETCH_IMAGE' };
    }
    const scheme = (url.split(':', 1)[0] ?? '').toLowerCase();
    if (scheme !== 'http' && scheme !== 'https') {
      return { success: false, error: `Unsupported URL scheme: ${scheme}` };
    }

    return await withDnrReferrerRule(url, async (referrerRule) => {
      const referrerOptions: RequestInit = referrerRule
        ? { referrer: referrerRule.referrer, referrerPolicy: 'no-referrer-when-downgrade' }
        : {};

      // Extension host permissions already bypass CORS; a no-cors retry would
      // only return an opaque, empty body, so surface the real failure instead.
      const response = await fetch(url, {
        method: 'GET',
        mode: 'cors',
        credentials: 'omit',
        cache: 'no-store',
        ...referrerOptions,
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status} fetching image`);
      }

      const blob = await response.blob();
      if (!blob || blob.size === 0) {
        throw new Error('Image response was empty');
      }
      if (blob.size > MAX_DECODED_IMAGE_BYTES) {
        throw new Error('Image exceeds 20 MB decoded image limit');
      }
      // Some CDNs serve images as octet-stream, so only clearly non-image types are refused.
      const blobType = String(blob.type || '').toLowerCase();
      if (blobType && !blobType.startsWith('image/') && !blobType.endsWith('/octet-stream')) {
        throw new Error(`Unexpected content type fetching image: ${blobType}`);
      }

      return { success: true as const, base64: await blobToBase64(blob) };
    });
  } catch (error) {
    console.error('[Service Worker] Fetch error:', url, error);
    return { success: false, error: errorMessage(error) };
  }
}
