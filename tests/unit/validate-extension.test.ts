import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import type { BundledFont } from '../../extension/lib/font-registry.ts';
import {
  DIST_DIR,
  PERMISSION_BUDGET,
  type ValidateOptions,
  validateExtension,
} from '../../scripts/validate-extension.ts';

type Files = Record<string, string>;

function writeFile(root: string, relativePath: string, contents = ''): void {
  const target = path.join(root, relativePath);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, contents);
}

function makeFixture(files: Files): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'validate-extension-'));
  for (const [relativePath, contents] of Object.entries(files)) {
    writeFile(root, relativePath, contents);
  }
  return root;
}

function manifestWith(extra: Record<string, unknown> = {}): string {
  return JSON.stringify({ manifest_version: 3, name: 'Fixture', version: '1.0.0', ...extra });
}

const NO_REQUIRED: ValidateOptions = { fonts: [], requiredRulesets: [] };

describe('validateExtension', () => {
  const fixtures: string[] = [];

  afterEach(() => {
    for (const fixture of fixtures.splice(0)) {
      fs.rmSync(fixture, { recursive: true, force: true });
    }
  });

  /** Fixture validation defaults to no required fonts or rulesets. */
  function validate(files: Files, options: ValidateOptions = {}) {
    const root = makeFixture(files);
    fixtures.push(root);
    return validateExtension(root, { ...NO_REQUIRED, ...options });
  }

  it('passes a WXT-shaped build with root-relative chunks and shared imports', () => {
    const result = validate({
      'manifest.json': manifestWith({
        permissions: ['storage'],
        action: { default_popup: 'popup.html', default_icon: { 16: 'icons/icon16.png' } },
        background: { service_worker: 'background.js' },
        content_scripts: [{ matches: ['<all_urls>'], js: ['content-scripts/content.js'] }],
      }),
      'popup.html':
        '<script type="module" crossorigin src="/chunks/popup-a.js"></script>' +
        '<link rel="modulepreload" crossorigin href="/chunks/shared-b.js">',
      'chunks/popup-a.js': 'import{a}from"./shared-b.js";a();',
      'chunks/shared-b.js': 'export const a=()=>{};',
      'background.js': 'console.log("worker")',
      'content-scripts/content.js': '(function(){})()',
      'icons/icon16.png': 'png',
    });

    expect(result.errors).toEqual([]);
    expect(result.ok).toBe(true);
    expect(result.files).toEqual(
      expect.arrayContaining([
        'popup.html',
        'chunks/popup-a.js',
        'chunks/shared-b.js',
        'background.js',
      ]),
    );
  });

  it('reports a missing manifest', () => {
    const result = validate({ 'unrelated.txt': 'x' });

    expect(result.ok).toBe(false);
    expect(result.errors[0]).toMatch(/^Missing manifest:/);
  });

  it('requires manifest_version 3, a name, and a version', () => {
    const result = validate({ 'manifest.json': JSON.stringify({ manifest_version: 2 }) });

    expect(result.errors).toEqual(
      expect.arrayContaining([
        'manifest.json must declare manifest_version 3',
        'manifest.json must declare name',
        'manifest.json must declare version',
      ]),
    );
  });

  it('recursively validates HTML script importScripts dependencies', () => {
    const result = validate({
      'manifest.json': manifestWith({ action: { default_popup: 'popup/popup.html' } }),
      'popup/popup.html': '<script src="popup.js"></script>',
      'popup/popup.js': "importScripts('../lib/missing-helper.js');",
    });

    expect(result.ok).toBe(false);
    expect(result.errors).toContain(
      'Missing extension file referenced by manifest or HTML: lib/missing-helper.js',
    );
  });

  it('validates missing chunks imported by a bundle', () => {
    const result = validate({
      'manifest.json': manifestWith({ action: { default_popup: 'popup.html' } }),
      'popup.html': '<script type="module" src="/chunks/popup.js"></script>',
      'chunks/popup.js': 'import"./gone-chunk.js";',
    });

    expect(result.errors).toContain(
      'Missing extension file referenced by manifest or HTML: chunks/gone-chunk.js',
    );
  });

  it('validates CSS url assets discovered from extension pages', () => {
    const result = validate({
      'manifest.json': manifestWith({ options_ui: { page: 'options/options.html' } }),
      'options/options.html': '<link rel="stylesheet" href="options.css">',
      'options/options.css': '.logo { background-image: url("../icons/missing.png"); }',
    });

    expect(result.ok).toBe(false);
    expect(result.errors).toContain(
      'Missing extension file referenced by manifest or HTML: icons/missing.png',
    );
  });

  it('validates static DNR rule resources referenced by the manifest', () => {
    const result = validate({
      'manifest.json': manifestWith({
        declarative_net_request: {
          rule_resources: [{ id: 'bookwalker_cors', enabled: true, path: 'rules/missing.json' }],
        },
      }),
    });

    expect(result.ok).toBe(false);
    expect(result.errors).toContain(
      'Missing extension file referenced by manifest or HTML: rules/missing.json',
    );
  });

  it('rejects manifest paths that escape the extension root', () => {
    const parent = makeFixture({
      'outside.js': 'console.log("outside")',
      'extension/manifest.json': manifestWith({ background: { service_worker: '../outside.js' } }),
    });
    fixtures.push(parent);

    const result = validateExtension(path.join(parent, 'extension'), NO_REQUIRED);

    expect(result.ok).toBe(false);
    expect(result.errors).toContain(
      'manifest.json references asset outside extension/: ../outside.js',
    );
  });

  it('rejects remote URLs in manifest asset fields', () => {
    const result = validate({
      'manifest.json': manifestWith({
        background: { service_worker: 'https://example.test/worker.js' },
      }),
    });

    expect(result.ok).toBe(false);
    expect(result.errors).toContain(
      'manifest.json must reference a local extension asset: https://example.test/worker.js',
    );
  });

  it('rejects referenced files that escape through a directory symlink', () => {
    const parent = makeFixture({
      'outside/worker.js': 'console.log("outside")',
      'extension/manifest.json': manifestWith({
        background: { service_worker: 'linked/worker.js' },
      }),
    });
    const root = path.join(parent, 'extension');
    fs.symlinkSync(
      path.join(parent, 'outside'),
      path.join(root, 'linked'),
      process.platform === 'win32' ? 'junction' : 'dir',
    );
    fixtures.push(parent);

    const result = validateExtension(root, NO_REQUIRED);

    expect(result.ok).toBe(false);
    expect(result.errors).toContain(
      'Referenced extension path escapes extension/ through symlink: linked/worker.js',
    );
  });

  describe('permission budget', () => {
    it.each(['tabs', 'scripting', 'activeTab'])('rejects the %s permission', (permission) => {
      const result = validate({
        'manifest.json': manifestWith({ permissions: ['storage', permission] }),
      });

      expect(result.ok).toBe(false);
      expect(result.errors).toContain(`Forbidden permission requested: ${permission}`);
    });

    it('rejects forbidden permissions requested as optional', () => {
      const result = validate({
        'manifest.json': manifestWith({ optional_permissions: ['tabs'] }),
      });

      expect(result.errors).toContain('Forbidden permission requested: tabs');
    });

    it('rejects permissions outside the budget', () => {
      const result = validate({ 'manifest.json': manifestWith({ permissions: ['history'] }) });

      expect(result.ok).toBe(false);
      expect(result.errors.join('\n')).toContain('Permission outside the budget');
    });

    it('accepts exactly the shipped budget', () => {
      const result = validate({
        'manifest.json': manifestWith({ permissions: [...PERMISSION_BUDGET] }),
      });

      expect(result.errors.join('\n')).not.toContain('Permission outside');
    });
  });

  describe('bundled fonts', () => {
    const fontBytes = 'font-bytes';
    const font: BundledFont = {
      id: 'probe',
      family: 'Probe',
      cssValue: "'Probe', sans-serif",
      file: 'fonts/Probe.ttf',
      sha256: createHash('sha256').update(fontBytes).digest('hex'),
    };

    it('passes when the font matches its registered checksum', () => {
      const result = validate(
        { 'manifest.json': manifestWith(), 'fonts/Probe.ttf': fontBytes },
        { fonts: [font] },
      );

      expect(result.errors).toEqual([]);
      expect(result.files).toContain('fonts/Probe.ttf');
    });

    it('fails on a checksum mismatch', () => {
      const result = validate(
        { 'manifest.json': manifestWith(), 'fonts/Probe.ttf': 'tampered' },
        { fonts: [font] },
      );

      expect(result.ok).toBe(false);
      expect(result.errors).toContain(
        'Bundled font does not match its registered checksum: fonts/Probe.ttf',
      );
    });

    it('fails when the font file is missing from the build', () => {
      const result = validate({ 'manifest.json': manifestWith() }, { fonts: [font] });

      expect(result.errors).toContain('Bundled font is missing from the build: fonts/Probe.ttf');
    });
  });

  describe('declarativeNetRequest rules', () => {
    const rules = {
      declarative_net_request: {
        rule_resources: [
          { id: 'bookwalker_cors', enabled: true, path: 'rules/bookwalker-cors.json' },
        ],
      },
    };

    it('requires the registered rulesets to be declared', () => {
      const result = validate(
        { 'manifest.json': manifestWith({ permissions: ['storage'] }) },
        { requiredRulesets: ['bookwalker_cors'] },
      );

      expect(result.errors).toContain('manifest.json is missing the DNR ruleset: bookwalker_cors');
    });

    it('requires rule resources when declarativeNetRequest is requested', () => {
      const result = validate({
        'manifest.json': manifestWith({ permissions: ['declarativeNetRequest'] }),
      });

      expect(result.errors).toContain(
        'manifest.json requests declarativeNetRequest but declares no rule_resources',
      );
    });

    it('accepts a present ruleset file containing a JSON array', () => {
      const result = validate(
        { 'manifest.json': manifestWith(rules), 'rules/bookwalker-cors.json': '[]' },
        { requiredRulesets: ['bookwalker_cors'] },
      );

      expect(result.errors).toEqual([]);
    });

    it('rejects a ruleset file that is not a JSON array', () => {
      const result = validate({
        'manifest.json': manifestWith(rules),
        'rules/bookwalker-cors.json': '{"not":"an array"}',
      });

      expect(result.errors).toContain(
        'DNR ruleset rules/bookwalker-cors.json must be a JSON array',
      );
    });
  });

  describe('remote code', () => {
    const popup = { action: { default_popup: 'popup.html' } };

    it('rejects remote scripts in extension pages', () => {
      const result = validate({
        'manifest.json': manifestWith(popup),
        'popup.html': '<script src="https://cdn.example/lib.js"></script>',
      });

      expect(result.errors).toContain(
        'popup.html loads a remote script: https://cdn.example/lib.js',
      );
    });

    it('rejects protocol-relative remote scripts', () => {
      const result = validate({
        'manifest.json': manifestWith(popup),
        'popup.html': '<script src="//cdn.example/lib.js"></script>',
      });

      expect(result.errors).toContain('popup.html loads a remote script: //cdn.example/lib.js');
    });

    it('rejects inline scripts and inline event handlers', () => {
      const result = validate({
        'manifest.json': manifestWith(popup),
        'popup.html': '<script>alert(1)</script><button onclick="go()">x</button>',
      });

      expect(result.errors).toEqual(
        expect.arrayContaining([
          'popup.html contains an inline script (blocked by the MV3 CSP)',
          'popup.html contains an inline event handler (blocked by the MV3 CSP)',
        ]),
      );
    });

    it('rejects remote importScripts and dynamic or static imports in bundles', () => {
      const result = validate({
        'manifest.json': manifestWith({ background: { service_worker: 'background.js' } }),
        'background.js':
          'importScripts("https://cdn.example/a.js");import("https://cdn.example/b.js");',
        'chunks/other.js': 'import{x}from"https://cdn.example/c.js";',
      });

      expect(result.errors).toEqual(
        expect.arrayContaining([
          'background.js calls importScripts with a remote URL',
          'background.js dynamically imports a remote module',
          'chunks/other.js statically imports a remote module',
        ]),
      );
    });

    it('does not flag ordinary URL strings in bundled code', () => {
      const result = validate({
        'manifest.json': manifestWith({ background: { service_worker: 'background.js' } }),
        'background.js': 'const api="https://openrouter.ai/api/v1";fetch(api);',
      });

      expect(result.errors).toEqual([]);
    });

    it('rejects remote and unsafe sources in the extension CSP', () => {
      const result = validate({
        'manifest.json': manifestWith({
          content_security_policy: {
            extension_pages:
              "script-src 'self' https://cdn.example 'unsafe-eval'; object-src 'self'",
          },
        }),
      });

      expect(result.errors).toEqual(
        expect.arrayContaining([
          'manifest.json CSP allows non-local code in script-src: https://cdn.example',
          "manifest.json CSP allows non-local code in script-src: 'unsafe-eval'",
        ]),
      );
    });
  });

  describe.skipIf(!fs.existsSync(path.join(DIST_DIR, 'manifest.json')))(
    'current build output',
    () => {
      it('passes validation', () => {
        expect(validateExtension(DIST_DIR).errors).toEqual([]);
      });
    },
  );
});
