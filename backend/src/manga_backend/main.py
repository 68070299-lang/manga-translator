"""
Manga Translation API Server

A simple REST API backend for manga translation.
Uses ONNX Runtime for inference - no PyTorch or build tools required.

Based on architecture from:
- comic-translate (ogkalu2/comic-translate)
- manga-image-translator (zyddnys/manga-image-translator)

Usage:
    python -m uvicorn main:app --host 127.0.0.1 --port 8001 --reload
"""

import os
import secrets
import site
import sys

# Disable PaddlePaddle PIR and OneDNN BEFORE any imports
# This fixes the "ConvertPirAttribute2RuntimeAttribute not support" error
os.environ['FLAGS_enable_pir_api'] = '0'
os.environ['FLAGS_enable_pir_in_executor'] = '0'
os.environ['FLAGS_use_mkldnn'] = '0'

# Add CUDA DLL directories to PATH and Windows DLL search path for onnxruntime-gpu.
# Resolve them from the active interpreter rather than assuming backend/venv.
_site_packages = []
try:
    _site_packages.extend(site.getsitepackages())
except AttributeError:
    pass
try:
    _site_packages.append(site.getusersitepackages())
except AttributeError:
    pass
_site_packages.append(os.path.join(sys.prefix, "Lib", "site-packages"))
_nvidia_roots = [os.path.join(root, "nvidia") for root in dict.fromkeys(_site_packages)]
_nvidia_dirs = [
    os.path.join(root, component, "bin")
    for root in _nvidia_roots
    for component in ["cublas", "cuda_runtime", "cudnn", "cuda_nvrtc", "cufft", "nvjitlink"]
]
_extra_paths = []
_dll_directory_handles = []
for _d in _nvidia_dirs:
    if os.path.isdir(_d):
        _extra_paths.append(_d)
        if hasattr(os, "add_dll_directory"):
            _dll_directory_handles.append(os.add_dll_directory(_d))
if _extra_paths:
    os.environ["PATH"] = ";".join(_extra_paths) + ";" + os.environ.get("PATH", "")

import asyncio
import io
import logging
import re
import time
import unicodedata
import uuid
from typing import Optional
from contextlib import asynccontextmanager

# Load .env file if exists
from dotenv import load_dotenv


def load_backend_env() -> bool:
    """Load backend/.env unless the test flag asks for a hermetic environment."""
    if os.environ.get("MANGA_TRANSLATOR_TESTING") == "1":
        return False
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env"))
    return True


load_backend_env()

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Request
from fastapi.responses import Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import numpy as np
from PIL import Image

from manga_backend.pipeline import (
    DEFAULT_INPAINTER,
    DetectorUnavailableError,
    GoogleOCRError,
    GoogleOCRTimeoutError,
    InpainterUnavailableError,
    LAZY_COMPONENTS,
    PaddleOCRUnavailableError,
    TranslationPipeline,
    align_texts,
)
from manga_backend.models.translator import (
    TranslatorError,
    TranslatorTimeoutError,
    validate_local_llm_base_url,
)
from manga_backend.utils.device import get_device


ALLOWED_TYPES = {"image/png", "image/jpeg", "image/webp"}
MAX_UPLOAD_SIZE = 20 * 1024 * 1024
PNG_COMPRESS_LEVEL = 2
MAX_IMAGE_PIXELS = 25_000_000
EXTENSION_ORIGIN_RE = re.compile(r"^chrome-extension://[a-z]{32}$", re.IGNORECASE)
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
VALID_TEXT_ALIGNMENTS = {"left", "center", "right"}
VALID_TEXT_DIRECTIONS = {"auto", "horizontal", "vertical"}
VALID_DETECTORS = {
    "default": "rtdetr",
    "rtdetr": "rtdetr",
    "animetext": "animetext-yolo",
    "animetext-yolo": "animetext-yolo",
    "animetext_yolo": "animetext-yolo",
    "anime-text-yolo": "animetext-yolo",
}
VALID_OCR_ENGINES = {
    "manga-ocr": "manga-ocr",
    "mangaocr": "manga-ocr",
    "paddle-ocr": "paddle-ocr",
    "paddleocr": "paddle-ocr",
    "paddle-vl": "paddle-ocr",
    "paddlevl": "paddle-ocr",
    "google-ocr": "google-ocr",
    "googleocr": "google-ocr",
    "google": "google-ocr",
}
VALID_TRANSLATORS = {
    "": "none",
    "none": "none",
    "gemini": "gemini",
    "google": "gemini",
    "openai": "openai",
    "openrouter": "openrouter",
    "local": "local",
}
VALID_INPAINTERS = {
    "": "none",
    "none": "none",
    "lama": "lama_large",
    "lama_large": "lama_large",
    "flux2_klein_lanpaint": "flux2_klein_lanpaint",
    "flux2-klein-lanpaint": "flux2_klein_lanpaint",
    "flux_lanpaint": "flux2_klein_lanpaint",
    "lanpaint": "flux2_klein_lanpaint",
    "flux2_klein_q4": "flux2_klein_q4",
    "flux2-klein-q4": "flux2_klein_q4",
    "flux_klein_q4": "flux2_klein_q4",
    "q4": "flux2_klein_q4",
}
VALID_PADDLE_LANGS = {"japan", "ch", "en", "korean"}
TRANSLATOR_API_KEY_ENV_VARS = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}
MAX_TARGET_LANG_LENGTH = 64
MAX_MODEL_NAME_LENGTH = 128
MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._:/@+-]+$")
LANGUAGE_HINT_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
REQUEST_ID_HEADER = "X-Request-ID"
BACKEND_AUTH_HEADER = "X-Manga-Translator-Token"
INTERNAL_ERROR_DETAIL = "Internal server error"


