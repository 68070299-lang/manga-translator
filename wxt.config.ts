import { defineConfig } from 'wxt';

// WXT builds the MV3 extension from extension/entrypoints with Vite:
// each entrypoint becomes one tree-shaken, minified bundle and the manifest
// is generated from this file plus the entrypoint definitions.
export default defineConfig({
  srcDir: 'extension',
  outDir: 'dist',
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
  vite: () => ({
    build: {
      target: 'chrome110',
      sourcemap: false,
    },
  }),
});
