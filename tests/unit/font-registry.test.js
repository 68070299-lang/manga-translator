const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {
  BUNDLED_FONTS,
  findBundledFont,
  getBundledFontById
} = require('../../extension/lib/font-registry');
const { validateExtension, EXTENSION_DIR } = require('../../scripts/validate-extension');

describe('bundled font registry', () => {
  it('finds fonts by stored setting value and by id', () => {
    const font = getBundledFontById('cc-wild-words');
    expect(font.family).toBe('CC Wild Words');
    expect(findBundledFont("'CC Wild Words', sans-serif")).toBe(font);
    expect(findBundledFont('sans-serif')).toBeNull();
    expect(getBundledFontById('missing')).toBeNull();
  });

  it('pins every bundled font file to its registered checksum', () => {
    for (const font of BUNDLED_FONTS) {
      const bytes = fs.readFileSync(path.join(EXTENSION_DIR, font.file));
      expect(crypto.createHash('sha256').update(bytes).digest('hex')).toBe(font.sha256);
    }
  });

  it('offers every bundled font in both settings screens', () => {
    for (const page of ['popup/popup.html', 'options/options.html']) {
      const html = fs.readFileSync(path.join(EXTENSION_DIR, page), 'utf8');
      for (const font of BUNDLED_FONTS) {
        expect(html).toContain(`value="${font.cssValue}"`);
      }
    }
  });

  it('packages bundled fonts through the extension validator', () => {
    const result = validateExtension();
    expect(result.errors).toEqual([]);
    expect(result.files).toContain('fonts/CCWildWords.ttf');
  });

  it('fails validation when a bundled font does not match its checksum', () => {
    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'font-registry-'));
    try {
      fs.cpSync(EXTENSION_DIR, tempDir, { recursive: true });
      fs.writeFileSync(path.join(tempDir, 'fonts', 'CCWildWords.ttf'), 'tampered');
      const result = validateExtension(tempDir);
      expect(result.ok).toBe(false);
      expect(result.errors.join('\n')).toContain('does not match its registered checksum');
    } finally {
      fs.rmSync(tempDir, { recursive: true, force: true });
    }
  });
});
