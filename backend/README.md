# Manga Translation API Backend

Local FastAPI backend for the Manga Translator Chrome extension.

The backend is designed for loopback, single-user use. It accepts manga images from the extension, validates request options, runs detection/OCR/translation/inpainting/rendering, and returns either JSON metadata or a rendered translated image.

The default inpainter (`flux2_klein_lanpaint`) needs the headless Flux.2 Klein + LanPaint
ComfyUI daemon on `127.0.0.1:8188`; see [`../docs/headless-lanpaint.md`](../docs/headless-lanpaint.md).
When LanPaint cannot start (the daemon is not running, or a Flux model file or the LanPaint node is
missing; the backend log names the cause), or the daemon stops answering mid-session (closed, crashed, or no
reply within 60 seconds), the page is inpainted with LaMa instead and the response says so in
`X-Inpainter-Used: lama_large`; the daemon is probed again after 60 seconds. The first page after the daemon
goes away can take about 2 seconds longer on Windows while the refused connection times out. The extension
does not cache such fallback results, so select `lama_large` (or the `lama` alias) or `none` as the
inpainter if you run without ComfyUI for good. Set `INPAINTER_FALLBACK=off` to get a 503 with a short setup
hint (no local paths) instead of the fallback; if LaMa itself cannot load, the 503 carries the same ComfyUI
hint. A failed ComfyUI job, its 1200-second job timeout, or an HTTP error from ComfyUI still fails the
request (500). With `COMFY_LANPAINT_FALLBACK_Q4=1`, the Q4 runner takes over before LaMa does. Other
unavailable models always return the 503.

## Features

