# Maintenance Completion Guide

This document turns the blindspot pass into a practical completion path for this repo. Prefer `docs/RUNTIME_CONTRACT.md`, `README.md`, and `backend/README.md` for current behavior; this guide tracks what maintenance work remains.

## Project Snapshot

This project has two main systems:

- Chrome Manifest V3 extension in `extension/`
- Local FastAPI image-translation backend in `backend/`

The core runtime flow is:

```text
content script captures page image/canvas
-> service worker checks cache and chooses cloud or local backend
-> backend /translate runs detector, OCR, translation, inpainting, rendering
-> content script overlays or replaces the translated image
```

Check `rtk git status --short` before starting; the checkout may contain unrelated in-progress work. `npm run verify:repository` is the gate for the repository boundary.

## Completion Principles

1. Stabilize the repo boundary before feature work.
   Keep old local scaffolding, generated artifacts, and stale docs out of the tracked tree so runtime changes stay reviewable.

2. Work in small vertical slices.
   For each change, identify the affected surface: extension runtime, backend API, ML pipeline, settings/cache, tests, docs, or packaging. Avoid cross-cutting edits unless the task genuinely requires them.

3. Treat script order as a runtime contract.
   There is no bundler. `extension/manifest.json`, popup/options script tags, and service-worker `importScripts()` define dependency order. Any new shared file must be added to validation and tests.

4. Treat privacy as product behavior, not a later audit.
   The extension can run on all URLs and send image/canvas data to local or cloud providers. Changes should preserve clear consent, local-first defaults, cache controls, and provider-key boundaries.

5. Keep backend local by default.
   The backend is safe only as a local tool unless authentication, rate limits, and deployment hardening are added. Do not normalize `BACKEND_HOST=0.0.0.0` as a default path.

6. Verify from contracts, not from old docs.
   Some docs are stale or damaged. Prefer source, tests, `package.json`, `backend/pyproject.toml`, and validation scripts as the current contract.

## Recommended Completion Order

### Phase 1: Repo Boundary (docs done, owner cleanup open)

Goal: make the tree understandable and safe to maintain.

Status: the documentation half is done. The root `README.md` exists, `backend/README.md` is current, and `verify:repository` guards the boundary. The cleanup half is open and needs the owner, because it means deleting or untracking the owner's own files: the root scratch script `fix3.py`, the ignored `artifacts/` directory, local diagnostics, and the one-off `scripts/bookwalker-*.ps1` / `bookwalker-page23.js` helpers. Decide per file whether to keep it, move it under an ignored path, or remove it. Keep the tasks below as a checklist when the tree drifts.

Tasks:

- Decide whether the current tracked deletions are intentional (open, owner).
- Remove or untrack forbidden local/generated paths reported by `npm run verify:repository`, and the scratch files named in Status above (open, owner).
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

Status: done. `docs/RUNTIME_CONTRACT.md` covers entrypoints and script order, messages, storage map and defaults, data flow, the data-leaving-the-browser matrix, cache contract, and expected first-run failures. Update it with any runtime change.

The contract covers:

- Extension entrypoints and script order
- Message types between popup, content script, and service worker
- Settings schema and where private API keys are stored
- Local backend request form fields
- Backend endpoints and response shapes
- Cache identity and retention behavior
- Supported providers and model/env precedence

Best source files:

- `extension/manifest.json`
- `extension/content/content.js`
- `extension/background/service-worker.js`
- `extension/lib/settings.js`
- `extension/lib/api.js`
- `extension/lib/cache.js`
- `backend/main.py`
- `backend/pipeline.py`
- `backend/models/translator.py`

Exit criteria:

- A maintainer can answer "where does this data go?" without reading the whole repo.
- Docs match tests and source.

### Phase 3: Security And Privacy Hardening

Goal: reduce high-impact failure modes before adding features.

Status of each item:

- Add an explicit permission budget test for `extension/manifest.json` - done (`tests/unit/manifest.test.js`; permissions are storage, unlimitedStorage, contextMenus, declarativeNetRequest).
- Add explicit cloud/OCR consent or per-site allowlisting before page data leaves the browser - open, pending owner decision. No consent gate exists; choosing a cloud pipeline or Google OCR and saving a key is the opt-in today (documented in the README and runtime contract).
- Add a backend auth token if the backend may be exposed beyond loopback - done. All routes except `GET /` and preflights require `X-Manga-Translator-Token` (`BACKEND_AUTH_TOKEN`).
- Add cache pruning on extension startup/session start - done (`pruneCacheSafely` runs at worker load, startup, and install; expiry is also enforced on read).
- Redact upstream provider error bodies before logs or UI - done (backend and `extension/lib/api.js` redact keys and bearer tokens).
- Add request concurrency/rate limiting around expensive backend routes - done (`BACKEND_MAX_CONCURRENT` queue with a bounded wait before 429); extension queues are local 1 / cloud 3.
- Pin or verify model downloads with revisions or checksums - done for the default models (`MODEL_PINS` in `backend/utils/download.py` pins a commit and sha256 for manga-ocr, LaMa, RT-DETR and PaddleOCR; `backend/test_download.py` fails if one is left unpinned). The gated AnimeText YOLO repo hides its hashes from unauthenticated requests, so it is still unpinned. Cached files are re-hashed at load, which adds about 1.3 s to startup.
- Serialize transient DNR referrer rule installs and removals - done (mutex plus reference-counted leases in the service worker).

Exit criteria:

```powershell
rtk npm run test:unit
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
rtk npm run smoke:backend
```

### Phase 5: Extension Reliability

Goal: protect the most fragile browser paths.

Tasks:

- Keep BookWalker/canvas behavior covered by E2E tests.
- Add tests for tainted canvas, failed background fetch, and stale canvas cleanup when touching content capture code.
- Preserve local backend URL validation.
- Preserve cache-key coverage whenever settings change.
- Do not add content-side assets without updating manifest/web-accessible-resource validation.

Exit criteria:

```powershell
rtk npm run lint
rtk npm run test:unit
rtk npm run test:integration
rtk npm run test:e2e
```

### Phase 6: Final Release Gate

Goal: the repo is shippable and maintainable.

Run:

```powershell
rtk npm run verify
```

Expected result:

- Lint passes.
- Unit, integration, backend, and E2E tests pass.
- Repository boundary check passes.
- Extension build/package succeeds.
- Generated artifacts remain ignored or intentionally packaged.

## Verification Ladder

Use the smallest reliable gate first:

```powershell
rtk npm run verify:repository
rtk npm run build
rtk npm run lint
rtk npm run test:unit
rtk npm run test:integration
rtk npm run lint:backend
rtk npm run test:backend
rtk npm run test:e2e
rtk npm run verify
```

Do not use `smoke:real-url` as a deterministic CI gate. It depends on live network and real site behavior.

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
- Preserve the extension runtime script order unless the task explicitly changes it.
- Treat privacy/security as product behavior: page images, OCR text, API keys, and translated cache data are sensitive.
- Backend must remain loopback-first unless authentication and exposure docs are added.

Relevant surfaces:
- Extension manifest and script loading: extension/manifest.json
- Content runtime and page capture: extension/content/content.js
- Service-worker routing/cache/provider path: extension/background/service-worker.js
- Settings and private keys: extension/lib/settings.js
- Cloud/local backend requests: extension/lib/api.js
- Cache behavior: extension/lib/cache.js
- Backend API contract: backend/main.py
- Backend ML/render pipeline: backend/pipeline.py
- Translator/provider env precedence: backend/models/translator.py

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
- [ ] Extension manifest/script order
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
rtk npm run test:unit
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
