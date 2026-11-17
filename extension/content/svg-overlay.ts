/**
 * SVG text overlays for the cloud pipeline: translated text blocks are drawn
 * in the browser over a white-ish box covering the original text, inside an
 * open shadow root (see lib/overlay-dom.ts).
 */
import { createShadowHost, mountInShadow } from '../lib/overlay-dom';
import type { BoundingBox, RenderSettings, TranslationBlock } from '../shared/types';
import { getCanvasOverlayHost } from './canvas-probe';
import {
  ensureCanvasOverlayId,
  positionCanvasOverlay,
  removeCanvasOverlays,
} from './canvas-reader';
import { CANVAS_OVERLAY_CLASS } from './constants';
import { ensurePositioned } from './dom';
import { ensureBundledFontLoaded } from './fonts';
import { state } from './state';
import { makeElement } from './utils';

type SvgAttributes = Record<string, string | number>;

function svgElement(
  tag: string,
  attributes: SvgAttributes = {},
  ...children: Array<Node | string>
): SVGElement {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [name, value] of Object.entries(attributes)) {
    node.setAttribute(name, String(value));
  }
  node.append(...children);
  return node;
}

function createGlowFilter(id: string, size: number, color: string): SVGElement {
  return svgElement(
    'filter',
    { id, x: '-50%', y: '-50%', width: '200%', height: '200%' },
    svgElement('feGaussianBlur', { stdDeviation: (size / 10).toFixed(2), result: 'coloredBlur' }),
    svgElement('feFlood', { 'flood-color': color, result: 'glowColor' }),
    svgElement('feComposite', {
      in: 'glowColor',
      in2: 'coloredBlur',
      operator: 'in',
      result: 'softGlow',
    }),
    svgElement(
      'feMerge',
      {},
      svgElement('feMergeNode', { in: 'softGlow' }),
      svgElement('feMergeNode', { in: 'SourceGraphic' }),
    ),
  );
}

const CJK_PATTERN = /[一-鿿㐀-䶿豈-﫿　-〿぀-ゟ゠-ヿ＀-￯]/;

/** Split text into lines of at most `maxCharsPerLine` characters (by word, or by character for CJK). */
export function wrapOverlayText(text: string, maxCharsPerLine: number): string[] {
  const wrapped: string[] = [];

  if (CJK_PATTERN.test(text)) {
    const characters = Array.from(text);
    for (let index = 0; index < characters.length; index += maxCharsPerLine) {
      wrapped.push(characters.slice(index, index + maxCharsPerLine).join(''));
    }
    return wrapped;
  }

  let currentLine = '';
  for (const word of text.split(/\s+/).filter(Boolean)) {
    const candidateLine = currentLine ? `${currentLine} ${word}` : word;
    if (candidateLine.length <= maxCharsPerLine) {
      currentLine = candidateLine;
      continue;
    }
    if (currentLine) wrapped.push(currentLine);
    currentLine = word;
    while (currentLine.length > maxCharsPerLine) {
      wrapped.push(currentLine.slice(0, maxCharsPerLine));
      currentLine = currentLine.slice(maxCharsPerLine);
    }
  }
  if (currentLine) wrapped.push(currentLine);
  return wrapped.length ? wrapped : [''];
}

export interface OverlayBox {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  centerX: number;
  centerY: number;
  width: number;
  height: number;
}

