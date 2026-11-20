# End-to-End Tests

Playwright specs that load the built extension unpacked from `dist/chrome-mv3` in Chromium (`tests/e2e/fixtures.ts`).

```bash
npm run test:e2e              # builds with `wxt build` (which also runs scripts/validate-extension.ts), then runs Playwright
npx playwright test tests/e2e/popup.spec.ts   # one spec; run `npm run build` first
npx playwright install chromium               # if the browser for this Playwright version is missing
```

- Pages are at the extension root: `chrome-extension://<id>/popup.html` and `options.html`.
- `bookwalker.spec.ts` and `context-menu.spec.ts` inject the generated content script (`content-scripts/content.js`, read from the built manifest) into fixture pages with a mocked `chrome` API.
- `content.spec.ts` bundles `extension/lib/overlay-dom.ts` into a plain script with Vite.
- `popup.spec.ts` expects no real backend on port 8001; a running backend that returns 401 fails the offline-state assertion.
