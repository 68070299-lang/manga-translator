/**
 * Turning page elements into base64 images: image, canvas (2D and WebGL),
 * composite (stitched slices) and background captures, the size limits that
 * guard them, and the fetch helpers that fall back to the service worker.
 */
import { blobToBase64 } from '../lib/utils';
import {
  type FailureResponse,
  type FetchImageRequest,
  type ResponseOf,
  sendToWorker,
} from '../shared/messages';
import { probeCanvas } from './canvas-probe';
import { LOG_PREFIX, MAX_DECODED_IMAGE_BYTES, MAX_IMAGE_PIXELS } from './constants';
import { createImageLimitError } from './errors';
import { fetchLocalUrlAsBase64, normalizeSourceUrl, shouldFetchInContentScript } from './helpers';
import type { ImageType } from './types';
import { debugLog, delay, processWithConcurrency, waitForFrames } from './utils';
import { isCanvasVisible, isPotentialMangaCanvas } from './visibility';

// ---------------------------------------------------------------------------
// Limits
// ---------------------------------------------------------------------------

/** True when a capture of this size stays within the pixel limit (25 MP). */
export function isCaptureDimensionsAllowed(width: unknown, height: unknown): boolean {
  const numericWidth = Number(width);
  const numericHeight = Number(height);
  return (
    Number.isFinite(numericWidth) &&
    Number.isFinite(numericHeight) &&
    numericWidth > 0 &&
    numericHeight > 0 &&
    numericWidth * numericHeight <= MAX_IMAGE_PIXELS
  );
}

export function assertCaptureDimensions(width: unknown, height: unknown): void {
  if (!isCaptureDimensionsAllowed(width, height)) {
    throw createImageLimitError('Image exceeds 25 MP pixel limit');
  }
}

/** Throws when the decoded size of a base64 capture is over 20 MB. */
export function assertCaptureBase64Size(base64: unknown): true {
  const payload =
    typeof base64 === 'string'
      ? base64.replace(/^data:[^,]*;base64,/i, '').replace(/\s+/g, '')
      : '';
  const padding = payload.endsWith('==') ? 2 : payload.endsWith('=') ? 1 : 0;
  const decodedBytes = payload ? Math.floor((payload.length * 3) / 4) - padding : 0;
  if (decodedBytes > MAX_DECODED_IMAGE_BYTES) {
    throw createImageLimitError('Image exceeds 20 MB capture limit');
  }
  return true;
}

// ---------------------------------------------------------------------------
// Waiting for content
// ---------------------------------------------------------------------------

export async function waitForSliceImages(
  container: HTMLElement,
  attempts: number,
  intervalMs: number,
): Promise<NodeListOf<HTMLImageElement>> {
  let slices = container.querySelectorAll('img');
  for (let attempt = 0; slices.length === 0 && attempt < attempts; attempt++) {
    await delay(intervalMs);
    slices = container.querySelectorAll('img');
  }
  return slices;
}

/** Resolves once the image is loaded with valid dimensions. */
export function waitForImageLoad(
  img: HTMLImageElement | null | undefined,
  timeoutMs = 5000,
): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    if (!img) {
      return reject(new Error('No image element'));
    }

    const isLoaded = () => img.complete && img.naturalWidth > 0 && img.naturalHeight > 0;
    if (isLoaded()) {
      return resolve();
    }

    const finish = (settle: () => void) => {
      clearTimeout(timer);
      clearInterval(poll);
      img.removeEventListener('load', onLoad);
      img.removeEventListener('error', onError);
      settle();
    };

    // Dimensions may need a frame to be computed.
    const onLoad = () =>
      requestAnimationFrame(() => {
        if (isLoaded()) finish(resolve);
      });
    const onError = () => finish(() => reject(new Error('Image failed to load')));

    // Blob URLs may load without firing events.
    const poll = setInterval(() => {
      if (isLoaded()) finish(resolve);
    }, 100);

    // A partially loaded image is still usable at the timeout.
    const timer = setTimeout(() => {
      finish(img.naturalWidth > 0 ? resolve : () => reject(new Error('Image load timeout')));
    }, timeoutMs);

    img.addEventListener('load', onLoad, { once: true });
    img.addEventListener('error', onError, { once: true });
  });
}

