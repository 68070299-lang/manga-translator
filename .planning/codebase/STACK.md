# Stack

- Chrome Manifest V3 extension.
- Vanilla JavaScript content script, popup, options page, and service worker.
- FastAPI/Python local backend with ONNX-oriented detector/OCR/inpainting pipeline.
- Jest/jsdom unit and integration tests; Playwright E2E tests; ESLint.
- Node scripts validate, build, package, and verify the extension.
- Optional local inpainting through a loopback ComfyUI daemon (Flux.2 Klein +
  LanPaint, `backend/comfy_lanpaint/`, `scripts/comfy-lanpaint/`); GPU ONNX
  runtime is an optional backend dependency group (`gpu` or `directml`).
- Shared extension modules without a bundler: `lib/translation-queue.js`,
  `lib/site-activation.js`, and `lib/referrer-rules.js` (see `docs/`).

## Runtime Boundaries

- Content scripts discover/render page images and send translation requests.
- Service worker owns provider calls, cache access, image fetching, and message routing.
- Backend owns detection, OCR, inpainting, translation, and full-image rendering.
