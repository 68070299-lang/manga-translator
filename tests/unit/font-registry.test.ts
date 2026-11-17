import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  BUNDLED_FONTS,
  findBundledFont,
  getBundledFontById,
} from '../../extension/lib/font-registry';

const PUBLIC_DIR = join(__dirname, '..', '..', 'extension', 'public');

describe('bundled font registry', () => {
  it('finds fonts by stored setting value and by id', () => {
    const font = getBundledFontById('cc-wild-words');
    expect(font?.family).toBe('CC Wild Words');
    expect(findBundledFont("'CC Wild Words', sans-serif")).toBe(font);
    expect(findBundledFont('sans-serif')).toBeNull();
    expect(getBundledFontById('missing')).toBeNull();
  });

  it('pins every bundled font file to its registered checksum', () => {
    for (const font of BUNDLED_FONTS) {
      const bytes = readFileSync(join(PUBLIC_DIR, font.file));
      expect(createHash('sha256').update(bytes).digest('hex')).toBe(font.sha256);
    }
  });
});
