/**
 * The translate pipeline: translate the page or a single image, capture each
 * element, send TRANSLATE_IMAGE to the service worker, and apply the result
 * or the error.
 */
import {
  type CheckBackendResponse,
  notifyPopup,
  type ResponseOf,
  sendToWorker,
  type TranslateImageRequest,
} from '../shared/messages';
import {
  clearBookWalkerQueueState,
  completeBookWalkerQueueState,
  refreshBookWalkerQueueStates,
  renderBookWalkerQueueState,
  setBookWalkerQueueState,
} from './bookwalker';
import { clearCanvasTranslationIfChanged, rememberCanvasTranslation } from './canvas-memory';
import {
  canvasMatchesStoredSource,
  createCanvasCacheSignature,
  getCanvasDimensions,
  isBookWalkerViewer,
  probeCanvas,
  storeCanvasDimensions,
  storeCanvasTranslationKey,
} from './canvas-probe';
import { clearCanvasTranslation } from './canvas-reader';
import {
  assertCaptureBase64Size,
  captureCompositeImage,
  getImageBase64,
  waitForCanvasReady,
  waitForCompositeSlices,
  waitForImageLoad,
  waitForSliceImages,
  waitForSlicesToLoad,
} from './capture';
import { clearImageDataTranslation } from './clear';
import {
  BOOKWALKER_QUEUE_ATTR,
  CANVAS_SOURCE_ATTR,
  LOG_PREFIX,
  MIN_IMAGE_DATA_LENGTH,
  PROCESSING_ATTR,
  STALE_CANVAS_BACKOFF_MS,
  TRANSLATION_ATTR,
} from './constants';
import { discoverImages, isAlreadyTranslatedElement } from './discovery';
import { getPageId } from './dom';
import { isNonRetryableImageError, isTranslationTransportError } from './errors';
import { getCachedPageImage } from './page-cache';
import { scheduleCanvasReconcile } from './reconcile-scheduler';
import { replaceWithTranslatedImage } from './render';
import {
  clearImageRetryBlock,
  getRetryBlockedError,
  isCanvasElementRetryBlocked,
  isImageRetryBlocked,
  markImageRetryBlocked,
} from './retry';
import { ensureSettingsLoaded } from './settings-store';
import { state } from './state';
import type {
  ImageTarget,
  ImageType,
  ProcessImageOptions,
  ProcessResult,
  ResultInput,
  TranslateImageDataOptions,
} from './types';
import {
  hideImageProgress,
  hidePersistentError,
  hideProgress,
  showImageError,
  showImageProgress,
  showNotification,
  updateProgress,
} from './ui';
import { debugLog, delay, generateImageId, getErrorMessage } from './utils';
import { isElementInViewport } from './visibility';

// ---------------------------------------------------------------------------
// Worker requests
// ---------------------------------------------------------------------------

/** Send TRANSLATE_IMAGE, retrying once when the worker connection drops (not for forced refreshes). */
export async function sendTranslationImageRequest(
  message: TranslateImageRequest,
): Promise<ResponseOf<TranslateImageRequest>> {
  const canRetry = !message?.forceRefresh;
  let attempt = 0;

  for (;;) {
    try {
      const response = await sendToWorker(message);
      if (response === undefined || response === null) {
        throw new Error('Could not establish connection. Receiving end does not exist.');
      }
      return response;
    } catch (error) {
      if (!canRetry || attempt >= 1 || !isTranslationTransportError(error)) {
        throw error;
      }
      attempt += 1;
    }
  }
}

function reportTranslationError(message: string, popupError = message): void {
  hideProgress();
  showNotification(message, true);
  void notifyPopup({ type: 'TRANSLATION_ERROR', error: popupError });
}

const BACKEND_PROBLEMS: Record<string, string> = {
  loading: 'is still loading its models. Wait a moment and try again.',
  degraded: 'is running but its models are not ready. Check the backend console, then try again.',
  unauthorized: 'rejected the Backend Access Token. Check the token in the extension options.',
  auth_unconfigured:
    'has no access token configured. Set BACKEND_AUTH_TOKEN on the backend, then restart it.',
};

