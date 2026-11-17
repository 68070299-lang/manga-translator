const fs = require('fs');
const os = require('os');
const path = require('path');
const { validateExtension } = require('../../scripts/validate-extension');

function writeFile(root, relativePath, contents = '') {
  const target = path.join(root, relativePath);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, contents);
}

function makeExtensionFixture(files) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'validate-extension-'));
  for (const [relativePath, contents] of Object.entries(files)) {
    writeFile(root, relativePath, contents);
  }
  return root;
}

describe('validateExtension', () => {
  const fixtures = [];

  afterEach(() => {
    for (const fixture of fixtures.splice(0)) {
      fs.rmSync(fixture, { recursive: true, force: true });
    }
  });

  it('recursively validates HTML script importScripts dependencies', () => {
    const root = makeExtensionFixture({
      'manifest.json': JSON.stringify({
        manifest_version: 3,
        name: 'Fixture',
        version: '1.0.0',
        action: { default_popup: 'popup/popup.html' }
      }),
      'popup/popup.html': '<script src="popup.js"></script>',
      'popup/popup.js': "importScripts('../lib/missing-helper.js');"
    });
    fixtures.push(root);

    const result = validateExtension(root);

    expect(result.ok).toBe(false);
    expect(result.errors).toContain('Missing extension file referenced by manifest or HTML: lib/missing-helper.js');
  });

  it('validates CSS url assets discovered from extension pages', () => {
    const root = makeExtensionFixture({
      'manifest.json': JSON.stringify({
        manifest_version: 3,
        name: 'Fixture',
        version: '1.0.0',
        options_page: 'options/options.html'
      }),
      'options/options.html': '<link rel="stylesheet" href="options.css">',
      'options/options.css': '.logo { background-image: url("../icons/missing.png"); }'
    });
    fixtures.push(root);

    const result = validateExtension(root);

    expect(result.ok).toBe(false);
    expect(result.errors).toContain('Missing extension file referenced by manifest or HTML: icons/missing.png');
  });

  it('validates static DNR rule resources referenced by the manifest', () => {
    const root = makeExtensionFixture({
      'manifest.json': JSON.stringify({
        manifest_version: 3,
        name: 'Fixture',
        version: '1.0.0',
        declarative_net_request: {
          rule_resources: [{
            id: 'bookwalker_cors',
            enabled: true,
            path: 'rules/missing.json'
          }]
        }
      })
    });
    fixtures.push(root);

    const result = validateExtension(root);

    expect(result.ok).toBe(false);
    expect(result.errors).toContain(
      'Missing extension file referenced by manifest or HTML: rules/missing.json'
    );
  });

  it('rejects manifest paths that escape the extension root', () => {
    const parent = makeExtensionFixture({
      'outside.js': 'console.log("outside")',
      'extension/manifest.json': JSON.stringify({
        manifest_version: 3,
        name: 'Fixture',
        version: '1.0.0',
        background: { service_worker: '../outside.js' }
      })
    });
    const root = path.join(parent, 'extension');
    fixtures.push(parent);

    const result = validateExtension(root);

    expect(result.ok).toBe(false);
    expect(result.errors).toContain(
      'manifest.json references asset outside extension/: ../outside.js'
    );
  });

  it('rejects remote URLs in manifest asset fields', () => {
    const root = makeExtensionFixture({
      'manifest.json': JSON.stringify({
        manifest_version: 3,
        name: 'Fixture',
        version: '1.0.0',
        background: { service_worker: 'https://example.test/worker.js' }
      })
    });
    fixtures.push(root);

    const result = validateExtension(root);

    expect(result.ok).toBe(false);
    expect(result.errors).toContain(
      'manifest.json must reference a local extension asset: https://example.test/worker.js'
    );
  });

  it('rejects referenced files that escape through a directory symlink', () => {
    const parent = makeExtensionFixture({
      'outside/worker.js': 'console.log("outside")',
      'extension/manifest.json': JSON.stringify({
        manifest_version: 3,
        name: 'Fixture',
        version: '1.0.0',
        background: { service_worker: 'linked/worker.js' }
      })
    });
    const root = path.join(parent, 'extension');
    fs.symlinkSync(
      path.join(parent, 'outside'),
      path.join(root, 'linked'),
      process.platform === 'win32' ? 'junction' : 'dir'
    );
    fixtures.push(parent);

    const result = validateExtension(root);

    expect(result.ok).toBe(false);
    expect(result.errors).toContain(
      'Referenced extension path escapes extension/ through symlink: linked/worker.js'
    );
  });
});
