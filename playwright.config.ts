import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright configuration for Chrome extension E2E tests. The specs load the
 * unpacked extension built by `wxt build` from dist/chrome-mv3
 * (`npm run test:e2e` builds first).
 * @see https://playwright.dev/docs/test-configuration
 */
export default defineConfig({
  testDir: './tests/e2e',
  testMatch: '**/*.spec.ts',
  fullyParallel: false, // Extensions require sequential testing
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1, // Single worker for extension testing
  reporter: [['html', { outputFolder: 'playwright-report' }], ['list']],
  timeout: 60000,
  expect: {
    timeout: 10000,
  },
  use: {
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium-extension',
      use: {
        ...devices['Desktop Chrome'],
        // tests/e2e/fixtures.ts owns browser launch and extension loading
      },
    },
  ],
  outputDir: 'test-results',
});
