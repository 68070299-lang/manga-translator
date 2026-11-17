# Integration Tests

Covers cross-module contracts that are broader than a unit test but still run without live browser pages or external provider calls.

Current coverage:

- service-worker-provider.test.js - service-worker routing, local/cloud provider selection, backend checks, cache behavior, and error handling.

Historical provider-specific tests were removed with the repository-boundary cleanup because they depended on stale scaffolding. Add new integration tests here when a behavior crosses extension modules but can still be exercised with Jest mocks.