/** Model output can use percentages or pixels, so boxes are normalized to image pixels. */
export function resolveOverlayBox(
  item: Pick<TranslationBlock, 'bbox' | 'bubble_bbox' | 'bubble_center'>,
  imageWidth: number,
  imageHeight: number,
): OverlayBox {
  const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));
  const looksLikePercent = (values: number[]) =>
    Math.max(...values) <= 100 && (imageWidth > 100 || imageHeight > 100);
  const toPixelsX = (value: number) => (value / 100) * imageWidth;
  const toPixelsY = (value: number) => (value / 100) * imageHeight;

  const layoutBox =
    Array.isArray(item.bubble_bbox) && item.bubble_bbox.length === 4 ? item.bubble_bbox : item.bbox;
  let [x1, y1, x2, y2] = layoutBox.map(Number) as BoundingBox;
  if (looksLikePercent([x1, y1, x2, y2])) {
    [x1, y1, x2, y2] = [toPixelsX(x1), toPixelsY(y1), toPixelsX(x2), toPixelsY(y2)];
  }

  x1 = clamp(x1, 0, imageWidth);
  x2 = clamp(x2, 0, imageWidth);
  y1 = clamp(y1, 0, imageHeight);
  y2 = clamp(y2, 0, imageHeight);

  let centerX = (x1 + x2) / 2;
  let centerY = (y1 + y2) / 2;
  if (
    Array.isArray(item.bubble_center) &&
    item.bubble_center.length === 2 &&
    item.bubble_center.every((value) => Number.isFinite(Number(value)))
  ) {
    [centerX, centerY] = item.bubble_center.map(Number) as [number, number];
    if (looksLikePercent([centerX, centerY])) {
      centerX = toPixelsX(centerX);
      centerY = toPixelsY(centerY);
    }
    centerX = clamp(centerX, x1, x2);
    centerY = clamp(centerY, y1, y2);
  }

  return { x1, y1, x2, y2, centerX, centerY, width: x2 - x1, height: y2 - y1 };
}

type SizedElement = HTMLElement &
  Partial<Pick<HTMLImageElement, 'naturalWidth' | 'naturalHeight' | 'width' | 'height'>>;

const TEXT_ANCHORS = { left: 'start', center: 'middle', right: 'end' } as const;

/**
 * Draw translated text blocks over an image or canvas. The overlay's SVG
 * viewBox is the image's natural size, so block coordinates stay in image
 * pixels and the browser scales them with the element.
 */
