# Architecture

1. The content script (`extension/entrypoints/content/index.ts` booting
   `extension/content/main.ts`) discovers candidate images, canvases,
   backgrounds, and reader composites.
2. It captures or fetches image data and sends `TRANSLATE_IMAGE` (typed in
   `extension/shared/messages.ts`) to the service worker.
3. The service worker (`extension/background/`, routed by `message-router.ts`)
   validates input, derives a settings-aware cache key, calls the local
   backend, and stores the result.
4. The content script renders the translated image on the source element.
5. The Svelte 5 popup/options pages persist user settings and send page commands.

Phase 1 inserts queueing between cache-key preparation and provider execution,
and adds hostname activation around content-script startup without changing the
rendering contract.

On the local path the provider calls the backend's `/translate` route
(`backend/src/manga_backend/api/routes/translate.py`), which runs the
`manga_backend.pipeline` orchestrator (detection, OCR, translation, inpainting,
rendering).
