import { describe, expect, it } from 'vitest';
import {
  checkRepository,
  findForbiddenPaths,
  findLegacyPaths,
  isForbiddenPath,
  isLegacyPath,
  normalizePath,
  packageScriptTargets,
  REQUIRED_PATHS,
} from '../../scripts/repository-check.ts';

describe('repository path classification', () => {
  it.each([
    ['website/demo.html', true],
    ['backend/models_cache/model.bin', true],
    ['artifacts/backend-1.out.log', true],
    ['extension/_metadata/generated_indexed_rulesets/_ruleset1', true],
    ['backend/src/manga_backend/__pycache__/main.pyc', true],
    ['.wxt/tsconfig.json', true],
    ['.output/chrome-mv3/manifest.json', true],
    ['dist/chrome-mv3/manifest.json', true],
    ['extension/entrypoints/content/index.ts', false],
    ['backend/src/manga_backend/models/detector.py', false],
    ['tests/unit/cache.test.ts', false],
  ])('classifies %s', (filePath, expected) => {
    expect(isForbiddenPath(filePath)).toBe(expected);
  });

  it('normalizes Windows paths before classification', () => {
    expect(normalizePath('.\\website\\demo.html')).toBe('website/demo.html');
    expect(isForbiddenPath('.\\website\\demo.html')).toBe(true);
  });

  it('deduplicates and sorts offenders', () => {
    expect(findForbiddenPaths(['website/z', 'extension/a', 'website/z', '.agents/a'])).toEqual([
      '.agents/a',
      'website/z',
    ]);
  });

  it('requires the experimental backend modules imported by the pipeline in the src layout', () => {
    expect(REQUIRED_PATHS).toEqual(
      expect.arrayContaining([
        'backend/src/manga_backend/models/animetext_yolo_detector.py',
        'backend/src/manga_backend/models/flux_klein_inpainter.py',
        'backend/src/manga_backend/models/flux_lanpaint_inpainter.py',
        'backend/src/manga_backend/comfy_lanpaint',
      ]),
    );
  });

  it('requires the WXT entrypoints and the shipped static assets', () => {
    expect(REQUIRED_PATHS).toEqual(
      expect.arrayContaining([
        'wxt.config.ts',
        'extension/entrypoints/background.ts',
        'extension/entrypoints/popup/index.html',
        'extension/public/fonts/CCWildWords.ttf',
        'extension/public/rules/bookwalker-cors.json',
        'scripts/validate-extension.ts',
      ]),
    );
  });

  it('flags legacy pre-WXT paths so they cannot come back', () => {
    expect(isLegacyPath('extension/manifest.json')).toBe(true);
    expect(isLegacyPath('extension/icons/icon16.png')).toBe(true);
    expect(isLegacyPath('backend/main.py')).toBe(true);
    expect(isLegacyPath('scripts/build.js')).toBe(true);
    expect(isLegacyPath('extension/public/icons/icon16.png')).toBe(false);
    expect(isLegacyPath('extension/assets/icons/icon16.svg')).toBe(false);
    expect(isLegacyPath('backend/src/manga_backend/main.py')).toBe(false);
    expect(
      findLegacyPaths([
        'extension/popup/popup.js',
        'extension/entrypoints/popup/main.ts',
        'extension/popup/popup.js',
      ]),
    ).toEqual(['extension/popup/popup.js']);
  });

  it('extracts repository paths from package scripts', () => {
    expect(
      packageScriptTargets({
        scripts: {
          validate: 'node scripts/validate-extension.ts',
          backend: 'uv run --project backend --locked pytest backend/tests',
          unrelated: 'wxt build',
          notAString: 3,
        },
      }),
    ).toEqual(['backend/tests', 'scripts/validate-extension.ts']);
  });

  // Becomes green once the legacy extension/ and flat backend/ files are removed
  // by the rewrite and the new files are staged.
  it('passes the current tracked runtime inventory', () => {
    expect(checkRepository()).toEqual([]);
  });
});