def _allowed_browser_origins() -> set[str]:
    value = os.environ.get("BACKEND_ALLOWED_ORIGINS", "")
    return {origin.strip() for origin in value.split(",") if origin.strip()}


def _env_positive_number(name: str, default, cast=int):
    """Read a positive numeric setting, falling back to the default when unset or invalid."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = cast(raw)
    except ValueError:
        logging.getLogger(__name__).warning("Invalid %s=%r; using %s", name, raw, default)
        return default
    if not value > 0:
        logging.getLogger(__name__).warning("Invalid %s=%r; using %s", name, raw, default)
        return default
    return value


ALLOWED_BROWSER_ORIGINS = _allowed_browser_origins()
BACKEND_AUTH_TOKEN = os.environ.get("BACKEND_AUTH_TOKEN", "").strip()
TESTING_MODE = os.environ.get("MANGA_TRANSLATOR_TESTING") == "1"
# Expensive routes queue behind this many concurrent pipeline calls; the wait is
# bounded so a stuck request cannot hold the queue forever.
MAX_CONCURRENT_REQUESTS = _env_positive_number("BACKEND_MAX_CONCURRENT", 2)
QUEUE_TIMEOUT_SECONDS = _env_positive_number("BACKEND_QUEUE_TIMEOUT_SECONDS", 600.0, float)
QUEUE_RETRY_AFTER_SECONDS = 10
# Multipart framing and form fields on top of the raw image bytes.
UPLOAD_OVERHEAD_BYTES = 1024 * 1024


def is_allowed_browser_origin(origin: str) -> bool:
    """Allow extension requests and explicitly configured development origins."""
    return origin in ALLOWED_BROWSER_ORIGINS or bool(EXTENSION_ORIGIN_RE.match(origin))

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global pipeline instance
pipeline: Optional[TranslationPipeline] = None
startup_time = time.monotonic()
active_translate_requests = 0
last_error: Optional[dict] = None


def get_request_id(request: Request) -> str:
    request_id = request.headers.get(REQUEST_ID_HEADER) or getattr(request.state, "request_id", "")
    return request_id or uuid.uuid4().hex


# Absolute filesystem paths (drive, UNC and common POSIX roots) must not reach
# clients or logs; the trailing class keeps sentence punctuation out of the match.
_FILESYSTEM_PATH_RE = re.compile(
    r"(?:(?<!\w)[A-Za-z]:[\\/]|\\\\)(?:[^\s'\"<>|*?]*[^\s'\"<>|*?.,;:)\]])?"
    r"|(?<![\w.~])/(?:home|Users|tmp|var|opt|usr|mnt|root|etc|private|srv|data)\b"
    r"(?:/[^\s'\"<>|*?]*[^\s'\"<>|*?.,;:)\]])?"
)


def scrub_filesystem_paths(text: str) -> str:
    return _FILESYSTEM_PATH_RE.sub("[path]", text)


def scrub_model_status(value):
    """Copy model status with filesystem paths removed from every error string."""
    if isinstance(value, dict):
        return {
            key: scrub_filesystem_paths(item)
            if key == "error" and isinstance(item, str)
            else scrub_model_status(item)
            for key, item in value.items()
        }
    return value


def sanitize_error(error: Exception, sensitive_values: Optional[list[str]] = None) -> str:
    text = str(error) or error.__class__.__name__
    replacements = [
        "api_key",
        "google_ocr_api_key",
        "local_llm_api_key",
    ]
    for token in replacements:
        text = re.sub(token, "[redacted_key_field]", text, flags=re.IGNORECASE)
    for value in sensitive_values or []:
        if value:
            text = text.replace(value, "[redacted_secret]")
    return scrub_filesystem_paths(text)[:500]


def record_last_error(
    request_id: str,
    path: str,
    error: Exception,
    status_code: int = 500,
    sensitive_values: Optional[list[str]] = None,
) -> None:
    global last_error
    last_error = {
        "request_id": request_id,
        "path": path,
        "status": status_code,
        "error": sanitize_error(error, sensitive_values),
        "timestamp": round(time.time(), 3),
    }


def get_sanitized_config() -> dict:
    return {
        "allowed_browser_origins_count": len(ALLOWED_BROWSER_ORIGINS),
        "max_upload_size": MAX_UPLOAD_SIZE,
        "max_image_pixels": MAX_IMAGE_PIXELS,
        "use_gpu": os.environ.get("USE_GPU", "false").lower() == "true",
    }


async def read_upload_image(image: UploadFile) -> np.ndarray:
    """Validate and decode an uploaded image as an RGB numpy array."""
    if image.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid image type: {image.content_type}")

    contents = await image.read(MAX_UPLOAD_SIZE + 1)
    if len(contents) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="File too large")

    return await asyncio.to_thread(decode_image, contents)


def decode_image(contents: bytes) -> np.ndarray:
    """Decode image bytes to RGB; slow enough on full pages to need a worker thread."""
    try:
        img = Image.open(io.BytesIO(contents))
        width, height = img.size
        if width <= 0 or height <= 0:
            raise HTTPException(status_code=400, detail="Invalid image dimensions")
        if width * height > MAX_IMAGE_PIXELS:
            raise HTTPException(status_code=413, detail="Image dimensions too large")
        img = img.convert("RGB")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid image data") from exc

    return np.array(img)


def encode_png(image: np.ndarray) -> bytes:
    """Encode a rendered page for a worker thread.

    Level 2 took 35-55% less time than Pillow's default level 6 on synthetic manga
    pages for 2-6% more bytes; level 1 grew noisy pages by over 30%.
    """
    buffer = io.BytesIO()
    Image.fromarray(image).save(buffer, format="PNG", compress_level=PNG_COMPRESS_LEVEL)
    return buffer.getvalue()


def raise_endpoint_error(
    message: str,
    error: Exception,
    request_id: str = "",
    path: str = "",
    sensitive_values: Optional[list[str]] = None,
) -> None:
    """Preserve intended HTTP errors and convert unexpected failures to 500."""
    if isinstance(error, HTTPException):
        record_last_error(request_id, path, error, error.status_code, sensitive_values)
        raise error
    if isinstance(
        error,
        (
            GoogleOCRTimeoutError,
            GoogleOCRError,
            InpainterUnavailableError,
            DetectorUnavailableError,
            PaddleOCRUnavailableError,
            TranslatorTimeoutError,
            TranslatorError,
        ),
    ):
        status_code = error.status_code
        detail = error.public_detail
        record_last_error(request_id, path, error, status_code, sensitive_values)
        raise HTTPException(status_code=status_code, detail=detail) from error
    if isinstance(error, ValueError):
        record_last_error(request_id, path, error, 400, sensitive_values)
        raise HTTPException(status_code=400, detail=str(error)) from error

    record_last_error(request_id, path, error, 500, sensitive_values)
    logger.error("%s request_id=%s error=%s", message, request_id, sanitize_error(error, sensitive_values))
    raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL) from error


def validate_render_options(
    line_spacing: float,
    border_size: float,
    text_color: str,
    border_color: str,
    bg_color: str,
    bg_opacity: float,
    text_alignment: str,
    text_direction: str,
    preferred_font_size: int,
) -> None:
    """Validate render settings before they reach PIL rendering code."""
    if not 0.5 <= line_spacing <= 3.0:
        raise HTTPException(status_code=400, detail="line_spacing must be between 0.5 and 3.0")
    if not 0 <= border_size <= 20:
        raise HTTPException(status_code=400, detail="border_size must be between 0 and 20")
    if not 0 <= bg_opacity <= 1:
        raise HTTPException(status_code=400, detail="bg_opacity must be between 0 and 1")
    if not 6 <= preferred_font_size <= 96:
        raise HTTPException(
            status_code=400,
            detail="font_size must be between 6 and 96",
        )

    for field_name, value in (
        ("text_color", text_color),
        ("border_color", border_color),
        ("bg_color", bg_color),
    ):
        if not HEX_COLOR_RE.match(value):
            raise HTTPException(status_code=400, detail=f"{field_name} must be a #RRGGBB hex color")

    if text_alignment not in VALID_TEXT_ALIGNMENTS:
        raise HTTPException(status_code=400, detail="text_alignment must be left, center, or right")
    if text_direction not in VALID_TEXT_DIRECTIONS:
        raise HTTPException(status_code=400, detail="text_direction must be auto, horizontal, or vertical")


def normalize_choice(value: str, choices: dict[str, str], field_name: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in choices:
        allowed = ", ".join(sorted(key or "none" for key in choices.keys()))
        raise HTTPException(status_code=400, detail=f"{field_name} must be one of: {allowed}")
    return choices[normalized]


def require_ready_pipeline() -> TranslationPipeline:
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline not initialized")
    if not getattr(pipeline, "is_ready", False):
        raise HTTPException(status_code=503, detail="Pipeline not ready")
    return pipeline


def require_components(*components: str) -> TranslationPipeline:
    """Require only the components needed by an endpoint.

    Lazy components (PaddleOCR) are loaded by the request itself, so they are
    not required to be loaded here and a failed load is retried, not latched.
    """
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline not initialized")
    if pipeline_is_loading():
        raise HTTPException(status_code=503, detail="Backend component unavailable: models are still loading")
    readiness = getattr(pipeline, "component_ready", None)
    if not callable(readiness) and not getattr(pipeline, "is_ready", False):
        raise HTTPException(status_code=503, detail="Pipeline not ready")
    unavailable = [
        component
        for component in components
        if component not in LAZY_COMPONENTS
        and not (readiness(component) if callable(readiness) else getattr(pipeline, "is_ready", False))
    ]
    if unavailable:
        names = ", ".join(unavailable)
        raise HTTPException(status_code=503, detail=f"Backend component unavailable: {names}")
    return pipeline


_request_limiter: Optional[tuple] = None


def get_request_semaphore() -> asyncio.Semaphore:
    """Return the shared pipeline-concurrency semaphore for the running event loop."""
    global _request_limiter
    loop = asyncio.get_running_loop()
    if (
        _request_limiter is None
        or _request_limiter[0] is not loop
        or _request_limiter[1] != MAX_CONCURRENT_REQUESTS
    ):
        _request_limiter = (loop, MAX_CONCURRENT_REQUESTS, asyncio.Semaphore(MAX_CONCURRENT_REQUESTS))
    return _request_limiter[2]


@asynccontextmanager
async def request_slot():
    """Count an expensive request and queue it behind the concurrency limit.

    Requests wait up to QUEUE_TIMEOUT_SECONDS for a slot (the extension has no
    429 handling, so queueing is the normal path) and only then get a 429.
    """
    global active_translate_requests
    active_translate_requests += 1
    try:
        semaphore = get_request_semaphore()
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=QUEUE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=429,
                detail="Backend is busy; try again later",
                headers={"Retry-After": str(QUEUE_RETRY_AFTER_SECONDS)},
            )
        try:
            yield
        finally:
            semaphore.release()
    finally:
        active_translate_requests = max(0, active_translate_requests - 1)


def _reject_control_characters(field_name: str, value: str) -> None:
    if any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in value):
        raise HTTPException(status_code=400, detail=f"{field_name} must not contain control characters")


def validate_target_lang(target_lang: str) -> None:
    if not target_lang.strip():
        raise HTTPException(status_code=400, detail="target_lang must not be empty")
    if len(target_lang) > MAX_TARGET_LANG_LENGTH:
        raise HTTPException(status_code=400, detail=f"target_lang must be at most {MAX_TARGET_LANG_LENGTH} characters")
    _reject_control_characters("target_lang", target_lang)


def validate_model_name(model: str) -> None:
    if len(model) > MAX_MODEL_NAME_LENGTH:
        raise HTTPException(status_code=400, detail=f"model must be at most {MAX_MODEL_NAME_LENGTH} characters")
    if model and not MODEL_NAME_RE.fullmatch(model):
        raise HTTPException(status_code=400, detail="model may only contain letters, digits and . _ : / @ + -")


def validate_language_hint(language_hint: str) -> None:
    if language_hint and not LANGUAGE_HINT_RE.fullmatch(language_hint):
        raise HTTPException(
            status_code=400,
            detail="google_ocr_language_hint must be a short language code such as ja or zh-Hant",
        )


def preflight_translator_inputs(
    translator: str,
    api_key: str,
    local_llm_base_url: str,
    target_lang: str,
    model: str,
    google_ocr_language_hint: str,
) -> None:
    """Reject bad translator inputs before any detector or OCR work starts."""
    validate_target_lang(target_lang)
    validate_model_name(model)
    validate_language_hint(google_ocr_language_hint)

    if translator == "local":
        base_url = local_llm_base_url.strip() or os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:8000/v1")
        try:
            validate_local_llm_base_url(base_url)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    elif translator in TRANSLATOR_API_KEY_ENV_VARS:
        env_var = TRANSLATOR_API_KEY_ENV_VARS[translator]
        if not api_key.strip() and not os.environ.get(env_var, "").strip():
            raise HTTPException(
                status_code=400,
                detail=f"api_key is required when translator is {translator} (or set {env_var} on the backend)",
            )


def required_ocr_components(ocr: str) -> list[str]:
    """Return local components required by the selected OCR engine."""
    if ocr == "paddle-ocr":
        return ["paddle_ocr"]
    if ocr == "manga-ocr":
        return ["ocr"]
    return []


def normalize_result_arrays(result: dict) -> dict:
    """Keep JSON contracts aligned even when a provider returns malformed cardinality."""
    if not isinstance(result, dict):
        return {"blocks": [], "original_texts": [], "translated_texts": []}
    blocks = result.get("blocks")
    if not isinstance(blocks, list):
        logger.warning("Pipeline blocks output was not an array; returning an empty result")
        blocks = []
    result["blocks"] = blocks
    count = len(blocks)
    result["original_texts"] = align_texts(result.get("original_texts"), count, "original_texts")
    result["translated_texts"] = align_texts(result.get("translated_texts"), count, "translated_texts")
    return result


def pipeline_is_loading() -> bool:
    """True while the background pipeline initialization task is still running."""
    task = getattr(app.state, "pipeline_init_task", None)
    return task is not None and not task.done()


async def initialize_pipeline(target) -> None:
    """Load the startup models, then the inpainter the first page will need.

    Creating an ONNX session holds the interpreter lock, so the server cannot
    answer while a model loads (LaMa takes 10-20 s). Doing that load here keeps
    it inside the "loading" phase instead of freezing the first translation.
    """
    await target.initialize()
    warm_up = getattr(target, "warm_up", None)
    if callable(warm_up):
        await warm_up()


def _on_pipeline_init_done(task: asyncio.Task) -> None:
    """Record a failed background initialization so /health can report it."""
    if task.cancelled():
        return
    error = task.exception()
    if error is None:
        logger.info("Pipeline initialized on device: %s", getattr(pipeline, "device", "unknown"))
        return
    logger.error("Pipeline initialization failed error=%s", sanitize_error(error))
    record_last_error("startup", "startup", error, 500)
    statuses = getattr(pipeline, "model_status", None)
    if isinstance(statuses, dict):
        for details in statuses.values():
            if isinstance(details, dict) and details.get("status") == "loading":
                details["status"] = "error"
                details["error"] = sanitize_error(error)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start model initialization in the background so / and /health answer immediately."""
    global pipeline
    injected_pipeline = pipeline is not None
    init_task: Optional[asyncio.Task] = None
    app.state.pipeline_init_task = None

    if TESTING_MODE:
        logger.warning(
            "MANGA_TRANSLATOR_TESTING=1: backend authentication is DISABLED and "
            "backend/.env is not loaded. Never use this mode outside tests."
        )

    if injected_pipeline:
        # A pre-built (test) pipeline is used as-is; it is not initialized or disposed here.
        logger.info("Using pre-initialized translation pipeline")
    else:
        logger.info("Initializing translation pipeline in the background...")
        # Check for GPU
        use_gpu_env = os.environ.get("USE_GPU", "false").lower() == "true"
        device = get_device(use_gpu=use_gpu_env)

        pipeline = TranslationPipeline(device=device)
        init_task = asyncio.create_task(initialize_pipeline(pipeline), name="pipeline-initialize")
        init_task.add_done_callback(_on_pipeline_init_done)
        app.state.pipeline_init_task = init_task

    try:
        yield
    finally:
        # Cleanup
        logger.info("Shutting down pipeline...")
        if init_task is not None and not init_task.done():
            init_task.cancel()
            try:
                await init_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass  # already recorded by the done callback
        app.state.pipeline_init_task = None
        if not injected_pipeline and pipeline is not None:
            dispose = getattr(pipeline, "dispose", None)
            if callable(dispose):
                dispose()
            pipeline = None


