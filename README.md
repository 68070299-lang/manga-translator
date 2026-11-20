# Manga Translator Extension

Chrome Manifest V3 extension and local FastAPI backend for translating manga images.

The extension captures images or canvases from the active page, routes work through a service worker, and either calls a local backend or a configured cloud provider. The backend runs detection, OCR, translation, inpainting, and rendering for local-first translation.

## Repository Layout

- extension/ - MV3 extension source (WXT + TypeScript + Svelte 5); `wxt.config.ts` at the repo root is the manifest source of truth.
  - extension/entrypoints/ - WXT entrypoints: `background.ts`, `content/`, `popup/`, `options/`.
  - extension/background/ - service-worker modules (message router, providers, translate, DNR rules, context menus, lifecycle).
  - extension/content/ - content-script modules (discovery, capture, rendering, queues, BookWalker reader support).
  - extension/lib/ - shared libraries (settings, api, cache, translation queue, site activation, referrer rules, overlay DOM, font registry, region capture).
  - extension/shared/ - typed message contracts (`messages.ts`) and shared types (`types.ts`).
  - extension/components/ and extension/ui/ - Svelte 5 components and state modules for the popup and options pages.
  - extension/public/ - static assets copied verbatim into the build (`icons/`, `fonts/`, `rules/`); extension/assets/icons/ holds the icon sources.
- backend/ - local FastAPI translation backend (src layout, package `manga_backend` in backend/src/manga_backend/).
- scripts/ - repository check, build validation, and backend/ComfyUI tooling. The TypeScript scripts run directly under Node 24.
- tests/unit/ - Vitest (happy-dom) contract and unit tests.
- tests/integration/ - Vitest integration tests for provider and service-worker contracts (fake browser from `wxt/testing`).
- tests/e2e/ - Playwright extension tests (TypeScript) that load the built extension from dist/chrome-mv3.
- backend/tests/ - pytest backend tests.
- docs/RUNTIME_CONTRACT.md - runtime data-flow, settings, API, cache, and verification contracts.
- docs/headless-lanpaint.md - setup for the loopback ComfyUI daemon used by the default inpainter.
- backend/README.md - backend API, request parameters, first-run model behavior, and backend tests.

Generated output belongs in ignored paths such as coverage/, dist/ (build output and zips), .wxt/, .output/, test-results/, playwright-report/, backend __pycache__ directories, and backend/models_cache/.

## Extension Runtime

The extension is built with WXT (Vite) from extension/entrypoints; strict TypeScript throughout, Svelte 5 (runes) for the popup and options UI. There is no hand-written manifest and no script ordering: `wxt.config.ts` plus the entrypoint definitions generate the manifest, and every entrypoint is bundled and tree-shaken on its own.

- The service worker is `extension/entrypoints/background.ts`, which registers every listener synchronously through `registerBackground()` in extension/background/index.ts. It builds to background.js.
- The content script is `extension/entrypoints/content/index.ts` (matches `<all_urls>`, `document_idle`, top frame only). It boots `extension/content/main.ts` and builds to the single bundle content-scripts/content.js, plus content-scripts/content.css.
- The popup (popup.html) and options page (options.html, declared as `options_ui` and opened in a tab) are Svelte 5 apps mounted from extension/entrypoints/popup/ and extension/entrypoints/options/.
- Permissions are storage, unlimitedStorage, contextMenus, and declarativeNetRequest, with host permission <all_urls>. A static declarativeNetRequest ruleset (extension/public/rules/bookwalker-cors.json, built to rules/bookwalker-cors.json) rewrites Referer and CORS headers for the BookWalker viewer; the service worker also installs transient session rules that set Referer for a fixed CDN allowlist while fetching images.

Every `wxt build` and `wxt zip` runs scripts/validate-extension.ts from a `build:done` hook in wxt.config.ts: it checks the permission budget, that every referenced file exists, bundled font checksums, DNR rule files, and that no remote code ships. Keep that validation and the tests in sync when changing entrypoints, bundled assets, or web-accessible resources.

Beyond whole-page and visible-image translation, you can drag over any part of the visible tab to translate just that area: use the Region button in the popup or reader controls, or "Translate a selected region" in the context menu. Overlays render in Shadow DOM, and CC Wild Words ships with the extension. See docs/RUNTIME_CONTRACT.md for how these work and their limits.

## Privacy And Data Boundaries

Page image data and OCR text are sensitive.

