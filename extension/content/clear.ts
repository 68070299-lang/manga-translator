/**
 * Undoing translations: per-element clears for each image type, clearing the
 * whole page, and requesting a stop of the running translation.
 */
import { notifyPopup } from '../shared/messages';
import { clearBookWalkerQueueState } from './bookwalker';
import { clearCanvasTranslation } from './canvas-reader';
import {
  BOOKWALKER_QUEUE_ATTR,
  BOOKWALKER_QUEUE_STATE_ATTR,
  CANVAS_TRANSLATION_KEY_ATTR,
  PROCESSING_ATTR,
  REGION_CONTAINER_CLASS,
  TRANSLATION_ATTR,
} from './constants';
import { resetTranslationState, unwrapFromTranslateWrapper } from './dom';
import { clearImageRetryBlock } from './retry';
import { state } from './state';
import type { ImageType } from './types';
import {
  hideImageProgress,
  hidePersistentError,
  hideProgress,
  showNotification,
  updateProgress,
} from './ui';

export function clearImgTranslation(img: HTMLElement): void {
  resetTranslationState(img);
  delete img.dataset.mangaOriginalSrc;
  clearImageRetryBlock(img, 'img');
  img.style.visibility = '';

  const overlay = img.nextElementSibling;
  if (overlay?.classList.contains('manga-translated-img-overlay')) {
    overlay.remove();
  }
  unwrapFromTranslateWrapper(img);
}

export function clearCompositeTranslation(container: HTMLElement): void {
  resetTranslationState(container);
  clearImageRetryBlock(container, 'composite');
  container.querySelector('.manga-composite-overlay')?.remove();
  unwrapFromTranslateWrapper(container);
}

export function clearBackgroundTranslation(element: HTMLElement): void {
  resetTranslationState(element);
  hideImageProgress(element);
  delete element.dataset.mangaTranslatedBg;
  clearImageRetryBlock(element, 'background');

  if (element.dataset?.mangaOriginalBg !== undefined) {
    element.style.backgroundImage = element.dataset.mangaOriginalBg;
    delete element.dataset.mangaOriginalBg;
  }
}

/** Clear one translated target by its image type. */
export function clearImageDataTranslation({
  element,
  type,
}: {
  element: HTMLElement;
  type: ImageType;
}): void {
  switch (type) {
    case 'canvas':
      clearCanvasTranslation(element as HTMLCanvasElement);
      break;
    case 'composite':
      clearCompositeTranslation(element);
      break;
    case 'background':
      clearBackgroundTranslation(element);
      break;
    default:
      clearImgTranslation(element);
  }
}

/** Remove every translation, overlay, queue entry and result from the page. */
export function clearPageTranslations(): { success: true; cleared: number } {
  const cleared = new Set<Element>();
  const clearAll = <T extends HTMLElement>(selector: string, clear: (element: T) => void) => {
    for (const element of document.querySelectorAll<T>(selector)) {
      clear(element);
      cleared.add(element);
    }
  };
  state.imageRetryBlockMemory.clear();
  state.canvasTranslationMemory.clear();

  // Clearing a page is also a cancellation boundary. Otherwise canvases that
  // are only queued (and have no translation key yet) can resume after the
  // visible overlays have been removed.
  state.translationCancelRequested = true;
  for (const canvas of state.bookWalkerCanvasQueue.splice(0)) {
    clearBookWalkerQueueState(canvas);
    cleared.add(canvas);
  }
  clearAll<HTMLCanvasElement>(
    `canvas[${BOOKWALKER_QUEUE_ATTR}], canvas[${BOOKWALKER_QUEUE_STATE_ATTR}]`,
    (canvas) => {
      clearBookWalkerQueueState(canvas);
      hideImageProgress(canvas);
    },
  );

  clearAll<HTMLCanvasElement>(
    `canvas[${TRANSLATION_ATTR}], canvas[${PROCESSING_ATTR}], canvas[data-manga-canvas-overlay-id], canvas[data-manga-retry-blocked-until], canvas[${CANVAS_TRANSLATION_KEY_ATTR}]`,
    clearCanvasTranslation,
  );
  clearAll(
    `img[${TRANSLATION_ATTR}], img[${PROCESSING_ATTR}], img[data-manga-original-src], img[data-manga-retry-blocked-until]`,
    clearImgTranslation,
  );
  clearAll(
    `.pt-img[${TRANSLATION_ATTR}], .pt-img[${PROCESSING_ATTR}], .pt-img[data-manga-retry-blocked-until], [id^="content-p"][${TRANSLATION_ATTR}], [id^="content-p"][${PROCESSING_ATTR}], [id^="content-p"][data-manga-retry-blocked-until]`,
    clearCompositeTranslation,
  );

  for (const overlay of document.querySelectorAll('.manga-composite-overlay')) {
    const container =
      overlay.closest<HTMLElement>('.pt-img, [id^="content-p"]') || overlay.parentElement;
    if (container) {
      clearCompositeTranslation(container);
      cleared.add(container);
    } else {
      overlay.remove();
    }
  }

  clearAll('[data-manga-original-bg]', clearBackgroundTranslation);
  clearAll('[data-manga-retry-blocked-until]', (element) => clearImageRetryBlock(element));

  for (const wrapper of document.querySelectorAll('.manga-translate-wrapper')) {
    const translatedElement = wrapper.querySelector<HTMLElement>(
      `[${TRANSLATION_ATTR}], [${PROCESSING_ATTR}]`,
    );
    if (translatedElement) {
      (translatedElement.tagName === 'IMG' ? clearImgTranslation : clearCompositeTranslation)(
        translatedElement,
      );
      cleared.add(translatedElement);
    } else {
      for (const overlay of wrapper.querySelectorAll('.manga-translate-overlay')) {
        overlay.remove();
      }
    }
  }

  for (const container of document.querySelectorAll(`.${REGION_CONTAINER_CLASS}`)) {
    container.remove();
  }
  state.regionSelectionCleanup?.();

  for (const progress of document.querySelectorAll('.manga-image-progress')) {
    progress.remove();
  }
  hideProgress();
  hidePersistentError();

  const count = cleared.size;
  showNotification(count > 0 ? `Cleared ${count} translations` : 'No translations to clear');
  void notifyPopup({ type: 'TRANSLATION_COMPLETE' });

  return { success: true, cleared: count };
}

/** Ask the running translation (page run, region, or BookWalker queue) to stop after the current image. */
export function requestStopTranslation(): { success: true; stopped: boolean } {
  const queuedCanvases = document.querySelectorAll<HTMLCanvasElement>(
    `canvas[${BOOKWALKER_QUEUE_ATTR}="true"], canvas[${BOOKWALKER_QUEUE_STATE_ATTR}="queued"]`,
  );
  const processingCanvases = document.querySelectorAll(`canvas[${PROCESSING_ATTR}]`);
  if (
    !state.isProcessing &&
    queuedCanvases.length === 0 &&
    processingCanvases.length === 0 &&
    state.bookWalkerCanvasQueue.length === 0
  ) {
    showNotification('No translation is running');
    return { success: true, stopped: false };
  }

  state.translationCancelRequested = true;
  for (const canvas of queuedCanvases) clearBookWalkerQueueState(canvas);
  for (const canvas of state.bookWalkerCanvasQueue.splice(0)) clearBookWalkerQueueState(canvas);
  updateProgress('Stopping after current image...', 96);
  return { success: true, stopped: true };
}
