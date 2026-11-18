/**
 * TRANSLATE_IMAGE: validate the payload, derive the cache key, consult the
 * cache, and run the provider job through the right queue.
 *
 * Local pipeline mode runs one job at a time, cloud mode up to three. Jobs with
 * the same cache key are de-duplicated across tabs: later requests subscribe to
 * the in-flight job and share its result. `forceRefresh` bypasses the cache and
 * de-duplication.
 */
import { browser } from 'wxt/browser';
import { generateCacheKey, getFromCache, saveToCache } from '../lib/cache';
import { mergeSettings } from '../lib/settings';
import { type ProgressNotifier, TranslationQueue } from '../lib/translation-queue';
import { errorMessage } from '../lib/utils';
import type { TranslateImageRequest, WorkerResponseMap } from '../shared/messages';
import type { Settings, TranslationResult } from '../shared/types';
import { generateImageHash, validateImagePayload } from './image-payload';
import { handleCloudTranslation, handleLocalBackendTranslation } from './providers';
import { notifyProgress, notifyTranslationError } from './tab-messaging';

const localTranslationQueue = new TranslationQueue({ concurrency: 1 });
const cloudTranslationQueue = new TranslationQueue({ concurrency: 3 });

/** The in-memory queues, exposed for diagnostics and tests. */
export function getTranslationQueues(): { local: TranslationQueue; cloud: TranslationQueue } {
  return { local: localTranslationQueue, cloud: cloudTranslationQueue };
}

/** A result the content script can render. */
export function isUsableTranslationResult(result: unknown): result is TranslationResult {
  if (Array.isArray(result)) {
    return true;
  }

  const candidate = result as Partial<TranslationResult> & Record<string, unknown>;
  if (candidate?.mode === 'cloud' && Array.isArray(candidate.blocks)) {
    return true;
  }

  return Boolean(
    candidate &&
      candidate.mode === 'local' &&
      typeof candidate.translatedImage === 'string' &&
      candidate.translatedImage.length > 100 &&
      !candidate.translationFailed,
  );
}

/** Usable results are cached unless the backend fell back to another inpainter. */
export function isCacheableTranslationResult(result: unknown): result is TranslationResult {
  return (
    isUsableTranslationResult(result) &&
    !(result as { inpainterFallback?: boolean } | null)?.inpainterFallback
  );
}

async function handleTranslationJob(
  base64: string,
  settings: Settings,
  cacheKey: string,
  notify: ProgressNotifier,
  options: { forceRefresh?: boolean } = {},
): Promise<WorkerResponseMap['TRANSLATE_IMAGE']> {
  if (!options.forceRefresh) {
    // Re-check inside the queued job: an earlier job may have filled the cache.
    const cached = await getFromCache(cacheKey);
    if (cached) {
      if (isCacheableTranslationResult(cached)) {
        notify('Using cached translation...', 80);
        return { success: true, result: cached, fromCache: true };
      }
      await browser.storage.local.remove(cacheKey);
    }
  }

  const result =
    settings.pipelineMode === 'local'
      ? await handleLocalBackendTranslation(base64, settings, notify)
      : await handleCloudTranslation(base64, settings, notify);

  if (!isUsableTranslationResult(result)) {
    throw new Error('Backend did not return a usable translated image');
  }

  if (isCacheableTranslationResult(result)) {
    await saveToCache(cacheKey, result);
  }
  notify('Complete!', 100);
  return { success: true, result };
}

/** Handle a TRANSLATE_IMAGE request from a content script. */
export async function handleTranslateImage(
  message: TranslateImageRequest,
  sender: { tab?: { id?: number } },
): Promise<WorkerResponseMap['TRANSLATE_IMAGE']> {
  const { imageId, base64, settings, canvasSignature, forceRefresh = false } = message;
  const tabId = sender.tab?.id;
  const normalizedSettings = mergeSettings(settings || {});

  try {
    await validateImagePayload(base64);
    // Canvas sources use their signature for a stable key; others hash the bytes.
    const imageHash = canvasSignature
      ? `canvas_${canvasSignature.replace(/[:/]/g, '_')}`
      : await generateImageHash(base64);
    const cacheKey = generateCacheKey(imageHash, normalizedSettings);

    if (forceRefresh) {
      await browser.storage.local.remove(cacheKey);
    } else {
      const cached = await getFromCache(cacheKey);
      if (cached) {
        if (isCacheableTranslationResult(cached)) {
          console.log('[Service Worker] Cache hit for image');
          notifyProgress(tabId, 'Using cached translation...', 80);
          return { success: true, result: cached, fromCache: true };
        }
        await browser.storage.local.remove(cacheKey);
      }
    }

    const queue =
      normalizedSettings.pipelineMode === 'local' ? localTranslationQueue : cloudTranslationQueue;
    const queueKey = forceRefresh
      ? `${cacheKey}:force:${Date.now()}:${Math.random().toString(36).slice(2)}`
      : cacheKey;

    return await queue.enqueue(
      queueKey,
      (notify) =>
        handleTranslationJob(base64, normalizedSettings, cacheKey, notify, { forceRefresh }),
      { onProgress: (status, percent) => notifyProgress(tabId, status, percent) },
    );
  } catch (error) {
    console.error('[Service Worker] Translation error:', error);

    const text = errorMessage(error);
    notifyTranslationError(tabId, imageId, text);
    return { success: false, error: text };
  }
}
