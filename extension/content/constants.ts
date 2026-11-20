/** Tunables, attribute names, selectors, and limits shared by the content script. */

export const MIN_IMAGE_WIDTH = 200;
export const MIN_IMAGE_HEIGHT = 200;
export const NON_MANGA_PATH_PATTERN =
  /(^|[/_.-])(ads?|banners?|avatars?|profiles?|icons?|logos?|emojis?|flags?|loading|thumbnails?|buttons?)([/_.\-?]|$)/;

export const TRANSLATION_ATTR = 'data-manga-translated';
export const PROCESSING_ATTR = 'data-manga-processing';
export const CANVAS_SOURCE_ATTR = 'data-manga-source-signature';
export const CANVAS_TRANSLATION_KEY_ATTR = 'data-manga-translation-key';
export const CANVAS_OVERLAY_CLASS = 'manga-canvas-overlay';
export const CANVAS_READER_SELECTOR = 'canvas, #renderer, [id^="viewport"], .currentScreen';
export const BLACK_IMAGE_MESSAGE =
  'Translated image appears black - please try again or clear cache';
export const REGION_CONTAINER_CLASS = 'manga-region-translation';
export const CANVAS_OVERLAY_STYLE = `
    position: absolute;
    z-index: 9999;
    pointer-events: none;
    display: none;
  `;
export const CANVAS_RECONCILE_INTERVAL_MS = 750;
export const CANVAS_TRANSLATION_MEMORY_MAX = 12;
/** Minimum base64 string length to consider image data valid (rejects corrupted/empty data). */
export const MIN_IMAGE_DATA_LENGTH = 100;
export const MAX_DECODED_IMAGE_BYTES = 20 * 1024 * 1024;
export const MAX_IMAGE_PIXELS = 25_000_000;
export const NON_RETRYABLE_IMAGE_BACKOFF_MS = 5 * 60 * 1000;
export const IMAGE_RETRY_BLOCK_MEMORY_MAX = 128;
export const STALE_CANVAS_BACKOFF_MS = 1500;
/** Page-based image cache for readers like cmoa.jp (page container id -> base64). */
export const PAGE_IMAGE_CACHE_MAX = 10;

export const BOOKWALKER_QUEUE_ATTR = 'data-manga-queued';
export const BOOKWALKER_QUEUE_STATE_ATTR = 'data-manga-queue-state';
export const BOOKWALKER_QUEUE_AHEAD_ATTR = 'data-manga-queue-ahead';
export const BOOKWALKER_WAIT_LABEL = 'wait_for translate';
export const BOOKWALKER_QUEUE_LABEL_PREFIX = 'load_img in queue';

/** Overlay and UI host ids. */
export const READER_CONTROLS_ID = 'manga-reader-controls';
export const GLOBAL_PROGRESS_ID = 'manga-global-progress';
export const ERROR_BANNER_ID = 'manga-error-banner';
export const REGION_SELECTOR_ID = 'manga-region-selector';

export const LOG_PREFIX = '[Manga Translator]';
