/**
 * Shadow DOM hosts for everything the extension draws on a page.
 *
 * Each overlay gets its own host and open shadow root rather than sharing one
 * page-level root: translated images and text must stay inside the page's
 * positioned containers to scroll and clip with the content they cover. The
 * host stays in the light DOM and carries the placement styles and the class
 * names used to find, count, and remove overlays.
 */

const SHADOW_STYLES = `
  * { box-sizing: border-box; }

  .manga-overlay-image,
  .manga-overlay-svg {
    display: block;
    width: 100%;
    height: 100%;
    max-width: none;
    max-height: none;
    margin: 0;
    padding: 0;
    border: 0;
  }

  .manga-progress-spinner {
    flex: none;
    width: 16px;
    height: 16px;
    border: 2px solid rgba(111, 45, 39, 0.22);
    border-top-color: #c94f45;
    border-radius: 50%;
    animation: manga-spin 1s linear infinite;
  }

  @keyframes manga-spin {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
  }

  .manga-progress-content {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .manga-progress-bar {
    width: 100%;
    height: 4px;
    background: rgba(111, 45, 39, 0.16);
    border-radius: 2px;
    overflow: hidden;
  }

  .manga-progress-fill {
    height: 100%;
    background: linear-gradient(90deg, #c94f45, #7aa5c6);
    border-radius: 2px;
    transition: width 0.3s ease;
  }

  .manga-reader-control-btn {
    display: grid;
    width: 36px;
    height: 36px;
    padding: 0;
    place-items: center;
    border: 1px solid rgba(111, 45, 39, 0.24);
    border-radius: 8px;
    background: #fff;
    color: #6f2d27;
    cursor: pointer;
  }

  .manga-reader-control-btn:hover {
    border-color: #7a3029;
    background: #fff4f5;
  }

  .manga-reader-control-btn:focus-visible {
    outline: 2px solid rgba(201, 79, 69, 0.45);
    outline-offset: 2px;
  }

  .manga-reader-control-btn svg {
    width: 18px;
    height: 18px;
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
  }

  .manga-chip-content {
    display: flex;
    align-items: center;
    gap: 8px;
    animation: manga-fade-in 0.2s ease;
  }

  @keyframes manga-fade-in {
    from { opacity: 0; transform: translateY(5px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .manga-region-box {
    position: absolute;
    display: none;
    border: 2px solid #c94f45;
    box-shadow: 0 0 0 100vmax rgba(0, 0, 0, 0.4);
  }

  .manga-region-hint {
    position: absolute;
    top: 16px;
    left: 50%;
    transform: translateX(-50%);
    padding: 8px 12px;
    border-radius: 8px;
    background: rgba(255, 248, 249, 0.98);
    color: #6f2d27;
    font: 13px -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    white-space: nowrap;
  }
`;

const CUSTOM_ELEMENT_NAME = 'manga-overlay';
let sharedSheet: CSSStyleSheet | null = null;

function applyShadowStyles(shadow: ShadowRoot): void {
  // Constructed sheets are not subject to the page's style-src CSP.
  try {
    if (!sharedSheet) {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(SHADOW_STYLES);
      sharedSheet = sheet;
    }
    shadow.adoptedStyleSheets = [sharedSheet];
  } catch {
    const style = document.createElement('style');
    style.textContent = SHADOW_STYLES;
    shadow.appendChild(style);
  }
}

export interface ShadowHostOptions {
  id?: string;
  className?: string;
  /** Extra inline declarations, appended after the `all: initial` reset. */
  styleText?: string;
}

/** An element whose shadowRoot is always present (created with `attachShadow`). */
export interface ShadowHost extends HTMLElement {
  readonly shadowRoot: ShadowRoot;
}

export function createShadowHost({
  id,
  className,
  styleText = '',
}: ShadowHostOptions = {}): ShadowHost {
  const host = document.createElement(CUSTOM_ELEMENT_NAME);
  if (id) host.id = id;
  if (className) host.className = className;
  // The reset keeps page rules that match any element from reaching the host.
  host.style.cssText = `all: initial; display: block; ${styleText}`;
  applyShadowStyles(host.attachShadow({ mode: 'open' }));
  return host as ShadowHost;
}

/** Replace everything in a host's shadow root except its style element. */
export function mountInShadow(host: ShadowHost, ...nodes: Node[]): void {
  for (const child of Array.from(host.shadowRoot.children)) {
    if (child.tagName !== 'STYLE') child.remove();
  }
  host.shadowRoot.append(...nodes);
}

export interface ImageOverlayOptions {
  className?: string;
  styleText?: string;
  alt?: string;
  objectFit?: string;
}

export function createImageOverlay({
  className,
  styleText,
  alt = '',
  objectFit = 'fill',
}: ImageOverlayOptions): { host: ShadowHost; image: HTMLImageElement } {
  const host = createShadowHost({ className, styleText });
  const image = document.createElement('img');
  image.className = 'manga-overlay-image';
  image.alt = alt;
  image.style.objectFit = objectFit;
  mountInShadow(host, image);
  return { host, image };
}
