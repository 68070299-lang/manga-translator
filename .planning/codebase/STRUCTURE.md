# Structure

- `extension/background/service-worker.js`: message router, provider calls,
  cache orchestration, image fetching, context menus.
- `extension/content/content.js`: discovery, reader-specific queues, capture,
  rendering, progress, and page controls.
- `extension/lib/settings.js`: defaults, normalization, storage splitting.
- `extension/lib/api.js`: local and cloud provider adapters.
- `extension/lib/cache.js`: persistent LRU-style translation cache.
- `extension/lib/translation-queue.js`: local-serial / cloud-capped FIFO queue
  with in-flight deduplication.
- `extension/lib/site-activation.js`: per-hostname enable/disable state.
- `extension/lib/referrer-rules.js`: fixed CDN referrer allowlist for transient
  DNR session rules.
- `extension/popup/`: compact active-tab controls.
- `extension/options/`: detailed settings UI.
- `backend/`: FastAPI application and translation pipeline.
- `backend/comfy_lanpaint/`: client, workflow, and CLI for the loopback ComfyUI
  LanPaint inpainting daemon.
- `scripts/comfy-lanpaint/`: model download, benchmark, and comparison helpers
  for the LanPaint runtime.
- `docs/`: `RUNTIME_CONTRACT.md` and `headless-lanpaint.md`.
- `tests/unit/`, `tests/integration/`, `tests/e2e/`: verification layers.
- `scripts/`: repository, manifest, build, package, and backend checks.
