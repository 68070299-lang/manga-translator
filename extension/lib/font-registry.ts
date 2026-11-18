/**
 * Fonts shipped with the extension for browser-rendered text overlays.
 *
 * `cssValue` is the stored fontFamily setting that selects a font. `sha256` pins
 * the file under `extension/public/fonts/`.
 */

export interface BundledFont {
  readonly id: string;
  readonly family: string;
  readonly cssValue: string;
  /** Path relative to the extension root (served from `public/`). */
  readonly file: string;
  readonly sha256: string;
}

export const BUNDLED_FONTS: readonly BundledFont[] = Object.freeze([
  Object.freeze({
    id: 'cc-wild-words',
    family: 'CC Wild Words',
    cssValue: "'CC Wild Words', sans-serif",
    file: 'fonts/CCWildWords.ttf',
    sha256: '9cc1367a737cc65d273e18150e20d77f0ac4300e5b71e66b164ad964fc53f01b',
  }),
]);

export function getBundledFontById(id: string): BundledFont | null {
  return BUNDLED_FONTS.find((font) => font.id === id) ?? null;
}

/** The bundled font selected by a stored `fontFamily` setting value, if any. */
export function findBundledFont(cssValue: string): BundledFont | null {
  return BUNDLED_FONTS.find((font) => font.cssValue === cssValue) ?? null;
}
