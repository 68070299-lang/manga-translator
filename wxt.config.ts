import { cpSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { defineConfig } from 'wxt';
import { UNPACKED_EXTENSION_KEY } from './scripts/extension-key.ts';
import { assertValidExtension } from './scripts/validate-extension.ts';

/**
 * Replace the unpacked extension (dist/chrome-mv3, the folder loaded in
 * chrome://extensions) with a validated staging build, adding the fixed `key`
 * so the extension ID never depends on where the folder lives.
 */
function promoteUnpackedBuild(stagingDir: string, unpackedDir: string): void {
  rmSync(unpackedDir, { recursive: true, force: true });
  cpSync(stagingDir, unpackedDir, { recursive: true });
  const manifestPath = path.join(unpackedDir, 'manifest.json');
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8')) as Record<string, unknown>;
  writeFileSync(manifestPath, JSON.stringify({ ...manifest, key: UNPACKED_EXTENSION_KEY }));
}

// WXT builds the MV3 extension from extension/entrypoints with Vite:
// each entrypoint becomes one tree-shaken, minified bundle and the manifest
// is generated from this file plus the entrypoint definitions.
export default defineConfig({
  srcDir: 'extension',
  // WXT resolves publicDir against the project root, not srcDir.
  publicDir: 'extension/public',
  outDir: 'dist',
  // WXT empties its output folder before building. Building into a staging
  // folder keeps a failed or invalid build from replacing the extension Chrome
  // has loaded: only a validated build is promoted to dist/chrome-mv3 (see
  // build:done). `wxt zip` packs the staging folder, which has no `key`.
  outDirTemplate: '.build/{{browser}}-mv{{manifestVersion}}{{modeSuffix}}',
  modules: ['@wxt-dev/module-svelte'],
  // Explicit imports only: keeps bundles traceable and tree-shakeable.
  imports: false,
  manifestVersion: 3,
  manifest: {
    name: 'Manga Translator',
    description: 'Translate manga images in-browser using LLM-powered OCR and translation',
    minimum_chrome_version: '110',
    // Permission budget is asserted by tests/unit/manifest.test.ts; do not add
    // tabs, scripting, or activeTab.
    permissions: ['storage', 'unlimitedStorage', 'contextMenus', 'declarativeNetRequest'],
    host_permissions: ['<all_urls>'],
    declarative_net_request: {
      rule_resources: [
        {
          id: 'bookwalker_cors',
          enabled: true,
          path: 'rules/bookwalker-cors.json',
        },
      ],
    },
    icons: {
      16: 'icons/icon16.png',
      48: 'icons/icon48.png',
      128: 'icons/icon128.png',
    },
    action: {
      default_icon: {
        16: 'icons/icon16.png',
        48: 'icons/icon48.png',
        128: 'icons/icon128.png',
      },
    },
  },
  hooks: {
    // Validate the generated build (permission budget, referenced files, font
    // checksums, DNR rules, no remote code). Runs for `wxt build` and `wxt zip`
    // (which builds first), so a bad build never reaches a zip or Chrome: the
    // throw comes before promotion. Skipped in dev mode, where WXT injects extra
    // permissions and HMR code on purpose.
    'build:done': (wxt) => {
      if (wxt.config.command !== 'build') return;
      const { files } = assertValidExtension(wxt.config.outDir);
      wxt.logger.success(`Validated extension build (${files.length} referenced files)`);
      const unpackedDir = path.join(
        wxt.config.outBaseDir,
        `${wxt.config.browser}-mv${wxt.config.manifestVersion}`,
      );
      promoteUnpackedBuild(wxt.config.outDir, unpackedDir);
      wxt.logger.success(
        `Promoted the validated build to ${path.relative(wxt.config.root, unpackedDir)}`,
      );
    },
  },
  vite: () => ({
    build: {
      target: 'chrome110',
      sourcemap: false,
    },
  }),
});
