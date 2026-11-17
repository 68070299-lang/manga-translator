import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vitest/config';
import { WxtVitest } from 'wxt/testing/vitest-plugin';

export default defineConfig({
  // Resolves WXT aliases/virtual modules and installs @webext-core/fake-browser
  // as the global `browser`/`chrome` so extension code runs under Node.
  // The Svelte plugin compiles the popup/options components for mount() tests.
  plugins: [WxtVitest(), svelte()],
  // Svelte's package exports pick the server build under Node; mount() needs the browser one.
  resolve: { conditions: ['browser'] },
  test: {
    environment: 'happy-dom',
    include: ['tests/unit/**/*.test.ts', 'tests/integration/**/*.test.ts'],
    setupFiles: ['tests/setup.ts'],
    restoreMocks: true,
    unstubGlobals: true,
    coverage: {
      provider: 'v8',
      include: ['extension/**/*.{ts,svelte}'],
      exclude: ['extension/public/**', '**/*.d.ts'],
      reporter: ['text', 'lcov', 'html'],
    },
  },
});
