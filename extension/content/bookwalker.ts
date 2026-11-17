/**
 * BookWalker canvas-reader queue state. BookWalker pages are canvases that are
 * translated one at a time; each canvas waiting or working carries
 * `data-manga-queued`, `data-manga-queue-state` and `data-manga-queue-ahead`
 * attributes, and a progress chip labelled with its place in the queue.
 *
 * The queue consumer (enqueue and drain) lives in `bookwalker-queue.ts`.
 */

import { getCanvasTranslationKey, isBookWalkerViewer } from './canvas-probe';
import {
  BOOKWALKER_QUEUE_AHEAD_ATTR,
  BOOKWALKER_QUEUE_ATTR,
  BOOKWALKER_QUEUE_LABEL_PREFIX,
  BOOKWALKER_QUEUE_STATE_ATTR,
  BOOKWALKER_WAIT_LABEL,
  CANVAS_TRANSLATION_KEY_ATTR,
  PROCESSING_ATTR,
  TRANSLATION_ATTR,
} from './constants';
import { state } from './state';
import type { BookWalkerQueueRecord, BookWalkerQueueStateName } from './types';
import { hideImageProgress, showImageProgress } from './ui';
import { getViewportIntersectionArea, isCanvasVisible, isPotentialMangaCanvas } from './visibility';

/** The canvases of the page currently on screen in BookWalker's viewer. */
export function getActiveBookWalkerCanvases(): HTMLCanvasElement[] {
  if (!isBookWalkerViewer()) return [];

  const isVisibleMangaCanvas = (canvas: HTMLCanvasElement) =>
    isPotentialMangaCanvas(canvas) && isCanvasVisible(canvas);
  const active = new Set<HTMLCanvasElement>();
  const points: Array<[number, number]> = [
    [window.innerWidth / 2, window.innerHeight / 2],
    [window.innerWidth * 0.25, window.innerHeight / 2],
    [window.innerWidth * 0.75, window.innerHeight / 2],
    [window.innerWidth / 2, window.innerHeight * 0.35],
    [window.innerWidth / 2, window.innerHeight * 0.65],
  ];

  for (const [x, y] of points) {
    const elements = document.elementsFromPoint(
      Math.max(1, Math.min(window.innerWidth - 1, x)),
      Math.max(1, Math.min(window.innerHeight - 1, y)),
    );
    const canvas = elements.find(
      (element): element is HTMLCanvasElement =>
        element?.tagName === 'CANVAS' && isVisibleMangaCanvas(element as HTMLCanvasElement),
    );
    if (canvas) active.add(canvas);
  }

  if (active.size > 0) return Array.from(active);

  const currentVisible = Array.from(
    document.querySelectorAll<HTMLCanvasElement>('#renderer .currentScreen canvas'),
  ).filter(isVisibleMangaCanvas);
  if (currentVisible.length > 0) return currentVisible;

  return Array.from(
    document.querySelectorAll<HTMLCanvasElement>(
      '#renderer [id^="viewport"] canvas, #renderer canvas',
    ),
  )
    .filter(isVisibleMangaCanvas)
    .sort((a, b) => getViewportIntersectionArea(b) - getViewportIntersectionArea(a))
    .slice(0, 2);
}

export function hasProcessingBookWalkerCanvas(): boolean {
  return document.querySelector(`canvas[${PROCESSING_ATTR}]`) !== null;
}

export function getBookWalkerQueueKey(canvas: HTMLCanvasElement | null | undefined): string {
  if (!canvas) return '';
  return (
    canvas.getAttribute?.(CANVAS_TRANSLATION_KEY_ATTR) ||
    canvas.dataset?.mangaCanvasTranslationKey ||
    getCanvasTranslationKey(canvas) ||
    canvas.id ||
    canvas.parentElement?.id ||
    canvas.closest?.('[id]')?.id ||
    ''
  );
}