/** Some slices may never load; the capture works with whichever did. */
export function waitForSlicesToLoad(
  slices: Iterable<HTMLImageElement>,
  timeoutMs?: number,
): Promise<unknown[]> {
  return Promise.all(
    Array.from(slices, (img) =>
      waitForImageLoad(img, timeoutMs).catch((error: Error) =>
        debugLog('Slice load warning:', error.message),
      ),
    ),
  );
}

/**
 * Wait until a canvas is drawn and stable (two equal non-blank samples).
 * Resolves to its signature, or the best one seen at the timeout.
 */
export async function waitForCanvasReady(
  canvas: HTMLCanvasElement,
  timeoutMs = 5000,
  options: { allowOffscreen?: boolean } = {},
): Promise<string> {
  const start = Date.now();
  let lastSignature = '';
  let stableCount = 0;
  let lastNonBlankSignature = '';
  const allowOffscreen = Boolean(options.allowOffscreen);

  while (Date.now() - start < timeoutMs) {
    if (isPotentialMangaCanvas(canvas) && (allowOffscreen || isCanvasVisible(canvas))) {
      await waitForFrames(2);
      const probe = probeCanvas(canvas);
      if (probe.signature) {
        lastSignature = probe.signature;
        if (!probe.blank) {
          if (probe.signature === lastNonBlankSignature) {
            stableCount++;
          } else {
            stableCount = 0;
            lastNonBlankSignature = probe.signature;
          }

          if (stableCount >= 1 || probe.unreadable) {
            return probe.signature;
          }
        }
      }
    }

    await delay(150);
  }

  return lastNonBlankSignature || lastSignature;
}

/** Wait for a composite container's slices to exist and load. Throws if none appear. */
export async function waitForCompositeSlices(
  container: HTMLElement,
  skipScroll?: boolean,
): Promise<void> {
  // Scrolling triggers lazy loading; background reconciliation skips it to keep the user's position.
  if (!skipScroll) {
    container.scrollIntoView({ behavior: 'auto', block: 'center' });
  }
  await delay(300);
  await waitForFrames(2);

  const slices = await waitForSliceImages(container, 10, 200);
  if (slices.length === 0) {
    throw new Error('No slice images found in composite container after waiting');
  }
  await waitForSlicesToLoad(slices);

  // Extra wait for blob URLs to stabilize
  await delay(100);
}

// ---------------------------------------------------------------------------
// Drawing to canvases
// ---------------------------------------------------------------------------

function createCaptureCanvas(width: number, height: number): HTMLCanvasElement {
  assertCaptureDimensions(width, height);
  return Object.assign(document.createElement('canvas'), { width, height });
}

function get2dContext(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('2D canvas context unavailable');
  return ctx;
}

function renderToDataUrl(source: CanvasImageSource, width: number, height: number): string {
  const canvas = createCaptureCanvas(width, height);
  get2dContext(canvas).drawImage(source, 0, 0);
  return canvas.toDataURL('image/png');
}

function captureWebGLCanvasToDataUrl(canvas: HTMLCanvasElement): string | null {
  const gl = (canvas.getContext('webgl2') ||
    canvas.getContext('webgl') ||
    canvas.getContext('experimental-webgl')) as WebGLRenderingContext | null;
  if (!gl) {
    return null;
  }

  const width = gl.drawingBufferWidth || canvas.width;
  const height = gl.drawingBufferHeight || canvas.height;
  const output = createCaptureCanvas(width, height);
  const pixels = new Uint8Array(width * height * 4);
  gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);

  const ctx = get2dContext(output);
  const imageData = ctx.createImageData(width, height);

  for (let y = 0; y < height; y++) {
    const sourceStart = (height - y - 1) * width * 4;
    const targetStart = y * width * 4;
    imageData.data.set(pixels.subarray(sourceStart, sourceStart + width * 4), targetStart);
  }

  ctx.putImageData(imageData, 0, 0);
  return output.toDataURL('image/png');
}

