# Architecture

1. The content script discovers candidate images, canvases, backgrounds, and
   reader composites.
2. It captures or fetches image data and sends `TRANSLATE_IMAGE` to the service
   worker.
3. The service worker validates input, derives a settings-aware cache key, calls
   the local or cloud provider, and stores the result.
4. The content script renders a local translated image or cloud bounding-box
   overlays on the source element.
5. Popup/options pages persist user settings and send page commands.

Phase 1 inserts queueing between cache-key preparation and provider execution,
and adds hostname activation around content-script startup without changing the
rendering contract.
