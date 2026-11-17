/**
 * Debounced canvas reconcile, kept apart from reconcile.ts so the translate
 * pipeline can request a reconcile without importing the module that drives
 * it (reconcile → bookwalker-queue → translate would otherwise be a cycle).
 * main.ts registers the real reconciler at start-up.
 */
import { state } from './state';

type CanvasReconciler = (autoProcess: boolean) => void;

let reconciler: CanvasReconciler | null = null;

export function setCanvasReconciler(fn: CanvasReconciler | null): void {
  reconciler = fn;
}

/** Debounced reconcile of visible canvases; a no-op until a reconciler is registered. */
export function scheduleCanvasReconcile(autoProcess = false, delayMs = 120): void {
  if (!state.siteEnabled) return;
  if (state.canvasReconcileDebounce !== null) clearTimeout(state.canvasReconcileDebounce);
  state.canvasReconcileDebounce = setTimeout(() => {
    reconciler?.(autoProcess);
  }, delayMs);
}