- Default mode is local backend mode.
- Local backend URLs must be loopback HTTP URLs such as http://localhost:8001.
- Local mode is not automatically offline: with a key-backed translator (Gemini, OpenAI, OpenRouter) the backend sends OCR text to that provider. Only manga-ocr or paddle-ocr with translator none keeps everything on loopback. See "Data leaving the browser" in docs/RUNTIME_CONTRACT.md.
- Cloud mode sends the full page image to the selected provider.
- Region translation captures the visible tab only after you finish a selection, and only the cropped selection leaves the service worker.
- There is no separate consent prompt: choosing a cloud pipeline or a key-backed translator and saving a key is the opt-in to that data sharing.
- API keys and the backend access token are private local settings (chrome.storage.local) and are not stored in synced settings.
- Translation cache entries live in chrome.storage.local, expire after seven days, are bounded to roughly 15 MB with least-recently-used eviction, and are keyed by image identity plus settings that affect output.

The backend is a local tool by default. Do not expose it on 0.0.0.0 without separate authentication, rate limits, and deployment hardening.

## Setup

Install Node dependencies from the repo root with: rtk npm install (Node 24 or newer; the `postinstall` step runs `wxt prepare`).

Build the extension with `npm run build`, then load `dist/chrome-mv3` as an unpacked extension in Chrome (chrome://extensions, Developer mode, Load unpacked). `npm run dev` runs WXT's dev mode with rebuild-on-change, and `npm run zip` produces a store-ready zip under dist/.

Builds are staged in `dist/.build/` and validated (`scripts/validate-extension.ts`) before they replace `dist/chrome-mv3`, so a failed build leaves the last good extension loaded. The unpacked copy carries a fixed manifest `key` (`scripts/extension-key.ts`), so its extension ID is always `jnakpckdlnjolbfdggkifidibbfenhdh`, wherever the folder lives. The store zip is packed from the staging build and has no `key`, because the Chrome Web Store rejects one.

### Upgrading from the pre-rewrite extension

The old extension was loaded unpacked from `extension/`, which no longer contains a manifest. Chrome gives an unpacked extension without a `key` an ID derived from its folder, and each ID has its own storage, so the rebuilt extension starts empty. After loading `dist/chrome-mv3`:

1. Remove the old "Manga Translator" entry in chrome://extensions (it now shows a load error).
2. In the extension options, re-enter the Backend Access Token (the value of `BACKEND_AUTH_TOKEN` in `backend/.env`) and any cloud provider API keys, and re-apply your settings.
3. Re-enable translation on the sites you used it on. Translated pages are re-cached as you read.

This happens once: the fixed `key` keeps the ID stable from now on.

Install backend dependencies with uv from backend/: uv sync --python 3.12 (CPU by default). For a GPU runtime sync exactly one group, for example `uv sync --no-default-groups --group gpu --python 3.12` (or `--group directml`); see backend/README.md.

Set `BACKEND_AUTH_TOKEN` in `backend/.env`, enter the same value in the
extension's Backend Access Token setting, then start the backend on loopback
from backend/: `uv run uvicorn manga_backend.main:app --host 127.0.0.1 --port 8001` (or `uv run python -m manga_backend`, which uses `BACKEND_HOST`/`BACKEND_PORT`, default 127.0.0.1:8001).
On Windows you can instead run `powershell -File backend/start.ps1` from the repo root, which syncs a per-runtime environment (`-Runtime gpu|cpu|directml`) and starts the same server.

The default inpainter is `flux2_klein_lanpaint`, which needs the headless ComfyUI daemon on 127.0.0.1:8188 (setup: docs/headless-lanpaint.md). Without ComfyUI the backend falls back to LaMa for each page, but the extension does not cache fallback results, so pick LaMa (or None) as the Inpainter in the extension options if you do not run ComfyUI. Expected first-run failures are listed in docs/RUNTIME_CONTRACT.md.

## Verification

Use the smallest useful gate first:

- rtk npm run verify:repository
- rtk npm run build (also validates the generated extension)
- rtk npm run lint (Biome)
- rtk npm run typecheck (`wxt prepare`, `tsc`, and `svelte-check`)
- rtk npm run test (Vitest: unit and integration)
- rtk npm run lint:backend (ruff)
- rtk npm run typecheck:backend (pyright)
- rtk npm run test:backend (pytest)
- rtk npm run test:e2e (builds, then runs Playwright against dist/chrome-mv3)
- rtk npm run verify (repository check, both lints and typechecks, Vitest, pytest, build, and zip)

## Backend

See backend/README.md for local API setup, request parameters, first-run model behavior, and backend test commands. See docs/headless-lanpaint.md for the optional Flux.2 Klein + LanPaint ComfyUI runtime.