app = FastAPI(
    title="Manga Translation API",
    description="Translate manga images using OCR and LLM translation",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Enable CORS for browser extension and optional explicitly configured origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_BROWSER_ORIGINS),
    allow_origin_regex=EXTENSION_ORIGIN_RE.pattern,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def log_rejected_request(
    request: Request, request_id: str, status_code: int, started: float, client_host: str
) -> None:
    """Log an early rejection (auth/size) without recording it as last_error or logging any token."""
    logger.info(
        "request complete request_id=%s method=%s path=%s status=%s duration=%.3fs client=%s",
        request_id, request.method, request.url.path, status_code, time.monotonic() - started, client_host
    )


@app.middleware("http")
async def reject_untrusted_browser_origins(request: Request, call_next):
    request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.monotonic()
    client_host = request.client.host if request.client else "unknown"

    def reject(status_code: int, detail: str) -> JSONResponse:
        response = JSONResponse(status_code=status_code, content={"detail": detail})
        response.headers[REQUEST_ID_HEADER] = request_id
        log_rejected_request(request, request_id, status_code, started, client_host)
        return response

    origin = request.headers.get("origin")
    if origin and not is_allowed_browser_origin(origin):
        return reject(403, "Origin is not allowed for this local backend")

    # Default-deny: only the liveness probe (GET /) and CORS preflights skip the token.
    is_public_request = request.method == "OPTIONS" or (request.method == "GET" and request.url.path == "/")
    if not TESTING_MODE and not is_public_request:
        if not BACKEND_AUTH_TOKEN:
            return reject(503, "Backend authentication is not configured")

        provided_token = request.headers.get(BACKEND_AUTH_HEADER, "")
        try:
            token_matches = secrets.compare_digest(
                provided_token.encode("utf-8"), BACKEND_AUTH_TOKEN.encode("utf-8")
            )
        except (TypeError, UnicodeError):
            token_matches = False
        if not token_matches:
            return reject(401, "Invalid backend authentication token")

    # Reject oversized bodies before Starlette spools the multipart upload to disk.
    content_length = request.headers.get("content-length", "")
    # A very long digit string is oversized by definition, and int() refuses to parse it.
    if content_length.isascii() and content_length.isdigit() and (
        len(content_length) > 18 or int(content_length) > MAX_UPLOAD_SIZE + UPLOAD_OVERHEAD_BYTES
    ):
        return reject(413, "Request body too large")

    try:
        response = await call_next(request)
    except Exception as exc:
        duration = time.monotonic() - started
        record_last_error(request_id, request.url.path, exc, 500, sensitive_values=[])
        logger.error(
            "request failed request_id=%s method=%s path=%s status=500 duration=%.3fs client=%s error=%s",
            request_id, request.method, request.url.path, duration, client_host, sanitize_error(exc, sensitive_values=[])
        )
        raise

    response.headers[REQUEST_ID_HEADER] = request_id
    duration = time.monotonic() - started
    logger.info(
        "request complete request_id=%s method=%s path=%s status=%s duration=%.3fs client=%s",
        request_id, request.method, request.url.path, response.status_code, duration, client_host
    )
    return response


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "message": "Manga Translation API is running"}


