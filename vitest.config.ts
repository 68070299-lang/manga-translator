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
      reporter: ['text-summary', 'lcov', 'html'],
      // Coverage gate, enforced by `npm run test:coverage` in CI (the Jest
      // suite had one too). Set a few points under the coverage measured when
      // it was added, so a real drop fails while normal churn does not. Raise
      // these as tests improve; the content script is the weakest area.
      thresholds: {
        statements: 74,
        branches: 62,
        functions: 81,
        lines: 74,
        'extension/lib/**': { statements: 88, branches: 86, functions: 94, lines: 89 },
        'extension/background/**': { statements: 87, branches: 77, functions: 82, lines: 89 },
        'extension/ui/**': { statements: 92, branches: 90, functions: 94, lines: 92 },
        'extension/content/**': { statements: 56, branches: 46, functions: 59, lines: 59 },
      },
    },
  },
});
