const { test, expect } = require('./fixtures');

async function getExtensionWorker(context) {
  return context.serviceWorkers()[0] || context.waitForEvent('serviceworker');
}

test('BookWalker CORS rule matches only viewer GET XHRs to the exact CDN', async ({ context }) => {
  const worker = await getExtensionWorker(context);
  const results = await worker.evaluate(async () => {
    const testMatch = details => chrome.declarativeNetRequest.testMatchOutcome(details);
    const base = {
      initiator: 'https://viewer.bookwalker.jp',
      method: 'get',
      type: 'xmlhttprequest'
    };

    return {
      matching: await testMatch({
        ...base,
        url: 'https://bw-bv-epubs.bookwalker.jp/c_product/book/page.jpeg?Policy=signed'
      }),
      wrongInitiator: await testMatch({
        ...base,
        initiator: 'https://example.com',
        url: 'https://bw-bv-epubs.bookwalker.jp/c_product/book/page.jpeg'
      }),
      lookalikeCdn: await testMatch({
        ...base,
        url: 'https://bw-bv-epubs.bookwalker.jp.evil.example/page.jpeg'
      }),
      postRequest: await testMatch({
        ...base,
        method: 'post',
        url: 'https://bw-bv-epubs.bookwalker.jp/c_product/book/page.jpeg'
      }),
      imageRequest: await testMatch({
        ...base,
        type: 'image',
        url: 'https://bw-bv-epubs.bookwalker.jp/c_product/book/page.jpeg'
      })
    };
  });

  expect(results.matching.matchedRules).toEqual(
    expect.arrayContaining([expect.objectContaining({ ruleId: 2001 })])
  );
  for (const result of [
    results.wrongInitiator,
    results.lookalikeCdn,
    results.postRequest,
    results.imageRequest
  ]) {
    expect(result.matchedRules).not.toEqual(
      expect.arrayContaining([expect.objectContaining({ ruleId: 2001 })])
    );
  }
});