@app.get("/health")
async def health():
    """Readiness check with model status; 'loading' while models initialize in the background."""
    loading = pipeline_is_loading()
    models_loaded = not loading and pipeline is not None and pipeline.is_ready
    readiness_fn = getattr(pipeline, "readiness", None) if pipeline else None
    components = readiness_fn() if callable(readiness_fn) else {
        name: details.get("status") == "loaded"
        for name, details in (pipeline.model_status if pipeline else {}).items()
        if isinstance(details, dict)
    }
    return {
        "status": "loading" if loading else ("ok" if models_loaded else "degraded"),
        "models_loaded": models_loaded,
        "device": pipeline.device if pipeline else "unknown",
        "models": scrub_model_status(pipeline.model_status) if pipeline else {},
        "components": components,
        "uptime_seconds": round(time.monotonic() - startup_time, 3),
        "active_translate_requests": active_translate_requests,
        "config": get_sanitized_config(),
        "last_error": last_error,
    }


@app.post("/translate")
async def translate_image(
    request: Request,
    image: UploadFile = File(..., description="Manga image to translate"),
    target_lang: str = Form(default="English", description="Target language"),
    detector: str = Form(default="rtdetr", description="Detection model"),
    ocr: str = Form(default="manga-ocr", description="OCR model: manga-ocr, paddle-ocr, google-ocr"),
    translator: str = Form(default="none", description="Translation: none, gemini, openai, openrouter, local"),
    inpainter: str = Form(default=DEFAULT_INPAINTER, description="Inpainting model"),
    render: bool = Form(default=True, description="Render text on image"),
    api_key: str = Form(default="", description="API key for translation (Gemini/OpenAI/OpenRouter/local)"),
    model: str = Form(default="", description="Model name for translation API"),
    google_ocr_api_key: str = Form(default="", description="Google Cloud Vision API key"),
    google_ocr_language_hint: str = Form(default="", description="Language hint for Google OCR"),
    local_llm_base_url: str = Form(default="", description="Base URL for local LLM (llama.cpp/vLLM)"),
    local_llm_api_key: str = Form(default="", description="API key for local LLM (if needed)"),
    line_spacing: float = Form(default=1.2, description="Line spacing multiplier for rendered text"),
    border_size: float = Form(default=2.0, description="Text stroke/border size in pixels"),
    text_color: str = Form(default="#000000", description="Text color (hex)"),
    border_color: str = Form(default="#ffffff", description="Text border/stroke color (hex)"),
    bg_color: str = Form(default="#ffffff", description="Background fill color (hex)"),
    bg_opacity: float = Form(default=0.0, description="Background fill opacity (0-1)"),
    text_alignment: str = Form(default="center", description="Text alignment: left, center, right"),
    text_direction: str = Form(default="auto", description="Text direction: auto, horizontal, vertical"),
    uppercase: bool = Form(default=False, description="Convert text to uppercase"),
    auto_scale: bool = Form(default=True, description="Auto-fit font size to bubble"),
    font_size: int = Form(
        default=16,
        description="Preferred maximum font size; auto-scale selects the largest fit",
    ),
    verify_translation: bool = Form(default=False, description="Re-OCR rendered text to verify translation"),
):
    """
    Translate a manga image.
    
    Returns the translated image or JSON with extracted text.
    """
    request_id = get_request_id(request)
    started = time.monotonic()

    try:
        validate_render_options(
            line_spacing=line_spacing,
            border_size=border_size,
            text_color=text_color,
            border_color=border_color,
            bg_color=bg_color,
            bg_opacity=bg_opacity,
            text_alignment=text_alignment,
            text_direction=text_direction,
            preferred_font_size=font_size,
        )

        detector = normalize_choice(detector, VALID_DETECTORS, "detector")
        ocr = normalize_choice(ocr, VALID_OCR_ENGINES, "ocr")
        translator = normalize_choice(translator, VALID_TRANSLATORS, "translator")
        inpainter = normalize_choice(inpainter, VALID_INPAINTERS, "inpainter")
        required_components = ["detector"] if detector == "rtdetr" else []
        required_components.extend(required_ocr_components(ocr))
        current_pipeline = require_components(*required_components)
        if ocr == "google-ocr" and not google_ocr_api_key:
            raise HTTPException(status_code=400, detail="google_ocr_api_key is required when ocr is google-ocr")
        preflight_translator_inputs(
            translator=translator,
            api_key=api_key,
            local_llm_base_url=local_llm_base_url,
            target_lang=target_lang,
            model=model,
            google_ocr_language_hint=google_ocr_language_hint,
        )
        async with request_slot():
            img_array = await read_upload_image(image)
            height, width = img_array.shape[:2]
            logger.info(
                "translate start request_id=%s content_type=%s width=%s height=%s detector=%s ocr=%s translator=%s inpainter=%s render=%s target_lang=%s",
                request_id, image.content_type, width, height, detector, ocr, translator, inpainter, render, target_lang
            )

            result = normalize_result_arrays(await current_pipeline.translate(
                image=img_array,
                target_lang=target_lang,
                detector=detector,
                ocr=ocr,
                translator=translator,
                inpainter=inpainter,
                render=render,
                api_key=api_key or None,
                model=model or None,
                google_ocr_api_key=google_ocr_api_key or None,
                google_ocr_language_hint=google_ocr_language_hint or None,
                line_spacing=line_spacing,
                border_size=border_size,
                text_color=text_color,
                border_color=border_color,
                bg_color=bg_color,
                bg_opacity=bg_opacity,
                text_alignment=text_alignment,
                text_direction=text_direction,
                uppercase=uppercase,
                auto_scale=auto_scale,
                preferred_font_size=font_size,
                local_llm_base_url=local_llm_base_url or None,
                local_llm_api_key=local_llm_api_key or None,
                verify_translation=verify_translation,
            ))

        if render and result.get("image") is not None:
            # Return translated image
            payload = await asyncio.to_thread(encode_png, result["image"])
            duration = time.monotonic() - started
            inpainter_used = result.get("inpainter_used", inpainter)
            inpainting_fallback = bool(result.get("inpainting_fallback", False))
            logger.info(
                "translate success request_id=%s response_type=image/png output_bytes=%s "
                "inpainter_used=%s inpainting_fallback=%s duration=%.3fs",
                request_id, len(payload), inpainter_used, inpainting_fallback, duration
            )
            return Response(
                content=payload,
                media_type="image/png",
                headers={"X-Inpainter-Used": inpainter_used},
            )
        else:
            # Return JSON with text data
            payload = {
                "blocks": result.get("blocks", []),
                "original_texts": result.get("original_texts", []),
                "translated_texts": result.get("translated_texts", [])
            }
            duration = time.monotonic() - started
            logger.info(
                "translate success request_id=%s response_type=application/json output_blocks=%s duration=%.3fs",
                request_id, len(payload["blocks"]), duration
            )
            return JSONResponse(content=payload)
            
    except Exception as e:
        duration = time.monotonic() - started
        status_code = getattr(e, "status_code", 500)
        sensitive_values = [api_key, google_ocr_api_key, local_llm_api_key]
        logger.error(
            "translate failed request_id=%s status=%s error=%s duration=%.3fs",
            request_id, status_code, sanitize_error(e, sensitive_values), duration
        )
        raise_endpoint_error("Translation failed", e, request_id=request_id, path="/translate", sensitive_values=sensitive_values)