/** Show the queue chip (waiting label or queue position) for a canvas. */
export function renderBookWalkerQueueState(canvas: HTMLCanvasElement | null | undefined): void {
  if (!canvas || !state.settings?.showProgress) return;
  const record = state.bookWalkerQueueStateByKey.get(getBookWalkerQueueKey(canvas));
  if (record?.state === 'processing') {
    showImageProgress(canvas, BOOKWALKER_WAIT_LABEL);
  } else if (record?.state === 'queued') {
    showImageProgress(canvas, `${BOOKWALKER_QUEUE_LABEL_PREFIX}${Math.max(1, record.queueAhead)}`);
  }
}

export function setBookWalkerQueueState(
  canvas: HTMLCanvasElement | null | undefined,
  queueState: BookWalkerQueueStateName,
  queueAhead: number | string = 0,
): void {
  if (!canvas) return;
  const key = getBookWalkerQueueKey(canvas);
  if (!key) return;

  const record: BookWalkerQueueRecord = {
    state: queueState,
    queueAhead: Math.max(0, Number(queueAhead) || 0),
    updatedAt: Date.now(),
  };
  state.bookWalkerQueueStateByKey.set(key, record);
  canvas.setAttribute?.(BOOKWALKER_QUEUE_STATE_ATTR, queueState);
  if (queueState === 'queued') {
    canvas.setAttribute?.(BOOKWALKER_QUEUE_AHEAD_ATTR, String(record.queueAhead));
  } else {
    canvas.removeAttribute?.(BOOKWALKER_QUEUE_AHEAD_ATTR);
  }
  renderBookWalkerQueueState(canvas);
}

export function clearBookWalkerQueueState(canvas: HTMLCanvasElement | null | undefined): void {
  if (!canvas) return;
  state.bookWalkerQueuedCanvases.delete(canvas);
  canvas.removeAttribute?.(BOOKWALKER_QUEUE_ATTR);
  canvas.removeAttribute?.(BOOKWALKER_QUEUE_AHEAD_ATTR);
  // Always delete the record, or compaction would orphan entries in the map.
  const key = getBookWalkerQueueKey(canvas);
  if (key) state.bookWalkerQueueStateByKey.delete(key);
  canvas.removeAttribute?.(BOOKWALKER_QUEUE_STATE_ATTR);
}

export function completeBookWalkerQueueState(
  canvas: HTMLCanvasElement | null | undefined,
  queueState: BookWalkerQueueStateName = 'translated',
): void {
  if (!canvas) return;
  clearBookWalkerQueueState(canvas);
  setBookWalkerQueueState(canvas, queueState);
  if (queueState === 'translated') {
    hideImageProgress(canvas);
  }
}

export function isBookWalkerQueueEligible(canvas: HTMLCanvasElement | undefined): boolean {
  return Boolean(
    canvas?.isConnected &&
      !canvas.hasAttribute(TRANSLATION_ATTR) &&
      !canvas.hasAttribute(PROCESSING_ATTR) &&
      isPotentialMangaCanvas(canvas),
  );
}

/** Drop queued canvases that went away or no longer need translating. */
export function compactBookWalkerCanvasQueue(): void {
  const queue = state.bookWalkerCanvasQueue;
  for (let index = queue.length - 1; index >= 0; index--) {
    const canvas = queue[index];
    if (!isBookWalkerQueueEligible(canvas)) {
      queue.splice(index, 1);
      clearBookWalkerQueueState(canvas);
    }
  }
}

/**
 * Recompute every queued canvas's position. `skipCompact` is for the drain
 * loop, which owns the queue while it iterates.
 */
export function refreshBookWalkerQueueStates({ skipCompact = false } = {}): void {
  if (!skipCompact) {
    compactBookWalkerCanvasQueue();
  }
  let queueAhead = hasProcessingBookWalkerCanvas() ? 1 : 0;
  for (const canvas of state.bookWalkerCanvasQueue) {
    setBookWalkerQueueState(canvas, 'queued', Math.max(1, queueAhead));
    queueAhead += 1;
  }
}
