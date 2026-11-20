# Maintenance Completion Guide

This document turns the blindspot pass into a practical completion path for this repo. Prefer `docs/RUNTIME_CONTRACT.md`, `README.md`, and `backend/README.md` for current behavior; this guide tracks what maintenance work remains.

## Project Snapshot

This project has two main systems:

- Chrome Manifest V3 extension in `extension/`
- Local FastAPI image-translation backend in `backend/`

The core runtime flow is:

```text
content script captures page image/canvas
-> service worker checks cache and sends the request to the local backend
-> backend /translate runs detector, OCR, translation, inpainting, rendering
-> content script overlays or replaces the translated image
```

Check `rtk git status --short` before starting; the checkout may contain unrelated in-progress work. `npm run verify:repository` is the gate for the repository boundary.

## Completion Principles

1. Stabilize the repo boundary before feature work.
   Keep old local scaffolding, generated artifacts, and stale docs out of the tracked tree so runtime changes stay reviewable.

2. Work in small vertical slices.
   For each change, identify the affected surface: extension runtime, backend API, ML pipeline, settings/cache, tests, docs, or packaging. Avoid cross-cutting edits unless the task genuinely requires them.

3. Treat the build and manifest as a runtime contract.
   The extension is built by WXT: `wxt.config.ts` is the manifest source of truth and `extension/entrypoints/` defines the service worker, content script, popup, and options page. Permissions, referenced files, bundled font checksums, DNR rules, and remote-code rules are asserted by `scripts/validate-extension.ts` (run by the `build:done` hook on every build) and `tests/unit/manifest.test.ts`. Any new bundled asset or permission must be reflected in validation and tests.

4. Treat privacy as product behavior, not a later audit.
   The extension can run on all URLs and send image/canvas data to the local backend. Changes should preserve clear consent, local-first defaults, cache controls, and provider-key boundaries.

5. Keep backend local by default.
   The backend is safe only as a local tool unless authentication, rate limits, and deployment hardening are added. Do not normalize `BACKEND_HOST=0.0.0.0` as a default path.

6. Verify from contracts, not from old docs.
   Some docs are stale or damaged. Prefer source, tests, `package.json`, `backend/pyproject.toml`, and validation scripts as the current contract.

## Recommended Completion Order

### Phase 1: Repo Boundary (docs done, owner cleanup open)

Goal: make the tree understandable and safe to maintain.

Status: the documentation half is done. The root `README.md` exists, `backend/README.md` is current, and `verify:repository` guards the boundary. The cleanup half is open and needs the owner, because it means deleting the owner's own local files: the ignored `artifacts/`, `website/`, and `work/` directories and other local diagnostics (all Git-ignored, so they do not affect the repository check). Keep the tasks below as a checklist when the tree drifts.

Tasks:

- Decide whether the current tracked deletions are intentional (open, owner).
- Remove or untrack forbidden local/generated paths reported by `npm run verify:repository`, and the local scratch directories named in Status above (open, owner).
- Keep the root `README.md`, `backend/README.md`, and `tests/integration/README.md` current.
- Keep generated output under ignored paths only: `coverage/`, `dist/`, `test-results/`, `playwright-report/`, backend caches, and virtualenvs.

Exit criteria:

```powershell
rtk git status --short
rtk npm run verify:repository
rtk npm run build
```

`verify:repository` and `build` should pass.

### Phase 2: Runtime Contract Map (done)

Goal: document the real behavior before changing it.

Status: done. `docs/RUNTIME_CONTRACT.md` covers entrypoints and bundles, messages, storage map and defaults, data flow, the data-leaving-the-browser matrix, cache contract, and expected first-run failures. Update it with any runtime change.

The contract covers:

- Extension entrypoints and the bundles they build to
- Message types between popup, content script, and service worker
- Settings schema and where private API keys are stored
- Local backend request form fields
- Backend endpoints and response shapes
- Cache identity and retention behavior
- Supported providers and model/env precedence

Best source files:

