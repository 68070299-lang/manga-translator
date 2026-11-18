/**
 * Manual popup smoke test against the built extension (`npm run smoke:popup`).
 * Usage: node scripts/popup-smoke.ts [url] [--headless] [--keep-open]
 */
import fs from 'node:fs';
import path from 'node:path';
import { chromium, type Page } from '@playwright/test';
import {
  assertExtensionBuilt,
  extensionIdFromWorker,
  extensionLaunchArgs,
  getExtensionWorker,
  REPO_ROOT,
} from './lib/extension.ts';

const outputDir = path.join(REPO_ROOT, 'test-results', 'manual-popup');
const targetUrl = process.argv.find((arg) => /^https?:\/\//i.test(arg)) ?? 'https://example.com';
const headless = process.argv.includes('--headless');
const keepOpen = process.argv.includes('--keep-open');

async function readPopupState(page: Page) {
  return {
    title: await page.title(),
    header: await page.locator('h1').textContent(),
    visible: {
      modeCloud: await page.locator('#mode-cloud').isVisible(),
      modeLocal: await page.locator('#mode-local').isVisible(),
      targetLang: await page.locator('#target-lang').isVisible(),
      translateBtn: await page.locator('#translate-btn').isVisible(),
      clearCacheBtn: await page.locator('#clear-cache-btn').isVisible(),
      fontSize: await page.locator('#font-size').isVisible(),
      fontFamily: await page.locator('#font-family').isVisible(),
      autoTranslate: await page.locator('#auto-translate').isVisible(),
      showProgress: await page.locator('#show-progress').isVisible(),
      settingsBtn: await page.locator('#settings-btn').isVisible(),
    },
  };
}

function waitForShutdownSignal(): Promise<void> {
  return new Promise((resolve) => {
    const finish = () => {
      process.removeListener('SIGINT', finish);
      process.removeListener('SIGTERM', finish);
      resolve();
    };
    process.once('SIGINT', finish);
    process.once('SIGTERM', finish);
  });
}

function assertState(condition: boolean, message: string): void {
  if (!condition) {
    throw new Error(`Popup smoke assertion failed: ${message}`);
  }
}

async function main(): Promise<void> {
  const extensionPath = assertExtensionBuilt();
  fs.mkdirSync(outputDir, { recursive: true });

  const context = await chromium.launchPersistentContext('', {
    channel: process.env.PLAYWRIGHT_CHANNEL ?? 'chromium',
    headless,
    args: [...extensionLaunchArgs(extensionPath), '--no-first-run'],
  });

  try {
    const worker = await getExtensionWorker(context);
    const extensionId = extensionIdFromWorker(worker);
    console.log('Extension ID:', extensionId);

    const tab = await context.newPage();
    await tab.goto(targetUrl, { waitUntil: 'domcontentloaded' });
    console.log('Opened tab:', tab.url());

    const popup = await context.newPage();
    await popup.goto(`chrome-extension://${extensionId}/popup.html`);
    await popup.waitForLoadState('domcontentloaded');
    await popup.waitForFunction(
      () => {
        const localMode = document.getElementById('mode-local') as HTMLInputElement | null;
        const indicator = document.getElementById('connection-indicator');
        return (
          document.readyState === 'complete' &&
          localMode?.checked === true &&
          indicator !== null &&
          getComputedStyle(indicator).display !== 'none'
        );
      },
      null,
      { timeout: 10000 },
    );

    const initialState = await readPopupState(popup);
    console.log('Popup state:', JSON.stringify(initialState, null, 2));
    assertState(initialState.title === 'Manga Translator', 'unexpected popup title');
    assertState(Boolean(initialState.header?.trim()), 'missing popup heading');
    const hiddenControls = Object.entries(initialState.visible)
      .filter(([, visible]) => !visible)
      .map(([name]) => name);
    assertState(hiddenControls.length === 0, `hidden controls: ${hiddenControls.join(', ')}`);

    const localChecked = await popup.locator('#mode-local').isChecked();
    console.log('Local mode checked by default:', localChecked);
    assertState(localChecked, 'local mode should be selected by default');

    const connectionIndicator = popup.locator('#connection-indicator');
    const initialConnectionVisible = await connectionIndicator.isVisible();
    console.log('Connection indicator visible:', initialConnectionVisible);
    assertState(
      initialConnectionVisible,
      'connection indicator should be visible in default local mode',
    );

    await popup.locator('#mode-cloud').check();
    const cloudConnectionVisible = await connectionIndicator.isVisible();
    console.log('Connection indicator after cloud:', cloudConnectionVisible);
    assertState(!cloudConnectionVisible, 'connection indicator should be hidden in cloud mode');

    await popup.locator('#mode-local').check();
    const localConnectionVisible = await connectionIndicator.isVisible();
    console.log('Connection indicator after local:', localConnectionVisible);
    assertState(localConnectionVisible, 'connection indicator should be visible in local mode');

    await popup.locator('#target-lang').selectOption('Spanish');
    const selectedLanguage = await popup.locator('#target-lang').inputValue();
    const fontSizeValue = await popup.locator('#font-size-value').textContent();
    console.log('Selected language:', selectedLanguage);
    console.log('Font size value:', fontSizeValue);
    assertState(selectedLanguage === 'Spanish', 'target language did not update');
    assertState(Boolean(fontSizeValue?.trim()), 'font size display is empty');

    const screenshotPath = path.join(outputDir, 'popup.png');
    await popup.screenshot({ path: screenshotPath, fullPage: true });
    console.log('Screenshot saved to:', screenshotPath);

    if (keepOpen) {
      console.log('Keeping browser open. Press Ctrl+C in this terminal to close it.');
      await waitForShutdownSignal();
    }
  } finally {
    await context.close();
  }
}

main().catch((error: unknown) => {
  console.error('Popup smoke test failed:', error instanceof Error ? error.message : error);
  process.exit(1);
});
