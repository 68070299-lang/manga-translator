# Structure

- `wxt.config.ts`: manifest source of truth, build settings, and the
  `build:done` validation hook.
- `extension/entrypoints/`: WXT entrypoints. `background.ts` (service worker),
  `content/` (content script and its CSS), `popup/`, and `options/` (Svelte 5
  apps with their HTML shells).
- `extension/background/`: service-worker modules. `index.ts` registers
  listeners, `message-router.ts` dispatches requests; `translate.ts`,
  `providers.ts`, `fetch-image.ts`, `capture-region.ts`, `bundled-font.ts`,
  `dnr-rules.ts`, `context-menus.ts`, `action-state.ts`, `lifecycle.ts`, and
  `settings-migration.ts` hold the handlers.
- `extension/content/`: content-script modules (start at `main.ts`): discovery,
  capture, rendering, per-reader queues, progress, region selection, and
  BookWalker/canvas support.
- `extension/lib/`: shared libraries.
  - `settings.ts`: defaults, normalization, storage splitting.
  - `api.ts`: local backend adapter.
  - `cache.ts`: persistent LRU-style translation cache.
  - `translation-queue.ts`: local-serial FIFO queue with in-flight
    deduplication.
  - `site-activation.ts`: per-hostname enable/disable state.
  - `referrer-rules.ts`: fixed CDN referrer allowlist for transient DNR session
    rules.
  - `overlay-dom.ts`, `translated-image.ts`, `region-capture.ts`,
    `font-registry.ts`, `utils.ts`.
- `extension/shared/`: typed message contracts (`messages.ts`) and shared types
  (`types.ts`).
- `extension/components/`, `extension/ui/`: Svelte 5 components and state
  modules for the popup (compact active-tab controls) and options (detailed
  settings) pages.
- `extension/public/{icons,fonts,rules}`: static assets copied verbatim into the
  build; `extension/assets/icons/` holds the icon sources.
- `backend/src/manga_backend/`: FastAPI application package.
  - `api/`: `create_app`, routes, Pydantic v2 schemas, auth, orjson responses.
  - `pipeline/`: orchestrator, detection, OCR, inpainting, masks, layout,
    rendering, and related modules.
  - `models/`, `utils/`, `fonts/`, `config.py`, `bootstrap.py`, `paths.py`,
    `main.py` (thin ASGI shim), `__main__.py` (`python -m manga_backend`).
  - `comfy_lanpaint/`: client, workflow, and CLI for the loopback ComfyUI
    LanPaint inpainting daemon.
- `backend/tests/`: pytest suite. `backend/models_cache/` and `backend/.env`
  stay in the `backend/` project root.
- `scripts/comfy-lanpaint/`: model download, benchmark, and comparison helpers
  for the LanPaint runtime.
- `docs/`: `RUNTIME_CONTRACT.md` and `headless-lanpaint.md`.
- `tests/unit/`, `tests/integration/`, `tests/e2e/`: extension verification
  layers (Vitest, Vitest with fake browser, Playwright).
- `scripts/`: TypeScript repository check and build validation, plus backend
  model and ComfyUI tooling.