export function renderTranslationOverlay(
  imageElement: HTMLElement,
  translations: readonly TranslationBlock[],
): void {
  const isCanvasElement = imageElement.tagName === 'CANVAS';
  let overlayParent: HTMLElement | null = imageElement.parentElement;

  if (isCanvasElement) {
    overlayParent = getCanvasOverlayHost(imageElement as HTMLCanvasElement);
    ensurePositioned(overlayParent);
    removeCanvasOverlays(ensureCanvasOverlayId(imageElement));
  } else {
    if (!overlayParent) return;
    if (!overlayParent.classList.contains('manga-translate-wrapper')) {
      const sized = imageElement as SizedElement;
      const wrapper = makeElement('div', { className: 'manga-translate-wrapper' });
      wrapper.style.cssText = `
          position: relative;
          display: inline-block;
          width: ${imageElement.offsetWidth || sized.width}px;
          height: ${imageElement.offsetHeight || sized.height}px;
        `;
      overlayParent.insertBefore(wrapper, imageElement);
      wrapper.appendChild(imageElement);
      overlayParent = wrapper;
    }
    overlayParent.querySelector('.manga-translate-overlay')?.remove();
  }

  const settings = state.settings;
  const render: Partial<RenderSettings> = settings?.render || {};
  const lineSpacing = render.lineSpacing || 1.2;
  const borderSize = render.borderSize || 2;
  const glowEnabled = render.glowEnabled || false;
  const autoScale = render.autoScale !== false;
  const textDirection = render.textDirection || 'auto';
  const textAlignment = render.textAlignment || 'center';

  const sized = imageElement as SizedElement;
  const imageWidth = sized.naturalWidth || sized.width || imageElement.offsetWidth || 100;
  const imageHeight = sized.naturalHeight || sized.height || imageElement.offsetHeight || 100;

  // Unique so overlays on the same page do not collide.
  const glowFilterId = `manga-glow-mg-${Math.random().toString(36).slice(2, 9)}`;

  void ensureBundledFontLoaded(settings?.fontFamily);

  const overlayElement = createShadowHost({
    className: isCanvasElement
      ? `manga-translate-overlay ${CANVAS_OVERLAY_CLASS}`
      : 'manga-translate-overlay',
    styleText: `
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        pointer-events: none;
        z-index: ${isCanvasElement ? '9999' : '100'};
      `,
  });
  if (isCanvasElement) {
    overlayElement.dataset.mangaCanvasOverlayId = imageElement.dataset.mangaCanvasOverlayId;
  }

  const overlay = svgElement('svg', {
    viewBox: `0 0 ${imageWidth} ${imageHeight}`,
    preserveAspectRatio: 'none',
  });
  overlay.classList.add('manga-overlay-svg');
  mountInShadow(overlayElement, overlay);

  if (glowEnabled) {
    overlay.appendChild(
      svgElement(
        'defs',
        {},
        createGlowFilter(glowFilterId, render.glowSize || 3, render.glowColor || '#ffffff'),
      ),
    );
  }

  for (const item of translations) {
    if (!item.translated || !item.bbox) continue;

    const displayText = render.uppercase ? item.translated.toUpperCase() : item.translated;
    const { x1, y1, x2, centerX, centerY, width, height } = resolveOverlayBox(
      item,
      imageWidth,
      imageHeight,
    );

    const useVertical =
      textDirection === 'vertical' || (textDirection === 'auto' && height > width * 1.5);
    const textX =
      textAlignment === 'left'
        ? x1 + width * 0.02
        : textAlignment === 'right'
          ? x2 - width * 0.02
          : centerX;

    overlay.appendChild(
      svgElement('rect', {
        x: x1,
        y: y1,
        width,
        height,
        fill: render.bgColor || '#ffffff',
        'fill-opacity': render.bgOpacity ?? 0.9,
        rx: '0.5',
      }),
    );

    const preferredFontSize = Math.max(6, Math.min(96, Number(settings?.fontSize) || 16));
    const minimumFontSize = Math.min(10, preferredFontSize);
    const lineExtent = useVertical ? height : width;
    const stackExtent = useVertical ? width : height;
    const wrapAt = (size: number) =>
      wrapOverlayText(displayText, Math.max(1, Math.floor(lineExtent / (size * 0.55))));

    let fontSize = preferredFontSize;
    let lines = wrapAt(fontSize);
    while (
      autoScale &&
      fontSize > minimumFontSize &&
      lines.length * fontSize * lineSpacing > stackExtent
    ) {
      fontSize -= 1;
      lines = wrapAt(fontSize);
    }

    const text = svgElement('text', {
      x: textX,
      y: centerY,
      'text-anchor': TEXT_ANCHORS[textAlignment] || 'middle',
      'dominant-baseline': 'central',
      'font-size': fontSize,
      'font-family': settings?.fontFamily || 'sans-serif',
      fill: render.textColor || '#000000',
      ...(useVertical && { 'writing-mode': 'vertical-rl', 'text-orientation': 'upright' }),
      ...(borderSize > 0 && {
        stroke: render.borderColor || '#ffffff',
        'stroke-width': ((borderSize / 10) * fontSize).toFixed(2),
        'paint-order': 'stroke fill',
      }),
      ...(glowEnabled && { filter: `url(#${glowFilterId})` }),
    });

    lines.forEach((line, lineIndex) => {
      const tspan = svgElement('tspan', {
        x: textX,
        dy:
          lineIndex === 0
            ? -((lines.length - 1) * fontSize * lineSpacing * 0.5)
            : fontSize * lineSpacing,
      });
      tspan.textContent = line;
      text.appendChild(tspan);
    });

    overlay.appendChild(text);
  }

  overlayParent.appendChild(overlayElement);
  if (isCanvasElement) {
    positionCanvasOverlay(imageElement as HTMLCanvasElement, overlayElement);
    setTimeout(() => positionCanvasOverlay(imageElement as HTMLCanvasElement, overlayElement), 50);
    setTimeout(() => positionCanvasOverlay(imageElement as HTMLCanvasElement, overlayElement), 250);
  }
}