/** Check the backend answers before spending time capturing images. */
async function ensureBackendReady(): Promise<boolean> {
  updateProgress('Checking backend connection...', 2);
  const backendCheck: CheckBackendResponse | undefined = await sendToWorker({
    type: 'CHECK_BACKEND',
    settings: state.settings ?? {},
  });

  if (backendCheck?.success) {
    return true;
  }

  const backendUrl = state.settings?.localBackend?.url || 'http://localhost:8001';
  const problem = backendCheck?.reason ? BACKEND_PROBLEMS[backendCheck.reason] : undefined;
  reportTranslationError(
    problem
      ? `The local backend at ${backendUrl} ${problem}`
      : `Backend connection failed. Please ensure the local server is running at ${backendUrl}`,
  );
  return false;
}

// ---------------------------------------------------------------------------
// Inpainter fallback notice
// ---------------------------------------------------------------------------

/**
 * Tell the user (once per page session) when the backend used a different
 * inpainter than the one requested; such results are not cached.
 */
function notifyInpainterFallbackOnce(result: ResultInput): void {
  if (
    state.inpainterFallbackNotified ||
    Array.isArray(result) ||
    result?.inpainterFallback !== true
  ) {
    return;
  }
  state.inpainterFallbackNotified = true;
  const requested = state.settings?.localBackend?.inpainter || 'configured';
  const notice = `Inpainter ${requested} unavailable; used ${result.inpainterUsed || 'a fallback'}. Result not cached.`;
  if (state.isProcessing) {
    state.pendingInpainterNotice = notice;
  } else {
    showNotification(notice);
  }
}

/** Append (and clear) a held-back inpainter fallback notice to a final progress label. */
function withPendingInpainterNotice(label: string): string {
  if (!state.pendingInpainterNotice) return label;
  const notice = state.pendingInpainterNotice;
  state.pendingInpainterNotice = '';
  return `${label}. ${notice}`;
}

// ---------------------------------------------------------------------------
// Results and errors
// ---------------------------------------------------------------------------

/**
 * Apply a translation to the element that is processing `imageId`. False when
 * the element is gone or (canvas) changed while the translation was in flight.
 */
export function handleTranslationResult(imageId: string, result: ResultInput): boolean {
  const element = document.querySelector<HTMLElement>(`[${PROCESSING_ATTR}="${imageId}"]`);
  if (!element) return false;

  if (element.tagName === 'CANVAS' && !canvasMatchesStoredSource(element as HTMLCanvasElement)) {
    debugLog('Ignoring stale canvas translation result; source changed while processing');
    state.canvasElementRetryBlocks.set(element as HTMLCanvasElement, {
      blockedUntil: Date.now() + STALE_CANVAS_BACKOFF_MS,
      reason: 'Canvas source changed during translation',
    });
    clearCanvasTranslation(element as HTMLCanvasElement);
    hideImageProgress(element);
    scheduleCanvasReconcile(false, 0);
    return false;
  }

  element.removeAttribute(PROCESSING_ATTR);
  hideImageProgress(element);

  if (!Array.isArray(result) && result?.translatedImage) {
    // Rendered image from the local backend.
    if (!replaceWithTranslatedImage(element, result.translatedImage, result.mimeType, imageId)) {
      return false;
    }
  } else {
    showImageProgress(element, 'No text found');
    setTimeout(() => hideImageProgress(element), 2000);
  }

  element.setAttribute(TRANSLATION_ATTR, 'true');
  if (element.dataset) {
    element.dataset.mangaTranslationHidden = 'false';
  }
  if (element.tagName === 'CANVAS') {
    const canvas = element as HTMLCanvasElement;
    storeCanvasTranslationKey(canvas);
    rememberCanvasTranslation(canvas, result);
    if (isBookWalkerViewer()) {
      completeBookWalkerQueueState(canvas, 'translated');
      refreshBookWalkerQueueStates();
    }
  }

  return true;
}

/** The worker reported an error for the element processing `imageId`. */
export function handleTranslationError(imageId: string | undefined, error: string): void {
  const element = document.querySelector<HTMLElement>(`[${PROCESSING_ATTR}="${imageId}"]`);
  if (!element) return;

  element.removeAttribute(PROCESSING_ATTR);
  hideImageProgress(element);
  showImageError(element, error);
}

