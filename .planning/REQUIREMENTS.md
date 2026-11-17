# Requirements: Manga Translator Extension

**Defined:** 2026-08-06
**Core Value:** The owner can translate a manga page quickly and repeatedly without an account or hosted service dependency.

## v1 Requirements

### Runtime Reliability

- [x] **RUNTIME-01**: Identical image/settings requests from multiple tabs share one in-flight provider job.
- [x] **RUNTIME-02**: Local jobs execute serially, cloud jobs execute with a maximum concurrency of three, and failed jobs can be retried.
- [x] **RUNTIME-03**: Queue deduplication does not merge force-refresh requests or requests with different cache keys.

### Site Activation

- [x] **SITE-01**: Translation is enabled by default for a hostname and can be explicitly disabled or re-enabled without deleting completed translations.
- [x] **SITE-02**: The popup and toolbar state show whether translation is enabled for the active hostname.
- [x] **SITE-03**: Disabled sites do not start the expensive content observers, automatic translation, or reader controls.

### Image Fetching

- [x] **FETCH-01**: Known hotlink-protected CDN hosts use a fixed referrer mapping through a transient DNR session rule when available.
- [x] **FETCH-02**: DNR session rules are removed after success, failure, timeout, or thrown exception, and unknown hosts receive no spoofed referrer.

### Verification

- [x] **TEST-01**: Focused unit/integration tests cover queue behavior, site state, action state, and DNR cleanup while the existing suites remain green.

## v1.1 Hardening Requirements

### MV3 Lifecycle

- [x] **HARDEN-01**: Transient referrer rules use fixed per-CDN IDs, reconcile owned stale rules after worker startup, and preserve unrelated session rules.
- [x] **HARDEN-02**: Referrer rules are domain-anchored and rule-installation failures fall back to an ordinary image fetch.
- [x] **HARDEN-03**: Disabling a hostname disposes expensive observers, timers, controls, and BookWalker intervals; re-enabling creates one fresh runtime without clearing completed translations.
- [x] **HARDEN-04**: Toolbar state refreshes on worker startup and extension installation, and storage-read failures do not re-enable an explicitly disabled site.
- [x] **HARDEN-05**: Queued jobs recheck the persistent cache and content retries at most once for transport-level worker failures while preserving force-refresh semantics.

## v2 Requirements

### Reader Features

- [x] **READER-01**: Translate an arbitrary calibrated visible-tab screenshot region.
- [x] **READER-02**: Render all overlays inside isolated Shadow DOM roots (one per overlay, so overlays stay in the page containers they cover).
- [x] **READER-03**: Register and bundle a verified runtime manga font set (CC Wild Words).

## Out of Scope

| Feature | Reason |
|---------|--------|
| Login, subscription tiers, model catalog, cookies, account state machine | Personal-use extension; no account product is needed |
| Signed CRX integrity files | Chrome/distribution packaging owns extension signing and verification |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| RUNTIME-01 | Phase 1 | Complete |
| RUNTIME-02 | Phase 1 | Complete |
| RUNTIME-03 | Phase 1 | Complete |
| SITE-01 | Phase 1 | Complete |
| SITE-02 | Phase 1 | Complete |
| SITE-03 | Phase 1 | Complete |
| FETCH-01 | Phase 1 | Complete |
| FETCH-02 | Phase 1 | Complete |
| TEST-01 | Phase 1 | Complete |
| HARDEN-01 | Phase 2 | Complete |
| HARDEN-02 | Phase 2 | Complete |
| HARDEN-03 | Phase 2 | Complete |
| HARDEN-04 | Phase 2 | Complete |
| HARDEN-05 | Phase 2 | Complete |
| READER-01 | Phase 3 | Complete |
| READER-02 | Phase 3 | Complete |
| READER-03 | Phase 3 | Complete |

**Coverage:**
- v1 requirements: 9 total
- Mapped to phases: 9
- Unmapped: 0
- v1.1 hardening requirements: 5 total
- Mapped to phases: 5
- Unmapped: 0

---
*Requirements defined: 2026-08-06*
*Last updated: 2026-08-06 after implementation and verification*
