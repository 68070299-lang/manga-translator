/**
 * Runtime message router: dispatches the worker's request messages to their
 * handlers and replies through `sendResponse`. Any other message type is
 * ignored (the listener returns false), including all broadcast messages.
 */
import type { Browser } from 'wxt/browser';
import { clearAllCache, getCacheStats } from '../lib/cache';
import { errorMessage } from '../lib/utils';
import { isWorkerRequest, type WorkerRequest, type WorkerResponseMap } from '../shared/messages';
import { handleGetBundledFont } from './bundled-font';
import { handleCaptureRegion } from './capture-region';
import { handleFetchImage } from './fetch-image';
import { handleCheckBackend } from './providers';
import { handleTranslateImage } from './translate';

/** Dispatch one worker request to its handler. */
export async function handleMessage(
  message: WorkerRequest,
  sender: Browser.runtime.MessageSender,
): Promise<WorkerResponseMap[WorkerRequest['type']]> {
  switch (message.type) {
    case 'TRANSLATE_IMAGE':
      return await handleTranslateImage(message, sender);

    case 'FETCH_IMAGE':
      return await handleFetchImage(message.url);

    case 'CLEAR_CACHE':
      return { success: true, cleared: await clearAllCache() };

    case 'GET_CACHE_STATS':
      return { success: true, stats: await getCacheStats() };

    case 'CHECK_BACKEND':
      return await handleCheckBackend(message.settings);

    case 'CAPTURE_REGION':
      return await handleCaptureRegion(message, sender);

    case 'GET_BUNDLED_FONT':
      return await handleGetBundledFont(message.fontId);

    default:
      return { success: false, error: 'Unknown message type' };
  }
}

/**
 * `runtime.onMessage` listener. Returns true (keeping the channel open) only
 * for supported request types.
 */
export function handleRuntimeMessage(
  message: unknown,
  sender: Browser.runtime.MessageSender,
  sendResponse: (response?: unknown) => void,
): boolean {
  if (!isWorkerRequest(message)) return false;

  handleMessage(message, sender)
    .then((response) => sendResponse(response))
    .catch((error) => sendResponse({ success: false, error: errorMessage(error) }));

  return true;
}
