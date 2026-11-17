import type { BrowserContext, Worker } from '@playwright/test';
import type { ChromeApi } from '../../scripts/lib/extension.ts';
import { expect, test } from './fixtures.ts';

declare const chrome: ChromeApi;

async function getExtensionWorker(context: BrowserContext): Promise<Worker> {
  return context.serviceWorkers()[0] || context.waitForEvent('serviceworker');
}

test('BookWalker CORS rule matches only viewer GET XHRs to the exact CDN', async ({ context }) => {
  const worker = await getExtensionWorker(context);
  const results = await worker.evaluate(async () => {
    type MatchDetails = Parameters<typeof chrome.declarativeNetRequest.testMatchOutcome>[0];
    const testMatch = (details: MatchDetails) =>
      chrome.declarativeNetRequest.testMatchOutcome(details);
    const base = {
      initiator: 'https://viewer.bookwalker.jp',
      method: 'get',
      type: 'xmlhttprequest',
    } as const;

    return {
      matching: await testMatch({
        ...base,
        url: 'https://bw-bv-epubs.bookwalker.jp/c_product/book/page.jpeg?Policy=signed',
      }),
      wrongInitiator: await testMatch({
        ...base,
        initiator: 'https://example.com',
        url: 'https://bw-bv-epubs.bookwalker.jp/c_product/book/page.jpeg',
      }),
      lookalikeCdn: await testMatch({
        ...base,
        url: 'https://bw-bv-epubs.bookwalker.jp.evil.example/page.jpeg',
      }),
      postRequest: await testMatch({
        ...base,
        method: 'post',
        url: 'https://bw-bv-epubs.bookwalker.jp/c_product/book/page.jpeg',
      }),
      imageRequest: await testMatch({
        ...base,
        type: 'image',
        url: 'https://bw-bv-epubs.bookwalker.jp/c_product/book/page.jpeg',
      }),
    };
  });

  expect(results.matching.matchedRules).toEqual(
    expect.arrayContaining([expect.objectContaining({ ruleId: 2001 })]),
  );
  for (const result of [
    results.wrongInitiator,
    results.lookalikeCdn,
    results.postRequest,
    results.imageRequest,
  ]) {
    expect(result.matchedRules).not.toEqual(
      expect.arrayContaining([expect.objectContaining({ ruleId: 2001 })]),
    );
  }
});
