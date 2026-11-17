/**
 * Fonts shipped with the extension for browser-rendered text overlays.
 *
 * `cssValue` is the stored fontFamily setting that selects a font. `sha256` pins
 * the file: scripts/validate-extension.js fails the build if it does not match.
 */
(function(root) {
  'use strict';

  const BUNDLED_FONTS = Object.freeze([
    Object.freeze({
      id: 'cc-wild-words',
      family: 'CC Wild Words',
      cssValue: "'CC Wild Words', sans-serif",
      file: 'fonts/CCWildWords.ttf',
      sha256: '9cc1367a737cc65d273e18150e20d77f0ac4300e5b71e66b164ad964fc53f01b'
    })
  ]);

  function getBundledFontById(id) {
    return BUNDLED_FONTS.find(font => font.id === id) || null;
  }

  function findBundledFont(cssValue) {
    return BUNDLED_FONTS.find(font => font.cssValue === cssValue) || null;
  }

  const exported = {
    BUNDLED_FONTS,
    getBundledFontById,
    findBundledFont
  };

  if (root) {
    root.MangaTranslatorFontRegistry = exported;
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = exported;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
