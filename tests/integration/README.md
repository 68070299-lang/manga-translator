# Integration Tests

Vitest tests for cross-module contracts that are broader than a unit test but run without live browser pages or external provider calls (`fake-browser` plus stubbed `fetch`, see `tests/helpers/`).

- `service-worker-provider.test.ts` - service-worker routing, local/cloud provider selection, backend checks, cache behavior, and error handling.

Add new tests here when a behavior crosses extension modules but can still run against the fake browser. Run with `npx vitest run tests/integration`.
