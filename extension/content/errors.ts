/** Error construction and classification for the translate pipeline. */
import { getErrorMessage } from './utils';

/** Thrown when an image is over the size limits; retrying cannot help. */
export class ImageLimitError extends Error {
  nonRetryable = true;
  retryable = false;
}

export function createImageLimitError(message: string): ImageLimitError {
  return new ImageLimitError(message);
}

/** True for failures that will not get better by trying the same image again soon. */
export function isNonRetryableImageError(error: unknown): boolean {
  const message = getErrorMessage(error);
  return (
    Boolean((error as { nonRetryable?: unknown } | null | undefined)?.nonRetryable) ||
    /timed?\s*out|timeout|server is busy|too large|20 MB|25 MP|pixel limit|capture limit/i.test(
      message,
    )
  );
}

/** True when the message never reached (or came back from) the service worker. */
export function isTranslationTransportError(error: unknown): boolean {
  const message = getErrorMessage(error).toLowerCase();
  return /could not establish connection|receiving end does not exist|message port closed|message channel closed|extension context invalidated|disconnected port|service worker.*(stopped|unavailable|terminated)/i.test(
    message,
  );
}
