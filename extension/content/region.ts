/**
 * Region translation: the user drags a rectangle over the visible tab, the
 * service worker returns a screenshot crop of just that area, and the crop is
 * translated through the normal pipeline. Nothing is captured until the drag
 * finishes. Runs in the top frame only (the content script is not injected
 * into subframes).
 */

import { createShadowHost, mountInShadow } from '../lib/overlay-dom';
import { isSelectionLargeEnough, normalizeSelection } from '../lib/region-capture';
import { sendToWorker } from '../shared/messages';
import type { ViewportRect } from '../shared/types';
import {
  ERROR_BANNER_ID,
  GLOBAL_PROGRESS_ID,
  LOG_PREFIX,
  READER_CONTROLS_ID,
  REGION_CONTAINER_CLASS,
  REGION_SELECTOR_ID,
} from './constants';
import { state } from './state';
import { translateImageData } from './translate';
import type { ProcessResult } from './types';
import { showNotification } from './ui';
import { getErrorMessage, makeElement, waitForFrames } from './utils';

/**
 * Let the user drag a rectangle over the visible tab, then translate that part
 * of a screenshot. Nothing is captured until the drag finishes.
 */
export function startRegionSelection(): void {
  if (state.regionSelectionCleanup) return;
  if (state.isProcessing) {
    showNotification('Translation is already running');
    return;
  }

  const box = makeElement('div', { className: 'manga-region-box' });
  const hint = makeElement('div', {
    className: 'manga-region-hint',
    textContent: 'Drag over the area to translate. Press Esc to cancel.',
  });
  const selector = createShadowHost({
    id: REGION_SELECTOR_ID,
    styleText: `
        position: fixed;
        top: 0;
        left: 0;
        width: 100vw;
        height: 100vh;
        z-index: 2147483646;
        background: rgba(0, 0, 0, 0.2);
        cursor: crosshair;
        touch-action: none;
      `,
  });
  mountInShadow(selector, box, hint);

  let start: { x: number; y: number } | null = null;
  const viewport = () => ({ width: window.innerWidth, height: window.innerHeight });
  const pointFrom = (event: MouseEvent) => ({ x: event.clientX, y: event.clientY });

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    event.stopPropagation();
    cleanup();
  };
  const cleanup = () => {
    state.regionSelectionCleanup = null;
    window.removeEventListener('keydown', onKeyDown, true);
    selector.remove();
  };
  state.regionSelectionCleanup = cleanup;

  selector.addEventListener('pointerdown', (event) => {
    event.preventDefault();
    start = pointFrom(event);
    selector.setPointerCapture?.(event.pointerId);
    selector.style.background = 'transparent';
    hint.remove();
  });
  selector.addEventListener('pointermove', (event) => {
    if (!start) return;
    const rect = normalizeSelection(start, pointFrom(event), viewport());
    box.style.display = 'block';
    box.style.left = `${rect.left}px`;
    box.style.top = `${rect.top}px`;
    box.style.width = `${rect.width}px`;
    box.style.height = `${rect.height}px`;
  });
  selector.addEventListener('pointerup', (event) => {
    if (!start) return;
    const rect = normalizeSelection(start, pointFrom(event), viewport());
    cleanup();
    if (!isSelectionLargeEnough(rect)) {
      showNotification('Selected region is too small to translate');
      return;
    }
    translateSelectedRegion(rect).catch((error: unknown) => {
      console.error(`${LOG_PREFIX} Region translation failed:`, error);
      showNotification(`Region translation failed: ${getErrorMessage(error)}`, true);
    });
  });
  selector.addEventListener('pointercancel', cleanup);

  window.addEventListener('keydown', onKeyDown, true);
  document.documentElement.appendChild(selector);
}

/** Ask the worker for a screenshot crop, hiding the extension UI for the capture. */
async function captureRegion(rect: ViewportRect) {
  const extensionUi = document.querySelectorAll<HTMLElement>(
    `#${READER_CONTROLS_ID}, #${GLOBAL_PROGRESS_ID}, #${ERROR_BANNER_ID}`,
  );
  for (const element of extensionUi) element.style.visibility = 'hidden';
  try {
    // Give the browser time to repaint without the selector and controls.
    await waitForFrames(2);
    return await sendToWorker({
      type: 'CAPTURE_REGION',
      rect,
      viewport: { width: window.innerWidth, height: window.innerHeight },
    });
  } finally {
    for (const element of extensionUi) element.style.visibility = '';
  }
}

async function translateSelectedRegion(rect: ViewportRect): Promise<ProcessResult> {
  const scrollX = window.scrollX;
  const scrollY = window.scrollY;
  const capture = await captureRegion(rect);
  if (!capture?.success) {
    throw new Error(
      (capture as { error?: string } | undefined)?.error || 'Could not capture the selected region',
    );
  }

  // The crop stands in for a page image so the normal translation path applies.
  // It lives outside <body> so the page's observers and body offsets ignore it.
  const container = document.createElement('div');
  container.className = REGION_CONTAINER_CLASS;
  container.style.cssText = `
      all: initial;
      position: absolute;
      display: block;
      left: ${rect.left + scrollX}px;
      top: ${rect.top + scrollY}px;
      width: ${rect.width}px;
      height: ${rect.height}px;
      z-index: 99990;
      pointer-events: none;
    `;
  const source = document.createElement('img');
  source.alt = '';
  // Marks the crop as extension-owned so page scans never pick it up.
  source.dataset.mangaOriginalSrc = 'region-capture';
  source.style.cssText = 'all: initial; display: block; width: 100%; height: 100%;';
  source.src = `data:image/png;base64,${capture.base64}`;
  container.appendChild(source);
  document.documentElement.appendChild(container);

  const result = await translateImageData(
    { element: source, type: 'img', src: null, base64: capture.base64 },
    {
      progressLabel: 'Translating selected region...',
      successLabel: 'Selected region translated',
      bypassRetryBlock: true,
    },
  );
  if (!result?.success) container.remove();
  return result;
}
