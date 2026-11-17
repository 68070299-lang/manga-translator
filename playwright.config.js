// @ts-check
// @ts-ignore - Playwright types are resolved at runtime after npm install
const { defineConfig, devices } = require('@playwright/test');

/**
 * Playwright configuration for Chrome Extension E2E tests
 * @see https://playwright.dev/docs/test-configuration
 */
module.exports = defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false, // Extensions require sequential testing
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1, // Single worker for extension testing
  reporter: [
    ['html', { outputFolder: 'playwright-report' }],
    ['list']
  ],
  timeout: 60000,
  expect: {
    timeout: 10000
  },
  use: {
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure'
  },
  projects: [
    {
      name: 'chromium-extension',
      use: {
        ...devices['Desktop Chrome']
        // tests/e2e/fixtures.js owns browser launch and extension loading
      }
    }
  ],
  outputDir: 'test-results'
});