// ---------------------------------------------------------------------------
// One image
// ---------------------------------------------------------------------------

/**
 * Capture one element, translate it, and apply the result.
 *
 * @param type 'img', 'composite', 'canvas' or 'background'
 * @param cachedBase64 Pre-cached data to use instead of re-capturing
 * @param options.forceRefresh Bypass the background translation cache for this request
 * @param options.bypassRetryBlock Ignore the local timeout backoff for manual retries
 */
export async function processImage(
  element: HTMLElement,
  type: ImageType = 'img',
  src: string | null = null,
  cachedBase64: string | null = null,
  options: ProcessImageOptions = {},
): Promise<ProcessResult> {
  const blocked = (error: string): ProcessResult => ({ success: false, retryable: false, error });

  if (!state.siteEnabled) {
    return blocked('Manga Translator is disabled on this site');
  }

  let canvasSourceSignature: string | null = null;

  if (type === 'canvas') {
    clearCanvasTranslationIfChanged(element as HTMLCanvasElement);
  }

  if (options.forceRefresh || options.bypassRetryBlock) {
    clearImageRetryBlock(element, type, src);
    if (type === 'canvas') state.canvasElementRetryBlocks.delete(element as HTMLCanvasElement);
  } else if (isImageRetryBlocked(element, type, src)) {
    return blocked(getRetryBlockedError(element, type));
  } else if (type === 'canvas' && isCanvasElementRetryBlocked(element as HTMLCanvasElement)) {
    return blocked(
      state.canvasElementRetryBlocks.get(element as HTMLCanvasElement)?.reason ??
        'Canvas translation is blocked',
    );
  }

  if (element.hasAttribute(TRANSLATION_ATTR)) {
    debugLog('Skipping already translated element');
    return { success: true, skipped: true };
  }

  if (element.hasAttribute(PROCESSING_ATTR)) {
    debugLog('Skipping element already in processing');
    return { success: true, skipped: true, alreadyProcessing: true };
  }

  const isBookWalkerCanvas = type === 'canvas' && isBookWalkerViewer();
  const imageId = generateImageId();
  element.setAttribute(PROCESSING_ATTR, imageId);
  if (type === 'canvas') {
    const canvas = element as HTMLCanvasElement;
    clearBookWalkerQueueState(canvas);
    canvas.dataset.mangaCanvasLogId = imageId;
    if (isBookWalkerCanvas) {
      setBookWalkerQueueState(canvas, 'processing');
      refreshBookWalkerQueueStates();
    }
  }

  if (state.settings?.showProgress) {
    if (isBookWalkerCanvas) {
      renderBookWalkerQueueState(element as HTMLCanvasElement);
    } else {
      showImageProgress(element, 'Extracting text...');
    }
  }

  try {
    let base64 = cachedBase64;

    if (!base64 && type === 'composite') {
      base64 = getCachedPageImage(element);
      if (base64) {
        debugLog(`Using globally cached page: ${getPageId(element) || 'unknown'}`);
      }
    }

    if (!base64) {
      if (type === 'img' && element?.tagName === 'IMG') {
        await waitForImageLoad(element as HTMLImageElement);
      } else if (type === 'canvas') {
        canvasSourceSignature = await waitForCanvasReady(element as HTMLCanvasElement, 6000, {
          allowOffscreen: isBookWalkerViewer(),
        });
        if (!canvasSourceSignature) {
          throw new Error('Canvas did not finish rendering');
        }
        element.setAttribute(CANVAS_SOURCE_ATTR, canvasSourceSignature);
      } else if (type === 'composite') {
        await waitForCompositeSlices(element, options.skipScroll);
      }

      base64 = await getImageBase64(element, type, src);
    } else {
      debugLog(`Using pre-cached data for ${element.parentElement?.id || 'unknown'}`);
    }

    if (!base64) {
      throw new Error(
        `Failed to extract image data from ${element.outerHTML?.substring(0, 200) || element.tagName}`,
      );
    }

    // Reject corrupted or truncated data before sending it anywhere.
    if (typeof base64 !== 'string' || base64.length < MIN_IMAGE_DATA_LENGTH) {
      throw new Error('Image data is empty or corrupted (too small)');
    }
    assertCaptureBase64Size(base64);

    let cacheSignature: string | undefined;
    if (type === 'canvas') {
      const canvas = element as HTMLCanvasElement;
      canvas.setAttribute(
        CANVAS_SOURCE_ATTR,
        canvasSourceSignature || probeCanvas(canvas).signature || '',
      );
      storeCanvasTranslationKey(canvas);
      storeCanvasDimensions(canvas, getCanvasDimensions(canvas));
      cacheSignature = await createCanvasCacheSignature(canvas, base64);
    }

    if (isBookWalkerCanvas) {
      renderBookWalkerQueueState(element as HTMLCanvasElement);
    } else {
      showImageProgress(element, 'Translating...');
    }

    const response = await sendTranslationImageRequest({
      type: 'TRANSLATE_IMAGE',
      imageId,
      base64,
      settings: state.settings ?? {},
      canvasSignature: cacheSignature,
      forceRefresh: Boolean(options.forceRefresh),
    });

    if (!response?.success) {
      throw new Error((response as { error?: string } | undefined)?.error || 'Translation failed');
    }

    if (response.result) {
      if (!handleTranslationResult(imageId, response.result)) {
        return {
          success: false,
          stale: type === 'canvas',
          error: 'Canvas changed before translation finished',
        };
      }
      notifyInpainterFallbackOnce(response.result);
    }

    return { success: true };
  } catch (error) {
    console.error(`${LOG_PREFIX} Image processing error:`, error);
    const message = (error as { message?: string } | null)?.message ?? String(error);
    if (type === 'canvas') {
      clearBookWalkerQueueState(element as HTMLCanvasElement);
      if (isBookWalkerCanvas) {
        setBookWalkerQueueState(element as HTMLCanvasElement, 'failed');
      }
    }
    element.removeAttribute(PROCESSING_ATTR);
    hideImageProgress(element);
    showImageError(element, message);
    if (isNonRetryableImageError(error)) {
      markImageRetryBlocked(element, type, error, src);
    }
    return {
      success: false,
      error: message,
      retryable: !isNonRetryableImageError(error),
    };
  }
}

