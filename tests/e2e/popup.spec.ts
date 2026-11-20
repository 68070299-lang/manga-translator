import type { Page } from '@playwright/test';
import type { ChromeApi } from '../../scripts/lib/extension.ts';
import { expect, test } from './fixtures.ts';

declare const chrome: ChromeApi;

async function gotoPopup(page: Page, extensionId: string): Promise<void> {
  await page.goto(`chrome-extension://${extensionId}/popup.html`);
}

async function resetExtensionStorage(page: Page, extensionId: string): Promise<void> {
  await gotoPopup(page, extensionId);
  await page.evaluate(async () => {
    await chrome.storage.sync.clear();
    await chrome.storage.local.clear();
    await chrome.storage.session.clear();
  });
}

async function openFreshPopup(page: Page, extensionId: string): Promise<void> {
  await resetExtensionStorage(page, extensionId);
  await gotoPopup(page, extensionId);
}

test('popup uses the Ichigo-style visual shell without compact text overflow', async ({
  page,
  extensionId,
}) => {
  await openFreshPopup(page, extensionId);

  await expect(page.locator('[data-testid="brand-mark"]')).toBeVisible();
  await expect(page.locator('.manga-strip')).toBeVisible();
  await expect(page.locator('.header-title')).toHaveText('Manga Translator');

  const colors = await page.evaluate(() => {
    const body = getComputedStyle(document.body);
    const title = getComputedStyle(document.querySelector('.header-title') as Element);
    return {
      bodyBackground: body.backgroundColor,
      titleColor: title.color,
    };
  });

  expect(colors).toEqual({
    bodyBackground: 'rgb(248, 232, 239)',
    titleColor: 'rgb(111, 45, 39)',
  });

  const overflowingText = await page
    .locator('.btn span, .checkbox-label span, .status-label')
    .evaluateAll((elements) =>
      elements
        .filter((element) => element.scrollWidth > element.clientWidth + 1)
        .map((element) => element.textContent.trim()),
    );
  expect(overflowingText).toEqual([]);
});
