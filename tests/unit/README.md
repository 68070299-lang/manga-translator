# Unit Tests

Vitest (`happy-dom`) tests for extension modules and the repo tooling. Run with `npm test` or `npx vitest run tests/unit/<file>.test.ts`.

- `tests/setup.ts` (via `vitest.config.ts`) installs `@webext-core/fake-browser` as the global `browser`/`chrome` and resets it between tests; shared stubs live in `tests/helpers/`.
- Extension logic: `api`, `cache`, `settings`, `site-activation`, `translation-queue`, `referrer-rules`, `region-capture`, `font-registry`, `utils`, content/overlay/popup/options tests.
- Build and repo tooling:
  - `manifest.test.ts` asserts the permission budget and DNR ruleset on the `wxt.config.ts` manifest, and on `dist/chrome-mv3/manifest.json` when a build exists.
  - `validate-extension.test.ts` covers `scripts/validate-extension.ts` (referenced files, font checksums, DNR rules, remote code, permission budget) with fixture builds.
  - `repository-check.test.ts` covers `scripts/repository-check.ts`.

Browser behavior is covered by the Playwright suite in `tests/e2e/` instead.