function isUsableImageDataUrl(dataUrl: unknown): dataUrl is string {
  return (
    typeof dataUrl === 'string' &&
    dataUrl.startsWith('data:image/') &&
    dataUrl.includes(',') &&
    (dataUrl.split(',')[1]?.length ?? 0) > 100
  );
}

/** Base64 PNG of a canvas, trying toDataURL, a 2D redraw, then a WebGL readback. */
export async function captureCanvasImageBase64(canvas: HTMLCanvasElement): Promise<string> {
  await waitForCanvasReady(canvas, 3000).catch(() => null);
  assertCaptureDimensions(canvas.width || canvas.clientWidth, canvas.height || canvas.clientHeight);

  const strategies: Array<() => string | null | Promise<string | null>> = [
    () => canvas.toDataURL('image/png'),
    () => renderToDataUrl(canvas, canvas.width, canvas.height),
    () => captureWebGLCanvasToDataUrl(canvas),
  ];

  for (const strategy of strategies) {
    try {
      const dataUrl = await strategy();
      if (isUsableImageDataUrl(dataUrl)) {
        return dataUrl.split(',')[1] as string;
      }
    } catch (error) {
      console.warn(
        `${LOG_PREFIX} Canvas capture strategy failed:`,
        (error as { message?: string } | null)?.message,
      );
    }
  }

  throw new Error('Unable to capture canvas image data');
}

// ---------------------------------------------------------------------------
// Fetching
// ---------------------------------------------------------------------------

/** Throws if the blob was revoked or is inaccessible, so the caller can try a fallback. */
async function fetchBlobAsDataUrl(blobUrl: string): Promise<string> {
  const response = await fetch(blobUrl);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
  }
  const blob = await response.blob();

  if (blob.size === 0) {
    throw new Error('Blob is empty (possibly revoked)');
  }

  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const result = reader.result;
      if (typeof result === 'string' && result.startsWith('data:')) {
        resolve(result);
      } else {
        reject(new Error('FileReader did not produce valid data URL'));
      }
    };
    reader.onerror = () => reject(reader.error || new Error('FileReader error'));
    reader.readAsDataURL(blob);
  });
}

/**
 * Base64 of an image URL. Data, blob and file URLs are read here; anything
 * else goes through the service worker (FETCH_IMAGE), which bypasses page CORS.
 */
export async function fetchAndConvertToBase64(url: unknown): Promise<string> {
  const rawUrl = typeof url === 'string' ? url.trim() : '';
  if (!rawUrl) throw new Error('Cannot fetch image: empty URL');
  const dataMatch = /^data:[^,]*;base64,(.*)$/i.exec(rawUrl);
  if (dataMatch?.[1] !== undefined) return decodeURIComponent(dataMatch[1]).replace(/\s+/g, '');

  const normalizedUrl = normalizeSourceUrl(rawUrl);
  // blob:/file: sources are only reachable from the page; the service worker rejects them.
  if (shouldFetchInContentScript(normalizedUrl)) {
    return fetchLocalUrlAsBase64(normalizedUrl, {
      fetch: globalThis.fetch.bind(globalThis),
      blobToBase64,
    });
  }
  const urlPreview = normalizedUrl.slice(0, 200);
  let response: ResponseOf<FetchImageRequest>;

  try {
    response = await sendToWorker({ type: 'FETCH_IMAGE', url: normalizedUrl });
  } catch (e) {
    throw new Error(
      `Failed to fetch image via background (sendMessage): ${(e as { message?: string } | null)?.message || String(e)} (${urlPreview})`,
    );
  }

  if (response?.success && response.base64) {
    return response.base64;
  }

  const reason = response ? (response as FailureResponse).error || 'Unknown error' : 'no response';
  throw new Error(`Failed to fetch image via background: ${reason} (${urlPreview})`);
}

// ---------------------------------------------------------------------------
// Composite (sliced) readers
// ---------------------------------------------------------------------------

