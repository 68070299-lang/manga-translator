# Manga Translator Extension

Chrome Manifest V3 extension and local FastAPI backend for translating manga images.

The extension captures images or canvases from the active page, routes work through a service worker, and either calls a local backend or a configured cloud provider. The backend runs detection, OCR, translation, inpainting, and rendering for local-first translation.

## Repository Layout

- extension/ - MV3 extension source.
- backend/ - local FastAPI translation backend.
- scripts/ - repository, build, package, backend, and smoke-check tooling.
- tests/unit/ - Jest contract and unit tests.
- tests/integration/ - Jest integration tests for provider and service-worker contracts.
- tests/e2e/ - Playwright extension tests.
- docs/RUNTIME_CONTRACT.md - runtime data-flow, settings, API, cache, and verification contracts.
- docs/headless-lanpaint.md - setup for the loopback ComfyUI daemon used by the default inpainter.
- backend/README.md - backend API, request parameters, first-run model behavior, and backend tests.

Generated output belongs in ignored paths such as coverage/, dist/, test-results/, playwright-report/, backend __pycache__ directories, and backend/models_cache/.

## Extension Runtime

There is no bundler. Script order is part of the runtime contract:

- Content scripts load from extension/manifest.json at document_idle in this order: lib/settings.js, lib/site-activation.js, lib/utils.js, lib/translated-image.js, lib/overlay-dom.js, lib/font-registry.js, lib/region-capture.js, content/content-helpers.js, content/content.js, plus content/content.css.
- The service worker loads shared modules with importScripts in this order: lib/settings.js, lib/utils.js, lib/api.js, lib/cache.js, lib/translation-queue.js, lib/site-activation.js, lib/referrer-rules.js, lib/font-registry.js, lib/region-capture.js.
- The popup loads lib/settings.js, lib/site-activation.js, then popup/popup.js. The options page loads lib/settings.js, then options/options.js.
- Permissions are storage, unlimitedStorage, contextMenus, and declarativeNetRequest, with host permission <all_urls>. A static declarativeNetRequest ruleset (rules/bookwalker-cors.json) rewrites Referer and CORS headers for the BookWalker viewer; the service worker also installs transient session rules that set Referer for a fixed CDN allowlist while fetching images.

Keep validation and tests in sync when changing entrypoints, shared files, or web-accessible resources.

Beyond whole-page and visible-image translation, you can drag over any part of the visible tab to translate just that area: use the Region button in the popup or reader controls, or "Translate a selected region" in the context menu. Overlays render in Shadow DOM, and CC Wild Words ships with the extension. See docs/RUNTIME_CONTRACT.md for how these work and their limits.

## Privacy And Data Boundaries

Page image data and OCR text are sensitive.

- Default mode is local backend mode.
- Local backend URLs must be loopback HTTP URLs such as http://localhost:8001.
- Local mode is not automatically offline: with a key-backed translator (Gemini, OpenAI, OpenRouter) the backend sends OCR text to that provider, and with OCR set to Google OCR it sends image crops to Google Cloud Vision. Only manga-ocr or paddle-ocr with translator none keeps everything on loopback. See "Data leaving the browser" in docs/RUNTIME_CONTRACT.md.
- Cloud mode sends the full page image to the selected provider.
- Region translation captures the visible tab only after you finish a selection, and only the cropped selection leaves the service worker.
- There is no separate consent prompt: choosing a cloud pipeline or Google OCR and saving a key is the opt-in to that data sharing.
- API keys and the backend access token are private local settings (chrome.storage.local) and are not stored in synced settings.
- Translation cache entries live in chrome.storage.local, expire after seven days, are bounded to roughly 15 MB with least-recently-used eviction, and are keyed by image identity plus settings that affect output.

The backend is a local tool by default. Do not expose it on 0.0.0.0 without separate authentication, rate limits, and deployment hardening.

## Setup

Install Node dependencies from the repo root with: rtk npm install

Install backend dependencies with uv from backend/: uv sync --python 3.12 (CPU by default). For a GPU runtime sync exactly one group, for example `uv sync --no-default-groups --group gpu --python 3.12` (or `--group directml`); see backend/README.md.

Set `BACKEND_AUTH_TOKEN` in `backend/.env`, enter the same value in the
extension's Backend Access Token setting, then start the backend on loopback
from backend/: `uv run uvicorn main:app --host 127.0.0.1 --port 8001`
On Windows you can instead run `powershell -File backend/start.ps1` from the repo root, which syncs a per-runtime environment (`-Runtime gpu|cpu|directml`) and starts the same server.

The default inpainter is `flux2_klein_lanpaint`, which needs the headless ComfyUI daemon on 127.0.0.1:8188 (setup: docs/headless-lanpaint.md). Without ComfyUI the backend falls back to LaMa for each page, but the extension does not cache fallback results, so pick LaMa (or None) as the Inpainter in the extension options if you do not run ComfyUI. Expected first-run failures are listed in docs/RUNTIME_CONTRACT.md.

## Verification

Use the smallest useful gate first:

- rtk npm run verify:repository
- rtk npm run build
- rtk npm run lint
- rtk npm run test:unit
- rtk npm run test:integration
- rtk npm run lint:backend
- rtk npm run test:backend
- rtk npm run test:e2e
- rtk npm run verify

smoke:real-url depends on live sites and should not be treated as a deterministic CI gate.

## Backend

See backend/README.md for local API setup, request parameters, first-run model behavior, and backend test commands. See docs/headless-lanpaint.md for the optional Flux.2 Klein + LanPaint ComfyUI runtime.
