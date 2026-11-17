/** Geometry and visibility checks for page elements. */
import { MIN_IMAGE_HEIGHT, MIN_IMAGE_WIDTH } from './constants';

export function isElementInViewport(element: Element | null | undefined): boolean {
  if (!element?.isConnected) return false;
  const rect = element.getBoundingClientRect();
  if (rect.width < 1 || rect.height < 1) return false;
  return (
    rect.bottom > 0 &&
    rect.right > 0 &&
    rect.top < window.innerHeight &&
    rect.left < window.innerWidth
  );
}

/** False when the element or any ancestor is hidden, collapsed to nothing, or transparent. */
export function isRenderedWithAncestors(element: Element | null | undefined): boolean {
  for (
    let node: Element | null | undefined = element;
    node?.nodeType === Node.ELEMENT_NODE;
    node = node.parentElement
  ) {
    const style = getComputedStyle(node);
    if (
      style.display === 'none' ||
      style.visibility === 'hidden' ||
      Number.parseFloat(style.opacity || '1') === 0
    ) {
      return false;
    }
  }
  return true;
}

export function isPotentialMangaCanvas(canvas: HTMLCanvasElement | null | undefined): boolean {
  if (!canvas || canvas.classList?.contains('dummy')) return false;
  const rect = canvas.getBoundingClientRect();
  const width = canvas.width || canvas.clientWidth || rect.width;
  const height = canvas.height || canvas.clientHeight || rect.height;
  return width >= MIN_IMAGE_WIDTH && height >= MIN_IMAGE_HEIGHT;
}

export function isCanvasVisible(canvas: HTMLCanvasElement): boolean {
  return isElementInViewport(canvas) && isRenderedWithAncestors(canvas);
}

/** Area of an element that is inside the viewport, in CSS pixels squared. */
export function getViewportIntersectionArea(element: Element): number {
  const rect = element.getBoundingClientRect();
  const left = Math.max(0, rect.left);
  const top = Math.max(0, rect.top);
  const right = Math.min(window.innerWidth, rect.right);
  const bottom = Math.min(window.innerHeight, rect.bottom);
  return Math.max(0, right - left) * Math.max(0, bottom - top);
}