- `wxt.config.ts`
- `extension/entrypoints/` (background, content, popup, options)
- `extension/content/` (content script modules, start at `main.ts`)
- `extension/background/` (service worker modules, start at `index.ts` and `message-router.ts`)
- `extension/shared/messages.ts`
- `extension/lib/settings.ts`
- `extension/lib/api.ts`
- `extension/lib/cache.ts`
- `backend/src/manga_backend/api/` (`app.py`, `routes/`, `schemas.py`)
- `backend/src/manga_backend/pipeline/orchestrator.py`
- `backend/src/manga_backend/models/translator.py`

Exit criteria:

- A maintainer can answer "where does this data go?" without reading the whole repo.
- Docs match tests and source.

### Phase 3: Security And Privacy Hardening

Goal: reduce high-impact failure modes before adding features.

Status of each item:

- Add an explicit permission budget test for the manifest - done (`tests/unit/manifest.test.ts` and `scripts/validate-extension.ts`; permissions are storage, unlimitedStorage, contextMenus, declarativeNetRequest).
- Add explicit consent or per-site allowlisting before page data leaves the browser - open, pending owner decision. No consent gate exists; choosing a key-backed translator and saving a key is the opt-in today (documented in the README and runtime contract).
- Add a backend auth token if the backend may be exposed beyond loopback - done. All routes except `GET /` and preflights require `X-Manga-Translator-Token` (`BACKEND_AUTH_TOKEN`).
- Add cache pruning on extension startup/session start - done (`pruneCacheSafely` runs at worker load, startup, and install; expiry is also enforced on read).
- Redact upstream provider error bodies before logs or UI - done (backend and `extension/lib/api.ts` redact keys and bearer tokens).
- Add request concurrency/rate limiting around expensive backend routes - done (`BACKEND_MAX_CONCURRENT` queue with a bounded wait before 429); the extension runs one translation job at a time.
- Pin or verify model downloads with revisions or checksums - done for the default models (`MODEL_PINS` in `backend/src/manga_backend/utils/download.py` pins a commit and sha256 for manga-ocr, LaMa, RT-DETR and PaddleOCR; `backend/tests/test_download.py` fails if one is left unpinned). The gated AnimeText YOLO repo hides its hashes from unauthenticated requests, so it is still unpinned. Cached files are re-hashed at load, which adds about 1.3 s to startup.
- Serialize transient DNR referrer rule installs and removals - done (mutex plus reference-counted leases in the service worker).

Exit criteria:

```powershell
rtk npm run test
rtk npm run test:backend
rtk npm run test:e2e
```

Relevant security tests should fail before the mitigation and pass after it.

### Phase 4: Backend Reliability

Goal: make local startup and failures predictable.

Tasks:

- Separate "server is alive" from "all ML models are ready" in health docs and tests.
- Make first-run model download behavior explicit.
- Ensure keyless smoke uses `translator=none`.
- Confirm `backend/pyproject.toml` is the primary dependency source, or intentionally preserve `requirements.txt` as a fallback.
- Avoid multiple-worker deployment unless global backend state is redesigned.

Exit criteria:

```powershell
rtk npm run lint:backend
rtk npm run test:backend
```

### Phase 5: Extension Reliability

Goal: protect the most fragile browser paths.

Tasks:

- Keep BookWalker/canvas behavior covered by E2E tests.
- Add tests for tainted canvas, failed background fetch, and stale canvas cleanup when touching content capture code.
- Preserve local backend URL validation.
- Preserve cache-key coverage whenever settings change.
- Do not add content-side assets without updating manifest/web-accessible-resource validation (`scripts/validate-extension.ts`).

Exit criteria:

```powershell
rtk npm run lint
rtk npm run typecheck
rtk npm run test
rtk npm run test:e2e
```

### Phase 6: Final Release Gate

Goal: the repo is shippable and maintainable.

Run:

```powershell
rtk npm run verify
```

Expected result:

