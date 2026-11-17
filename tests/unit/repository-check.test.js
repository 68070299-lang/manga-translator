const {
  findForbiddenPaths,
  findUnreferencedExtensionFiles,
  REQUIRED_PATHS,
  isForbiddenPath,
  normalizePath,
  checkRepository
} = require('../../scripts/repository-check');

describe('repository path classification', () => {
  it.each([
    ['website/demo.html', true],
    ['backend/models_cache/model.bin', true],
    ['artifacts/backend-1.out.log', true],
    ['extension/_metadata/generated_indexed_rulesets/_ruleset1', true],
    ['backend/__pycache__/main.pyc', true],
    ['extension/content/content.js', false],
    ['backend/models/detector.py', false],
    ['tests/unit/cache.test.js', false]
  ])('classifies %s', (filePath, expected) => {
    expect(isForbiddenPath(filePath)).toBe(expected);
  });

  it('normalizes Windows paths before classification', () => {
    expect(normalizePath('.\\website\\demo.html')).toBe('website/demo.html');
    expect(isForbiddenPath('.\\website\\demo.html')).toBe(true);
  });

  it('deduplicates and sorts offenders', () => {
    expect(findForbiddenPaths(['website/z', 'extension/a', 'website/z', '.agents/a']))
      .toEqual(['.agents/a', 'website/z']);
  });

  it('requires the experimental backend modules imported by the pipeline', () => {
    expect(REQUIRED_PATHS).toEqual(expect.arrayContaining([
      'backend/models/animetext_yolo_detector.py',
      'backend/models/flux_klein_inpainter.py',
      'backend/models/flux_lanpaint_inpainter.py',
      'backend/comfy_lanpaint'
    ]));
  });

  it('flags git-visible extension files outside the manifest inventory and allowlist', () => {
    const referenced = ['background/service-worker.js', 'popup/popup.html'];
    expect(findUnreferencedExtensionFiles([
      'extension/manifest.json',
      'extension/background/service-worker.js',
      'extension/popup/popup.html',
      'extension/icons/icon16.svg',
      'extension/icons/generate-icons.html',
      'extension/rules/bookwalker-cors.json',
      'extension/scratch/notes.txt',
      'extension/icons/old.png',
      'extension/scratch/notes.txt',
      'backend/main.py'
    ], referenced)).toEqual(['extension/icons/old.png', 'extension/scratch/notes.txt']);
  });

  it('passes the current tracked runtime inventory', () => {
    expect(checkRepository()).toEqual([]);
  });
});
