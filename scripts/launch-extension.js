const { chromium } = require('@playwright/test');
const path = require('path');

(async () => {
  const extensionPath = path.resolve(__dirname, '..', 'extension');

  console.log('Launching Chrome with extension...');

  const context = await chromium.launchPersistentContext('', {
    headless: false,
    args: [
      `--disable-extensions-except=${extensionPath}`,
      `--load-extension=${extensionPath}`,
      '--no-first-run'
    ]
  });

  const page = await context.newPage();
  await page.goto('https://example.com');

  console.log('Chrome is open with the extension loaded.');
  console.log('Navigate to a page with manga/images to test the extension.');
  console.log('Press Ctrl+C to close.');

  // Keep the process running
  await new Promise(() => {});
})();
