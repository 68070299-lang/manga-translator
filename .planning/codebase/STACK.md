# Stack

- Chrome Manifest V3 extension built with WXT 0.21 (Vite) and strict TypeScript;
  `wxt.config.ts` is the manifest source of truth. Output is `dist/chrome-mv3`.
- Svelte 5 (runes) popup and options pages; plain TypeScript modules for the
  service worker and the content script (one bundled `content-scripts/content.js`).
- FastAPI/Python local backend (src layout, package `manga_backend`) with
  ONNX-oriented detector/OCR/inpainting pipeline; Pydantic v2 schemas,
  pydantic-settings configuration, orjson responses.
- Vitest + happy-dom unit and integration tests (fake browser via `wxt/testing`);
  Playwright E2E tests in TypeScript; pytest for the backend.
- Biome (lint and format), `tsc` + `svelte-check` for the extension, ruff +
  pyright for the backend.
- TypeScript scripts under `scripts/` run directly on Node 24
  (`validate-extension.ts`, `repository-check.ts`, `popup-smoke.ts`,
  `real-url-smoke.ts`, `prepare-bookwalker-profile.ts`); every `wxt build`
  validates the generated extension through a `build:done` hook.
- Optional local inpainting through a loopback ComfyUI daemon (Flux.2 Klein +
  LanPaint, `backend/src/manga_backend/comfy_lanpaint/`, `scripts/comfy-lanpaint/`);
  GPU ONNX runtime is an optional backend dependency group (`gpu` or `directml`).
- Shared extension libraries in `extension/lib/*.ts`, including
  `translation-queue.ts`, `site-activation.ts`, and `referrer-rules.ts`
  (see `docs/`).

## Runtime Boundaries

- Content scripts discover/render page images and send translation requests.
- Service worker owns provider calls, cache access, image fetching, and message routing.
- Backend owns detection, OCR, inpainting, translation, and full-image rendering.
