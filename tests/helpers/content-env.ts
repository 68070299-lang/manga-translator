import { type Mock, vi } from 'vitest';
import { browser } from 'wxt/browser';

export interface WorkerMessage {
  type: string;
  [key: string]: unknown;
}

export type SendMessageMock = Mock<(message: WorkerMessage) => unknown>;

/** Spy on `browser.runtime.sendMessage` (the content script's channel to the worker); resolves `undefined` by default. */
export function mockRuntimeSendMessage(): SendMessageMock {
  const spy = vi.spyOn(browser.runtime, 'sendMessage') as unknown as SendMessageMock;
  spy.mockImplementation(async () => undefined);
  return spy;
}

/** Messages sent so far, optionally only those of one `type`. */
export function sentMessages(mock: SendMessageMock, type?: string): WorkerMessage[] {
  const messages = mock.mock.calls.map(([message]) => message);
  return type ? messages.filter((message) => message?.type === type) : messages;
}

/** happy-dom has no `document.elementsFromPoint`; install a controllable one. */
export function setElementsFromPoint(impl: (x: number, y: number) => Element[]): void {
  Object.defineProperty(document, 'elementsFromPoint', {
    configurable: true,
    writable: true,
    value: impl,
  });
}

export function createCanvasSample(seed: number): Uint8ClampedArray {
  const data = new Uint8ClampedArray(64 * 64 * 4);
  for (let pixel = 0; pixel < 64 * 64; pixel++) {
    const offset = pixel * 4;
    data[offset] = (seed + pixel) % 251;
    data[offset + 1] = (seed * 3 + pixel * 7) % 251;
    data[offset + 2] = (seed * 5 + pixel * 11) % 251;
    data[offset + 3] = 255;
  }
  return data;
}

/** Make canvas 2D sampling return deterministic, non-blank pixels. */
export function mockCanvasSampling(seed = 17) {
  let sampleSeed = seed;
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation((() => ({
    drawImage: vi.fn(),
    getImageData: vi.fn(() => ({ data: createCanvasSample(sampleSeed) })),
  })) as never);
  return {
    setSeed: (value: number) => {
      sampleSeed = value;
    },
  };
}

/** Make canvas 2D sampling return a flat white image. */
export function mockFlatCanvasSampling(): void {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation((() => ({
    drawImage: vi.fn(),
    getImageData: vi.fn(() => ({ data: new Uint8ClampedArray(64 * 64 * 4).fill(255) })),
  })) as never);
}

/** Make image capture work: a 2D context that accepts drawImage and a usable toDataURL. */
export function mockImageCapture(
  dataUrl: string,
  { failAt = null }: { failAt?: 'drawImage' | 'toDataURL' | null } = {},
): void {
  const securityError = () =>
    new DOMException('Tainted canvases may not be exported.', 'SecurityError');
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation((() => ({
    drawImage: vi.fn(() => {
      if (failAt === 'drawImage') throw securityError();
    }),
  })) as never);
  vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockImplementation(() => {
    if (failAt === 'toDataURL') throw securityError();
    return dataUrl;
  });
}

const RECT = {
  x: 10,
  y: 10,
  left: 10,
  top: 10,
  right: 940,
  bottom: 675,
  width: 930,
  height: 665,
  toJSON: () => ({}),
};

/** A BookWalker-style reader: #renderer > #viewport0.currentScreen > canvas, all with a visible rect. */
export function createVisibleBookWalkerCanvas(id = 'bookwalker-page') {
  const renderer = document.createElement('div');
  renderer.id = 'renderer';

  const viewport = document.createElement('div');
  viewport.id = 'viewport0';
  viewport.className = 'currentScreen';

  const canvas = document.createElement('canvas');
  canvas.id = id;
  canvas.width = 930;
  canvas.height = 665;

  canvas.getBoundingClientRect = vi.fn(() => RECT as DOMRect);
  viewport.getBoundingClientRect = vi.fn(() => RECT as DOMRect);
  renderer.getBoundingClientRect = vi.fn(() => RECT as DOMRect);

  viewport.appendChild(canvas);
  renderer.appendChild(viewport);
  document.body.appendChild(renderer);

  return { canvas, renderer, viewport };
}

/** An `img` that reports itself loaded at the given natural size. */
export function createLoadedImage(
  src: string,
  {
    width = 400,
    height = 600,
    append = true,
  }: { width?: number; height?: number; append?: boolean } = {},
): HTMLImageElement {
  const img = document.createElement('img');
  img.src = src;
  img.width = width;
  img.height = height;
  Object.defineProperty(img, 'complete', { configurable: true, value: true });
  Object.defineProperty(img, 'naturalWidth', { configurable: true, value: width });
  Object.defineProperty(img, 'naturalHeight', { configurable: true, value: height });
  if (append) document.body.appendChild(img);
  return img;
}
