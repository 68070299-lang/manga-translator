# Runtime Contract

This document describes the current extension/backend behavior from source and tests. Update it when changing runtime entrypoints, data flow, settings, providers, cache identity, or backend APIs.

## Entrypoints And Bundles

The extension is built with WXT 0.21 and strict TypeScript from `extension/` (WXT `srcDir`) into `dist/chrome-mv3`, which is what Chrome loads unpacked. There is no hand-written manifest, no `importScripts`, and no load order to maintain: each entrypoint is one Vite bundle that imports what it needs. `wxt.config.ts` is the manifest source of truth (name, permissions, host permissions, DNR rule resources, icons, action); WXT adds the entries below from the entrypoint definitions.

| Entrypoint | Source | Built output | Manifest entry |
| --- | --- | --- | --- |
| Service worker | extension/entrypoints/background.ts | background.js | `background.service_worker` |
| Content script | extension/entrypoints/content/index.ts (+ style.css) | content-scripts/content.js, content-scripts/content.css | `content_scripts` |
| Popup | extension/entrypoints/popup/ (index.html, main.ts, App.svelte) | popup.html | `action.default_popup` |
| Options page | extension/entrypoints/options/ (index.html, main.ts, App.svelte) | options.html | `options_ui` (`open_in_tab: true`) |

