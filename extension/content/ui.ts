/**
 * Extension UI drawn on the page: the global progress bar, the persistent
 * error banner, per-image progress chips, and the reader control strip. Every
 * piece lives in its own shadow host (see lib/overlay-dom.ts).
 */
import { createShadowHost, mountInShadow, type ShadowHost } from '../lib/overlay-dom';
import { notifyPopup } from '../shared/messages';
import { ERROR_BANNER_ID, GLOBAL_PROGRESS_ID, READER_CONTROLS_ID } from './constants';
import { ensurePositioned } from './dom';
import { state } from './state';
import type { RuntimeScope, UiActions } from './types';
import { makeElement } from './utils';

/** Register what the UI buttons trigger (done once by the runtime). */
export function setUiActions(actions: UiActions | null): void {
  state.uiActions = actions;
}

function getHost(id: string): ShadowHost | null {
  return document.getElementById(id) as ShadowHost | null;
}

// ---------------------------------------------------------------------------
// Reader controls
// ---------------------------------------------------------------------------

interface ControlButton {
  action: string;
  label: string;
  svg: string;
  run: () => void;
}

export function setupReaderControls(scope?: RuntimeScope | null): void {
  if (document.getElementById(READER_CONTROLS_ID)) return;

  const controls = createShadowHost({
    id: READER_CONTROLS_ID,
    styleText: `
        position: fixed;
        right: 18px;
        bottom: 92px;
        z-index: 100000;
        display: grid;
        grid-template-columns: repeat(5, 36px);
        gap: 6px;
        padding: 6px;
        border: 1px solid rgba(111, 45, 39, 0.26);
        border-radius: 8px;
        background: rgba(255, 248, 249, 0.96);
        box-shadow: 0 12px 28px rgba(111, 45, 39, 0.16);
      `,
  });
  controls.setAttribute('role', 'toolbar');
  controls.setAttribute('aria-label', 'Manga translation controls');

  const buttons: ControlButton[] = [
    {
      action: 'visible',
      label: 'Translate visible images',
      svg: '<path d="M4 5h16v14H4z"></path><path d="M8 9h8"></path><path d="M8 13h5"></path>',
      run: () => state.uiActions?.translatePage({ visibleOnly: true }),
    },
    {
      action: 'all',
      label: 'Translate all images',
      svg: '<path d="M3 12a9 9 0 0 1 15.3-6.4"></path><path d="M21 12a9 9 0 0 1-15.3 6.4"></path><path d="M18 3v5h-5"></path><path d="M6 21v-5h5"></path>',
      run: () => state.uiActions?.translatePage(),
    },
    {
      action: 'region',
      label: 'Translate a selected region',
      svg: '<path d="M4 8V4h4"></path><path d="M16 4h4v4"></path><path d="M20 16v4h-4"></path><path d="M8 20H4v-4"></path>',
      run: () => state.uiActions?.startRegionSelection(),
    },
    {
      action: 'stop',
      label: 'Stop translation',
      svg: '<rect x="6" y="6" width="12" height="12"></rect>',
      run: () => state.uiActions?.requestStopTranslation(),
    },
    {
      action: 'clear',
      label: 'Clear translations',
      svg: '<path d="M3 6h18"></path><path d="M8 6V4h8v2"></path><path d="M19 6l-1 14H6L5 6"></path><path d="M10 11v5"></path><path d="M14 11v5"></path>',
      run: () => state.uiActions?.clearPageTranslations(),
    },
  ];

  const controlButtons = buttons.map(({ action, label, svg, run }) => {
    const button = makeElement('button', {
      type: 'button',
      className: 'manga-reader-control-btn',
      title: label,
    });
    button.dataset.mangaAction = action;
    button.setAttribute('aria-label', label);
    button.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${svg}</svg>`;
    button.addEventListener('click', () => run());
    return button;
  });

  mountInShadow(controls, ...controlButtons);

  document.body.appendChild(controls);
  scope?.addCleanup(() => controls.remove());
}

// ---------------------------------------------------------------------------
// Per-image progress chip
// ---------------------------------------------------------------------------

export function showImageProgress(element: HTMLElement, text: string): void {
  const parent = element.parentElement;
  let progress = parent?.querySelector<ShadowHost>('.manga-image-progress') ?? null;

  if (!progress) {
    progress = createShadowHost({
      className: 'manga-image-progress',
      styleText: `
          position: absolute;
          bottom: 10px;
          left: 10px;
          background: rgba(255, 248, 249, 0.96);
          color: #6f2d27;
          padding: 8px 12px;
          border: 1px solid rgba(111, 45, 39, 0.24);
          border-radius: 8px;
          font-size: 12px;
          z-index: 10000;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
          box-shadow: 0 8px 20px rgba(111, 45, 39, 0.16);
        `,
    });
    const content = makeElement('div', { className: 'manga-chip-content' });
    content.append(
      makeElement('div', { className: 'manga-progress-spinner' }),
      makeElement('span', { className: 'manga-progress-text' }),
    );
    mountInShadow(progress, content);

    if (parent) {
      ensurePositioned(parent);
      parent.appendChild(progress);
    }
  }

  const label = progress.shadowRoot.querySelector('.manga-progress-text');
  if (label) label.textContent = text;
}

export function hideImageProgress(element: HTMLElement): void {
  element.parentElement?.querySelector('.manga-image-progress')?.remove();
}

export function showImageError(element: HTMLElement, errorText: string): void {
  showImageProgress(element, `Error: ${errorText}`);
  setTimeout(() => hideImageProgress(element), 3000);
}

// ---------------------------------------------------------------------------
// Global progress bar and notifications
// ---------------------------------------------------------------------------

export function updateProgress(text: string, percent: number): void {
  let progressBar = getHost(GLOBAL_PROGRESS_ID);

  if (!progressBar) {
    progressBar = createShadowHost({
      id: GLOBAL_PROGRESS_ID,
      styleText: `
          position: fixed;
          bottom: 20px;
          right: 20px;
          background: rgba(255, 248, 249, 0.98);
          color: #6f2d27;
          padding: 12px 16px;
          border: 1px solid rgba(111, 45, 39, 0.24);
          border-radius: 8px;
          z-index: 100000;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
          font-size: 13px;
          min-width: 200px;
          box-shadow: 0 12px 28px rgba(111, 45, 39, 0.16);
        `,
    });

    const bar = makeElement('div', { className: 'manga-progress-bar' });
    bar.appendChild(makeElement('div', { className: 'manga-progress-fill' }));
    const content = makeElement('div', { className: 'manga-progress-content' });
    content.append(makeElement('span', { className: 'manga-progress-text' }), bar);
    mountInShadow(progressBar, content);
    document.body.appendChild(progressBar);
  }

  const label = progressBar.shadowRoot.querySelector('.manga-progress-text');
  if (label) label.textContent = text;
  const fill = progressBar.shadowRoot.querySelector<HTMLElement>('.manga-progress-fill');
  if (fill) fill.style.width = `${percent}%`;

  void notifyPopup({ type: 'TRANSLATION_PROGRESS', status: text, percent });
}

export function hideProgress(): void {
  document.getElementById(GLOBAL_PROGRESS_ID)?.remove();
}

/** An error shows as a persistent banner; anything else as a brief progress message. */
export function showNotification(message: string, isError = false): void {
  if (isError) {
    showPersistentError(message);
  } else {
    updateProgress(message, 100);
    setTimeout(hideProgress, 3000);
  }
}

// ---------------------------------------------------------------------------
// Persistent error banner
// ---------------------------------------------------------------------------

/** Stays until dismissed or the backend connects. */
export function showPersistentError(message: string): void {
  let errorBanner = getHost(ERROR_BANNER_ID);

  if (!errorBanner) {
    errorBanner = createShadowHost({
      id: ERROR_BANNER_ID,
      styleText: `
          position: fixed;
          top: 0;
          left: 0;
          right: 0;
          background: linear-gradient(135deg, #c94f45 0%, #a53d35 100%);
          color: #fff;
          padding: 12px 20px;
          z-index: 100001;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
          font-size: 14px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          box-shadow: 0 10px 24px rgba(111, 45, 39, 0.24);
        `,
    });
    document.body.appendChild(errorBanner);
  }

  const left = document.createElement('div');
  left.style.cssText = 'display:flex; align-items:center; gap:10px;';
  const icon = makeElement('span', { textContent: '!' });
  icon.style.fontSize = '18px';
  left.append(icon, makeElement('span', { textContent: message }));

  const right = document.createElement('div');
  right.style.cssText = 'display:flex; align-items:center; gap:10px;';

  const retryBtn = makeElement('button', {
    id: 'manga-retry-btn',
    textContent: 'Retry Connection',
  });
  retryBtn.style.cssText =
    'background: rgba(255,255,255,0.2); border: 1px solid rgba(255,255,255,0.3); color: #fff; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 12px';

  const dismissBtn = makeElement('button', { id: 'manga-dismiss-btn', textContent: 'x' });
  dismissBtn.style.cssText =
    'background: transparent; border: none; color: #fff; cursor: pointer; font-size: 18px; padding: 4px';

  right.append(retryBtn, dismissBtn);
  mountInShadow(errorBanner, left, right);

  retryBtn.addEventListener('click', () => {
    hidePersistentError();
    state.uiActions?.translatePage();
  });

  dismissBtn.addEventListener('click', hidePersistentError);
}

export function hidePersistentError(): void {
  document.getElementById(ERROR_BANNER_ID)?.remove();
}
