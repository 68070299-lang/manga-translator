# Integration Tests

Vitest tests for cross-module contracts that are broader than a unit test but run without live browser pages or external provider calls (`fake-browser` plus stubbed `fetch`, see `tests/helpers/`).

Add new tests here when a behavior crosses extension modules but can still run against the fake browser. Run with `npx vitest run tests/integration`.