- Service worker: `defineBackground` calls `registerBackground()` (extension/background/index.ts), which registers every listener synchronously (runtime messages, install, startup, action state, context menus, activate) before any async startup work. The worker then re-runs DNR reconciliation, settings migration, action-state refresh, and cache pruning on every start. Handlers live in extension/background/*.ts, and message-router.ts dispatches the request messages.
- Content script: matches `<all_urls>` at `document_idle`, top frame only (no allFrames). It boots extension/content/main.ts, which registers message and storage listeners, reads the per-hostname activation state, and only loads settings and starts the runtime when the site is enabled. The whole content script, including the shared libraries it imports (extension/lib/*.ts), is a single bundled file; there is no separate script list.
- Popup and options pages: Svelte 5 (runes) apps. Components are in extension/components/{popup,options}/ and extension/components/Toast.svelte; state modules are in extension/ui/ (`*.svelte.ts` hold reactive state). The popup opens the options page with `runtime.openOptionsPage()`.
- Shared code: extension/lib/*.ts (settings, api, cache, translation queue, site activation, referrer rules, overlay DOM, translated image, font registry, region capture, utils) is imported by the worker, content script, and pages as needed. The typed message contracts are in extension/shared/messages.ts and shared types in extension/shared/types.ts.
- Static assets: extension/public/{icons,fonts,rules} is copied verbatim into the build root (icons/, fonts/, rules/). Icon sources (SVG and the generator page) are in extension/assets/icons and are not shipped.

Every `wxt build` and `wxt zip` runs scripts/validate-extension.ts from the `build:done` hook in `wxt.config.ts` (skipped in dev mode, where WXT injects extra permissions and HMR code), so an invalid build never reaches a zip. `npm run validate` re-checks an existing build. Adding a new bundled asset (a font, a rule file) means putting it under extension/public/ and updating the registry or manifest entry that the validator checks.

### Permissions

The manifest requests `storage`, `unlimitedStorage`, `contextMenus`, and `declarativeNetRequest`, plus host permission `<all_urls>`. The `tabs`, `scripting`, and `activeTab` permissions are not requested; tests/unit/manifest.test.ts asserts this permission budget (on the wxt.config.ts manifest, and on dist/chrome-mv3/manifest.json when a build exists).

- storage - settings, API keys, site activation, translation cache, and per-session progress.
- unlimitedStorage - lets the translation cache hold rendered page images.
- contextMenus - right-click translate, stop, and clear actions.
- declarativeNetRequest - Referer/CORS rewriting described in the next section.
- `<all_urls>` - content scripts run on every page and the worker fetches image URLs from arbitrary hosts. It also grants `chrome.tabs.captureVisibleTab` for region translation, so no extra capture permission is requested.

### Page Overlays

Everything the extension draws on a page lives in an open shadow root, so page CSS cannot restyle it and overlay images do not appear in page-level `img` queries. extension/lib/overlay-dom.ts owns the hosts and the stylesheet.

- Each overlay has its own `<manga-overlay>` host instead of one shared page root, because translated images and text must stay inside the page's positioned containers to scroll and clip with their content.
- Hosts keep the class names and ids used to find overlays (`.manga-translated-img-overlay`, `.manga-composite-overlay`, `.manga-canvas-overlay`, `.manga-translate-overlay`, `.manga-image-progress`, `#manga-reader-controls`, `#manga-global-progress`, `#manga-error-banner`). Placement, display, and dataset are on the host; the `img` or `svg` is inside `host.shadowRoot`.
- The host's inline `all: initial` blocks inherited and non-important page rules. A page rule using `!important` on the `manga-overlay` element itself can still affect the host box, but not its shadow content.
- extension/entrypoints/content/style.css (built to content-scripts/content.css) keeps only the light-DOM `.manga-translate-wrapper` rules.

### Bundled Fonts

extension/lib/font-registry.ts lists the fonts shipped in extension/public/fonts/ (built to fonts/ in dist/chrome-mv3), each pinned by sha256. scripts/validate-extension.ts fails the build on a missing file or checksum mismatch; the manifest does not reference the font files, so that check is what keeps them in the package.

- The font setting selects a bundled font by its `cssValue`. Other options depend on fonts installed on the system.
- On first use in a page, the content script requests the bytes with GET_BUNDLED_FONT and registers them with the FontFace API. Page CSP does not apply to font data supplied this way, and fonts are not web-accessible, so sites cannot detect the extension through them. The registered family is visible to the page's `document.fonts`.
- Text falls back to the next family if loading fails, and loading is retried on the next render.
- The font setting only affects text drawn by the browser (cloud pipeline overlays). In local pipeline mode the backend renders text into the image with its own font.

### Region Translation

Region translation is started from the reader control, the page or image context menu, or the popup. It runs in the top frame only.

1. The content script shows a full-viewport selector. Nothing is captured until a drag of at least 16x16 CSS pixels finishes; Esc cancels.
2. The selector and extension UI are hidden for two frames, then CAPTURE_REGION sends the rectangle and viewport size to the worker.
3. The worker requires an active sender tab, screenshots it, crops with OffscreenCanvas, and returns only the crop. The full screenshot is never stored or sent. The scale comes from screenshot size divided by viewport size, not devicePixelRatio, so browser zoom is handled.
4. The content script places the crop in `.manga-region-translation` over the selected area and sends it through the normal translation path. The crop reaches the same destinations as any page image (see "Data leaving the browser").

Limits: region results are positioned against the document, so they follow document scroll but not inner scroll containers. Translated overlays already inside the selection are captured as displayed. Clearing page translations removes region results.

### Referrer And CORS Rules (DNR)

- Static ruleset: manifest `declarative_net_request.rule_resources` enables `bookwalker_cors` (extension/public/rules/bookwalker-cors.json, built to rules/bookwalker-cors.json). For GET XHR from viewer.bookwalker.jp to bw-bv-epubs.bookwalker.jp it sets the Referer request header and the Access-Control-Allow-Origin response header.
- Session rules: while FETCH_IMAGE fetches a URL on a fixed CDN allowlist (pximg.net, i.hamreus.com, hitomi.la, klimv1.xyz; extension/lib/referrer-rules.ts), the worker installs a transient `modifyHeaders` session rule that sets Referer for XHR requests to that one host. Rule ids are limited to 1000-1999, installs and removals are serialized, concurrent fetches share one reference-counted rule, and leftover rules from an earlier worker are reconciled on worker load, install, and first use. Hosts outside the allowlist get an ordinary fetch with no rule.
- The allowlist is fixed in code. Nothing in settings or page content can add a host.

## Message Contracts

### Requests handled by the service worker

The service worker handles these request messages (typed in extension/shared/messages.ts, dispatched by extension/background/message-router.ts) and replies through sendResponse:

- TRANSLATE_IMAGE - content script sends imageId, base64, settings, optional canvasSignature, and optional forceRefresh. Base64 payloads over 20 MB decoded or images over 25 MP are rejected.
- FETCH_IMAGE - fetches an http(s) image URL through the extension context. Responses over 20 MB, empty bodies, and clearly non-image content types (octet-stream is allowed) are rejected.
- CLEAR_CACHE - clears stored translation cache.
- GET_CACHE_STATS - returns cache count and size information.
- CHECK_BACKEND - checks the configured local backend health endpoint.
- CAPTURE_REGION - top-frame content script sends `rect` and `viewport` in CSS pixels; returns PNG `base64`, `width`, and `height` for the crop.
- GET_BUNDLED_FONT - content script sends a registered `fontId`; returns the font file as `base64`. Only registry entries can be read.

Any other message type is ignored by the service worker (its listener returns false). The worker never handles the broadcast messages below; they are addressed to tabs and the popup.

### Cross-context messages

| Message | Direction | Payload and use |
| --- | --- | --- |
| TRANSLATE_PAGE | popup or worker context menu -> content | Translate all images on the page |
| TRANSLATE_VISIBLE_IMAGES | popup or worker context menu -> content | Translate images currently in view |
| TRANSLATE_IMAGE_BY_SRC | worker context menu -> content | `src`: translate the right-clicked image |
| START_REGION_SELECTION | popup or worker context menu -> content | Start the drag selector for region translation |
| CLEAR_PAGE_TRANSLATIONS | popup or worker context menu -> content | Remove translated overlays on the page |
| STOP_TRANSLATION | popup or worker context menu -> content | Cancel the running page translation |
| SITE_ACTIVATION_CHANGED | popup -> content | Re-read per-hostname activation state |
| SETTINGS_UPDATED | popup -> content | `settings`: public (synced) settings only, no keys |
| TRANSLATION_PROGRESS | worker or content -> popup | `status`, `percent` |
| TRANSLATION_ERROR | worker -> tab, content -> popup | `error`, and `imageId` from the worker |
| TRANSLATION_COMPLETE | content -> popup | `status`, `total`, `succeeded`, `failed` after a translate run; no payload after a clear |
| TRANSLATION_STOPPED | content -> popup | `status`, `total`, `succeeded`, `failed` |

TRANSLATION_RESULT no longer exists. The popup mirrors progress into chrome.storage.session under `translationProgress` so it can restore the display when reopened.

## Settings And Keys

extension/lib/settings.ts owns defaults and storage shaping.

- Public synced settings include provider, model, target language, local backend options, render options, and local LLM endpoint/model metadata.
- Private key material is split out before sync storage: provider apiKey, googleOcrApiKey, localLlmApiKey, and backendAuthToken (localBackend.authToken, sent as the backend access token).
- Local backend base URLs must be loopback HTTP origins: localhost, 127.0.0.1, or ::1.
- In cloud pipeline mode, provider selects the external OCR/translation provider.
- In local pipeline mode, localBackend.translator wins when present; otherwise provider is used for backend translation.

### Storage map

| Area | Key | Contents |
| --- | --- | --- |
| chrome.storage.sync | `settings` | Public settings only (never keys or the backend token) |
| chrome.storage.local | `apiKeys` | `apiKey`, `googleOcrApiKey`, `localLlmApiKey`, `backendAuthToken` |
| chrome.storage.local | `activeUrls`, `disabledUrls` | Per-hostname site activation lists |
| chrome.storage.local | `manga_cache_*` | Translation cache entries; `manga_cache_metadata` lists the keys |
| chrome.storage.session | `translationProgress` | In-flight progress shown by the popup |

### Defaults

From DEFAULT_SETTINGS in extension/lib/settings.ts:

| Setting | Default |
| --- | --- |
| settingsVersion | 2 |
| pipelineMode | local |
| provider | openrouter |
| model, baseUrl | empty |
| targetLang | English |
| fontFamily | 'CC Wild Words', sans-serif (bundled; existing stored values are kept) |
| fontSize | 16 |
| autoTranslate | false |
| localBackend.url | http://localhost:8001 |
| localBackend.detector | default |
| localBackend.ocr | manga-ocr |
| localBackend.inpainter | flux2_klein_lanpaint |
| localBackend.translator | empty (falls back to provider) |
| localBackend.requestTimeoutMs | 600000 |
| googleOcr.languageHint | empty |
| localLlm.baseUrl, localLlm.model | empty |
| render | center alignment, auto direction, line spacing 1.2, black text, white border 2, auto-scale on |

mergeSettings normalizes detector (default, rtdetr, animetext-yolo) and inpainter (flux2_klein_lanpaint, flux2_klein_q4, lama_large, none; the legacy value lama maps to lama_large; unknown values fall back to the default).

### Migration

SETTINGS_VERSION is 2. migrateStoredSettings upgrades stored synced settings, and the service worker applies it at install, browser startup, and worker load:

- Version below 1: localBackend.inpainter `lama_large` becomes `flux2_klein_lanpaint`.
- Version below 2: fontSize 14 (the old implicit default) or missing becomes 16; custom sizes are kept.
- Independently of the version, API keys and the backend token that an older version synced into chrome.storage.sync are moved to chrome.storage.local `apiKeys` by the worker. A non-empty local value always wins over the legacy synced one.

### Backend authentication

LocalBackendProvider (extension/lib/api.ts) sends the backend access token as the `X-Manga-Translator-Token` header on /health and /translate requests. The backend reads BACKEND_AUTH_TOKEN from its environment and rejects every request except GET / and CORS preflights without a matching token: 401 when the header is missing or wrong, 503 when the backend has no token configured. GET /health is protected too.

## Data Flow

The content script captures image or canvas data and sends base64 image data to the service worker. The service worker normalizes settings, computes cache identity, and chooses a provider path.

Cloud path:

1. TranslationProvider receives image base64, provider, API key, model, and optional base URL.
2. The image is sent to Gemini, OpenAI, OpenRouter, or a custom OpenAI-compatible endpoint.
3. The provider response is parsed into translated text blocks.
4. The content script renders overlays from returned blocks.

Local path:

1. LocalBackendProvider validates the backend URL and builds a multipart/form-data request with the X-Manga-Translator-Token header.
2. /translate receives the image and form fields for target language, detector, OCR, translator, inpainter, render settings, Google OCR hints, and local LLM settings. Provider keys (api_key, google_ocr_api_key, local_llm_api_key) travel in the same form to the loopback backend.
3. The backend validates image type, upload size, pixel count, option names, colors, and render values before running the pipeline.
4. The backend returns a rendered translated image blob when render=true.

### Scheduling And Limits

- The worker has two translation queues: local pipeline mode runs one job at a time, cloud mode runs up to three.
- Jobs with the same cache key are deduplicated across tabs: later requests subscribe to the in-flight job and share its result. forceRefresh bypasses the cache and dedup.
- TRANSLATE_IMAGE rejects payloads over 20 MB decoded or 25 megapixels; FETCH_IMAGE rejects bodies over 20 MB and non-image content types.
- The backend additionally queues expensive routes behind BACKEND_MAX_CONCURRENT (default 2) and answers 429 only after the bounded queue wait.

### Site Activation

Activation is per hostname (extension/lib/site-activation.ts). An empty `activeUrls` list means every http(s) host is enabled; a hostname in `disabledUrls` is always disabled. The popup toggles the current host, and the worker shows an OFF badge on disabled tabs.

## Backend API

Primary endpoints, defined in backend/src/manga_backend/api/routes/ (health.py, translate.py, detect.py, ocr.py) and assembled by `create_app` in api/app.py (`manga_backend.main:app` is the ASGI entry point; request and response models are Pydantic v2 schemas in api/schemas.py):

- GET / - basic alive response (no token required).
- GET /health - detailed status, sanitized config, request counters, and last sanitized error. Status is `ok`, `degraded`, or `loading` while models initialize in the background.
- POST /translate - full image translation pipeline.
- POST /detect - detection-only path.
- POST /ocr - detection plus OCR path.
- POST /paddle-ocr - PaddleOCR detection plus recognition in one call (form fields `image` and `lang`).

Response shapes: /translate with `render=true` returns `image/png` with `X-Inpainter-Used` (the inpainter that actually ran, which can differ from the requested one) and `X-Request-ID`; with `render=false` it returns JSON `{blocks, original_texts, translated_texts}`. /detect returns `{blocks}`, /ocr returns `{texts}`, and /paddle-ocr returns `{blocks, total}`. Every response carries `X-Request-ID`. Common statuses: 400 invalid field or image, 401 bad token, 403 disallowed browser origin, 413 oversize upload, 429 all concurrency slots busy (with `Retry-After`), 502/504 upstream provider failure or timeout, 503 auth not configured or a model/inpainter component unavailable. See backend/README.md for the full table.

The backend accepts PNG, JPEG, and WebP uploads up to the configured size and pixel limits. Unsupported provider, detector, OCR, inpainter, render, and color values are rejected before model execution.

GET / only proves the process is alive. It does not prove all ML models have downloaded or initialized. First model use may download files into backend/models_cache/.

## Cache Contract

extension/lib/cache.ts stores translation results in chrome.storage.local.

- Cache keys start with manga_cache_. Cache metadata is maintained under manga_cache_metadata.
- Cache identity includes image hash or canvas signature plus every setting that affects output: pipeline mode, provider/model/base URL, target language, fontSize (clamped to 6-96), the local backend URL, detector, OCR, translator, and inpainter (normalized, so `lama` and `lama_large` share an entry), local LLM endpoint/model, Google OCR language hint, and render settings.
- API keys and the backend token are intentionally excluded from cache identity and storage metadata.
- Entries older than seven days are dropped when read (getFromCache) and are also pruned in the background at worker load, browser startup, and install (pruneExpiredCache).
- Total size is bounded to about 15 MB (five pages of 2 MB, base64 expanded, plus 2 MB headroom). Saving past the bound evicts least-recently-used entries first.
- A cache hit updates lastAccessed at most once every five minutes, because each update rewrites the whole entry. Size checks use storage.getBytesInUse and key listing uses storage.getKeys, so they do not load stored images; Chrome below 130 falls back to a full read for key listing.
- Results marked inpainterFallback are not cached.

## Provider And Model Precedence

Direct cloud providers are selected by provider and support Gemini, OpenAI, OpenRouter, or a custom OpenAI-compatible base URL.

Local backend translator selection is:

1. Explicit localBackend.translator when set.
2. Extension provider setting (default `openrouter`, so the extension never submits an empty translator).

Without an explicit translator and without a key, a local-mode request therefore asks for `openrouter` and the backend answers 400 unless it has `OPENROUTER_API_KEY` in its environment. Choose Translator "None (OCR only)" in the options to run the pipeline without any key (OCR only, no translation). The backend itself normalizes an empty translator to none.

Keyless backend smoke tests should use translator=none and can disable inpainting with inpainter=none.

## Data Leaving The Browser

Page images and OCR text are sensitive. Only the first row keeps everything on loopback.

| Mode | OCR | Translator | What is sent, to whom | Key used |
| --- | --- | --- | --- | --- |
| local | manga-ocr / paddle-ocr | none | Nothing leaves loopback: image goes to the local backend only | Backend token only |
| local | manga-ocr / paddle-ocr | gemini, openai, openrouter | Image goes to the loopback backend; the backend sends OCR text (not the image) to the provider | Provider key is sent to the loopback backend, which uses it upstream |
| local | manga-ocr / paddle-ocr | local | Image to the loopback backend; OCR text to the configured local LLM base URL, which must be loopback unless the backend sets ALLOW_REMOTE_LOCAL_LLM=true | Optional local LLM key |
| local | google-ocr | any of the above | Image crops go from the backend to Google Cloud Vision, plus the translator rows above | Google OCR key is sent to the loopback backend; plus any translator key |
| cloud | provider handles OCR | gemini, openai, openrouter, or custom base URL | The full page image goes straight from the extension to the provider | Provider key |

Choosing a cloud pipeline or Google OCR and saving the matching key is the user's opt-in to this data sharing. There is no separate consent prompt today (pending owner decision). The local inpainters (FLUX Klein + LanPaint via a loopback ComfyUI daemon, FLUX Klein Q4 via sd-cli, LaMa) run locally and send nothing out.

## Expected First-Run Failures

- 503 `Backend authentication is not configured` - BACKEND_AUTH_TOKEN is not set in backend/.env.
- 401 `Invalid backend authentication token` - the extension's Backend Access Token differs from BACKEND_AUTH_TOKEN.
- 503 `Backend component unavailable` - models are still loading or a component failed to load; retry once /health no longer reports `loading`.
- /health reports `loading` while the detector, manga-ocr and LaMa download and initialize in the background; the first run can take a long time, and with the models already present it takes about 25 s on an RTX 4050. During that phase the backend may not answer at all for several seconds at a time, so the popup can briefly show it as offline. PaddleOCR is not part of that wait: it loads on the first `paddle-ocr` request, which then takes longer (about 170 MB of models on a first run), and a failed load is reported under `models.paddle_ocr` and returns 503 only for requests that use PaddleOCR.
- 503 `PaddleOCR is unavailable` - the PaddleOCR models failed to download or load (the first `paddle-ocr` request fetches about 170 MB from Hugging Face); the cause is under `models.paddle_ocr` in /health, and the next paddle-ocr request retries.
- Detector `animetext-yolo` fails when the Hugging Face model repo is gated and unauthenticated; authenticate Hugging Face or set ANIMETEXT_YOLO_MODEL to a local ONNX file (see backend/README.md).
- Inpainter `flux2_klein_lanpaint` needs the ComfyUI daemon on 127.0.0.1:8188 (see docs/headless-lanpaint.md). When it is not running, or stops answering mid-session, the backend inpaints with LaMa, reports `X-Inpainter-Used: lama_large`, and probes ComfyUI again after 60 seconds; the extension shows a notice and does not cache that result. Pick LaMa or None in the options to make the choice permanent and cacheable. With `INPAINTER_FALLBACK=off` on the backend the request fails with 503 instead.
- Inpainter `flux2_klein_q4` needs the sd-cli executable (SD_CPP_CLI or models_cache/flux2_klein_q4/sd-cli.exe).
- Cannot connect to the local backend - the server is not running on the configured loopback URL.

## Security And Privacy Notes

- Host permissions and content scripts cover all URLs, so page image data must be treated as sensitive.
- Cloud mode sends image content to an external provider.
- Local backend mode keeps the image on loopback when the configured URL passes validation, but a key-backed translator or Google OCR sends OCR text or image crops to an external service (see the matrix above).
- The backend requires the X-Manga-Translator-Token header on all routes except GET / and preflights.
- Backend CORS allows Chrome extension origins and any explicitly configured development origins.
- Backend error handling redacts known key fields and configured secret values from stored last-error state; the extension redacts keys and bearer tokens in provider error text.
- declarativeNetRequest rules only rewrite headers for the static BookWalker ruleset and the fixed CDN allowlist above.
- Model downloads support optional pinned revisions and sha256 checks (`MODEL_PINS` in backend/src/manga_backend/utils/download.py). The default models are pinned to a commit and sha256; the gated AnimeText YOLO repo is not, so it tracks the latest revision.
- The backend should remain loopback-first unless an authenticated deployment boundary is added.

## Verification Commands

Run targeted gates before the full suite:

- rtk npm run verify:repository
- rtk npm run build (runs the build validation)
- rtk npm run lint (Biome)
- rtk npm run typecheck (`tsc` and `svelte-check`)
- rtk npm run test (Vitest: unit and integration)
- rtk npm run lint:backend
- rtk npm run typecheck:backend
- rtk npm run test:backend
- rtk npm run test:e2e
- rtk npm run verify
