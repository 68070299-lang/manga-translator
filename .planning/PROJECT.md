# Manga Translator Extension

## What This Is

This is a personal-use Chrome MV3 extension for translating manga and webtoon
images in place. It discovers regular images, canvases, backgrounds, and
reader composites, then routes them through a local FastAPI/ONNX pipeline or an
optional cloud provider.

## Core Value

The owner can translate a manga page quickly and repeatedly without an account
or hosted service dependency.

## Requirements

### Validated

- MV3 extension with popup/options controls and context-menu workflows.
- Local loopback backend with detector, OCR, inpainting, translation, and
  rendering settings.
- Reader-aware discovery for lazy images, backgrounds, canvases, and composite
  pages.
- Persistent translation cache, payload limits, backend auth token support, and
  automated unit/integration/backend verification.
- Cross-tab in-flight deduplication with local serial execution and cloud
  concurrency capped at three jobs.
- Per-hostname activation controls with popup and toolbar state, plus inert
  disabled sites.
- Narrow, transient referrer handling for known hotlink-protected image CDNs.
- Restart-safe DNR ownership and domain anchoring, disposable site runtime
  scopes, startup toolbar refresh, and transport-level recovery.

### Active

- No active requirements for this milestone; Phases 1 and 2 are complete.

### Experimental / optional

Work on the `experiment/animetext-yolo` branch that has no requirement IDs or
phase; documented in `docs/RUNTIME_CONTRACT.md` and `backend/README.md`.

- AnimeText YOLO detector (`detector=animetext-yolo`): opt-in; the Hugging Face
  model repo is gated, so authenticate or point `ANIMETEXT_YOLO_MODEL` at a
  local ONNX file.
- FLUX.2 Klein inpainters: `flux2_klein_lanpaint` (default, needs the loopback
  ComfyUI daemon from `docs/headless-lanpaint.md`) and `flux2_klein_q4`
  (optional, needs `sd-cli`). LaMa and None remain available.
- GPU ONNX runtime and batched manga-ocr inference.
- Backend auth token: `X-Manga-Translator-Token` on all routes except `GET /`,
  stored as `backendAuthToken` in `chrome.storage.local`.
- Settings migration v2 (`lama_large` to `flux2_klein_lanpaint`, default font
  size 14 to 16, legacy synced keys moved to local storage).

### Out of Scope

- Login, subscription tiers, model catalog, cookies, or account state machine --
  this extension is for one owner's use.
- Signed CRX integrity metadata -- Chrome/distribution packaging owns this
  concern and it is not useful to the local development workflow.

## Context

The project was compared with the reverse-engineered Ichigo v0.0.94 extension
documentation in `C:/Users/acer/Downloads/ichigo-extension-docs`. The useful
portable ideas are queueing, per-site activation, action icon state, and
allowlisted DNR referrer rules. Phase 2 hardens those features for MV3 worker
restarts and runtime toggles. The implementation stays local-first and keeps
the mature BookWalker/composite reader path at page concurrency one.

## Constraints

- **Compatibility**: Keep Chrome MV3 and the current vanilla JS/FastAPI stack --
  the project has no build framework to absorb a large runtime rewrite.
- **Performance**: Local backend work stays serial; cloud work may use at most
  three concurrent jobs in the service worker.
- **Privacy**: Never accept an arbitrary referrer from page content. DNR rules
  may only target the fixed, documented CDN mappings and must be session-only.
- **Reliability**: Cache checks remain authoritative because MV3 workers can be
  suspended; in-memory queue state is an optimization, not durable state.
- **Scope**: Do not add account or hosted-service behavior for Ichigo parity.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Use a local FIFO queue plus promise deduplication instead of copying Ichigo's `FullQueue` protocol | The extension owns both sides of the request and does not need a polling protocol | Validated |
| Keep local concurrency at 1 and cloud concurrency at 3 | Protects ONNX/GPU and preserves existing reader ordering while improving cloud throughput | Validated |
| Treat sites as enabled by default, with explicit hostname overrides | Preserves current behavior for existing users while allowing expensive observers to be disabled | Validated |
| Use only fixed CDN-to-referrer mappings in transient DNR session rules | Provides hotlink compatibility without arbitrary header injection | Validated |
| Keep DNR ownership narrow and reconcile only exact extension-shaped rules | Prevents stale cleanup from touching unrelated session rules | Validated |
| Use a disposable content runtime while keeping blob cleanup always-on | Makes site disable/re-enable idempotent without leaking translated blob URLs | Validated |
| Use cache rechecks and one transport retry instead of durable raw-payload queues | Covers MV3 restart races without storing sensitive image data in extension storage | Validated |

---
*Last updated: 2026-08-06 after Ichigo feature comparison, implementation, and verification*
