import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import {
  DIST_DIR,
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

describe('remote code', () => {
  const popup = { action: { default_popup: 'popup.html' } };

  it('rejects remote scripts in extension pages', () => {
    const result = validate({
      'manifest.json': manifestWith(popup),
      'popup.html': '<script src="https://cdn.example/lib.js"></script>',
    });

    expect(result.errors).toContain('popup.html loads a remote script: https://cdn.example/lib.js');
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
          extension_pages: "script-src 'self' https://cdn.example 'unsafe-eval'; object-src 'self'",
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

// CI builds before the unit tests, so there a missing build fails here instead of skipping.
describe.skipIf(!process.env.CI && !fs.existsSync(path.join(DIST_DIR, 'manifest.json')))(
  'current build output',
  () => {
    it('passes validation', () => {
      expect(validateExtension(DIST_DIR).errors).toEqual([]);
    });
  },
);
