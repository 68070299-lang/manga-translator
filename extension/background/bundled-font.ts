/**
 * GET_BUNDLED_FONT: serve the bytes of a font from the bundled-font registry.
 * Only registry entries can be read; fonts are not web-accessible resources.
 */
import { browser, type PublicPath } from 'wxt/browser';
import { getBundledFontById } from '../lib/font-registry';
import { blobToBase64 } from '../lib/utils';
import type { WorkerResponseMap } from '../shared/messages';

export async function handleGetBundledFont(
  fontId: string,
): Promise<WorkerResponseMap['GET_BUNDLED_FONT']> {
  const font = getBundledFontById(fontId);
  if (!font) {
    return { success: false, error: 'Unknown bundled font' };
  }
  const response = await fetch(browser.runtime.getURL(`/${font.file}` as PublicPath));
  if (!response.ok) {
    return { success: false, error: `Could not read bundled font: HTTP ${response.status}` };
  }
  return { success: true, base64: await blobToBase64(await response.blob()) };
}