/**
 * Translate one specific image (context menu, region crop). `force` clears an
 * existing translation first.
 */
export async function translateImageData(
  imageData: ImageTarget | null,
  options: TranslateImageDataOptions = {},
): Promise<ProcessResult> {
  const force = Boolean(options.force);
  const progressLabel = options.progressLabel || 'Translating selected image...';
  const successLabel = options.successLabel || 'Selected image translated';

  if (state.isProcessing) {
    const message = 'Translation is already running';
    showNotification(message);
    return { success: false, error: message };
  }

  state.isProcessing = true;
  state.translationCancelRequested = false;
  hidePersistentError();

  try {
    await ensureSettingsLoaded();

    if (!imageData?.element?.isConnected) {
      const message = 'Could not find the selected image on this page';
      reportTranslationError(message);
      return { success: false, error: message };
    }

    const ready = await ensureBackendReady();
    if (!ready) {
      return { success: false, error: 'Backend connection failed' };
    }

    const shouldForceRefresh = force && isAlreadyTranslatedElement(imageData.element);
    if (shouldForceRefresh) {
      clearImageDataTranslation(imageData);
    }

    const singleImageComplete = {
      type: 'TRANSLATION_COMPLETE',
      status: 'complete',
      total: 1,
      succeeded: 1,
      failed: 0,
    } as const;

    if (isAlreadyTranslatedElement(imageData.element)) {
      hideProgress();
      showNotification('Selected image is already translated');
      void notifyPopup(singleImageComplete);
      return { success: true, skipped: true };
    }

    updateProgress(progressLabel, 15);
    const cachedBase64 =
      imageData.base64 ||
      (imageData.type === 'composite' ? getCachedPageImage(imageData.element) : null);
    const result = await processImage(
      imageData.element,
      imageData.type,
      imageData.src,
      cachedBase64,
      {
        forceRefresh: shouldForceRefresh,
        bypassRetryBlock: Boolean(options.bypassRetryBlock || force),
      },
    );

    if (result?.success) {
      if (imageData.element?.dataset) {
        imageData.element.dataset.mangaTranslationHidden = 'false';
      }
      const finalLabel = withPendingInpainterNotice(successLabel);
      updateProgress(finalLabel, 100);
      void notifyPopup(singleImageComplete);
      setTimeout(() => hideProgress(), finalLabel === successLabel ? 1600 : 6000);
      return result;
    }

    const message = result?.error || 'Selected image translation failed';
    reportTranslationError(message);
    return { success: false, error: message };
  } catch (error) {
    console.error(`${LOG_PREFIX} Selected image translation failed:`, error);
    const message = getErrorMessage(error);
    reportTranslationError(`Translation failed: ${message}`, message);
    return { success: false, error: message };
  } finally {
    state.isProcessing = false;
  }
}