async function getImageDataUrlWithRetry(
  img: HTMLImageElement,
  maxRetries = 3,
): Promise<string | null> {
  const isImageReady = () => img.complete && img.naturalWidth > 0 && img.naturalHeight > 0;

  for (let attempt = 0; attempt < maxRetries; attempt++) {
    if (!isImageReady()) {
      try {
        await waitForImageLoad(img, 2000);
        await waitForFrames(2);
      } catch (e) {
        console.warn(
          `${LOG_PREFIX} Image not ready on attempt ${attempt + 1}:`,
          (e as { message?: string } | null)?.message,
        );
      }
    }

    // Drawing directly is fastest but fails on tainted images.
    if (isImageReady()) {
      try {
        const dataUrl = renderToDataUrl(img, img.naturalWidth, img.naturalHeight);
        if (dataUrl && dataUrl.length > 100 && !dataUrl.includes('data:,')) {
          return dataUrl;
        }
      } catch (e) {
        if ((e as { nonRetryable?: unknown } | null)?.nonRetryable) throw e;
        console.warn(
          `${LOG_PREFIX} Canvas draw attempt ${attempt + 1} failed:`,
          (e as { message?: string } | null)?.message,
        );
      }
    }

    const imageSrc = img.currentSrc || img.src;
    if (imageSrc?.startsWith('data:')) return imageSrc;

    if (imageSrc) {
      try {
        if (imageSrc.startsWith('blob:')) {
          const dataUrl = await fetchBlobAsDataUrl(imageSrc);
          if (dataUrl.length > 100) return dataUrl;
        } else {
          const base64 = await fetchAndConvertToBase64(imageSrc);
          if (base64) return `data:image/png;base64,${base64}`;
        }
      } catch (e) {
        console.warn(
          `${LOG_PREFIX} Fetch attempt ${attempt + 1} failed:`,
          (e as { message?: string } | null)?.message,
        );
      }
    }

    if (attempt < maxRetries - 1) {
      await delay(150 * 2 ** attempt);
    }
  }

  return null;
}

/** Fallback without pre-conversion, which may produce a tainted canvas. */
async function captureCompositeDirectDraw(
  container: HTMLElement,
  images: HTMLImageElement[],
  scale: number,
): Promise<string | null> {
  debugLog('Attempting direct draw fallback');

  const rect = container.getBoundingClientRect();
  const canvas = createCaptureCanvas(
    Math.round(container.offsetWidth * scale),
    Math.round(container.offsetHeight * scale),
  );
  const ctx = get2dContext(canvas);
  ctx.scale(scale, scale);

  let drawnCount = 0;
  for (const img of images) {
    if (!img.complete || img.naturalWidth === 0) continue;

    try {
      const imgRect = img.getBoundingClientRect();
      ctx.drawImage(
        img,
        imgRect.left - rect.left,
        imgRect.top - rect.top,
        imgRect.width,
        imgRect.height,
      );
      drawnCount++;
    } catch {
      // Individual image draw failed
    }
  }

  if (drawnCount === 0) {
    console.error(`${LOG_PREFIX} Direct draw failed - no images drawn`);
    return null;
  }

  try {
    const base64 = canvas.toDataURL('image/png').split(',')[1] ?? null;
    debugLog(`Direct draw succeeded with ${drawnCount} images`);
    return base64;
  } catch (e) {
    console.error(`${LOG_PREFIX} Direct draw canvas tainted:`, e);
    return null;
  }
}