- Biome lint, `tsc`/`svelte-check`, ruff, and pyright pass.
- Vitest (unit and integration) and backend pytest pass; run `npm run test:e2e` separately for the Playwright suite (it is not part of `verify`).
- Repository boundary check passes.
- Extension build (with its validation hook) and zip succeed.
- Generated artifacts remain ignored or intentionally packaged.

## Verification Ladder

Use the smallest reliable gate first:

```powershell
rtk npm run verify:repository
rtk npm run build
rtk npm run lint
rtk npm run typecheck
rtk npm run test
rtk npm run lint:backend
rtk npm run typecheck:backend
rtk npm run test:backend
rtk npm run test:e2e
rtk npm run verify
```

## High-Quality Maintenance Prompt

Use this prompt when asking an agent to work on this repo:

```text
You are maintaining <path to this repository checkout>, a Chrome MV3 manga translation extension with a local FastAPI backend.

Objective:
<one concrete outcome>

Current constraints:
- The repo may be dirty. Inspect git status first and do not revert unrelated user changes.
- Use RTK for shell commands.
- Keep changes scoped to the affected surface.
- Preserve the extension entrypoint layout and the manifest permission budget unless the task explicitly changes them.
- Treat privacy/security as product behavior: page images, OCR text, API keys, and translated cache data are sensitive.
- Backend must remain loopback-first unless authentication and exposure docs are added.

Relevant surfaces:
- Manifest and build: wxt.config.ts, extension/entrypoints/, scripts/validate-extension.ts
- Content runtime and page capture: extension/content/ (entry: main.ts)
- Service-worker routing/cache/provider path: extension/background/ (entry: index.ts, message-router.ts)
- Message contracts: extension/shared/messages.ts
- Settings and private keys: extension/lib/settings.ts
- Local backend requests: extension/lib/api.ts
- Cache behavior: extension/lib/cache.ts
- Backend API contract: backend/src/manga_backend/api/ (app.py, routes/, schemas.py)
- Backend ML/render pipeline: backend/src/manga_backend/pipeline/ (orchestrator.py)
- Translator/provider env precedence: backend/src/manga_backend/models/translator.py

Required workflow:
1. Inspect current git status and relevant files.
2. State the risk surface before editing.
3. Make the smallest coherent change.
4. Add or update tests that would fail without the change.
5. Run the narrowest useful verification first, then broader gates if needed.
6. Report changed files, commands run, pass/fail results, and any residual risk.

Definition of done:
- Behavior is implemented or documented.
- Tests cover the changed contract.
- npm run verify:repository still passes or any failure is clearly unrelated and explained.
- No secrets, generated caches, model files, pycache, node_modules, coverage, dist, or local workspace artifacts are added to Git.
```

## Change Planning Template

Use this before starting a non-trivial maintenance change:

````markdown
# Change Plan: <title>

## Goal
<one sentence>

## Affected Surface
- [ ] Extension manifest/entrypoints/build
- [ ] Content capture/overlay
- [ ] Service worker/cache/provider routing
- [ ] Settings/API keys
- [ ] Backend API
- [ ] Backend ML/render pipeline
- [ ] Tests/tooling
- [ ] Docs

## Risk Notes
- Data leaving browser:
- API key handling:
- Cache/storage impact:
- Backend exposure impact:
- Generated files/artifacts:

## Files To Read First
- 

## Files Expected To Change
- 

## Tests To Add Or Update
- 

## Verification Commands
```powershell
rtk npm run verify:repository
rtk npm run test
rtk npm run test:backend
```

## Done When
- 
````

## Documentation Quality Bar

A high-quality doc for this repo should answer:

- What starts the system?
- What data crosses each boundary?
- Which settings are public sync settings and which are private local settings?
- Which providers receive images or text?
- What is cached, where, and for how long?
- What commands verify each subsystem?
- Which failures are expected on first run?
- Which paths are local-only and which paths can reach external services?

Avoid docs that only list files. Good maintenance docs explain contracts, failure modes, and verification.
