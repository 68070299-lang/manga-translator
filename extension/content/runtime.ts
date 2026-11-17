/**
 * Site runtime: starts and stops everything the content script does on an
 * active page (reader controls, observers, scroll caching, auto-translate)
 * and keeps it in step with the per-hostname activation setting.
 *
 * A page whose site is disabled never gets past the activation check.
 */
import { getSiteState } from '../lib/site-activation';
import { revokeTranslatedSrc } from '../lib/translated-image';
import { isBookWalkerViewer } from './canvas-probe';
import { requestStopTranslation } from './clear';
import {
  CANVAS_RECONCILE_INTERVAL_MS,
  LOG_PREFIX,
  PROCESSING_ATTR,
  READER_CONTROLS_ID,
} from './constants';
import { isTranslatableImage } from './discovery';
import { preCacheVisiblePages } from './page-cache';
import {
  canAutoProcessImages,
  mutationTouchesCanvasReader,
  reconcileVisibleCanvases,
  scheduleCanvasReconcile,
} from './reconcile';
import { ensureSettingsLoaded } from './settings-store';
import { state } from './state';
import { processImage, translatePage } from './translate';
import type { RuntimeScope } from './types';
import { hidePersistentError, hideProgress, setupReaderControls } from './ui';
import { delay } from './utils';

// ---------------------------------------------------------------------------
// Scope: everything a started runtime registers, so stopping can undo it all
// ---------------------------------------------------------------------------

type TimerId = ReturnType<typeof setTimeout>;

export function createRuntimeScope(): RuntimeScope {
  const cleanups: Array<() => void> = [];
  let disposed = false;

  function addCleanup<T>(cleanup: T): T {
    if (typeof cleanup !== 'function') return cleanup;
    if (disposed) {
      cleanup();
    } else {
      cleanups.push(cleanup as () => void);
    }
    return cleanup;
  }

  return {
    get disposed() {
      return disposed;
    },
    addCleanup,
    listen(target, type, handler, options) {
      target?.addEventListener?.(type, handler, options);
      addCleanup(() => target?.removeEventListener?.(type, handler, options));
      return handler;
    },
    observe(observer) {
      addCleanup(() => observer?.disconnect?.());
      return observer;
    },
    timer(timerId, clear = clearTimeout) {
      if (timerId !== null && timerId !== undefined) {
        addCleanup(() => clear(timerId));
      }
      return timerId;
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      for (const cleanup of cleanups.splice(0).reverse()) {
        try {
          cleanup();
        } catch (error) {
          console.warn(
            `${LOG_PREFIX} Runtime cleanup failed:`,
            (error as { message?: string } | null)?.message || error,
          );
        }
      }
    },
  };
}

function setRuntimeTimeout(
  scope: RuntimeScope | null,
  callback: () => void,
  delayMs: number,
): TimerId {
  const timerId = setTimeout(() => {
    if (!scope?.disposed) callback();
  }, delayMs);
  scope?.timer(timerId);
  return timerId;
}

// ---------------------------------------------------------------------------
// Blob URL cleanup
// ---------------------------------------------------------------------------

/**
 * Revoke the translated blob URLs of a node that was removed from the page.
 * Moves (re-parenting, reordering) are reported as removals; a node that is
 * connected again still owns its translated blob URL.
 */
export function cleanupBlobUrls(element: Element): void {
  const node = element as HTMLElement;
  if (!node.dataset || node.isConnected === true) return;
  revokeTranslatedSrc(node);
  for (const child of node.querySelectorAll?.<HTMLElement>('[data-manga-translated-url]') ?? []) {
    revokeTranslatedSrc(child);
  }
}

/** Watch for removed translated nodes so their blob URLs are revoked. Idempotent. */
export function setupBlobUrlCleanup(): void {
  if (state.blobUrlCleanupObserver) return;

  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      for (const node of mutation.removedNodes) {
        if (node.nodeType === Node.ELEMENT_NODE) {
          cleanupBlobUrls(node as Element);
        }
      }
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
  state.blobUrlCleanupObserver = observer;
}

// ---------------------------------------------------------------------------
// Observers
// ---------------------------------------------------------------------------

