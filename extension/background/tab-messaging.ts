/**
 * Worker -> tab notifications and tolerant handling of `browser.*` promises.
 *
 * MV3 calls reject for closed tabs and pages without a content script; those
 * expected failures are swallowed, anything else is logged.
 */
import { browser } from 'wxt/browser';
import type { TranslationErrorMessage, TranslationProgressMessage } from '../shared/messages';

export const IGNORABLE_BROWSER_CALL_ERRORS =
  /Receiving end does not exist|No tab with id|closed|Could not establish connection/i;

function messageOf(error: unknown): string {
  return (error as { message?: string } | null | undefined)?.message || String(error);
}

/**
 * Attach a rejection handler to a browser API result. Expected "tab is gone"
 * failures are ignored; others are warned. Non-promises pass through.
 */
export function safeBrowserCall<T>(result: T): T {
  if (result && typeof (result as { then?: unknown }).then === 'function') {
    (result as unknown as Promise<unknown>).then(undefined, (error) => {
      const message = messageOf(error);
      if (!IGNORABLE_BROWSER_CALL_ERRORS.test(message)) {
        console.warn('[Service Worker] Chrome API call failed:', message);
      }
    });
  }
  return result;
}

/** Send translation progress to a tab; failures other than a missing receiver are warned. */
export function notifyProgress(tabId: number | undefined, status: string, percent: number): void {
  if (!tabId) return;

  const message: TranslationProgressMessage = { type: 'TRANSLATION_PROGRESS', status, percent };
  browser.tabs.sendMessage(tabId, message).catch((err: unknown) => {
    // Tab may be closed or navigated away: only log unexpected errors.
    const text = (err as { message?: string } | null | undefined)?.message;
    if (text && !IGNORABLE_BROWSER_CALL_ERRORS.test(text)) {
      console.warn('[Service Worker] notifyProgress failed:', text);
    }
  });
}

/** Tell a tab which image failed and why. */
export function notifyTranslationError(
  tabId: number | undefined,
  imageId: string,
  error: string,
): void {
  if (!tabId) return;

  try {
    const message: TranslationErrorMessage = { type: 'TRANSLATION_ERROR', imageId, error };
    const response = browser.tabs.sendMessage(tabId, message) as Promise<unknown> | undefined;
    response?.catch?.((sendError: unknown) => {
      if (!IGNORABLE_BROWSER_CALL_ERRORS.test(messageOf(sendError))) {
        console.warn('[Service Worker] notifyTranslationError failed:', messageOf(sendError));
      }
    });
  } catch (sendError) {
    console.warn('[Service Worker] notifyTranslationError failed:', messageOf(sendError));
  }
}
