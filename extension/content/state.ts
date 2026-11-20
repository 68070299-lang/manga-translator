/**
 * The content script's mutable state, in one typed module-level object.
 *
 * Nothing here runs at import time beyond creating empty containers, so a page
 * that never activates the extension pays for one object literal.
 */
import type { Settings } from '../shared/types';
import type {
  BookWalkerQueueRecord,
  CanvasMemoryRecord,
  PageCacheStatus,
  RetryBlockRecord,
  RuntimeScope,
  UiActions,
} from './types';

type TimeoutId = ReturnType<typeof setTimeout>;
type IntervalId = ReturnType<typeof setInterval>;

export interface ContentState {
  /** Loaded lazily; `null` until the first command or an enabled site needs it. */
  settings: Settings | null;
  lastKnownSettings: Settings | null;
  settingsLoad: Promise<void> | null;
  siteEnabled: boolean;
  siteRuntimeStarted: boolean;
  siteRuntimeScope: RuntimeScope | null;
  isProcessing: boolean;
  translationCancelRequested: boolean;
  inpainterFallbackNotified: boolean;
  /**
   * Fallback text held back while a managed run owns the progress bar, so the
   * run's final progress line carries it instead of overwriting it.
   */
  pendingInpainterNotice: string;
  lastScrollPosition: number;
  scrollCacheDebounce: TimeoutId | null;
  canvasReconcileDebounce: TimeoutId | null;
  canvasReconcileInterval: IntervalId | null;
  isRestoringOverlay: boolean;
  regionSelectionCleanup: (() => void) | null;
  uiActions: UiActions | null;
  blobUrlCleanupObserver: MutationObserver | null;
  isDrainingBookWalkerCanvasQueue: boolean;
  /** Page container id -> base64 composite capture. */
  pageImageCache: Map<string, string>;
  pageCacheStatus: Map<string, PageCacheStatus>;
  bundledFontLoads: Map<string, Promise<boolean>>;
  canvasTranslationMemory: Map<string, CanvasMemoryRecord>;
  imageRetryBlockMemory: Map<string, RetryBlockRecord>;
  canvasElementRetryBlocks: WeakMap<HTMLCanvasElement, RetryBlockRecord>;
  bookWalkerCanvasQueue: HTMLCanvasElement[];
  bookWalkerQueuedCanvases: WeakSet<HTMLCanvasElement>;
  bookWalkerQueueStateByKey: Map<string, BookWalkerQueueRecord>;
}

function createInitialState(): ContentState {
  return {
    settings: null,
    lastKnownSettings: null,
    settingsLoad: null,
    siteEnabled: true,
    siteRuntimeStarted: false,
    siteRuntimeScope: null,
    isProcessing: false,
    translationCancelRequested: false,
    inpainterFallbackNotified: false,
    pendingInpainterNotice: '',
    lastScrollPosition: 0,
    scrollCacheDebounce: null,
    canvasReconcileDebounce: null,
    canvasReconcileInterval: null,
    isRestoringOverlay: false,
    regionSelectionCleanup: null,
    uiActions: null,
    blobUrlCleanupObserver: null,
    isDrainingBookWalkerCanvasQueue: false,
    pageImageCache: new Map(),
    pageCacheStatus: new Map(),
    bundledFontLoads: new Map(),
    canvasTranslationMemory: new Map(),
    imageRetryBlockMemory: new Map(),
    canvasElementRetryBlocks: new WeakMap(),
    bookWalkerCanvasQueue: [],
    bookWalkerQueuedCanvases: new WeakSet(),
    bookWalkerQueueStateByKey: new Map(),
  };
}

export const state: ContentState = createInitialState();

/** Back to a fresh page session (tests; the runtime itself never calls this). */
export function resetState(): void {
  Object.assign(state, createInitialState());
}