- Text detection with RT-DETR-v2 (default) or optional AnimeText YOLO12.
- OCR with manga-ocr, Paddle OCR, or Google OCR when configured.
- Inpainting with one of four options (see [Inpainters](#inpainters)): `flux2_klein_lanpaint` (default, needs ComfyUI), `lama_large`, `flux2_klein_q4` (optional), or `none`.
- Translation with none, Gemini, OpenAI, OpenRouter, or local OpenAI-compatible LLM backends.
- ONNX Runtime based local inference paths.

## Install

Install uv once on the machine, then from backend/ sync exactly one runtime group
(the groups conflict, so they cannot be combined; `cpu` is the default group):

```powershell
uv sync --no-default-groups --group cpu --python 3.12
uv sync --no-default-groups --group gpu --python 3.12        # NVIDIA CUDA
uv sync --no-default-groups --group directml --python 3.12   # Windows DirectML
```

Plain `uv sync` creates `backend/.venv`. `start.ps1` (below) keeps one environment per runtime
instead: `backend/.venv-cpu`, `backend/.venv-gpu`, or `backend/.venv-directml`.

Before starting, set a long random `BACKEND_AUTH_TOKEN` in `backend/.env` and
enter the same value in the extension's **Backend Access Token** setting. The
backend rejects protected requests without this token.

Generate one with `uv run python -c "import secrets; print(secrets.token_urlsafe(32))"`.

Set provider keys only when using a key-backed translator or OCR provider. Keyless smoke tests should use translator=none.

`pyproject.toml` and `uv.lock` are the primary dependency source. `requirements.txt` is an
unlocked, unpinned fallback for plain `pip` use; it does not pick a GPU runtime for you
(its GPU lines are commented out) and can drift from `pyproject.toml`.

## Start (recommended)

From the repository root:

```powershell
powershell -File backend/start.ps1 [-Runtime gpu|cpu|directml] [-Port 8001] [-LanPaintProfile fast|balanced|quality]
```

Defaults are `-Runtime gpu`, `-Port 8001`, and `-LanPaintProfile fast`. The script syncs
`backend/.venv-<runtime>` from `uv.lock` (`uv sync --locked --no-default-groups --group <runtime>`),
sets `USE_GPU` (false for `cpu`, true otherwise) and `COMFY_LANPAINT_PROFILE` for the run, and starts
uvicorn on `127.0.0.1`. Exit codes:

| Code | Meaning |
| ---: | --- |
| 1 | `uv` is not installed |
| 2 | `BACKEND_AUTH_TOKEN` is not set in the environment or `backend/.env` |
| 3 | The port is already in use |
| 4 | `-Runtime gpu` was requested but ONNX Runtime has no `CUDAExecutionProvider` |

Any other non-zero code comes from `uv sync` or uvicorn.

## Start (manual)

From backend/, after syncing the group you want:

```powershell
uv run --no-sync uvicorn manga_backend.main:app --host 127.0.0.1 --port 8001
```

`manga_backend.main:app` is a thin ASGI entry point: it prepares the process environment (`backend/.env`, CUDA DLL paths) and builds the app with `create_app()`. `uv run python -m manga_backend` does the same through uvicorn, taking its bind address from `BACKEND_HOST` and `BACKEND_PORT` (or `--host` / `--port`).

Pass `--no-sync` after syncing a non-default group, as `start.ps1` does. `USE_GPU=true` must also be set
for a GPU run; without it the backend picks the CPU device.

The backend should remain bound to loopback by default. Exposing it beyond loopback requires a separate authenticated deployment boundary; browser-origin checks are not API authentication.

The backend is single-process only: it keeps models, locks, counters, and `/health` state in process
globals. Never run it with `--workers` or as multiple instances.

## Endpoints

- GET / - server process alive check (liveness).
- GET /health - readiness, detailed runtime state, sanitized config, counters, and last sanitized error.
- POST /translate - full image translation pipeline.
- POST /detect - text detection only.
- POST /ocr - detection plus OCR.
- POST /paddle-ocr - PaddleOCR detection plus recognition in one call. Form fields: `image` and `lang` (`japan` default, `ch`, `en`, or `korean`). Returns `{blocks, total}`.

## Authentication

Every route except `GET /` (and CORS `OPTIONS` preflights) requires the `X-Manga-Translator-Token`
header to equal `BACKEND_AUTH_TOKEN`. That includes `GET /health`.

- 401: the header is missing or does not match.
- 503: the backend has no `BACKEND_AUTH_TOKEN` configured, so every protected route is refused.
- 403: a browser `Origin` that is neither `chrome-extension://<32 letters>` nor listed in `BACKEND_ALLOWED_ORIGINS`.

The interactive docs (`/docs`, `/redoc`, `/openapi.json`) are disabled.

`MANGA_TRANSLATOR_TESTING=1` is for the automated tests only: it turns the token check off and stops
`backend/.env` from loading. Never set it on a backend you actually use. `start.ps1` refuses to start (exit
code 5) if the variable is set in the shell, and the backend logs a WARNING at startup when it is on.

## Startup and health

`GET /` is liveness only. `GET /health` is readiness: the port opens before the models are ready, and the
models load in the background.

- Loaded at startup: RT-DETR detector, manga-ocr, then LaMa. LaMa is loaded because it is the bundled
  inpainter and the stand-in for LanPaint, and it takes 10-20 s to load; with `INPAINTER_FALLBACK=off` and
  the default LanPaint inpainter it is not loaded. A LaMa load failure at startup only logs, and the first
  request that needs it tries again. Missing model files are downloaded into backend/models_cache/ during
  this load.
- Loaded lazily, on first use: PaddleOCR (the first `ocr=paddle-ocr` request or `/paddle-ocr` call loads its
  two sessions, and downloads about 170 MB of models if they are missing; concurrent first requests share one load, and if that load fails each waiting request tries it again before its 503), the
  LanPaint ComfyUI connection and the Q4 inpainter, the AnimeText YOLO detector, and the translators.

While the startup models load, `/health` reports status `loading` and the model routes return 503
("models are still loading"). Creating an ONNX session holds Python's interpreter lock, so the server may not
answer at all for several seconds at a time during this phase (about 25 s in total on an RTX 4050 with the
models already downloaded). `ok` means the detector and manga-ocr are loaded; `degraded` means loading
finished but one of them is unavailable. PaddleOCR never delays or fails startup: `models.paddle_ocr` is
`not_loaded` (and `components.paddle_ocr` is `true`, meaning available on demand) until it is first used,
then `loaded`. If loading it fails (for example the download does not finish) the status is
`error` with the message and `components.paddle_ocr` is `false`; only requests that use PaddleOCR get a 503
and they retry the load, so the default manga-ocr path is unaffected. Use `GET /health` to wait for
readiness.

## Request limits

- BACKEND_MAX_CONCURRENT (default 2) caps simultaneous /translate, /detect, /ocr, and /paddle-ocr requests. Extra requests wait for a slot for up to BACKEND_QUEUE_TIMEOUT_SECONDS (default 600), then get 429 with a `Retry-After: 10` header.
- Uploads must be PNG, JPEG, or WebP, at most 20 MiB and 25,000,000 pixels.
- A request whose Content-Length is above the upload limit plus 1 MiB of multipart overhead gets 413 before the body is parsed.
- /translate rejects missing translator credentials and invalid field values with 400 before any model runs.

## Translate Form Fields

POST /translate accepts multipart/form-data with an `image` file and these fields (defaults in parentheses).
Request fields override the matching environment variable.

- target_lang (`English`): at most 64 characters, no control characters.
- detector (`rtdetr`): `rtdetr` (alias `default`) or `animetext-yolo` (aliases `animetext`, `animetext_yolo`, `anime-text-yolo`).
- ocr (`manga-ocr`): `manga-ocr`, `paddle-ocr`, or `google-ocr` (common aliases are accepted).
- translator (`none`): `none`, `gemini`, `openai`, `openrouter`, or `local`.
- inpainter (`flux2_klein_lanpaint`): `flux2_klein_lanpaint`, `lama_large`, `flux2_klein_q4`, or `none`. Legacy `lama` normalizes to `lama_large`; an empty value means `none`.
- render (`true`): `true` returns a rendered PNG; `false` returns JSON text only.
- api_key (empty) and model (empty): credentials and model name for gemini/openai/openrouter, and the model name for local. They override the `*_API_KEY` and `*_MODEL` environment variables. `model` is limited to 128 characters of letters, digits, and `. _ : / @ + -`.
- google_ocr_api_key (empty): required with `ocr=google-ocr`.
- google_ocr_language_hint (empty): short language code such as `ja` or `zh-Hant`.
- local_llm_base_url (empty) and local_llm_api_key (empty): used with `translator=local`; the base URL falls back to `LOCAL_LLM_BASE_URL`.
- line_spacing (`1.2`, 0.5 to 3.0), border_size (`2.0`, 0 to 20), bg_opacity (`0.0`, 0 to 1).
- text_color (`#000000`), border_color (`#ffffff`), bg_color (`#ffffff`): `#RRGGBB` hex.
- text_alignment (`center`): `left`, `center`, or `right`.
- text_direction (`auto`): `auto`, `horizontal`, or `vertical`.
- uppercase (`false`) and auto_scale (`true`).
- font_size (`16`, 6 to 96): preferred maximum font size; auto-scale selects the largest size that fits.
- verify_translation (`false`): re-OCR the rendered text to check that the translation took.

Unsupported detector, OCR, translator, inpainter, render, image type, image size, and color values are rejected before model work starts.

## Inpainters

| Value | Notes |
| --- | --- |
| `flux2_klein_lanpaint` (default) | Cropped Flux.2 Klein + LanPaint through the ComfyUI daemon at `COMFYUI_URL`. Aliases: `flux2-klein-lanpaint`, `flux_lanpaint`, `lanpaint`. Tuning and the default LaMa structural prefill are in [`../docs/headless-lanpaint.md`](../docs/headless-lanpaint.md). |
| `lama_large` | LaMa ONNX model, downloaded if missing. Alias: `lama`. Runs without ComfyUI. Only masked pixels change. A page larger than `LAMA_MAX_PIXELS` (derived from GPU memory on a GPU, unset on CPU) is inpainted as equal tiles that overlap by 192 px (tiles without text are skipped), and with CUDA the GPU memory used for a page is released after it; see [LaMa and GPU memory](#lama-and-gpu-memory). |
| `flux2_klein_q4` | Optional Q4_K_M GGUF run through a local `sd-cli.exe` (stable-diffusion.cpp). Aliases: `flux2-klein-q4`, `flux_klein_q4`, `q4`. See below. |
| `none` | No inpainting. |

`flux2_klein_q4` needs three files that this repository does not ship. `SD_CPP_CLI` points at `sd-cli.exe`
(default `backend/models_cache/flux2_klein_q4/sd-cli.exe`). `SD_CPP_MODELS_DIR` is the folder that holds
`qwen3-4b-Q4_K_M.gguf` and `flux2-vae.safetensors` (default `backend/models_cache/flux2_klein_q4`). The
diffusion GGUF `flux-2-klein-4b-Q4_K_M.gguf` is always read from `backend/models_cache/flux2_klein_q4`.
`SD_CPP_TIMEOUT_SECONDS` (default 120) bounds each sd-cli run. A missing file gives a 503.

## Responses and errors

POST /translate:

- `render=true`: `200 image/png` with `X-Inpainter-Used` (the inpainter that actually ran, which can differ from the requested one, for example when ComfyUI is not running and LaMa stands in, or when LanPaint skips an oversized page or falls back to Q4) and `X-Request-ID` headers.
- `render=false`: `200` JSON `{"blocks": [...], "original_texts": [...], "translated_texts": [...]}`; the three arrays always have the same length.

POST /detect returns `{"blocks": [{"bbox": ..., "type": ...}]}`. POST /ocr returns `{"texts": [...]}`.
POST /paddle-ocr returns `{"blocks": [...], "total": N}`.

Every response carries an `X-Request-ID` header (the request's own header value is reused when sent).

| Status | Meaning |
| ---: | --- |
| 400 | Invalid field, image type, or image data; missing `api_key` or `google_ocr_api_key`; a local LLM URL that is not loopback (unless `ALLOW_REMOTE_LOCAL_LLM=true`) |
| 401 | Missing or wrong `X-Manga-Translator-Token` |
| 403 | Browser origin not allowed |
| 413 | Request body, file, or image dimensions too large |
| 429 | All concurrency slots stayed busy for `BACKEND_QUEUE_TIMEOUT_SECONDS`; retry later |
| 500 | Unexpected internal error (details are logged, not returned) |
| 502 | Google OCR or the translation provider failed upstream |
| 503 | Backend auth not configured; models still loading or a needed component unavailable; the selected inpainter or detector runtime is unavailable (the detail carries a short setup hint) |
| 504 | Google OCR or the translation provider timed out |

## Environment variables

Set these in the process environment or `backend/.env`. Request fields (`api_key`, `model`, and so on) take
precedence over the matching variable.

| Variable | Default | Purpose |
| --- | --- | --- |
| `BACKEND_AUTH_TOKEN` | none (required) | Shared secret for the `X-Manga-Translator-Token` header |
| `USE_GPU` | `false` | `true` picks the best available ONNX provider; `start.ps1` sets it from `-Runtime` |
| `BACKEND_HOST`, `BACKEND_PORT` | `127.0.0.1`, `8001` | Bind address used only by `python -m manga_backend`; `start.ps1` and the uvicorn command above pass `--host` and `--port` themselves |
| `BACKEND_ALLOWED_ORIGINS` | empty | Comma-separated extra browser origins (extension origins are always allowed) |
| `BACKEND_MAX_CONCURRENT` | `2` | Concurrent expensive requests |
| `BACKEND_QUEUE_TIMEOUT_SECONDS` | `600` | How long a request waits for a slot before 429 |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | none, `gemini-2.0-flash` | Gemini translator |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | none, `gpt-4o-mini` | OpenAI translator |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | none, `google/gemini-2.0-flash-001` | OpenRouter translator |
| `LOCAL_LLM_BASE_URL` | `http://localhost:8000/v1` | Local OpenAI-compatible endpoint |
| `LOCAL_LLM_MODEL`, `LOCAL_LLM_API_KEY` | empty, `not-needed` | Local model name and key |
| `ALLOW_REMOTE_LOCAL_LLM` | `false` | Allow a non-loopback `LOCAL_LLM_BASE_URL` / `local_llm_base_url` |
| `MANGA_OCR_BATCH_SIZE` | 16 on CUDA/TensorRT/DirectML, else 4 | manga-ocr batch size |
| `ORT_INTRA_OP_THREADS` | CPU: cores/2 (max 8); GPU: 1 | ONNX Runtime intra-op threads |
| `ORT_CUDNN_CONV_ALGO_SEARCH` | `HEURISTIC` | CUDA only: `HEURISTIC`, `DEFAULT`, or `EXHAUSTIVE`. `EXHAUSTIVE` (ONNX Runtime's own default) picks algorithms that need about 0.75 GiB more GPU memory for LaMa, which made a 1600x1250 page about five times slower on a 6 GB card |
| `LAMA_MAX_PIXELS` | from GPU memory: (GiB - 2.5) / 3 megapixels, about 1.17 MP on 6 GB; no limit on CPU | Largest array LaMa sees in one pass; larger pages are tiled, also on CPU when set. Tiles are at least 448 px per side, so values below about 200,000 are exceeded. `0` removes the limit |
| `INPAINTER_FALLBACK` | on | `off` returns 503 instead of using LaMa when LanPaint cannot start or ComfyUI stops answering |
| `ANIMETEXT_YOLO_MODEL` | empty | Local AnimeText ONNX file; skips the gated download |
| `ANIMETEXT_YOLO_VARIANT` | `m` | `n`, `s`, `m`, `l`, or `x` |
| `ANIMETEXT_YOLO_CONFIDENCE` | published per variant (`m`: 0.299) | Detection confidence threshold |
| `ANIMETEXT_YOLO_IOU` | `0.35` | NMS IoU threshold |
| `ANIMETEXT_YOLO_MAX_CANDIDATES` | `1000` | Candidate cap before NMS |
| `ANIMETEXT_YOLO_INPUT_SIZE` | read from the ONNX | Square input size; must match a fixed-size model |
| `COMFYUI_URL` | `http://127.0.0.1:8188` | ComfyUI daemon for `flux2_klein_lanpaint` |
| `COMFY_LANPAINT_*` | see headless-lanpaint.md | LanPaint tuning (`COMFY_LANPAINT_PROFILE` defaults to `fast`) |
| `SD_CPP_CLI`, `SD_CPP_MODELS_DIR`, `SD_CPP_TIMEOUT_SECONDS` | see Inpainters | `flux2_klein_q4` runner |
| `PADDLEOCR_DET_REPO`, `PADDLEOCR_ONNX_REPO`, `PADDLEOCR_DICT_REPO` | `xberg-io/paddleocr-onnx-models` (twice), `PaddlePaddle/PP-OCRv5_server_rec` | Hugging Face repos for the PaddleOCR detector, recognizer (`rec/chinese/model.onnx`) and character dictionary (`inference.yml`). A repo set here is not pinned |

## Tests

From the repo root:

rtk npm run lint:backend (ruff)
rtk npm run typecheck:backend (pyright)
rtk npm run test:backend (pytest, `uv run --project backend --locked pytest backend/tests`)
rtk npm run smoke:backend

The tests live in `backend/tests/` (pytest settings are in `backend/pyproject.toml`). The backend unit tests use fakes where possible, so they do not need to download models or call external APIs.

## Models

Models are downloaded during the background startup load (RT-DETR, manga-ocr, LaMa) or on first use (PaddleOCR and the optional AnimeText detector) and stored under backend/models_cache/, which is ignored by Git.

### LaMa and GPU memory

LaMa needs roughly 2.2-2.8 GiB of GPU memory per megapixel. A page that does not fit next to the detector
and OCR sessions spills into shared system memory and gets several times slower, so the backend keeps each
pass under `LAMA_MAX_PIXELS` by splitting larger pages into equal, overlapping tiles. The tiles of a page
share one shape on purpose: every change of input shape costs the session about 0.7 s. Tiles are never
smaller than 448 px per side, so a `LAMA_MAX_PIXELS` below about 200,000 pixels is exceeded. With CUDA as
the first execution provider, the session returns its free GPU memory after the last tile of a page,
because ONNX Runtime otherwise keeps the blocks of every page size it has seen until the card is full;
TensorRT and DirectML sessions keep that memory.

Measured on an RTX 4050 Laptop (6 GB) with the detector and manga-ocr loaded, inpainting only, warm:

| Page | One full-page pass | Tiled |
| --- | ---: | ---: |
| 1280x1000 | 0.65 s | 0.74 s (2 tiles) |
| 1100x1600 | 6.2-7.1 s | 0.93 s (2 tiles) |
| 1600x1250 | 8.8-9.7 s | 1.13 s (2 tiles) |

Whole `/translate` requests (38 text blocks, `translator=none`, rendered PNG) took about 2.0-2.5 s at
1280x1000 and 2.7-3.7 s at 1600x1250. The first request after startup takes a few seconds longer.

manga-ocr stops decoding a crop once its output has repeated one character or a cycle of up to four for 20
tokens. It keeps the text read before that run plus four copies of the repeating unit (so a long
「うおおおお…」 stays 「うおおおお」); shorter runs are kept whole, and anything after a run that reaches the
limit, such as a closing ！, is dropped. Crops with no real text otherwise ran to the 300-token limit and
cost several seconds per page.

Model sources:

- RT-DETR-v2 detector from ogkalu/comic-text-and-bubble-detector.
- AnimeText YOLO12 detector from deepghs/AnimeText_yolo.
- manga-ocr ONNX export from mayocream/manga-ocr-onnx.
- PaddleOCR PP-OCRv5 server ONNX models from xberg-io/paddleocr-onnx-models: the detector
  (`PP-OCRv5_server_det_infer.onnx`) and the recognizer (`rec/chinese/model.onnx`, about 85 MB; it covers
  Japanese). The previous recognizer source, AIPLUX/paddleocr-ppocrv5-onnx, answers 401 now. The recognizer
  is a third-party ONNX conversion, so it was checked against the official PaddlePaddle/PP-OCRv5_server_rec
  weights: run through Paddle's own inference on the same inputs, its output differs by at most 4e-5 and
  every predicted character matches. Both files are pinned by revision and sha256 in `MODEL_PINS`.
- PaddleOCR character dictionary from the official PaddlePaddle/PP-OCRv5_server_rec repo (`inference.yml`,
  pinned the same way). The decoder is blank + dictionary + the ASCII space, 18385 classes.
- LaMa inpainting from ogkalu/lama-manga-onnx-dynamic.

Model integrity: `MODEL_PINS` in `src/manga_backend/utils/download.py` pins a Hugging Face commit and sha256 for each
downloaded file; the commit is passed to the download and the sha256 is checked on download and on cached
files, which are deleted on a mismatch. Pinned: manga-ocr (`encoder_model.onnx`, `decoder_model.onnx`,
`vocab.txt`), LaMa (`lama-manga-dynamic.onnx`), the RT-DETR detector (`detector.onnx`) and the PaddleOCR
files above, each to the commit whose file matches the verified local copy. AnimeText YOLO
(`deepghs/AnimeText_yolo`) is not pinned: the repo is gated and Hugging Face hides its file hashes from
unauthenticated requests, so it still downloads from the mutable `main` revision with no checksum check.
`backend/tests/test_download.py` fails if any other entry is left unpinned. Pins apply only to the default repos, not to
a repo overridden through the environment.

### AnimeText YOLO experiment

RT-DETR remains the default and is loaded at startup. AnimeText YOLO is loaded
only when `detector=animetext-yolo` is selected. The official model repository is
gated, so either authenticate Hugging Face access (accept the repository terms,
then log in or set `HF_TOKEN`) or set `ANIMETEXT_YOLO_MODEL` to a local ONNX file.
Without access the request gets a 503 asking you to set `ANIMETEXT_YOLO_MODEL` or
authenticate with Hugging Face. The default variant is YOLO12m at
640px with its published 0.299 confidence threshold and 0.35 NMS IoU.

Optional environment settings:

```text
ANIMETEXT_YOLO_MODEL=C:\path\to\yolo12m_animetext.onnx
ANIMETEXT_YOLO_VARIANT=m
ANIMETEXT_YOLO_CONFIDENCE=0.299
ANIMETEXT_YOLO_IOU=0.35
ANIMETEXT_YOLO_MAX_CANDIDATES=1000
ANIMETEXT_YOLO_INPUT_SIZE=640
```

For the local SafeTensors conversion used by this experiment, install
Ultralytics 8.3.105, ONNX, and SafeTensors in the backend environment, then run:

```powershell
python scripts/export_animetext_yolo.py --variant m
```

The conversion fallback downloads from `mayocream/anime-text-yolo`, which is
published under GPL-3.0. Keep its weights and generated ONNX files local unless
you have reviewed the license requirements for redistribution.

Compare both detectors on the same page and mask margin with:

```powershell
python scripts/compare_detectors.py "C:\path\to\page.png" --device cuda
```

The report records the requested device and each session's active ONNX providers.
It is a single-image observation, not a ground-truth accuracy evaluation.

## Project Structure

The code is a `src` layout package, `manga_backend`. Runtime data (`models_cache/`, `.env`) stays in the `backend/` project root.

backend/
|-- pyproject.toml       Primary dependency source for uv (with uv.lock); ruff, pytest, and pyright settings
|-- requirements.txt     Unlocked pip fallback
|-- start.ps1            Recommended launcher (runtime sync, checks, uvicorn manga_backend.main:app)
|-- tests/               pytest suite
|-- models_cache/        Downloaded models, auto-created and ignored
|-- src/manga_backend/
    |-- __main__.py      `python -m manga_backend` launcher
    |-- main.py          Thin ASGI shim exposing `app`
    |-- bootstrap.py     Process environment setup (backend/.env, CUDA DLL paths) before onnxruntime loads
    |-- config.py        pydantic-settings configuration and fixed request limits
    |-- paths.py         Package, models_cache, .env, and fonts locations
    |-- api/             create_app, lifespan, auth and origin middleware, routes/ (health, translate, detect, ocr), Pydantic v2 schemas, validation, orjson responses
    |-- pipeline/        Translation pipeline: orchestrator, detection, ocr, translation, inpainting, masks, bubbles, regions, layout, fonts, rendering, text
    |-- models/          Detector, OCR, inpainter, and translator wrappers
    |-- comfy_lanpaint/  ComfyUI client, workflow, crop helpers, and CLI for LanPaint
    |-- utils/           Device, download (model pins), and text block helpers
    |-- fonts/           Rendering fonts

## Extension Integration

Configure the extension local backend URL as http://localhost:8001 and its Backend Access Token to match `BACKEND_AUTH_TOKEN`. The extension will call /translate with manga images and receive translated images or errors from the local backend.