/** Translate images the page adds later (auto-translate), once per animation frame. */
function setupMutationObserver(scope: RuntimeScope): void {
  let pendingMutations: MutationRecord[] = [];
  let rafScheduled = false;
  let rafId: number | null = null;

  function processBatch(): void {
    rafScheduled = false;
    rafId = null;
    if (!state.siteEnabled) {
      pendingMutations = [];
      return;
    }
    const batch = pendingMutations;
    pendingMutations = [];

    const processIfTranslatable = (img: HTMLImageElement) => {
      if (canAutoProcessImages() && isTranslatableImage(img)) {
        void processImage(img, 'img', null, null, { skipScroll: true });
      }
    };

    for (const mutation of batch) {
      for (const node of mutation.addedNodes) {
        if (node.nodeName === 'IMG') processIfTranslatable(node as HTMLImageElement);
        for (const img of (node as Element).querySelectorAll?.<HTMLImageElement>('img') ?? []) {
          processIfTranslatable(img);
        }
      }
    }
  }

  const observer = new MutationObserver((mutations) => {
    pendingMutations.push(...mutations);
    if (!rafScheduled) {
      rafScheduled = true;
      rafId = requestAnimationFrame(processBatch);
    }
  });

  observer.observe(document.body, { childList: true, subtree: true });
  scope.observe(observer);
  scope.addCleanup(() => {
    pendingMutations = [];
    if (rafScheduled && rafId !== null) globalThis.cancelAnimationFrame?.(rafId);
    rafScheduled = false;
    rafId = null;
  });
}

/** Pre-caches upcoming pages while the user scrolls cmoa.jp style readers. */
function setupScrollBasedCaching(scope: RuntimeScope): void {
  const schedulePreCache = (delayMs: number, shouldCache: () => boolean = () => true) => {
    if (!state.siteEnabled || !document.querySelector('[id^="content-p"], .pt-img')) return;
    if (state.scrollCacheDebounce !== null) clearTimeout(state.scrollCacheDebounce);
    state.scrollCacheDebounce = setTimeout(() => {
      if (!scope.disposed && state.siteEnabled && shouldCache()) void preCacheVisiblePages();
    }, delayMs);
  };

  scope.listen(
    window,
    'scroll',
    () =>
      schedulePreCache(300, () => {
        const scrolledFar = Math.abs(window.scrollY - state.lastScrollPosition) > 200;
        if (scrolledFar) state.lastScrollPosition = window.scrollY;
        return scrolledFar;
      }),
    { passive: true },
  );
  scope.listen(document, 'wheel', () => schedulePreCache(500), { passive: true });

  scope.addCleanup(() => {
    if (state.scrollCacheDebounce !== null) clearTimeout(state.scrollCacheDebounce);
    state.scrollCacheDebounce = null;
  });
}

/** Keep canvas overlays in step with canvas readers (mutations, resize, visibility). */
function setupCanvasViewportObserver(scope: RuntimeScope): void {
  const observer = new MutationObserver((mutations) => {
    if (!state.siteEnabled || state.isRestoringOverlay) return;
    if (mutations.some(mutationTouchesCanvasReader)) {
      scheduleCanvasReconcile(state.settings?.autoTranslate);
    }
  });

  observer.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['class', 'style'],
  });
  scope.observe(observer);

  scope.listen(window, 'resize', () => scheduleCanvasReconcile(false), { passive: true });
  scope.listen(window, 'orientationchange', () => scheduleCanvasReconcile(false), {
    passive: true,
  });
  scope.listen(document, 'visibilitychange', () =>
    scheduleCanvasReconcile(state.settings?.autoTranslate),
  );

  scheduleCanvasReconcile(state.settings?.autoTranslate, 500);

  // BookWalker's viewer redraws canvases without DOM mutations, so it alone is polled.
  if (isBookWalkerViewer() && state.canvasReconcileInterval === null) {
    state.canvasReconcileInterval = setInterval(() => {
      if (scope.disposed || !state.siteEnabled) return;
      reconcileVisibleCanvases(state.settings?.autoTranslate);
    }, CANVAS_RECONCILE_INTERVAL_MS);
  }

  scope.addCleanup(() => {
    if (state.canvasReconcileDebounce !== null) clearTimeout(state.canvasReconcileDebounce);
    state.canvasReconcileDebounce = null;
    if (state.canvasReconcileInterval !== null) clearInterval(state.canvasReconcileInterval);
    state.canvasReconcileInterval = null;
  });
}

// ---------------------------------------------------------------------------
// Start / stop
// ---------------------------------------------------------------------------

function hasActiveTranslationWork(): boolean {
  return (
    state.isProcessing ||
    state.isDrainingBookWalkerCanvasQueue ||
    state.bookWalkerCanvasQueue.length > 0 ||
    document.querySelector(`[${PROCESSING_ATTR}]`) !== null
  );
}

