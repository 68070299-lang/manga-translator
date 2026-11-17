const { test, expect } = require('./fixtures');

test('extension popup loads', async ({ page, extensionId }) => {
  const popupUrl = `chrome-extension://${extensionId}/popup/popup.html`;

  await page.goto(popupUrl);
  await expect(page.locator('text=Manga Translator')).toBeVisible();
});
