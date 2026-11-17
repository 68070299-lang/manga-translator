const fs = require('fs');
const os = require('os');
const path = require('path');
const { packageExtension } = require('../../scripts/package');

function writeFile(root, relativePath, contents = '') {
  const target = path.join(root, relativePath);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, contents);
}

function createFixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'package-extension-'));
  const extensionDir = path.join(root, 'source');
  const distRoot = path.join(root, 'dist');
  writeFile(extensionDir, 'manifest.json', '{"manifest_version":3}');
  writeFile(extensionDir, 'runtime.js', 'self.runtime = true;');
  writeFile(extensionDir, 'development-notes.txt', 'do not package');
  return { root, extensionDir, distRoot };
}

function validInventory() {
  return {
    ok: true,
    errors: [],
    files: ['manifest.json', 'runtime.js']
  };
}

describe('packageExtension', () => {
  const fixtures = [];

  afterEach(() => {
    for (const fixture of fixtures.splice(0)) {
      fs.rmSync(fixture, { recursive: true, force: true });
    }
  });

  it('packages exactly the validated runtime inventory', () => {
    const fixture = createFixture();
    fixtures.push(fixture.root);

    const packageDir = packageExtension({
      extensionDir: fixture.extensionDir,
      distRoot: fixture.distRoot,
      validate: validInventory
    });

    expect(fs.existsSync(path.join(packageDir, 'manifest.json'))).toBe(true);
    expect(fs.existsSync(path.join(packageDir, 'runtime.js'))).toBe(true);
    expect(fs.existsSync(path.join(packageDir, 'development-notes.txt'))).toBe(false);
  });

  it('preserves the last good package when staged validation fails', () => {
    const fixture = createFixture();
    fixtures.push(fixture.root);
    writeFile(fixture.distRoot, 'extension/sentinel.txt', 'last-good');
    let validationCount = 0;

    expect(() => packageExtension({
      extensionDir: fixture.extensionDir,
      distRoot: fixture.distRoot,
      validate: () => (++validationCount === 1
        ? validInventory()
        : { ok: false, errors: ['staged package invalid'], files: [] })
    })).toThrow('Packaged extension validation failed');

    expect(fs.readFileSync(path.join(fixture.distRoot, 'extension/sentinel.txt'), 'utf8'))
      .toBe('last-good');
    expect(fs.existsSync(path.join(fixture.distRoot, '.extension-staging'))).toBe(false);
  });

  it('restores the last good package when final rename fails', () => {
    const fixture = createFixture();
    fixtures.push(fixture.root);
    writeFile(fixture.distRoot, 'extension/sentinel.txt', 'last-good');
    const fileSystem = Object.create(fs);
    fileSystem.renameSync = (source, target) => {
      if (path.basename(source) === '.extension-staging' && path.basename(target) === 'extension') {
        throw new Error('simulated rename failure');
      }
      return fs.renameSync(source, target);
    };

    expect(() => packageExtension({
      extensionDir: fixture.extensionDir,
      distRoot: fixture.distRoot,
      fileSystem,
      validate: validInventory
    })).toThrow('simulated rename failure');

    expect(fs.readFileSync(path.join(fixture.distRoot, 'extension/sentinel.txt'), 'utf8'))
      .toBe('last-good');
  });

  it('recovers a stale backup before replacing it', () => {
    const fixture = createFixture();
    fixtures.push(fixture.root);
    writeFile(fixture.distRoot, '.extension-backup/sentinel.txt', 'recoverable');

    const packageDir = packageExtension({
      extensionDir: fixture.extensionDir,
      distRoot: fixture.distRoot,
      validate: validInventory
    });

    expect(fs.existsSync(path.join(packageDir, 'runtime.js'))).toBe(true);
    expect(fs.existsSync(path.join(fixture.distRoot, '.extension-backup'))).toBe(false);
  });
});