async function waitForTranslationIdle(): Promise<void> {
  while (hasActiveTranslationWork()) {
    await delay(50);
  }
}

/** Start the runtime on an enabled site (settings must be loaded). No-op if already started. */
export function startSiteRuntime(): void {
  if (!state.siteEnabled || state.siteRuntimeStarted) return;
  state.siteRuntimeStarted = true;
  const scope = createRuntimeScope();
  state.siteRuntimeScope = scope;

  if (state.translationCancelRequested) {
    void waitForTranslationIdle().then(() => {
      if (state.siteEnabled && state.siteRuntimeScope === scope) {
        state.translationCancelRequested = false;
        if (state.settings?.autoTranslate) void translatePage();
      }
    });
  }

  try {
    setupReaderControls(scope);
    setupMutationObserver(scope);
    setupScrollBasedCaching(scope);
    setupCanvasViewportObserver(scope);

    const pageLoaded = document.readyState === 'complete';

    // Auto-translate waits for the page load so image sizes are known.
    if (state.settings?.autoTranslate) {
      const autoTranslate = () => {
        if (state.siteEnabled && !state.translationCancelRequested) void translatePage();
      };
      if (pageLoaded) {
        setRuntimeTimeout(scope, autoTranslate, 300);
      } else {
        scope.listen(window, 'load', autoTranslate, { once: true });
      }
    }

    const schedulePreCache = () =>
      setRuntimeTimeout(
        scope,
        () => {
          if (state.siteEnabled) void preCacheVisiblePages();
        },
        500,
      );
    if (pageLoaded) {
      schedulePreCache();
    } else {
      scope.listen(window, 'load', schedulePreCache, { once: true });
    }
  } catch (error) {
    scope.dispose();
    state.siteRuntimeScope = null;
    state.siteRuntimeStarted = false;
    throw error;
  }
}

/** Stop the runtime: cancel work, tear down listeners/observers/timers, remove the extension UI. */
export function stopSiteRuntime(): void {
  if (hasActiveTranslationWork()) {
    requestStopTranslation();
  } else {
    state.translationCancelRequested = false;
  }

  state.siteRuntimeScope?.dispose();
  state.siteRuntimeScope = null;
  state.siteRuntimeStarted = false;

  if (state.scrollCacheDebounce !== null) clearTimeout(state.scrollCacheDebounce);
  state.scrollCacheDebounce = null;
  if (state.canvasReconcileDebounce !== null) clearTimeout(state.canvasReconcileDebounce);
  state.canvasReconcileDebounce = null;
  if (state.canvasReconcileInterval !== null) clearInterval(state.canvasReconcileInterval);
  state.canvasReconcileInterval = null;
  document.getElementById(READER_CONTROLS_ID)?.remove();
  state.regionSelectionCleanup?.();
  hideProgress();
  hidePersistentError();
}

/** Start with settings and the blob-URL watcher in place (the usual way an enabled site starts). */
async function activateSite(): Promise<void> {
  await ensureSettingsLoaded();
  setupBlobUrlCleanup();
  startSiteRuntime();
}

/**
 * Re-read this hostname's activation state, starting or stopping the runtime
 * when it changed. Resolves to whether the site is enabled. A failed read
 * keeps the last known state (it never fails open after a disabled state).
 */
export async function refreshSiteActivation(): Promise<boolean> {
  try {
    const nextState = await getSiteState(location.href);
    const wasEnabled = state.siteEnabled;
    state.siteEnabled = nextState.enabled;

    if (state.siteEnabled && !wasEnabled) {
      await activateSite();
    } else if (!state.siteEnabled && wasEnabled) {
      stopSiteRuntime();
    }
  } catch (error) {
    console.warn(
      `${LOG_PREFIX} Failed to read site activation:`,
      (error as { message?: string } | null)?.message || error,
    );
  }

  return state.siteEnabled;
}

/** Runtime status, for tests and diagnostics. */
export function getSiteRuntimeState(): { enabled: boolean; started: boolean; hasScope: boolean } {
  return {
    enabled: state.siteEnabled,
    started: state.siteRuntimeStarted,
    hasScope: Boolean(state.siteRuntimeScope),
  };
}

/** First start on page load: settings, blob-URL watcher, then the runtime. */
export function startInitialSiteRuntime(): Promise<void> {
  return activateSite();
}
