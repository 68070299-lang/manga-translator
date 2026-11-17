import { defineConfig } from 'vitest/config';
import { WxtVitest } from 'wxt/testing/vitest-plugin';

export default defineConfig({
  // Resolves WXT aliases/virtual modules and installs @webext-core/fake-browser
  // as the global `browser`/`chrome` so extension code runs under Node.
  plugins: [WxtVitest()],
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