// ---------------------------------------------------------------------------
// The whole page
// ---------------------------------------------------------------------------

export interface TranslatePageOptions {
  visibleOnly?: boolean;
  /** Accepted for manual retries; page runs honor retry blocks per image. */
  bypassRetryBlock?: boolean;
}

interface AttemptOutcome {
  error?: string;
  retryable?: boolean;
}

/** Translate every untranslated image on the page (or only those in view). */
export async function translatePage(
  options: TranslatePageOptions = {},
): Promise<ProcessResult | undefined> {
  const visibleOnly = Boolean(options.visibleOnly);

  if (!state.siteEnabled) {
    return { success: false, error: 'Manga Translator is disabled on this site' };
  }

  if (state.isProcessing) {
    debugLog('Already processing');
    return undefined;
  }

  state.isProcessing = true;
  state.translationCancelRequested = false;
  hidePersistentError();

  try {
    await ensureSettingsLoaded();

    const ready = await ensureBackendReady();
    if (!ready) {
      return undefined;
    }

    const images = discoverImages();
    const scopedImages = visibleOnly
      ? images.filter((img) => isElementInViewport(img.element))
      : images;
    const untranslatedImages = scopedImages.filter(
      ({ element }) =>
        !isAlreadyTranslatedElement(element) && !element.hasAttribute(BOOKWALKER_QUEUE_ATTR),
    );

    debugLog(`Found ${images.length} images, ${untranslatedImages.length} need translation`);

    if (untranslatedImages.length === 0) {
      if (scopedImages.length > 0) {
        showNotification(
          visibleOnly ? 'Visible images already translated' : 'All images already translated',
        );
      } else {
        showNotification(
          visibleOnly
            ? 'No visible translatable images found'
            : 'No translatable images found on this page',
        );
      }
      hideProgress();
      void notifyPopup({
        type: 'TRANSLATION_COMPLETE',
        status: 'complete',
        total: 0,
        succeeded: 0,
        failed: 0,
      });
      return undefined;
    }

    // Page order, for readers with ids like content-p1, content-p2.
    const getPageNum = (element: HTMLElement) => {
      const match = (element.id || element.parentElement?.id || '').match(/content-p(\d+)/i);
      return match?.[1] ? Number.parseInt(match[1], 10) : 999;
    };
    untranslatedImages.sort((a, b) => getPageNum(a.element) - getPageNum(b.element));

    // Cache visible composites before scrolling, which can revoke their blob URLs.
    const imageCache = new Map<HTMLElement, string>();
    updateProgress(visibleOnly ? 'Caching visible images...' : 'Caching images...', 5);

    for (const { type, element } of untranslatedImages) {
      if (state.translationCancelRequested) break;
      if (type !== 'composite' || !element.isConnected) continue;

      const rect = element.getBoundingClientRect();
      if (rect.top >= window.innerHeight || rect.bottom <= 0 || rect.width <= 0) continue;

      try {
        const slices = await waitForSliceImages(element, 1, 100);
        if (slices.length === 0) continue;

        await waitForSlicesToLoad(slices);
        const base64 = await captureCompositeImage(element);
        if (base64 && base64.length > MIN_IMAGE_DATA_LENGTH) {
          imageCache.set(element, base64);
          debugLog(`Pre-cached: ${getPageId(element) || 'unknown'}`);
        }
      } catch (e) {
        console.warn(
          `${LOG_PREFIX} Pre-cache failed:`,
          (e as { message?: string } | null)?.message,
        );
      }
    }

    debugLog(`Pre-cached ${imageCache.size} visible images`);

    // Also include pages already cached while scrolling.
    for (const [pageId, base64] of state.pageImageCache.entries()) {
      const element = document.getElementById(pageId);
      if (element && !imageCache.has(element)) {
        imageCache.set(element, base64);
        debugLog(`Using global pre-cached page: ${pageId}`);
      }
    }

    let succeeded = 0;

    // Returns the failure, if any, and whether the image is worth retrying.
    const attemptImage = async (
      imageData: ImageTarget,
      isRetry: boolean,
    ): Promise<AttemptOutcome> => {
      try {
        // Readers like cmoa.jp can detach the element while the run is going.
        let element = imageData.element;
        if (!element.isConnected && imageData.selector) {
          element = document.querySelector<HTMLElement>(imageData.selector) || element;
        }

        if (isRetry && !element.isConnected) return {};
        if (element.hasAttribute(TRANSLATION_ATTR)) {
          if (!isRetry) succeeded++;
          return {};
        }

        const cachedBase64 = isRetry
          ? null
          : (imageCache.get(imageData.element) ?? imageCache.get(element) ?? null);
        const result = await processImage(element, imageData.type, imageData.src, cachedBase64);
        if (result?.success) {
          succeeded++;
          return {};
        }
        if (result?.stale && imageData.type === 'canvas' && isBookWalkerViewer()) {
          debugLog('Skipping retry for stale BookWalker canvas');
          return {};
        }
        return {
          error: result?.error || 'Processing failed',
          retryable: result?.retryable !== false,
        };
      } catch (error) {
        console.error(`${LOG_PREFIX} Error processing image:`, error);
        return { error: getErrorMessage(error), retryable: !isNonRetryableImageError(error) };
      }
    };

    const retryImages: ImageTarget[] = [];
    // The first failure reason is shown if every image fails (per-image
    // errors only flash for 3 seconds).
    let firstFailureMessage = '';

    for (const imageData of untranslatedImages) {
      if (state.translationCancelRequested) break;
      const { error, retryable } = await attemptImage(imageData, false);
      if (error && !firstFailureMessage) firstFailureMessage = String(error);
      if (retryable) retryImages.push(imageData);
    }

    // Retry once after a delay, because the images may have loaded since.
    if (!state.translationCancelRequested && retryImages.length > 0) {
      debugLog(`Retrying ${retryImages.length} failed images...`);
      updateProgress(`Retrying ${retryImages.length} failed images...`, 95);
      await delay(1000);

      for (const imageData of retryImages) {
        if (state.translationCancelRequested) break;
        const { error } = await attemptImage(imageData, true);
        if (error) console.error(`${LOG_PREFIX} Retry also failed:`, error);
      }
    }

    const total = untranslatedImages.length;
    const failed = total - succeeded;
    const outcome = {
      status: succeeded === 0 ? 'error' : failed > 0 ? 'partial' : 'complete',
      total,
      succeeded,
      failed,
    } as const;

    const hasInpainterNotice = Boolean(state.pendingInpainterNotice);
    if (state.translationCancelRequested) {
      updateProgress(withPendingInpainterNotice(`Stopped after ${succeeded}/${total} images`), 100);
      void notifyPopup({ type: 'TRANSLATION_STOPPED', ...outcome });
    } else {
      updateProgress(
        withPendingInpainterNotice(
          `${visibleOnly ? 'Visible translated' : 'Translated'} ${succeeded}/${total} images`,
        ),
        100,
      );
      let error: string | undefined;
      if (succeeded === 0 && firstFailureMessage) {
        error =
          firstFailureMessage.length > 400
            ? `${firstFailureMessage.slice(0, 400)}...`
            : firstFailureMessage;
        showNotification(error, true);
      }
      void notifyPopup({ type: 'TRANSLATION_COMPLETE', ...outcome, ...(error && { error }) });
    }
    setTimeout(() => hideProgress(), hasInpainterNotice ? 6000 : 2000);
  } catch (error) {
    console.error(`${LOG_PREFIX} Page translation failed:`, error);
    const message = getErrorMessage(error);
    reportTranslationError(`Translation failed: ${message}`, message);
  } finally {
    state.isProcessing = false;
  }
  return undefined;
}
