/**
 * The BookWalker canvas queue consumer. Canvases are translated strictly one
 * at a time, in the order they were queued; a revisited page restores from
 * page memory instead of translating again.
 */

import {
  clearBookWalkerQueueState,
  compactBookWalkerCanvasQueue,
  completeBookWalkerQueueState,
  hasProcessingBookWalkerCanvas,
  isBookWalkerQueueEligible,
  refreshBookWalkerQueueStates,
  renderBookWalkerQueueState,
  setBookWalkerQueueState,
} from './bookwalker';
import { restoreCanvasTranslationFromMemory } from './canvas-memory';
import { getCanvasLogId } from './canvas-probe';
import { BOOKWALKER_QUEUE_ATTR, LOG_PREFIX, PROCESSING_ATTR, TRANSLATION_ATTR } from './constants';
import { state } from './state';
import { processImage } from './translate';
import { debugLog, delay } from './utils';

function dropQueuedCanvas(canvas: HTMLCanvasElement | undefined): void {
  state.bookWalkerCanvasQueue.shift();
  clearBookWalkerQueueState(canvas);
  refreshBookWalkerQueueStates({ skipCompact: true });
}

async function waitForBookWalkerQueueSlot(): Promise<void> {
  while (!state.translationCancelRequested && hasProcessingBookWalkerCanvas()) {
    await delay(150);
  }
}

/**
 * Single consumer of the queue: `isDrainingBookWalkerCanvasQueue` guards
 * against re-entrance, and items added while draining restart the loop at the end.
 */
export async function drainBookWalkerCanvasQueue(): Promise<void> {
  if (state.isDrainingBookWalkerCanvasQueue) return;
  state.isDrainingBookWalkerCanvasQueue = true;
  const queue = state.bookWalkerCanvasQueue;

  try {
    compactBookWalkerCanvasQueue();
    refreshBookWalkerQueueStates({ skipCompact: true });

    while (queue.length > 0 && !state.translationCancelRequested) {
      const canvas = queue[0];
      if (!canvas) break;

      if (!isBookWalkerQueueEligible(canvas)) {
        dropQueuedCanvas(canvas);
        continue;
      }

      // A revisited page restores from memory instead of re-translating.
      if (restoreCanvasTranslationFromMemory(canvas)) {
        queue.shift();
        completeBookWalkerQueueState(canvas, 'translated');
        refreshBookWalkerQueueStates({ skipCompact: true });
        continue;
      }

      await waitForBookWalkerQueueSlot();
      if (state.translationCancelRequested) break;
      if (!isBookWalkerQueueEligible(canvas)) {
        dropQueuedCanvas(canvas);
        continue;
      }

      queue.shift();
      clearBookWalkerQueueState(canvas);
      setBookWalkerQueueState(canvas, 'processing');
      refreshBookWalkerQueueStates({ skipCompact: true });
      const logId = getCanvasLogId(canvas);
      debugLog('BookWalker canvas wait for translate', logId);
      renderBookWalkerQueueState(canvas);

      try {
        const result = await processImage(canvas, 'canvas', null, null, { skipScroll: true });
        if (!result?.success && result?.error) {
          setBookWalkerQueueState(canvas, 'failed');
          console.warn(
            `${LOG_PREFIX} BookWalker queued canvas translation failed`,
            logId,
            result.error,
          );
        }
      } catch (err) {
        setBookWalkerQueueState(canvas, 'failed');
        console.warn(`${LOG_PREFIX} BookWalker queued canvas translation error`, logId, err);
      }
      refreshBookWalkerQueueStates({ skipCompact: true });
    }
  } finally {
    state.isDrainingBookWalkerCanvasQueue = false;
    if (queue.length > 0 && !state.translationCancelRequested) {
      setTimeout(() => void drainBookWalkerCanvasQueue(), 0);
    }
  }
}

/** Returns false if the canvas was ineligible (disconnected, already translated, etc.). */
export function enqueueBookWalkerCanvas(canvas: HTMLCanvasElement, reason = 'auto'): boolean {
  if (state.translationCancelRequested) return false;
  if (
    !canvas?.isConnected ||
    canvas.hasAttribute(TRANSLATION_ATTR) ||
    canvas.hasAttribute(PROCESSING_ATTR) ||
    canvas.hasAttribute(BOOKWALKER_QUEUE_ATTR) ||
    state.bookWalkerQueuedCanvases.has(canvas)
  ) {
    return false;
  }

  state.bookWalkerQueuedCanvases.add(canvas);
  canvas.setAttribute(BOOKWALKER_QUEUE_ATTR, 'true');
  state.bookWalkerCanvasQueue.push(canvas);
  refreshBookWalkerQueueStates();
  debugLog('BookWalker canvas queued for translate', getCanvasLogId(canvas), reason);
  void drainBookWalkerCanvasQueue();
  return true;
}
