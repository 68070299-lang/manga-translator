import fs from 'node:fs';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { requiredEnv, resolveNativeChrome } from '../../scripts/prepare-bookwalker-profile.ts';

describe('native BookWalker GPU UAT wiring', () => {
  const scriptPath = path.join(
    import.meta.dirname,
    '..',
    '..',
    'scripts',
    'bookwalker-real-uat.ps1',
  );

  afterEach(() => {
    delete process.env.BOOKWALKER_TEST_REQUIRED;
    delete process.env.BOOKWALKER_CHROME_PATH;
  });

  it('requires missing bootstrap environment without exposing a value', () => {
    expect(() => requiredEnv('BOOKWALKER_TEST_REQUIRED')).toThrow(
      'BOOKWALKER_TEST_REQUIRED is required',
    );
  });

  it('uses exact product-first and viewer contracts with five pages', () => {
    const source = fs.readFileSync(scriptPath, 'utf8');

    expect(source).toContain('https://bookwalker.jp/deace0472d-fc15-43da-942f-f993e0253047/');
    expect(source).toContain(
      'https://viewer.bookwalker.jp/03/30/viewer.html?cid=ace0472d-fc15-43da-942f-f993e0253047&cty=1',
    );
    expect(source).toContain('[ValidateRange(5, 20)][int]$Pages = 5');
    expect(source).toContain('$env:BOOKWALKER_PRODUCT_URL = $ProductUrl');
    expect(source).toContain('browserRestartCacheHit = $true');
    expect(source).toContain('backendRecoveryPassed = $true');
  });

  it('builds the extension and loads it from dist/chrome-mv3 through the TypeScript helper', () => {
    const source = fs.readFileSync(scriptPath, 'utf8');

    expect(source).toContain('dist\\chrome-mv3');
    expect(source).toContain('npm run build');
    expect(source).toContain('prepare-bookwalker-profile.ts');
    expect(source).toContain('manga_backend.main:app');
    expect(source).not.toContain('prepare-bookwalker-profile.js');
  });

  it('accepts an explicit native Chrome executable', () => {
    process.env.BOOKWALKER_CHROME_PATH = process.execPath;
    expect(resolveNativeChrome()).toBe(path.resolve(process.execPath));
  });
});
