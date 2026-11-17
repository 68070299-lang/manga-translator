/** Types local to the content script. */
import type { TranslationBlock } from '../shared/types';

/** How a page image is captured and replaced. */
export type ImageType = 'img' | 'composite' | 'canvas' | 'background';

/** One translatable thing found on the page (or resolved from a context-menu src). */
export interface ImageTarget {
  element: HTMLElement;
  type: ImageType;
  src: string | null;
  /** Selector that finds the element again if the reader re-creates it. */
  selector?: string | null;
  offsetTop?: number;
  offsetLeft?: number;
  /** Capture already in hand (region crops, pre-cached composites). */
  base64?: string;
}

/** Outcome of `processImage` and `translateImageData`. */
export interface ProcessResult {
  success: boolean;
  skipped?: boolean;
  alreadyProcessing?: boolean;
  retryable?: boolean;
  /** The canvas changed while its translation was in flight. */
  stale?: boolean;
  error?: string;
}

export interface ProcessImageOptions {
  /** Bypass the background translation cache for this request. */
  forceRefresh?: boolean;
  /** Ignore the local timeout backoff (manual retries). */
  bypassRetryBlock?: boolean;
  /** Do not scroll composites into view to trigger lazy loading. */
  skipScroll?: boolean;
}

export interface TranslateImageDataOptions {
  force?: boolean;
  progressLabel?: string;
  successLabel?: string;
  bypassRetryBlock?: boolean;
}

/**
 * What a translate result may look like to the renderer: the worker's
 * `TranslationResult` variants, plus partial objects older cache entries and
 * providers produce.
 */
export interface TranslationResultLike {
  mode?: 'cloud' | 'local';
  blocks?: TranslationBlock[];
  translatedImage?: string;
  mimeType?: string;
  inpainterUsed?: string;
  inpainterFallback?: boolean;
  translationFailed?: boolean;
}

export type ResultInput = TranslationResultLike | TranslationBlock[];

export interface RetryBlockRecord {
  blockedUntil: number;
  reason: string;
}

/** What a remembered canvas translation draws: a rendered image or text blocks. */
export type CanvasMemoryBody =
  | { type: 'image'; translatedImage: string; mimeType: string }
  | { type: 'blocks'; blocks: TranslationBlock[] };

/** A canvas translation kept in page memory, with the source it was made from. */
export type CanvasMemoryRecord = CanvasMemoryBody & {
  sourceSignature: string;
  canvasWidth: number;
  canvasHeight: number;
};

export type BookWalkerQueueStateName = 'queued' | 'processing' | 'translated' | 'failed';

export interface BookWalkerQueueRecord {
  state: BookWalkerQueueStateName;
  queueAhead: number;
  updatedAt: number;
}

export type PageCacheStatus = 'pending' | 'cached' | 'failed';

/** Everything a started site runtime registers, so stopping it can undo it all. */
export interface RuntimeScope {
  readonly disposed: boolean;
  addCleanup<T>(cleanup: T): T;
  listen(
    target: EventTarget | null | undefined,
    type: string,
    handler: EventListener,
    options?: AddEventListenerOptions | boolean,
  ): EventListener;
  observe<T extends MutationObserver>(observer: T): T;
  timer<T extends ReturnType<typeof setTimeout> | null | undefined>(
    timerId: T,
    clear?: (id: ReturnType<typeof setTimeout>) => void,
  ): T;
  dispose(): void;
}

/** Canvas sampling result of `probeCanvas`. */
export interface CanvasProbe {
  signature: string;
  blank: boolean;
  unreadable?: boolean;
  error?: unknown;
}

/**
 * Entry points the UI (reader controls, error banner) triggers. Registered by
 * the runtime so ui.ts stays below the translate pipeline in the import graph.
 */
export interface UiActions {
  translatePage(options?: { visibleOnly?: boolean }): unknown;
  startRegionSelection(): unknown;
  requestStopTranslation(): unknown;
  clearPageTranslations(): unknown;
}
