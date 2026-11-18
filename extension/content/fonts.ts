/**
 * Bundled font loading. The bytes come from the worker (GET_BUNDLED_FONT)
 * because the page's CSP can block a page-context load of extension URLs, and
 * shipping the file as web-accessible would let any site detect the extension.
 */
import { type BundledFont, findBundledFont } from '../lib/font-registry';
import { sendToWorker } from '../shared/messages';
import { LOG_PREFIX } from './constants';
import { state } from './state';

async function loadBundledFont(font: BundledFont): Promise<boolean> {
  const response = await sendToWorker({ type: 'GET_BUNDLED_FONT', fontId: font.id });
  if (!response?.success) {
    throw new Error((response as { error?: string } | undefined)?.error || 'Font request failed');
  }
  const bytes = Uint8Array.from(atob(response.base64), (character) => character.charCodeAt(0));
  const face = await new FontFace(font.family, bytes.buffer).load();
  document.fonts.add(face);
  return true;
}

/**
 * Register a bundled font in this document, once per page session. Text falls
 * back to the next family and reflows when the font arrives. Resolves to
 * whether the font is registered; a failed load is retried on the next call.
 */
export function ensureBundledFontLoaded(fontFamily: string | null | undefined): Promise<boolean> {
  const font = fontFamily ? findBundledFont(fontFamily) : null;
  if (!font || typeof FontFace !== 'function' || !document.fonts) {
    return Promise.resolve(false);
  }

  let load = state.bundledFontLoads.get(font.id);
  if (!load) {
    load = loadBundledFont(font).catch((error: unknown) => {
      state.bundledFontLoads.delete(font.id);
      console.warn(
        `${LOG_PREFIX} Could not load bundled font:`,
        (error as { message?: string } | null)?.message || error,
      );
      return false;
    });
    state.bundledFontLoads.set(font.id, load);
  }
  return load;
}