@app.post("/detect")
async def detect_text(
    request: Request,
    image: UploadFile = File(..., description="Image to detect text in"),
    detector: str = Form(default="rtdetr", description="Detection model"),
):
    """Detect text regions in an image."""
    try:
        detector = normalize_choice(detector, VALID_DETECTORS, "detector")
        required_components = ["detector"] if detector == "rtdetr" else []
        current_pipeline = require_components(*required_components)
        async with request_slot():
            img_array = await read_upload_image(image)
            blocks = await current_pipeline.detect(img_array, detector=detector)
        
        return JSONResponse(content={
            "blocks": [{"bbox": block["bbox"], "type": block.get("type", "text")} for block in blocks]
        })
        
    except Exception as e:
        raise_endpoint_error("Detection failed", e, request_id=get_request_id(request), path=request.url.path, sensitive_values=[])


@app.post("/ocr")
async def ocr_image(
    request: Request,
    image: UploadFile = File(..., description="Image to OCR"),
    ocr: str = Form(default="manga-ocr", description="OCR model: manga-ocr, paddle-ocr, or google-ocr"),
    google_ocr_api_key: str = Form(default="", description="Google Cloud Vision API key"),
    google_ocr_language_hint: str = Form(default="", description="Language hint for Google OCR"),
):
    """Extract text from an image."""
    try:
        ocr = normalize_choice(ocr, VALID_OCR_ENGINES, "ocr")
        current_pipeline = require_components(
            "detector", *required_ocr_components(ocr)
        )
        if ocr == "google-ocr" and not google_ocr_api_key:
            raise HTTPException(status_code=400, detail="google_ocr_api_key is required when ocr is google-ocr")
        async with request_slot():
            img_array = await read_upload_image(image)

            blocks = await current_pipeline.detect(img_array)
            texts = await current_pipeline.ocr(
                img_array,
                blocks,
                ocr=ocr,
                google_ocr_api_key=google_ocr_api_key or None,
                google_ocr_language_hint=google_ocr_language_hint or None,
            )
        
        return JSONResponse(content={"texts": texts})
        
    except Exception as e:
        raise_endpoint_error("OCR failed", e, request_id=get_request_id(request), path=request.url.path, sensitive_values=[google_ocr_api_key])


@app.post("/paddle-ocr")
async def paddle_ocr_full(
    request: Request,
    image: UploadFile = File(..., description="Image for PaddleOCR"),
    lang: str = Form(default="japan", description="Language: japan, ch, en, korean"),
):
    """
    Full PaddleOCR pipeline - detection + recognition in one call.
    Uses PP-OCRv5 for text detection and recognition.
    """
    try:
        lang = lang.strip().lower()
        if lang not in VALID_PADDLE_LANGS:
            allowed = ", ".join(sorted(VALID_PADDLE_LANGS))
            raise HTTPException(status_code=400, detail=f"lang must be one of: {allowed}")
        
        current_pipeline = require_components("paddle_ocr")
        async with request_slot():
            img_array = await read_upload_image(image)
            results = await current_pipeline.paddle_ocr_full(img_array, lang=lang)
        
        return JSONResponse(content={"blocks": results, "total": len(results)})
        
    except Exception as e:
        raise_endpoint_error("PaddleOCR failed", e, request_id=get_request_id(request), path=request.url.path, sensitive_values=[])


if __name__ == "__main__":
    import uvicorn
    backend_host = os.environ.get("BACKEND_HOST", "127.0.0.1")
    uvicorn.run(app, host=backend_host, port=8001)
