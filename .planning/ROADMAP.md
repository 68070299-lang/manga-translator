# Roadmap: Manga Translator Extension

## Overview

This milestone improves the existing local-first extension with the Ichigo
ideas that directly reduce repeated work: shared runtime scheduling, explicit
hostname activation, visible toolbar state, and narrowly scoped image-fetch
fallbacks. It intentionally avoids account and hosted-service behavior.

## Phases

- [x] **Phase 1: Reliable Personal Translation Runtime** - Deduplicate work, control site activation, and make protected image fetching dependable.
- [x] **Phase 2: Runtime Lifecycle Hardening** - Make the Phase 1 runtime safe across worker restarts, site toggles, and transport failures.
- [x] **Phase 3: Reader Surface Features** - Add region translation, isolated overlays, and a bundled manga font.

## Phase Details

### Phase 1: Reliable Personal Translation Runtime
**Goal**: Make repeated personal translation sessions predictable across tabs and sites without changing the mature rendering pipeline.
**Depends on**: Nothing (first phase)
**Requirements**: [RUNTIME-01, RUNTIME-02, RUNTIME-03, SITE-01, SITE-02, SITE-03, FETCH-01, FETCH-02, TEST-01]
**Success Criteria** (what must be TRUE):
  1. Duplicate tab requests share one provider call while local work remains serial and cloud work is capped at three jobs.
  2. The owner can toggle translation for the active hostname, and disabled sites stay inert until re-enabled.
  3. Protected CDN fetches use only fixed transient referrer rules and always clean them up.
  4. Existing repository, extension, frontend, integration, and backend checks remain green.
**Plans**: 3 plans in 2 waves (complete)

Plans:
- [x] 01-01: Add the service-worker translation queue and cross-tab in-flight deduplication.
- [x] 01-02: Add hostname activation storage, popup controls, and action state updates. (Wave 2; depends on 01-01)
- [x] 01-03: Add allowlisted DNR referrer fallback and focused verification. (Wave 2; depends on 01-01)

## Progress

**Execution Order:**
Phases execute in numeric order: 1, 2, 3

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Reliable Personal Translation Runtime | 3/3 | Complete | 2026-08-06 |
| 2. Runtime Lifecycle Hardening | 1/1 | Complete | 2026-08-06 |
| 3. Reader Surface Features | Unplanned, built directly | Complete | 2026-11-02 |

### Phase 2: Runtime Lifecycle Hardening
**Goal**: Preserve the reliability improvements when MV3 workers restart, users disable and re-enable a hostname, or a message channel disappears.
**Depends on**: Phase 1
**Requirements**: [HARDEN-01, HARDEN-02, HARDEN-03, HARDEN-04, HARDEN-05]
**Success Criteria** (what must be TRUE):
  1. Owned transient DNR rules reconcile safely and unrelated rules are never removed or replaced.
  2. Disabling a hostname disposes runtime observers and controls, while re-enabling creates one fresh runtime.
  3. Existing tabs receive toolbar state on worker startup and extension installation.
  4. Queued jobs recheck cache state and content retries only transport-level worker failures, not force-refresh or backend failures.
  5. Focused tests cover lifecycle, DNR ownership/collision, cache races, and transport recovery.
**Plans**: 1 plan (complete)

Plans:
- [x] 02-01: Harden DNR lifecycle, site runtime disposal, toolbar startup refresh, and MV3 transport recovery.

### Phase 3: Reader Surface Features
**Goal**: Let the owner translate any visible area, keep overlays safe from page CSS, and ship a manga font.
**Depends on**: Phase 2
**Requirements**: [READER-01, READER-02, READER-03]
**Success Criteria** (what must be TRUE):
  1. A dragged region is cropped at the screenshot's real scale, translated, and removed by Clear; Esc sends nothing.
  2. Hostile page CSS cannot restyle overlay content or reader controls.
  3. CC Wild Words loads at runtime from checksum-verified bytes without becoming web-accessible.