interface CapturedSlice {
  dataUrl: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Stitches the slices of a container into one base64 image. */
export async function captureCompositeImage(container: HTMLElement): Promise<string | null> {
  const rect = container.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;

  let containerWidth = container.offsetWidth;
  let containerHeight = container.offsetHeight;

  if (containerWidth === 0 || containerHeight === 0) {
    const firstImg = container.querySelector('img');
    if (!firstImg || firstImg.naturalWidth <= 0) {
      console.error(`${LOG_PREFIX} Cannot determine container dimensions`);
      return null;
    }
    containerWidth = firstImg.naturalWidth;
    containerHeight = firstImg.naturalHeight;
  }

  const canvas = createCaptureCanvas(
    Math.round(containerWidth * scale),
    Math.round(containerHeight * scale),
  );
  const ctx = get2dContext(canvas);
  ctx.scale(scale, scale);

  await waitForFrames(2);

  const images = Array.from(container.querySelectorAll('img'));
  debugLog(
    `Capturing ${images.length} slices from composite, container size: ${containerWidth}x${containerHeight}`,
  );

  if (images.length === 0) {
    console.error(`${LOG_PREFIX} No images found in composite container`);
    return null;
  }

  const captureSlice = async (img: HTMLImageElement): Promise<CapturedSlice | null> => {
    const imgRect = img.getBoundingClientRect();
    if (imgRect.width === 0 || imgRect.height === 0) return null;

    const dataUrl = await getImageDataUrlWithRetry(img, 3);
    if (!dataUrl) {
      console.warn(
        `${LOG_PREFIX} Could not get data for image: ${(img.currentSrc || img.src || '').substring(0, 50)}`,
      );
      return null;
    }

    return {
      dataUrl,
      x: imgRect.left - rect.left,
      y: imgRect.top - rect.top,
      width: imgRect.width,
      height: imgRect.height,
    };
  };

  // Data URLs cannot taint the canvas. Converting concurrently reduces blob revocation races.
  const slices = await processWithConcurrency(images, 6, (img) =>
    captureSlice(img).catch(() => null),
  );
  const captured = slices.filter((slice): slice is CapturedSlice => Boolean(slice));

  if (captured.length > 0 && captured.length < images.length) {
    debugLog(`Only captured ${captured.length}/${images.length} images, retrying missed ones...`);
    await delay(500);

    for (const [index, img] of images.entries()) {
      if (!slices[index] && img.complete && img.naturalWidth > 0) {
        const slice = await captureSlice(img);
        if (slice) captured.push(slice);
      }
    }
  }

  if (captured.length === 0) {
    console.error(`${LOG_PREFIX} Failed to convert any images to data URLs`);
    return captureCompositeDirectDraw(container, images, scale);
  }

  debugLog(`Successfully converted ${captured.length}/${images.length} images`);

  for (const slice of captured) {
    try {
      const tempImg = new Image();
      await new Promise<void>((resolve, reject) => {
        tempImg.onload = () => resolve();
        tempImg.onerror = () => reject(new Error('Failed to load converted image'));
        tempImg.src = slice.dataUrl;
      });
      ctx.drawImage(tempImg, slice.x, slice.y, slice.width, slice.height);
    } catch (drawError) {
      console.error(`${LOG_PREFIX} Error drawing image to canvas:`, drawError);
    }
  }

  try {
    const base64 = canvas.toDataURL('image/png').split(',')[1] ?? null;
    debugLog('Composite capture successful');
    return base64;
  } catch (taintError) {
    console.error(`${LOG_PREFIX} Canvas export failed:`, taintError);
    return null;
  }
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

/**
 * Base64 of a page element by type. Returns null when nothing could be
 * extracted; limit errors and worker fetch failures are rethrown so the real
 * reason reaches the user.
 */
export async function getImageBase64(
  element: HTMLElement,
  type: ImageType,
  src: string | null,
): Promise<string | null> {
  try {
    if (type === 'composite') {
      return await captureCompositeImage(element);
    }

    if (type === 'canvas') {
      return await captureCanvasImageBase64(element as HTMLCanvasElement);
    }

    if (type === 'background') {
      return await fetchAndConvertToBase64(src);
    }

    const img = element as HTMLImageElement;
    try {
      const width = img.naturalWidth || img.width;
      const height = img.naturalHeight || img.height;
      return renderToDataUrl(img, width, height).split(',')[1] ?? null;
    } catch {
      debugLog('Canvas blocked by CORS, using fetch');
      return await fetchAndConvertToBase64(img.src);
    }
  } catch (error) {
    console.error(`${LOG_PREFIX} Base64 conversion error:`, error);
    if ((error as { nonRetryable?: unknown } | null)?.nonRetryable) throw error;
    // Keep the worker's real reason (HTTP status, content type, 20 MB limit)
    // instead of the generic "Failed to extract image data" message.
    if (
      String((error as { message?: unknown } | null)?.message || '').startsWith(
        'Failed to fetch image via background',
      )
    ) {
      throw error;
    }
    return null;
  }
}
