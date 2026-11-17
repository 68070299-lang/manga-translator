"""
Translation Pipeline - Orchestrates detection, OCR, translation, and inpainting.
"""

import asyncio
import logging
import os
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from manga_backend.comfy_lanpaint.client import ComfyUIUnreachableError
from manga_backend.models.detector import TextDetector
from manga_backend.models.ocr import OCREngine
from manga_backend.models.inpainter import Inpainter
from manga_backend.models.translator import Translator
from manga_backend.utils.textblock import TextBlock
from manga_backend.paths import FONTS_DIR

logger = logging.getLogger(__name__)

DEFAULT_INPAINTER = "flux2_klein_lanpaint"
LAZY_INPAINTERS = {"flux2_klein_lanpaint", "flux2_klein_q4"}
# Components loaded by the first request that needs them. Startup does not wait
# for them and a failed load only affects the routes that use them.
LAZY_COMPONENTS = frozenset({"paddle_ocr"})
# An inpainter that needs an external runtime falls back to the bundled one when
# that runtime is not running, so a page still translates. INPAINTER_FALLBACK=off
# restores the strict 503.
INPAINTER_FALLBACKS = {"flux2_klein_lanpaint": "lama_large"}
# How long a failed runtime is left alone before the next request probes it again.
INPAINTER_RETRY_SECONDS = 60.0
MIN_READABLE_FONT_SIZE = 10
GOOGLE_OCR_CONCURRENCY = 6


class GoogleOCRError(RuntimeError):
    """Sanitized upstream Google Vision failure."""

    status_code = 502
    public_detail = "Google OCR upstream request failed"


class GoogleOCRTimeoutError(GoogleOCRError):
    """Google Vision did not respond before the request timeout."""

    status_code = 504
    public_detail = "Google OCR upstream request timed out"


# Fixed, path-free setup hints keyed by the requested option name (never by
# exception text, which can carry local paths or upstream details).
INPAINTER_UNAVAILABLE_HINTS = {
    "flux2_klein_lanpaint": (
        "Requested inpainter runtime is unavailable; start ComfyUI "
        "(scripts/comfy-lanpaint/start.ps1) or choose LaMa in options"
    ),
    "flux2_klein_q4": (
        "Requested inpainter runtime is unavailable; "
        "set SD_CPP_CLI / SD_CPP_MODELS_DIR"
    ),
}
DETECTOR_UNAVAILABLE_HINTS = {
    "animetext-yolo": (
        "Requested detector runtime is unavailable; "
        "set ANIMETEXT_YOLO_MODEL or authenticate with Hugging Face"
    ),
}


class InpainterUnavailableError(RuntimeError):
    """The requested local inpainting runtime could not be initialized."""

    status_code = 503
    public_detail = "Requested inpainter runtime is unavailable"

    def __init__(self, message: str = "", inpainter: Optional[str] = None):
        super().__init__(message)
        hint = INPAINTER_UNAVAILABLE_HINTS.get(inpainter)
        if hint:
            self.public_detail = hint


class DetectorUnavailableError(RuntimeError):
    """The requested optional detector runtime could not be initialized."""

    status_code = 503
    public_detail = "Requested detector runtime is unavailable"

    def __init__(self, message: str = "", detector: Optional[str] = None):
        super().__init__(message)
        hint = DETECTOR_UNAVAILABLE_HINTS.get(detector)
        if hint:
            self.public_detail = hint


class PaddleOCRUnavailableError(RuntimeError):
    """PaddleOCR could not be loaded (model download or runtime failure)."""

    status_code = 503
    public_detail = (
        "PaddleOCR is unavailable (its models failed to download or load); "
        "see models.paddle_ocr in /health"
    )


def _inpainter_fallback_disabled() -> bool:
    return os.environ.get("INPAINTER_FALLBACK", "").strip().lower() in ("off", "none", "0", "false")


def align_texts(values: Any, count: int, field_name: str) -> List[str]:
    """Return exactly one safe string per detected block."""
    if values is None:
        normalized: List[str] = []
    elif isinstance(values, (list, tuple)):
        normalized = ["" if value is None else str(value) for value in values]
    else:
        logger.warning(
            "%s provider output was not an array; replacing it with blanks", field_name
        )
        normalized = []

    if len(normalized) != count:
        logger.warning(
            "%s provider output had %s entries but expected %s; aligning cardinality",
            field_name,
            len(normalized),
            count,
        )
    return (normalized + [""] * count)[:count]


def _has_cjk(text):
    for ch in text:
        cp = ord(ch)
        if (
            0x4E00 <= cp <= 0x9FFF
            or 0x3400 <= cp <= 0x4DBF
            or 0x20000 <= cp <= 0x2A6DF
            or 0xF900 <= cp <= 0xFAFF
            or 0x3040 <= cp <= 0x309F
            or 0x30A0 <= cp <= 0x30FF
            or 0xAC00 <= cp <= 0xD7AF
            or 0x1100 <= cp <= 0x11FF
            or 0x3000 <= cp <= 0x303F
            or 0xFF00 <= cp <= 0xFFEF
        ):
            return True
    return False


def _is_unchanged_punctuation(original: Any, translated: Any) -> bool:
    """Keep punctuation-only blocks untouched when translation changes nothing."""
    source = unicodedata.normalize("NFKC", str(original or "")).strip()
    target = unicodedata.normalize("NFKC", str(translated or "")).strip()
    return bool(source) and source == target and not any(
        character.isalnum() for character in source
    )


_font_cache: Dict[str, Any] = {}
_font_object_cache: Dict[Tuple[str, int], Any] = {}
# Rendering runs in worker threads, so cache fills must not race.
_font_object_cache_lock = threading.Lock()

# Bundled fonts are anchored to this module so rendering does not depend on cwd.
_BUNDLED_FONT_DIR = FONTS_DIR

_ENGLISH_FONT_PATHS = [
    str(_BUNDLED_FONT_DIR / "CCWildWords.ttf"),
    str(_BUNDLED_FONT_DIR / "ccwildwords.ttf"),
    "C:\\Windows\\Fonts\\CCWildWords.ttf",
    "C:\\Windows\\Fonts\\ccwild.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]

_CJK_FONT_PATHS = [
    "C:\\Windows\\Fonts\\msgothic.ttc",
    "C:\\Windows\\Fonts\\meiryo.ttc",
    "C:\\Windows\\Fonts\\YuGothM.ttc",
    "C:\\Windows\\Fonts\\msjh.ttc",
    "C:\\Windows\\Fonts\\malgun.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
]

_FALLBACK_FONT_PATHS = [
    "C:\\Windows\\Fonts\\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def _resolve_font_path(paths):
    from PIL import ImageFont

    for path in paths:
        if path in _font_cache:
            return _font_cache[path]
        try:
            font = ImageFont.truetype(path, 12)
            mask = font.getmask("A")
            if mask and mask.size[0] * mask.size[1] > 0:
                _font_cache[path] = path
                return path
        except Exception:
            pass
    return None


def _get_font_object(font_path: Optional[str], font_size: int):
    """Return a cached PIL font object, loading it once under the cache lock."""
    from PIL import ImageFont

    key = (font_path or "default", font_size)
    with _font_object_cache_lock:
        font = _font_object_cache.get(key)
        if font is None:
            try:
                font = (
                    ImageFont.truetype(font_path, font_size)
                    if font_path
                    else ImageFont.load_default()
                )
            except Exception:
                font = ImageFont.load_default()
            _font_object_cache[key] = font
        return font


_FONT_PATHS_BY_CATEGORY = {
    "english": _ENGLISH_FONT_PATHS,
    "cjk": _CJK_FONT_PATHS,
    "fallback": _FALLBACK_FONT_PATHS,
}


def _get_cached_font(category: str) -> Optional[str]:
    return _resolve_font_path(_FONT_PATHS_BY_CATEGORY.get(category, []))


# Lazy import for PaddleOCR ONNX to avoid startup delay.
_paddle_ocr_engines: Dict[str, Any] = {}


def get_paddle_ocr(lang: str = "japan", device: str = "cpu"):
    """Get or create PaddleOCR ONNX engine (lazy loading)."""
    # The Paddle models are multilingual and the engine ignores `lang`, so share
    # one engine (and one set of ORT sessions) per device.
    key = device
    if key not in _paddle_ocr_engines:
        # Use ONNX version to avoid PaddlePaddle PIR/OneDNN issues
        from manga_backend.models.paddle_ocr_onnx import PaddleOCROnnxEngine

        _paddle_ocr_engines[key] = PaddleOCROnnxEngine(lang=lang, device=device)
    return _paddle_ocr_engines[key]


def _bbox_list(block: TextBlock) -> List[Any]:
    return block.xyxy.tolist() if hasattr(block.xyxy, "tolist") else list(block.xyxy)


def _uniform_white_color(pixels: np.ndarray, min_bright_ratio: float) -> Optional[np.ndarray]:
    """Return the median white when most pixels are bright, flat and neutral."""
    luminance = cv2.cvtColor(pixels.reshape(-1, 1, 3), cv2.COLOR_RGB2GRAY).reshape(-1)
    is_bright = luminance >= 235
    if np.count_nonzero(is_bright) / pixels.shape[0] < min_bright_ratio:
        return None

    color = np.median(pixels[is_bright], axis=0)
    if (
        float(np.min(color)) < 242
        or float(np.max(color) - np.min(color)) > 8
        or float(np.std(luminance[is_bright])) > 7
    ):
        return None
    return np.rint(color).astype(np.uint8)


def _overlap_slices(first_bounds, second_bounds):
    """Return array slices for where two xyxy windows overlap, or None."""
    fx1, fy1, fx2, fy2 = first_bounds
    sx1, sy1, sx2, sy2 = second_bounds
    x1, y1, x2, y2 = max(fx1, sx1), max(fy1, sy1), min(fx2, sx2), min(fy2, sy2)
    if x2 <= x1 or y2 <= y1:
        return None
    return (
        (slice(y1 - fy1, y2 - fy1), slice(x1 - fx1, x2 - fx1)),
        (slice(y1 - sy1, y2 - sy1), slice(x1 - sx1, x2 - sx1)),
    )


def _clamp_xyxy(box, width: int, height: int) -> List[int]:
    x1, y1, x2, y2 = box
    return [
        max(0, min(width, x1)),
        max(0, min(height, y1)),
        max(0, min(width, x2)),
        max(0, min(height, y2)),
    ]


def _longest_word_length(block: TextBlock) -> int:
    translated = str(getattr(block, "translated_text", "") or "")
    words = re.findall(r"[\w']+", translated, flags=re.UNICODE)
    return max((len(word) for word in words), default=1)


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return (0, 0, 0)
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def text_block_to_dict(block: TextBlock) -> Dict[str, Any]:
    """Serialize a TextBlock using the API response shape."""
    payload = {"bbox": _bbox_list(block), "type": block.block_type}
    bubble = getattr(block, "bubble_xyxy", None)
    if bubble is not None:
        payload["bubble_bbox"] = [int(value) for value in bubble]
    center = getattr(block, "bubble_text_center", None)
    if center is not None:
        payload["bubble_center"] = [float(value) for value in center]
    return payload


class TranslationPipeline:
    """Main pipeline for manga translation."""

    def __init__(self, device: str = "cpu"):
        self.device = device
        self.is_initialized = False
        self._detector_initialization_lock = asyncio.Lock()
        self._inpainter_initialization_lock = asyncio.Lock()
        self._inpainter_execution_lock = asyncio.Lock()

        # Model instances (lazy loaded)
        self._detector: Optional[TextDetector] = None
        self._optional_detectors: Dict[str, Any] = {}
        self._ocr: Optional[OCREngine] = None
        self._inpainter: Optional[Inpainter] = None
        self._translator: Optional[Translator] = None
        self._inpainter_retry_at: Dict[str, float] = {}

        # Model configurations
        self._detector_type = "rtdetr"
        self._ocr_type = "manga-ocr"
        self._inpainter_type = DEFAULT_INPAINTER
        self._translator_type = "none"

        # Model status tracking
        self.model_status = {
            "detector": {"status": "not_loaded", "name": "rtdetr"},
            "animetext_yolo": {
                "status": "not_loaded",
                "name": "animetext-yolo",
            },
            "ocr": {"status": "not_loaded", "name": "manga-ocr"},
            "paddle_ocr": {"status": "not_loaded", "name": "paddle-ocr"},
            "inpainter": {"status": "not_loaded", "name": DEFAULT_INPAINTER},
        }

    @property
    def is_ready(self) -> bool:
        """True when the core detector and OCR components are available."""
        return (
            self.is_initialized
            and self.component_ready("detector")
            and self.component_ready("ocr")
        )

    def component_ready(self, component: str) -> bool:
        """Report readiness independently so optional failures do not mask healthy routes."""
        if component == "inpainter" and self._inpainter_type == "none":
            return True
        # Large Flux runtimes load on first use so health checks remain fast.
        if component == "inpainter" and self._inpainter_type in LAZY_INPAINTERS:
            status = self.model_status.get(component, {}).get("status")
            return self.is_initialized and status in ("loaded", "not_loaded", "loading")
        status = self.model_status.get(component, {}).get("status")
        if component in LAZY_COMPONENTS:
            # Healthy until a load attempt fails; the next request retries it.
            return self.is_initialized and status in ("loaded", "not_loaded", "loading")
        return self.is_initialized and status == "loaded"

    def readiness(self) -> Dict[str, bool]:
        return {name: self.component_ready(name) for name in self.model_status}

    async def _prepare_paddle_engine(self, lang: str = "japan"):
        """Return an initialized Paddle engine, loading it on first use.

        get_paddle_ocr() caches one engine per device without awaiting, and the
        engine serializes initialize() behind its own lock, so concurrent first
        requests share a single load. Only an initialize() failure marks the
        component as error (and the next request retries it). Recognition
        failures and cancellation happen after this returns, so they leave the
        loaded status untouched and concurrent requests keep being admitted.
        """
        status = self.model_status["paddle_ocr"]
        previous = status.get("status")
        if previous != "loaded":
            status["status"] = "loading"
        try:
            engine = get_paddle_ocr(lang=lang, device=self.device)
            await engine.initialize()
        except asyncio.CancelledError:
            status["status"] = previous
            raise
        except Exception as error:
            status.update({"status": "error", "error": str(error)})
            logger.warning("Failed to load paddle_ocr: %s", error)
            raise PaddleOCRUnavailableError(f"PaddleOCR failed to load: {error}") from error
        status.update({"status": "loaded", "name": "paddle-ocr"})
        status.pop("error", None)
        return engine

    async def _preload(self, component, attribute, factory, required=True) -> None:
        """Load one startup model, recording failures without aborting startup."""
        status = self.model_status[component]
        status["status"] = "loading"
        try:
            engine = factory()
            if attribute:
                setattr(self, attribute, engine)
            await engine.initialize()
            status["status"] = "loaded"
            logger.info("%s loaded", component)
        except Exception as e:
            status.update({"status": "error", "error": str(e)})
            log_failure = logger.error if required else logger.warning
            log_failure("Failed to load %s: %s", component, e)

    async def initialize(self):
        """Initialize the pipeline and pre-load default models."""
        logger.info("Initializing pipeline components...")

        await self._preload(
            "detector", "_detector", lambda: TextDetector(device=self.device)
        )
        await self._preload("ocr", "_ocr", lambda: OCREngine(device=self.device))
        # PaddleOCR (about 170 MB, two more sessions) is not preloaded: the
        # default path is ready once the detector and manga-ocr are, and the
        # first paddle-ocr request loads it through _prepare_paddle_engine.
        logger.info("PaddleOCR deferred to first use")

        # Defer large local Flux runtimes to the first translation request.
        if self._inpainter_type in LAZY_INPAINTERS:
            self.model_status["inpainter"]["status"] = "not_loaded"
            self.model_status["inpainter"]["name"] = self._inpainter_type
            logger.info(
                "Inpainter %s deferred to first translate", self._inpainter_type
            )
        else:
            await self._preload(
                "inpainter",
                "_inpainter",
                lambda: Inpainter(
                    inpainter_type=self._inpainter_type, device=self.device
                ),
                required=False,
            )

        self.is_initialized = True
        logger.info("Pipeline initialization complete")

    async def warm_up(self) -> None:
        """Load the inpainter a first page will most likely need.

        LaMa is the bundled inpainter and the stand-in for one that needs an
        external runtime, and it takes 10-20 s to load, so startup loads it
        rather than the first translation. A failure here only logs: the first
        request that needs the inpainter tries again and reports the error.
        """
        target = self._inpainter_type
        if not _inpainter_fallback_disabled():
            target = INPAINTER_FALLBACKS.get(target, target)
        if target == "none" or target in LAZY_INPAINTERS or self._inpainter is not None:
            return
        try:
            await self._get_inpainter(target)
            logger.info("Inpainter %s loaded ahead of the first request", target)
        except Exception as error:
            logger.warning("Could not load inpainter %s ahead of time: %s", target, error)

    @staticmethod
    def _normalize_detector_type(detector_type: str) -> str:
        """Return the canonical detector name used by routing and masking."""
        normalized = detector_type.strip().lower().replace("_", "-")
        aliases = {
            "": "rtdetr",
            "default": "rtdetr",
            "rtdetr": "rtdetr",
            "animetext": "animetext-yolo",
            "anime-text-yolo": "animetext-yolo",
            "animetext-yolo": "animetext-yolo",
        }
        selected = aliases.get(normalized)
        if selected is None:
            raise ValueError(f"Unsupported detector: {detector_type}")
        return selected

    async def _get_detector(self, detector_type: str):
        """Return RT-DETR or lazily initialize AnimeText YOLO."""
        selected = self._normalize_detector_type(detector_type)
        if selected == "rtdetr":
            if self._detector is None:
                raise RuntimeError("RT-DETR detector not initialized")
            return self._detector

        existing = self._optional_detectors.get(selected)
        if existing is not None:
            return existing
        async with self._detector_initialization_lock:
            existing = self._optional_detectors.get(selected)
            if existing is not None:
                return existing

            from manga_backend.models.animetext_yolo_detector import AnimeTextYoloDetector

            status = self.model_status["animetext_yolo"]
            status.update({"status": "loading", "name": selected})
            candidate = None
            try:
                candidate = AnimeTextYoloDetector(device=self.device)
                await candidate.initialize()
            except Exception as error:
                dispose = getattr(candidate, "dispose", None) if candidate else None
                if callable(dispose):
                    dispose()
                status.update({"status": "error", "error": str(error)})
                raise DetectorUnavailableError(
                    f"Failed to initialize {selected}: {error}", detector=selected
                ) from error
            self._optional_detectors[selected] = candidate
            status.update({"status": "loaded", "name": selected})
            status.pop("error", None)
            return candidate

    async def detect(
        self, image: np.ndarray, detector: str = "rtdetr"
    ) -> List[Dict[str, Any]]:
        """Detect text regions in an image."""
        detector_type = self._normalize_detector_type(detector)
        selected_detector = await self._get_detector(detector_type)
        blocks = await selected_detector.detect(image)
        return [text_block_to_dict(block) for block in blocks]

    async def ocr(
        self,
        image: np.ndarray,
        blocks: List[Dict[str, Any]],
        ocr: str = "manga-ocr",
        google_ocr_api_key: str = None,
        google_ocr_language_hint: str = None,
    ) -> List[str]:
        """Extract text from detected regions."""
        # Convert dicts to TextBlock objects if needed
        text_blocks = []
        for block in blocks:
            if isinstance(block, TextBlock):
                text_blocks.append(block)
                continue

            bbox = block.get("bbox", block.get("xyxy", [0, 0, 0, 0]))
            text_blocks.append(
                TextBlock(xyxy=bbox, block_type=block.get("type", "text"))
            )

        return await self._run_ocr(
            image, text_blocks, ocr, google_ocr_api_key, google_ocr_language_hint
        )

    async def _run_ocr(
        self,
        image: np.ndarray,
        blocks: List[TextBlock],
        ocr: str,
        google_ocr_api_key: Optional[str],
        google_ocr_language_hint: Optional[str],
    ) -> List[str]:
        """Recognize exactly one string per block with the requested engine."""
        if ocr in ["paddle-ocr", "paddleocr", "paddle-vl", "paddlevl"]:
            paddle_engine = await self._prepare_paddle_engine(lang="japan")
            # PaddleOCR can do its own detection, but we use pre-detected regions
            values = await paddle_engine.recognize_regions(
                image, [{"bbox": _bbox_list(block)} for block in blocks]
            )
        elif ocr in ["google-ocr", "googleocr", "google"]:
            if not google_ocr_api_key:
                raise ValueError(
                    "google_ocr_api_key is required when ocr is google-ocr"
                )
            values = await self._google_ocr(
                image, blocks, google_ocr_api_key, google_ocr_language_hint
            )
        else:
            if self._ocr is None:
                raise RuntimeError("OCR not initialized")
            values = await self._ocr.process(image, blocks)
        return align_texts(values, len(blocks), "OCR")

    async def paddle_ocr_full(
        self, image: np.ndarray, lang: str = "japan"
    ) -> List[Dict[str, Any]]:
        """Run full Paddle OCR and publish component readiness for status endpoints."""
        engine = await self._prepare_paddle_engine(lang=lang)
        results = await engine.detect_and_recognize(image)
        return results if isinstance(results, list) else []

    async def _get_inpainter(self, inpainter_type: str):
        """Initialize one inpainting model at a time and publish only healthy instances."""
        async with self._inpainter_initialization_lock:
            if self._inpainter is not None and self._inpainter_type == inpainter_type:
                if self.model_status.get("inpainter", {}).get("status") == "loaded":
                    return self._inpainter

            previous = (self._inpainter, self._inpainter_type)
            status = self.model_status.setdefault("inpainter", {})
            status.update({"status": "loading", "name": inpainter_type})
            candidate = None
            try:
                if inpainter_type == "flux2_klein_lanpaint":
                    from manga_backend.models.flux_lanpaint_inpainter import FluxLanPaintInpainter

                    candidate = FluxLanPaintInpainter(device=self.device)
                elif inpainter_type == "flux2_klein_q4":
                    from manga_backend.models.flux_klein_inpainter import FluxKleinInpainter

                    candidate = FluxKleinInpainter(device=self.device)
                else:
                    candidate = Inpainter(
                        inpainter_type=inpainter_type, device=self.device
                    )
                await candidate.initialize()
            except Exception as error:
                if candidate is not None:
                    self._dispose_inpainter(candidate, inpainter_type)
                if previous[0] is not None:
                    self._inpainter, self._inpainter_type = previous
                    status.update(
                        {
                            "status": "loaded",
                            "name": previous[1],
                            "last_load_error": {
                                "name": inpainter_type,
                                "error": str(error),
                            },
                        }
                    )
                else:
                    status.update(
                        {"status": "error", "name": inpainter_type, "error": str(error)}
                    )
                raise InpainterUnavailableError(
                    f"failed to initialize inpainter {inpainter_type}: {error}",
                    inpainter=inpainter_type,
                ) from error

            self._inpainter = candidate
            self._inpainter_type = inpainter_type
            status.update({"status": "loaded", "name": inpainter_type})
            status.pop("error", None)
            if previous[0] is not None and previous[0] is not candidate:
                self._dispose_inpainter(previous[0], previous[1])
            return candidate

    async def _get_inpainter_or_fallback(self, inpainter_type: str):
        """Return (inpainter, type actually loaded), falling back when allowed."""
        fallback = self._inpainter_fallback(inpainter_type)
        if fallback is None:
            return await self._get_inpainter(inpainter_type), inpainter_type

        if time.monotonic() >= self._inpainter_retry_at.get(inpainter_type, 0.0):
            try:
                return await self._get_inpainter(inpainter_type), inpainter_type
            except InpainterUnavailableError as error:
                self._defer_inpainter_retry(inpainter_type, fallback, error)
        return await self._get_fallback_inpainter(inpainter_type, fallback), fallback

    @staticmethod
    def _inpainter_fallback(inpainter_type: str) -> Optional[str]:
        if _inpainter_fallback_disabled():
            return None
        return INPAINTER_FALLBACKS.get(inpainter_type)

    def _defer_inpainter_retry(
        self, inpainter_type: str, fallback: str, error: Exception
    ) -> None:
        self._inpainter_retry_at[inpainter_type] = (
            time.monotonic() + INPAINTER_RETRY_SECONDS
        )
        logger.warning(
            "%s is unavailable, using %s instead and retrying in %.0fs: %s",
            inpainter_type,
            fallback,
            INPAINTER_RETRY_SECONDS,
            error,
        )

    async def _get_fallback_inpainter(self, inpainter_type: str, fallback: str):
        """Load the stand-in for an unavailable runtime.

        If the stand-in cannot load either (LaMa not downloaded yet and the
        machine is offline, say), the error names the requested inpainter so
        the user still gets its setup hint rather than a generic one.
        """
        try:
            return await self._get_inpainter(fallback)
        except InpainterUnavailableError as error:
            logger.warning(
                "Fallback inpainter %s for %s failed to load: %s",
                fallback,
                inpainter_type,
                error,
            )
            raise InpainterUnavailableError(
                f"{inpainter_type} is unavailable and its fallback {fallback} "
                f"failed to load: {error}",
                inpainter=inpainter_type,
            ) from error

    async def _inpaint_page(self, inpainter_type: str, image: np.ndarray, mask: np.ndarray):
        """Inpaint one page; return it with the name of the inpainter that ran.

        The caller holds _inpainter_execution_lock. A runtime that was up when
        its inpainter loaded can stop later (ComfyUI closed or crashed): the
        instance is then dropped so a later request probes the runtime again,
        and this page is inpainted by the fallback, or fails with the
        runtime's 503 hint when fallback is off.
        """
        model, loaded_type = await self._get_inpainter_or_fallback(inpainter_type)
        try:
            output = await model.inpaint(image, mask)
        except ComfyUIUnreachableError as error:
            model, loaded_type = await self._replace_lost_inpainter(
                model, loaded_type, error
            )
            output = await model.inpaint(image, mask)
        return output, getattr(model, "last_backend_used", loaded_type)

    async def _replace_lost_inpainter(
        self, instance, inpainter_type: str, error: Exception
    ):
        """Drop an inpainter whose runtime went away; return its fallback."""
        async with self._inpainter_initialization_lock:
            if self._inpainter is instance:
                self._inpainter = None
                self.model_status.setdefault("inpainter", {}).update(
                    {"status": "error", "name": inpainter_type, "error": str(error)}
                )
                self._dispose_inpainter(instance, inpainter_type)

        fallback = self._inpainter_fallback(inpainter_type)
        if fallback is None:
            raise InpainterUnavailableError(
                f"{inpainter_type} runtime became unreachable: {error}",
                inpainter=inpainter_type,
            ) from error
        self._defer_inpainter_retry(inpainter_type, fallback, error)
        return await self._get_fallback_inpainter(inpainter_type, fallback), fallback

    @staticmethod
    def _dispose_inpainter(instance, inpainter_type: str) -> None:
        dispose = getattr(instance, "dispose", None)
        if not callable(dispose):
            return
        try:
            dispose()
        except Exception:
            logger.exception("Failed to dispose inpainter %s", inpainter_type)

    async def translate(
        self,
        image: np.ndarray,
        target_lang: str = "English",
        detector: str = "rtdetr",
        ocr: str = "manga-ocr",
        translator: str = "none",
        inpainter: str = DEFAULT_INPAINTER,
        render: bool = True,
        api_key: str = None,
        model: str = None,
        google_ocr_api_key: str = None,
        google_ocr_language_hint: str = None,
        local_llm_base_url: str = None,
        local_llm_api_key: str = None,
        verify_translation: bool = False,
        **render_options: Any,
    ) -> Dict[str, Any]:
        """Full translation pipeline; render_options are passed to _render_text."""

        result = {
            "blocks": [],
            "original_texts": [],
            "translated_texts": [],
            "image": None,
        }

        # Step 1: Detect text regions
        start_time = time.time()
        logger.info("Step 1: Detecting text regions with %s...", detector)
        detector_type = self._normalize_detector_type(detector)
        selected_detector = await self._get_detector(detector_type)
        blocks = await selected_detector.detect(image)
        det_time = time.time() - start_time
        logger.info(
            f"Step 1 complete: Detected {len(blocks)} blocks in {det_time:.2f}s"
        )

        result["blocks"] = [text_block_to_dict(block) for block in blocks]

        if not blocks:
            logger.info("No text blocks detected")
            result["image"] = image
            return result

        # Step 2: OCR
        start_time = time.time()
        logger.info(f"Step 2: Running OCR ({ocr}) on {len(blocks)} blocks...")

        original_texts = await self._run_ocr(
            image, blocks, ocr, google_ocr_api_key, google_ocr_language_hint
        )
        ocr_time = time.time() - start_time
        logger.info(f"Step 2 complete: OCR finished in {ocr_time:.2f}s")

        result["original_texts"] = original_texts

        # Update blocks with OCR text
        for block, text in zip(blocks, original_texts):
            block.text = text

        # Step 3: Translation (if translator specified)
        if translator and translator != "none":
            start_time = time.time()
            logger.info("Step 3: Translating text...")
            translator_client = Translator(
                translator_type=translator,
                api_key=api_key,
                model=model,
                local_llm_base_url=local_llm_base_url,
                local_llm_api_key=local_llm_api_key,
            )
            try:
                await translator_client.initialize()

                translated_texts = await translator_client.translate(
                    original_texts, target_lang
                )
            finally:
                await translator_client.close()
            translated_texts = align_texts(translated_texts, len(blocks), "translation")
            trans_time = time.time() - start_time
            logger.info(f"Step 3 complete: Translated {len(blocks)} blocks in {trans_time:.2f}s")

            if translated_texts == original_texts and any(
                _has_cjk(orig) for orig in original_texts
            ):
                raise RuntimeError(
                    f"Translation produced no changes for {len(original_texts)} CJK text blocks"
                )
        else:
            translated_texts = list(original_texts)

        result["translated_texts"] = translated_texts
        for block, text in zip(blocks, translated_texts):
            block.translated_text = text

        # Step 4: Inpainting
        render_image = image.copy()
        if render and inpainter and inpainter != "none":
            start_time = time.time()
            logger.info(f"Step 4: Inpainting using {inpainter}...")
            async with self._inpainter_execution_lock:
                inpaint_source = image
                inpaint_blocks = [
                    block
                    for block in blocks
                    if str(getattr(block, "translated_text", "") or "").strip()
                    and not _is_unchanged_punctuation(
                        getattr(block, "text", ""),
                        getattr(block, "translated_text", ""),
                    )
                ]
                rectangular_masks = detector_type == "animetext-yolo"
                if inpainter == "flux2_klein_lanpaint":
                    inpaint_source, mask, white_count, textured_count = (
                        await asyncio.to_thread(
                            self._prepare_flux_inpainting,
                            image,
                            inpaint_blocks,
                            rectangular=rectangular_masks,
                        )
                    )
                    result["white_regions_direct_filled"] = white_count
                    result["flux_regions"] = textured_count
                else:
                    mask = await asyncio.to_thread(
                        self._create_mask,
                        image,
                        inpaint_blocks,
                        rectangular=rectangular_masks,
                    )

                # Skip model initialization when every detected block was safely
                # removed from a plain white background.
                if np.any(mask):
                    render_image, result["inpainter_used"] = await self._inpaint_page(
                        inpainter, inpaint_source, mask
                    )
                    result["inpainting_fallback"] = (
                        result["inpainter_used"] != inpainter
                    )
                else:
                    render_image = inpaint_source
                    result["inpainter_used"] = inpainter
                    result["inpainting_fallback"] = False
                    result["inpainting_strategy"] = "white_glyph_fill"
            inpaint_time = time.time() - start_time
            logger.info(f"Step 4 complete: Inpainting finished in {inpaint_time:.2f}s")
        else:
            logger.info("Step 4: Inpainting skipped")

        # Step 5: Render translated text
        if render:
            start_time = time.time()
            logger.info("Step 5: Rendering text...")
            result["image"] = await asyncio.to_thread(
                self._render_text, render_image, blocks, **render_options
            )
            render_time = time.time() - start_time
            logger.info(f"Step 5 complete: Rendering finished in {render_time:.2f}s")

            # Validate output is not mostly black (indicates inpainting/rendering failure)
            mean_brightness = result["image"].mean()
            if mean_brightness < 5:
                logger.warning(
                    f"Rendered image is almost completely black (mean brightness={mean_brightness:.2f}). "
                    f"Returning original image to avoid showing a black result."
                )
                result["image"] = image
        else:
            result["image"] = render_image

        # Step 6: Verify rendered text (re-OCR to ensure it's not still Japanese)
        if (
            verify_translation
            and render
            and translator
            and translator != "none"
            and self._ocr is not None
        ):
            try:
                start_time = time.time()
                logger.info("Step 6: Verifying rendered text (re-OCR)...")
                verified_texts = await self._ocr.process(result["image"], blocks)
                issue_count = 0
                for i, (block, verified) in enumerate(zip(blocks, verified_texts)):
                    block.verified_text = verified
                    block.verification_passed = not (verified and _has_cjk(verified))
                    if not block.verification_passed:
                        issue_count += 1
                        logger.warning(
                            f"VERIFY FAIL block {i}: rendered text still contains CJK: "
                            f"orig={block.text!r} translated={block.translated_text!r} verified={verified!r}"
                        )
                logger.info(
                    f"Step 6 complete: {issue_count}/{len(blocks)} blocks still contain "
                    f"CJK text ({time.time() - start_time:.2f}s)"
                )
            except Exception as e:
                logger.warning(f"Step 6: Verification skipped due to error: {e}")

        self._infer_bubble_render_bounds(render_image, blocks)
        self._infer_bubble_text_centers(render_image, blocks)
        result["blocks"] = [text_block_to_dict(block) for block in blocks]
        return result

    def dispose(self) -> None:
        """Release loaded detector and inpainter runtimes."""
        detector_instances = [self._detector, *self._optional_detectors.values()]
        seen = set()
        for instance in detector_instances:
            if instance is None or id(instance) in seen:
                continue
            seen.add(id(instance))
            dispose = getattr(instance, "dispose", None)
            if callable(dispose):
                dispose()
        self._optional_detectors.clear()
        if self._inpainter is not None:
            self._dispose_inpainter(self._inpainter, self._inpainter_type)
            self._inpainter = None

    async def _google_ocr(
        self,
        image: np.ndarray,
        blocks: List[TextBlock],
        api_key: str,
        language_hint: str = None,
    ) -> List[str]:
        """Perform OCR using Google Cloud Vision API."""
        import base64
        import aiohttp
        from PIL import Image as PILImage
        import io

        url = "https://vision.googleapis.com/v1/images:annotate"
        headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
        limiter = asyncio.Semaphore(GOOGLE_OCR_CONCURRENCY)

        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:

            async def recognize(block: TextBlock) -> str:
                x1, y1, x2, y2 = [int(v) for v in block.xyxy]
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(image.shape[1], x2), min(image.shape[0], y2)

                if x2 <= x1 or y2 <= y1:
                    return ""

                region = image[y1:y2, x1:x2]

                pil_img = PILImage.fromarray(region)
                buffer = io.BytesIO()
                pil_img.save(buffer, format="PNG")
                img_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

                image_context = {}
                if language_hint:
                    image_context["languageHints"] = [language_hint]

                request_body = {
                    "requests": [
                        {
                            "image": {"content": img_base64},
                            "features": [{"type": "TEXT_DETECTION"}],
                            "imageContext": image_context,
                        }
                    ]
                }

                try:
                    async with session.post(
                        url, headers=headers, json=request_body
                    ) as response:
                        if response.status != 200:
                            # Do not include Google's response body: it can echo credentials or request data.
                            if response.status in (401, 403):
                                detail = "Google OCR authentication failed"
                            elif response.status == 429:
                                detail = "Google OCR quota exceeded"
                            elif response.status >= 500:
                                detail = "Google OCR service unavailable"
                            else:
                                detail = "Google OCR request rejected"
                            logger.warning(
                                "Google OCR upstream returned status=%s",
                                response.status,
                            )
                            raise GoogleOCRError(detail)

                        try:
                            result = await response.json()
                        except (
                            TypeError,
                            ValueError,
                            aiohttp.ContentTypeError,
                        ) as error:
                            logger.warning(
                                "Google OCR returned invalid JSON: %s",
                                error.__class__.__name__,
                            )
                            raise GoogleOCRError(
                                "Google OCR returned an invalid response"
                            ) from error
                        if not isinstance(result, dict):
                            raise GoogleOCRError(
                                "Google OCR returned an invalid response"
                            )
                        responses = result.get("responses")
                        first_response = (
                            responses[0]
                            if isinstance(responses, list) and responses
                            else {}
                        )
                        annotations = (
                            first_response.get("textAnnotations", [])
                            if isinstance(first_response, dict)
                            else []
                        )
                        if annotations:
                            return annotations[0].get("description", "").strip()
                        # A valid response with no text is a successful OCR result.
                        return ""
                except (asyncio.TimeoutError, aiohttp.ServerTimeoutError) as error:
                    logger.warning("Google OCR request timed out")
                    raise GoogleOCRTimeoutError() from error
                except aiohttp.ClientError as error:
                    logger.warning(
                        "Google OCR network request failed: %s",
                        error.__class__.__name__,
                    )
                    raise GoogleOCRError() from error

            async def recognize_limited(block: TextBlock) -> str:
                async with limiter:
                    return await recognize(block)

            # Results keep block order; the first failure stops the remaining requests.
            tasks = [asyncio.create_task(recognize_limited(block)) for block in blocks]
            try:
                return list(await asyncio.gather(*tasks))
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

    def _create_mask(
        self,
        image: np.ndarray,
        blocks: List[TextBlock],
        rectangular: bool = False,
    ) -> np.ndarray:
        """Create a rectangular YOLO mask or the default contour-safe mask."""
        h, w = image.shape[:2]
        mask = np.zeros((h, w), dtype=np.uint8)

        for block in self._suppress_composite_render_regions(blocks):
            mask_builder = (
                self._rectangular_mask_for_block
                if rectangular
                else self._contour_safe_mask_for_block
            )
            local_mask, (x1, y1, x2, y2), strategy = mask_builder(image, block)
            if local_mask.size == 0 or not np.any(local_mask):
                logger.warning(
                    "Skipping unsafe inpaint fallback for block %s (%s)",
                    block.xyxy.tolist(),
                    strategy,
                )
                continue
            target = mask[y1:y2, x1:x2]
            np.maximum(target, local_mask, out=target)

        return mask

    @staticmethod
    def _rectangular_mask_for_block(
        image: np.ndarray, block: TextBlock
    ) -> Tuple[np.ndarray, Tuple[int, int, int, int], str]:
        """Mask an AnimeText YOLO box while retaining an outer sampling ring."""
        height, width = image.shape[:2]
        tx1, ty1, tx2, ty2 = [int(value) for value in block.xyxy]
        rx1, ry1 = max(0, tx1), max(0, ty1)
        rx2, ry2 = min(width, tx2), min(height, ty2)
        if rx2 <= rx1 or ry2 <= ry1:
            return (
                np.zeros((0, 0), dtype=np.uint8),
                (rx1, ry1, rx2, ry2),
                "rectangle",
            )

        context = max(4, int(round(min(rx2 - rx1, ry2 - ry1) * 0.15)))
        x1, y1 = max(0, rx1 - context), max(0, ry1 - context)
        x2, y2 = min(width, rx2 + context), min(height, ry2 + context)
        local_mask = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
        local_mask[ry1 - y1 : ry2 - y1, rx1 - x1 : rx2 - x1] = 255
        return local_mask, (x1, y1, x2, y2), "rectangle"

    @staticmethod
    def _glyph_mask_for_block(
        image: np.ndarray,
        block: TextBlock,
    ) -> Tuple[np.ndarray, Tuple[int, int, int, int]]:
        """Extract dark glyphs while rejecting line art crossing a text crop."""
        height, width = image.shape[:2]
        x1, y1, x2, y2 = [int(value) for value in block.xyxy]
        padding = 2
        x1, y1 = max(0, x1 - padding), max(0, y1 - padding)
        x2, y2 = min(width, x2 + padding), min(height, y2 + padding)
        if x2 <= x1 or y2 <= y1:
            return np.zeros((0, 0), dtype=np.uint8), (x1, y1, x2, y2)

        roi = image[y1:y2, x1:x2]
        gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
        background_level = float(np.percentile(gray, 80))
        # Manga glyphs and outlines are normally the darkest cluster. A low cap
        # avoids treating gray screentone as text on textured regions.
        threshold = int(max(24, min(110, background_level - 32)))
        roi_height, roi_width = gray.shape

        def extract_components(candidate: np.ndarray) -> Tuple[np.ndarray, int]:
            count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
                (candidate > 0).astype(np.uint8), connectivity=8
            )
            selected = np.zeros_like(candidate)
            largest_component = 0
            for label in range(1, count):
                area = int(stats[label, cv2.CC_STAT_AREA])
                if area < 2:
                    continue
                left = int(stats[label, cv2.CC_STAT_LEFT])
                top = int(stats[label, cv2.CC_STAT_TOP])
                component_width = int(stats[label, cv2.CC_STAT_WIDTH])
                component_height = int(stats[label, cv2.CC_STAT_HEIGHT])
                touches_edge = (
                    left == 0
                    or top == 0
                    or left + component_width >= roi_width
                    or top + component_height >= roi_height
                )
                touches_image_edge = (
                    (x1 == 0 and left == 0)
                    or (y1 == 0 and top == 0)
                    or (x2 == width and left + component_width >= roi_width)
                    or (y2 == height and top + component_height >= roi_height)
                )
                long_rule = (
                    component_width >= roi_width * 0.65
                    and component_height <= 3
                ) or (
                    component_height >= roi_height * 0.65
                    and component_width <= 3
                )
                edge_structure_rule = (
                    touches_edge
                    and not touches_image_edge
                    and (
                        component_width >= roi_width * 0.2
                        or component_height >= roi_height * 0.2
                    )
                )
                if long_rule or edge_structure_rule:
                    continue
                selected[labels == label] = 255
                largest_component = max(largest_component, area)
            return selected, largest_component

        dark = np.where(gray <= threshold, 255, 0).astype(np.uint8)
        glyphs, dark_largest = extract_components(dark)

        median_level = float(np.percentile(gray, 50))
        bright_level = float(np.percentile(gray, 95))
        if median_level < 210 and bright_level - median_level >= 28:
            bright_threshold = int(max(180, min(245, median_level + 30)))
            bright = np.where(gray >= bright_threshold, 255, 0).astype(np.uint8)
            bright_glyphs, bright_largest = extract_components(bright)
            # Light-on-dark UI text tends to form a strong connected outline,
            # while dark candidates in the same crop are isolated screen tone.
            if bright_largest >= max(12, int(dark_largest * 1.5)):
                glyphs = bright_glyphs

        if np.any(glyphs):
            glyphs = cv2.dilate(glyphs, np.ones((3, 3), np.uint8), iterations=1)
        return glyphs, (x1, y1, x2, y2)

    @staticmethod
    def _bubble_interior_mask_for_block(
        image: np.ndarray, block: TextBlock
    ) -> Tuple[np.ndarray, Tuple[int, int, int, int]]:
        """Find the smallest closed bubble contour surrounding a text block."""
        height, width = image.shape[:2]
        tx1, ty1, tx2, ty2 = [int(value) for value in block.xyxy]
        tx1, ty1 = max(0, tx1), max(0, ty1)
        tx2, ty2 = min(width, tx2), min(height, ty2)
        if tx2 <= tx1 or ty2 <= ty1:
            return np.zeros((0, 0), dtype=np.uint8), (tx1, ty1, tx2, ty2)

        bubble = getattr(block, "bubble_xyxy", None)
        has_detector_bounds = False
        if bubble is not None:
            try:
                bx1, by1, bx2, by2 = [int(value) for value in bubble]
                has_detector_bounds = bx2 > bx1 and by2 > by1
            except (TypeError, ValueError):
                has_detector_bounds = False

        block_width = tx2 - tx1
        block_height = ty2 - ty1
        if has_detector_bounds:
            bubble_width = bx2 - bx1
            bubble_height = by2 - by1
            padding = max(6, int(min(bubble_width, bubble_height) * 0.08))
            x1, y1 = max(0, bx1 - padding), max(0, by1 - padding)
            x2, y2 = min(width, bx2 + padding), min(height, by2 + padding)
        else:
            padding_x = max(
                32, int(block_width * 1.25), int(block_height * 0.5)
            )
            padding_y = max(
                32, int(block_height * 0.75), int(block_width * 1.25)
            )
            x1, y1 = max(0, tx1 - padding_x), max(0, ty1 - padding_y)
            x2, y2 = min(width, tx2 + padding_x), min(height, ty2 + padding_y)

        roi = image[y1:y2, x1:x2]
        if roi.size == 0 or min(roi.shape[:2]) < 5:
            return np.zeros(roi.shape[:2], dtype=np.uint8), (x1, y1, x2, y2)

        gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
        local_text = (tx1 - x1, ty1 - y1, tx2 - x1, ty2 - y1)
        structure_gray = gray.copy()
        sx1, sy1 = max(0, local_text[0]), max(0, local_text[1])
        sx2, sy2 = min(gray.shape[1], local_text[2]), min(
            gray.shape[0], local_text[3]
        )
        if sx2 > sx1 and sy2 > sy1:
            # OCR ink close to a bubble edge can otherwise join the outline
            # and make contour extraction carve character-shaped holes into
            # the inferred interior. Remove the known text rectangle only for
            # structural contour discovery; the source image is untouched.
            structure_gray[sy1:sy2, sx1:sx2] = int(np.percentile(gray, 90))
        blurred = cv2.GaussianBlur(structure_gray, (3, 3), 0)
        otsu_threshold, _binary = cv2.threshold(
            blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        dark_threshold = int(np.clip(otsu_threshold, 70, 210))
        dark = np.where(blurred <= dark_threshold, 255, 0).astype(np.uint8)
        edges = cv2.Canny(blurred, 50, 150)
        outline = cv2.bitwise_or(dark, edges)
        outline = cv2.morphologyEx(
            outline,
            cv2.MORPH_CLOSE,
            np.ones((3, 3), np.uint8),
            iterations=1,
        )
        contours, _hierarchy = cv2.findContours(
            outline, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE
        )

        text_width = max(1, local_text[2] - local_text[0])
        text_height = max(1, local_text[3] - local_text[1])
        text_area = text_width * text_height
        center = (
            (local_text[0] + local_text[2]) / 2.0,
            (local_text[1] + local_text[3]) / 2.0,
        )
        roi_height, roi_width = gray.shape
        candidates = []
        for contour in contours:
            area = float(abs(cv2.contourArea(contour)))
            if area < max(48.0, text_area * 1.15) or area >= roi.size / 3 * 0.9:
                continue
            if cv2.pointPolygonTest(contour, center, False) < 0:
                continue
            cx, cy, cw, ch = cv2.boundingRect(contour)
            if cx <= 1 or cy <= 1 or cx + cw >= roi_width - 1 or cy + ch >= roi_height - 1:
                continue
            overlap_width = max(
                0, min(cx + cw, local_text[2]) - max(cx, local_text[0])
            )
            overlap_height = max(
                0, min(cy + ch, local_text[3]) - max(cy, local_text[1])
            )
            if overlap_width * overlap_height < text_area * 0.75:
                continue
            candidates.append((area, contour))

        if not candidates:
            return np.zeros(gray.shape, dtype=np.uint8), (x1, y1, x2, y2)

        _area, contour = min(candidates, key=lambda candidate: candidate[0])
        interior = np.zeros(gray.shape, dtype=np.uint8)
        cv2.drawContours(interior, [contour], -1, 255, thickness=cv2.FILLED)
        interior = cv2.erode(
            interior, np.ones((3, 3), np.uint8), iterations=2
        )
        text_roi = interior[
            max(0, local_text[1]) : min(roi_height, local_text[3]),
            max(0, local_text[0]) : min(roi_width, local_text[2]),
        ]
        if text_roi.size == 0 or np.count_nonzero(text_roi) < text_area * 0.35:
            interior.fill(0)
        return interior, (x1, y1, x2, y2)

    def _contour_safe_mask_for_block(
        self, image: np.ndarray, block: TextBlock
    ) -> Tuple[np.ndarray, Tuple[int, int, int, int], str]:
        """Extract ink, using a detected bubble contour only as a safety boundary."""
        glyphs, glyph_bounds = self._glyph_mask_for_block(image, block)
        has_glyphs = glyphs.size and np.any(glyphs)
        interior = np.zeros((0, 0), dtype=np.uint8)
        interior_bounds = glyph_bounds
        if block.block_type == "text":
            interior, interior_bounds = self._bubble_interior_mask_for_block(
                image, block
            )
            if has_glyphs and interior.size and np.any(interior):
                # Shape-aware edge filtering removes broad crossing lines but
                # keeps compact clipped characters. The bubble interior is the
                # final safety boundary around those candidates.
                clipped = np.zeros_like(glyphs)
                overlap = _overlap_slices(glyph_bounds, interior_bounds)
                if overlap:
                    clipped[overlap[0]] = cv2.bitwise_and(glyphs[overlap[0]], interior[overlap[1]])
                if np.any(clipped):
                    return clipped, glyph_bounds, "bubble-clipped-glyph"

        if has_glyphs:
            return glyphs, glyph_bounds, "glyph"

        if block.block_type != "text":
            interior, interior_bounds = self._bubble_interior_mask_for_block(
                image, block
            )
        bounds = interior_bounds
        if interior.size == 0 or not np.any(interior):
            return interior, bounds, "no-closed-bubble"

        x1, y1, x2, y2 = bounds
        roi = image[y1:y2, x1:x2]
        gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
        window = np.zeros_like(interior)
        bx1, by1, bx2, by2 = [int(value) for value in block.xyxy]
        bx1, by1 = max(x1, bx1 - 2), max(y1, by1 - 2)
        bx2, by2 = min(x2, bx2 + 2), min(y2, by2 + 2)
        window[by1 - y1 : by2 - y1, bx1 - x1 : bx2 - x1] = 255

        adaptive_block_size = min(15, min(gray.shape))
        if adaptive_block_size % 2 == 0:
            adaptive_block_size -= 1
        adaptive_block_size = max(3, adaptive_block_size)
        adaptive = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            adaptive_block_size,
            7,
        )
        adaptive = cv2.bitwise_and(adaptive, interior)
        adaptive = cv2.bitwise_and(adaptive, window)
        count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
            (adaptive > 0).astype(np.uint8), connectivity=8
        )
        ink = np.zeros_like(adaptive)
        for label in range(1, count):
            if int(stats[label, cv2.CC_STAT_AREA]) >= 2:
                ink[labels == label] = 255
        if np.any(ink):
            ink = cv2.dilate(ink, np.ones((3, 3), np.uint8), iterations=1)
            ink = cv2.bitwise_and(ink, interior)
            return ink, bounds, "contour-clipped-glyph"

        if block.block_type == "text":
            return interior, bounds, "bubble-interior"
        return np.zeros_like(interior), bounds, "unsafe-sfx"

    @staticmethod
    def _plain_white_fill_color(
        roi: np.ndarray, glyph_mask: np.ndarray
    ) -> Optional[np.ndarray]:
        """Return the local white background color only for a uniform white ROI."""
        if roi.size == 0 or glyph_mask.size == 0 or not np.any(glyph_mask):
            return None
        background = roi[glyph_mask == 0]
        if background.shape[0] < max(16, roi.shape[0] * roi.shape[1] // 4):
            return None

        # Tight detector boxes around small chat bubbles include some gray UI
        # outside the bubble. A strong two-thirds white majority is sufficient
        # because the color and low-variance checks still reject tone.
        return _uniform_white_color(background, 0.68)

    @staticmethod
    def _plain_white_interior_color(
        roi: np.ndarray, interior_mask: np.ndarray
    ) -> Optional[np.ndarray]:
        """Sample a uniform white bubble interior without consulting its exterior."""
        if roi.size == 0 or interior_mask.size == 0 or not np.any(interior_mask):
            return None
        interior = roi[interior_mask > 0]
        if interior.shape[0] < 16:
            return None
        return _uniform_white_color(interior, 0.72)

    def _white_fill_mask(
        self,
        image: np.ndarray,
        block: TextBlock,
        glyphs: np.ndarray,
        bounds: Tuple[int, int, int, int],
        strategy: str,
    ) -> np.ndarray:
        """Grow glyphs to cover the gray antialiasing around tiny UI text.

        Only uniform white regions use this, and structural components were
        removed from the glyphs already.
        """
        x1, y1, x2, y2 = bounds
        fill_mask = cv2.dilate(glyphs, np.ones((3, 3), np.uint8), iterations=1)
        text_window = np.zeros_like(fill_mask)
        tx1, ty1, tx2, ty2 = [int(value) for value in block.xyxy]
        wx1, wy1 = max(0, tx1 - x1), max(0, ty1 - y1)
        wx2, wy2 = min(x2 - x1, tx2 - x1), min(y2 - y1, ty2 - y1)
        if wx2 > wx1 and wy2 > wy1:
            text_window[wy1:wy2, wx1:wx2] = 255
        roi_gray = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_RGB2GRAY)
        antialias = np.where(roi_gray <= 245, 255, 0).astype(np.uint8)
        antialias = cv2.bitwise_and(antialias, text_window)
        near_glyphs = cv2.dilate(glyphs, np.ones((7, 7), np.uint8), iterations=1)
        fill_mask = cv2.bitwise_or(fill_mask, cv2.bitwise_and(antialias, near_glyphs))

        if strategy == "bubble-clipped-glyph":
            interior, interior_bounds = self._bubble_interior_mask_for_block(image, block)
            safety = np.zeros_like(fill_mask)
            overlap = _overlap_slices(bounds, interior_bounds)
            if overlap:
                safety[overlap[0]] = interior[overlap[1]]
            fill_mask = cv2.bitwise_and(fill_mask, safety)
        return fill_mask

    def _prepare_flux_inpainting(
        self,
        image: np.ndarray,
        blocks: List[TextBlock],
        rectangular: bool = False,
    ) -> Tuple[np.ndarray, np.ndarray, int, int]:
        """Build a FLUX mask, preserving RT-DETR's white-region shortcut."""
        cleaned = image.copy()
        flux_mask = np.zeros(image.shape[:2], dtype=np.uint8)
        white_count = 0
        textured_count = 0

        for block in self._suppress_composite_render_regions(blocks):
            glyphs, bounds, strategy = self._contour_safe_mask_for_block(image, block)
            if glyphs.size == 0 or not np.any(glyphs):
                logger.warning(
                    "Skipping FLUX block with no safe mask: %s (%s)",
                    block.xyxy.tolist(),
                    strategy,
                )
                continue

            x1, y1, x2, y2 = bounds
            roi = image[y1:y2, x1:x2]
            if strategy == "bubble-interior" and not rectangular:
                fill_color = self._plain_white_interior_color(roi, glyphs)
            else:
                fill_color = self._plain_white_fill_color(roi, glyphs)

            if fill_color is None:
                local_mask = flux_mask[y1:y2, x1:x2]
                np.maximum(local_mask, glyphs, out=local_mask)
                textured_count += 1
                continue

            fill_mask = (
                self._white_fill_mask(image, block, glyphs, bounds, strategy)
                if rectangular
                else glyphs
            )
            cleaned[y1:y2, x1:x2][fill_mask > 0] = fill_color
            white_count += 1

        logger.info(
            "FLUX mask: direct-filled %s white regions; queued %s regions",
            white_count,
            textured_count,
        )
        return cleaned, flux_mask, white_count, textured_count

    def _infer_bubble_render_bounds(
        self, image: np.ndarray, blocks: List[TextBlock]
    ) -> int:
        """Give YOLO dialogue the detected bubble interior as its render region."""
        height, width = image.shape[:2]
        inferred = 0
        for block in blocks:
            if (
                block.block_type != "text"
                or getattr(block, "bubble_xyxy", None) is not None
            ):
                continue
            interior, (x1, y1, _x2, _y2) = self._bubble_interior_mask_for_block(
                image, block
            )
            ys, xs = np.where(interior > 0)
            if not len(xs):
                continue
            bubble_xyxy = [
                int(xs.min() + x1),
                int(ys.min() + y1),
                int(xs.max() + x1 + 1),
                int(ys.max() + y1 + 1),
            ]
            body_center = self._bubble_body_center(interior, (x1, y1))
            visual_center = self._visual_bubble_center(body_center, bubble_xyxy)
            block_center = [
                (float(block.x1) + float(block.x2)) / 2.0,
                (float(block.y1) + float(block.y2)) / 2.0,
            ]
            if (
                abs(visual_center[0] - block_center[0]) > max(8.0, block.width * 0.6)
                or abs(visual_center[1] - block_center[1]) > max(8.0, block.height * 0.6)
            ):
                logger.debug(
                    "Rejected off-center bubble inference for block %s: %s",
                    block.xyxy.tolist(),
                    bubble_xyxy,
                )
                continue
            block.bubble_xyxy = bubble_xyxy
            block.bubble_text_center = visual_center
            inferred += 1

        for parent in blocks:
            parent_source = self._normalize_render_source(parent.text)
            if not parent_source:
                continue
            children = [
                child
                for child in blocks
                if child is not parent
                and child.block_type == "text"
                and child.area < parent.area
                and self._is_inside(child, parent)
            ]
            if len(children) >= 2 and self._reading_order_source(parent, children) == parent_source:
                self._assign_composite_child_regions(parent, parent_source, children, width, height)

        logger.info("Inferred %s YOLO bubble render regions", inferred)
        return inferred

    @staticmethod
    def _assign_composite_child_regions(
        parent: TextBlock,
        parent_source: str,
        children: List[TextBlock],
        width: int,
        height: int,
    ) -> None:
        """Split the area around an OCR composite's parts into one region per part."""
        group_key = (tuple(int(value) for value in parent.xyxy), parent_source)
        for child in children:
            pad_x = max(4, int(round(child.width * 0.2)))
            pad_y = max(4, int(round(child.height * 0.1)))
            child.bubble_xyxy = _clamp_xyxy(
                [child.x1 - pad_x, child.y1 - pad_y, child.x2 + pad_x, child.y2 + pad_y],
                width,
                height,
            )
            child._composite_render_group = group_key

        centers_x = [child.center[0] for child in children]
        centers_y = [child.center[1] for child in children]
        axis = 0 if max(centers_x) - min(centers_x) >= max(centers_y) - min(centers_y) else 1
        ordered_children = sorted(
            children,
            key=lambda child: (child.center[axis], child.center[1 - axis]),
        )
        for first, second in zip(ordered_children, ordered_children[1:]):
            first_box = first.bubble_xyxy
            second_box = second.bubble_xyxy
            overlap_start = max(first_box[axis], second_box[axis])
            overlap_end = min(first_box[axis + 2], second_box[axis + 2])
            if overlap_end <= overlap_start:
                continue

            split = (
                overlap_end
                if _longest_word_length(first) >= _longest_word_length(second)
                else overlap_start
            )
            first_box[axis + 2] = split
            second_box[axis] = split

        for child in children:
            bx1, by1, bx2, by2 = child.bubble_xyxy
            child.bubble_text_center = [(bx1 + bx2) / 2.0, (by1 + by2) / 2.0]

    @staticmethod
    def _is_inside(child: TextBlock, parent: TextBlock) -> bool:
        return (
            child.x1 >= parent.x1
            and child.y1 >= parent.y1
            and child.x2 <= parent.x2
            and child.y2 <= parent.y2
        )

    def _reading_order_source(self, parent: TextBlock, children: List[TextBlock]) -> str:
        """Sort children into the parent's reading order and join their source text."""
        if parent.height > parent.width * 1.25:
            children.sort(key=lambda child: (-child.x2, child.y1))
        else:
            children.sort(key=lambda child: (child.y1, child.x1))
        return "".join(self._normalize_render_source(child.text) for child in children)

    @staticmethod
    def _bubble_body_center(
        interior: np.ndarray, offset: Tuple[int, int]
    ) -> Optional[List[float]]:
        """Return the robust center of a bubble body while ignoring its tail."""
        ys, xs = np.where(interior > 0)
        if not len(xs):
            return None

        def median_center_of_long_spans(positions: np.ndarray, lines: np.ndarray) -> float:
            centers = []
            spans = []
            for line in np.unique(lines):
                line_positions = positions[lines == line]
                centers.append((float(line_positions.min()) + float(line_positions.max()) + 1) / 2)
                spans.append(len(line_positions))
            cutoff = float(np.percentile(spans, 40))
            return np.median([center for center, span in zip(centers, spans) if span >= cutoff])

        return [
            float(median_center_of_long_spans(xs, ys) + offset[0]),
            float(median_center_of_long_spans(ys, xs) + offset[1]),
        ]

    @staticmethod
    def _visual_bubble_center(
        body_center: Optional[List[float]], bubble_xyxy: List[int]
    ) -> List[float]:
        """Balance contour center with bounds so tail removal cannot over-correct."""
        bx1, by1, bx2, by2 = [float(value) for value in bubble_xyxy]
        bounds_center = [(bx1 + bx2) / 2.0, (by1 + by2) / 2.0]
        if body_center is None:
            return bounds_center
        body_weight = 0.6
        return [
            bounds + (float(body) - bounds) * body_weight
            for bounds, body in zip(bounds_center, body_center)
        ]

    def _infer_bubble_text_centers(
        self, image: np.ndarray, blocks: List[TextBlock]
    ) -> int:
        """Attach contour-aware visual centers to all detected bubble regions."""
        inferred = 0
        for block in blocks:
            if (
                block.block_type != "text"
                or getattr(block, "bubble_xyxy", None) is None
                or getattr(block, "bubble_text_center", None) is not None
            ):
                continue
            interior, (x1, y1, _x2, _y2) = self._bubble_interior_mask_for_block(
                image, block
            )
            center = self._bubble_body_center(interior, (x1, y1))
            block.bubble_text_center = self._visual_bubble_center(
                center, block.bubble_xyxy
            )
            inferred += 1
        return inferred

    def _render_text(
        self,
        image: np.ndarray,
        blocks: List[TextBlock],
        line_spacing: float = 1.2,
        border_size: float = 2.0,
        text_color: str = "#000000",
        border_color: str = "#ffffff",
        bg_color: str = "#ffffff",
        bg_opacity: float = 0.9,
        text_alignment: str = "center",
        text_direction: str = "auto",
        uppercase: bool = False,
        auto_scale: bool = True,
        preferred_font_size: int = 16,
    ) -> np.ndarray:
        """Render translated text onto the image with wrapping and styling."""
        from PIL import Image, ImageDraw

        img = Image.fromarray(image).convert("RGBA")
        self._infer_bubble_render_bounds(image, blocks)
        self._infer_bubble_text_centers(image, blocks)
        render_regions = self._build_render_regions(blocks, image.shape)
        self._expand_short_standalone_regions(render_regions, image.shape)

        english_font_path = _get_cached_font("english")
        cjk_font_path = _get_cached_font("cjk")
        fallback_font_path = _get_cached_font("fallback")

        user_text_color = _hex_to_rgb(text_color)
        user_border_color = _hex_to_rgb(border_color)
        user_bg_color = _hex_to_rgb(bg_color)
        bg_alpha = int(bg_opacity * 255)
        pil_align = text_alignment if text_alignment in ("left", "right") else "center"

        for block in render_regions:
            if hasattr(block, "translated_text"):
                text = block.translated_text
            else:
                text = getattr(block, "text", "")
            if not text:
                continue

            if uppercase:
                text = text.upper()

            x1, y1, x2, y2 = [int(v) for v in block.xyxy]
            original_box = (x1, y1, x2, y2)
            width = x2 - x1
            height = y2 - y1

            if width <= 0 or height <= 0:
                continue

            font_path = (
                cjk_font_path if _has_cjk(text) else english_font_path or cjk_font_path
            ) or fallback_font_path

            stroke_width = max(0, int(border_size))
            is_vertical = text_direction == "vertical" or (
                text_direction == "auto" and _has_cjk(text) and height > width * 1.5
            )
            group = getattr(block, "_composite_render_group", None)

            def inner_area(box_width: int, box_height: int):
                padding_limit = 1 if group is not None else max(2, stroke_width + 1)
                box_padding = min(padding_limit, max(0, (min(box_width, box_height) - 1) // 4))
                return (
                    box_padding,
                    max(1, box_width - box_padding * 2),
                    max(1, box_height - box_padding * 2),
                )

            def fit_box(box_width: int, box_height: int):
                box_padding, inner_width, inner_height = inner_area(box_width, box_height)
                fitted_size = self._auto_fit_font_size(
                    font_path,
                    text,
                    inner_width,
                    inner_height,
                    line_spacing,
                    pil_align,
                    stroke_width=stroke_width,
                    preferred_font_size=preferred_font_size,
                    vertical=is_vertical,
                )
                return box_padding, inner_width, inner_height, fitted_size

            if auto_scale:
                padding, available_width, available_height, font_size = fit_box(width, height)
                minimum_font_size = min(
                    8 if group is not None else MIN_READABLE_FONT_SIZE,
                    max(1, int(preferred_font_size)),
                )
                compact_text = re.sub(r"\s+", "", str(text))
                can_overflow = not _has_cjk(compact_text) and (
                    getattr(block, "_render_from_bubble", False)
                    or len(compact_text) <= 12
                )
                if can_overflow and font_size < minimum_font_size:
                    overflow = self._find_overflow_region(
                        block, render_regions, image.shape, fit_box, minimum_font_size
                    )
                    if overflow is not None:
                        box, (padding, available_width, available_height, font_size) = overflow
                        x1, y1, x2, y2 = box
                        width, height = x2 - x1, y2 - y1
                        block.xyxy = box

                if font_size <= 0:
                    logger.warning(
                        "Skipping translated text that cannot fit render region %s",
                        [x1, y1, x2, y2],
                    )
                    continue
            else:
                font_size = preferred_font_size
                padding, available_width, available_height = inner_area(width, height)

            layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(layer, "RGBA")
            if bg_opacity > 0:
                background_box = [
                    max(0, original_box[0] - x1),
                    max(0, original_box[1] - y1),
                    min(width - 1, original_box[2] - x1 - 1),
                    min(height - 1, original_box[3] - y1 - 1),
                ]
                if (
                    background_box[2] >= background_box[0]
                    and background_box[3] >= background_box[1]
                ):
                    draw.rectangle(background_box, fill=(*user_bg_color, bg_alpha))

            stroke_width = min(stroke_width, max(0, int(round(font_size * 0.15))))
            font = _get_font_object(font_path, font_size)
            text_spacing = max(0, int((line_spacing - 1.0) * font_size))
            wrapped_text = "\n".join(
                self._wrap_text_to_lines(text, font, available_width, stroke_width=stroke_width)
            )

            body_center = getattr(block, "bubble_text_center", None)
            body_center_x = (
                float(body_center[0]) - x1
                if body_center is not None
                else padding + available_width / 2
            )
            body_center_y = (
                float(body_center[1]) - y1
                if body_center is not None
                else padding + available_height / 2
            )

            if is_vertical:
                text_x = (
                    body_center_x
                    if text_alignment == "center"
                    else (padding if text_alignment == "left" else width - padding)
                )
                self._draw_vertical_text(
                    draw,
                    text,
                    (text_x, padding),
                    font,
                    user_text_color,
                    stroke_width=stroke_width,
                    stroke_fill=user_border_color,
                    spacing=text_spacing,
                )
            else:
                bbox = draw.multiline_textbbox(
                    (0, 0),
                    wrapped_text,
                    font=font,
                    align=pil_align,
                    stroke_width=stroke_width,
                    spacing=text_spacing,
                )
                rendered_width = bbox[2] - bbox[0]
                rendered_height = bbox[3] - bbox[1]
                if text_alignment == "left":
                    painted_x = padding
                elif text_alignment == "right":
                    painted_x = width - padding - rendered_width
                else:
                    painted_x = max(
                        padding,
                        min(width - padding - rendered_width, body_center_x - rendered_width / 2),
                    )
                painted_y = max(
                    padding,
                    min(height - padding - rendered_height, body_center_y - rendered_height / 2),
                )
                draw.multiline_text(
                    (painted_x - bbox[0], painted_y - bbox[1]),
                    wrapped_text,
                    font=font,
                    fill=user_text_color,
                    align=pil_align,
                    stroke_width=stroke_width,
                    stroke_fill=user_border_color,
                    spacing=text_spacing,
                )

            img.alpha_composite(layer, dest=(x1, y1))

        return np.array(img.convert("RGB"))

    @staticmethod
    def _find_overflow_region(
        block: TextBlock,
        regions: List[TextBlock],
        image_shape: Tuple[int, ...],
        fit_box,
        minimum_font_size: int,
    ):
        """Find the smallest, least overlapping growth that reaches a readable font."""
        x1, y1, x2, y2 = [int(value) for value in block.xyxy]
        width, height = x2 - x1, y2 - y1
        image_height, image_width = image_shape[:2]
        group = getattr(block, "_composite_render_group", None)
        width_growth = 16 if group is not None else max(48, min(72, int(round(width * 0.75))))
        height_growth = 12 if group is not None else max(16, min(48, int(round(height * 0.3))))

        def dimensions(start: int, end: int) -> List[int]:
            values = list(range(start, end + 1, 2))
            if not values or values[-1] != end:
                values.append(end)
            return values

        neighbors = [
            other
            for other in regions
            if other is not block
            and (group is None or getattr(other, "_composite_render_group", None) != group)
        ]
        candidate_sizes = sorted(
            (
                (candidate_width, candidate_height)
                for candidate_width in dimensions(width, min(image_width, width + width_growth))
                for candidate_height in dimensions(height, min(image_height, height + height_growth))
            ),
            key=lambda size: (size[0] - width + size[1] - height, size[0] * size[1]),
        )

        best = None
        best_growth = None
        for candidate_width, candidate_height in candidate_sizes:
            growth = candidate_width - width + candidate_height - height
            if best_growth is not None and growth > best_growth:
                break
            layout = fit_box(candidate_width, candidate_height)
            if layout[3] < minimum_font_size:
                continue

            left = max(0, min(image_width - candidate_width, int(round((x1 + x2) / 2 - candidate_width / 2))))
            top = max(0, min(image_height - candidate_height, int(round((y1 + y2) / 2 - candidate_height / 2))))
            right, bottom = left + candidate_width, top + candidate_height

            overlap_area = sum(
                max(0, min(right, other.x2) - max(left, other.x1))
                * max(0, min(bottom, other.y2) - max(top, other.y1))
                for other in neighbors
            )
            score = (overlap_area, candidate_width * candidate_height)
            if best is None or score < best[0]:
                best_growth = growth
                best = (score, [left, top, right, bottom], layout)

        return None if best is None else (best[1], best[2])

    def _build_render_regions(
        self, blocks: List[TextBlock], image_shape: Tuple[int, ...]
    ) -> List[TextBlock]:
        """Build non-mutating render regions while preserving detector/OCR blocks."""
        height, width = image_shape[:2]
        bubble_groups: Dict[Tuple[int, int, int, int], List[TextBlock]] = {}
        ordered_items: List[Tuple[Optional[Tuple[int, int, int, int]], List[TextBlock]]] = []

        for block in self._suppress_composite_render_regions(blocks):
            translated = getattr(block, "translated_text", None)
            if translated is None or str(translated).strip() == "":
                continue
            if _is_unchanged_punctuation(getattr(block, "text", ""), translated):
                continue

            bubble = getattr(block, "bubble_xyxy", None)
            bubble_key = None
            if block.block_type == "text" and bubble is not None:
                try:
                    values = tuple(int(value) for value in bubble)
                    if len(values) == 4:
                        clamped = _clamp_xyxy(values, width, height)
                        if clamped[2] > clamped[0] and clamped[3] > clamped[1]:
                            bubble_key = tuple(clamped)
                except (TypeError, ValueError):
                    bubble_key = None

            if bubble_key is None:
                ordered_items.append((None, [block]))
            elif bubble_key in bubble_groups:
                bubble_groups[bubble_key].append(block)
            else:
                bubble_groups[bubble_key] = [block]
                ordered_items.append((bubble_key, bubble_groups[bubble_key]))

        regions: List[TextBlock] = []
        for bubble_key, grouped in ordered_items:
            region = (
                self._standalone_region(grouped[0], width, height)
                if bubble_key is None
                else self._bubble_region(bubble_key, grouped)
            )
            if region is not None:
                regions.append(region)

        return self._partition_render_regions(self._suppress_composite_render_regions(regions))

    @staticmethod
    def _standalone_region(source: TextBlock, width: int, height: int) -> Optional[TextBlock]:
        coords = _clamp_xyxy([int(coord) for coord in source.xyxy], width, height)
        if coords[2] <= coords[0] or coords[3] <= coords[1]:
            return None
        region = TextBlock(
            coords,
            block_type=source.block_type,
            text=getattr(source, "text", ""),
            translated_text=getattr(source, "translated_text", ""),
        )
        region.confidence = float(getattr(source, "confidence", 1.0))
        region._render_from_bubble = False
        if getattr(source, "bubble_text_center", None) is not None:
            region.bubble_text_center = list(source.bubble_text_center)
        return region

    def _bubble_region(
        self, bubble_key: Tuple[int, int, int, int], blocks: List[TextBlock]
    ) -> TextBlock:
        """Merge the blocks sharing a bubble into one region inset from its edge."""
        deduplicated: Dict[Tuple[str, str], TextBlock] = {}
        for block in blocks:
            key = (
                self._normalize_render_source(getattr(block, "text", "")),
                self._normalize_render_source(getattr(block, "translated_text", "")),
            )
            existing = deduplicated.get(key)
            if existing is None or float(getattr(block, "confidence", 1.0)) > float(
                getattr(existing, "confidence", 1.0)
            ):
                deduplicated[key] = block
        grouped = list(deduplicated.values())

        vertical_source = sum(
            block.height > block.width * 1.25 for block in grouped
        ) > len(grouped) / 2
        if vertical_source:
            grouped.sort(key=lambda block: (-block.x2, block.y1))
        else:
            grouped.sort(key=lambda block: (block.y1, block.x1))

        composite_groups = {
            getattr(block, "_composite_render_group", None)
            for block in grouped
            if getattr(block, "_composite_render_group", None) is not None
        }
        composite_group = next(iter(composite_groups)) if len(composite_groups) == 1 else None

        bx1, by1, bx2, by2 = bubble_key
        inset = 0
        if composite_group is None:
            shorter_side = min(bx2 - bx1, by2 - by1)
            inset = min(8, max(2, int(shorter_side * 0.05)), max(0, (shorter_side - 1) // 2))
        coords = [bx1 + inset, by1 + inset, bx2 - inset, by2 - inset]
        if coords[2] <= coords[0] or coords[3] <= coords[1]:
            coords = list(bubble_key)

        def joined(attribute: str) -> str:
            values = (str(getattr(block, attribute, "")).strip() for block in grouped)
            return " ".join(value for value in values if value)

        region = TextBlock(
            coords,
            block_type="text",
            text=joined("text"),
            translated_text=joined("translated_text"),
        )
        region.confidence = max(
            (float(getattr(block, "confidence", 1.0)) for block in grouped),
            default=1.0,
        )
        region._render_from_bubble = True
        body_centers = [
            block.bubble_text_center
            for block in grouped
            if getattr(block, "bubble_text_center", None) is not None
        ]
        if body_centers:
            region.bubble_text_center = [
                float(np.median([center[0] for center in body_centers])),
                float(np.median([center[1] for center in body_centers])),
            ]
        if composite_group is not None:
            region._composite_render_group = composite_group
        return region

    @staticmethod
    def _expand_short_standalone_regions(
        regions: List[TextBlock], image_shape: Tuple[int, ...]
    ) -> int:
        """Give tiny short labels enough room to remain readable."""
        height, width = image_shape[:2]
        expanded = 0

        def grown_start(start, end, center, neighbor_center, size, original_size) -> int:
            # Grow away from the nearest bubble, or evenly when there is none.
            if size == original_size or (neighbor_center is not None and center > neighbor_center):
                return start
            if neighbor_center is not None and center < neighbor_center:
                return end - size
            return int(round(center - size / 2))

        for region in regions:
            if getattr(region, "_render_from_bubble", False):
                continue
            text = str(getattr(region, "translated_text", "") or "").strip()
            compact = re.sub(r"\s+", "", text)
            if not compact or len(compact) > 12 or _has_cjk(compact):
                continue

            original_width = region.width
            original_height = region.height
            target_width = min(
                width,
                original_width + 16,
                max(original_width, min(48, max(30, len(compact) * 6))),
            )
            target_height = min(height, original_height + 12, max(original_height, 24))
            if target_width == original_width and target_height == original_height:
                continue

            center_x, center_y = region.center
            others = [other for other in regions if other is not region]
            nearest_bubble = min(
                (other for other in others if getattr(other, "_render_from_bubble", False)),
                key=lambda other: (center_x - other.center[0]) ** 2
                + (center_y - other.center[1]) ** 2,
                default=None,
            )
            neighbor_x, neighbor_y = nearest_bubble.center if nearest_bubble is not None else (None, None)

            def candidate_box(box_width: int, box_height: int) -> List[int]:
                x1 = grown_start(region.x1, region.x2, center_x, neighbor_x, box_width, original_width)
                y1 = grown_start(region.y1, region.y2, center_y, neighbor_y, box_height, original_height)
                x1 = max(0, min(width - box_width, x1))
                y1 = max(0, min(height - box_height, y1))
                return [x1, y1, x1 + box_width, y1 + box_height]

            def overlaps_other(box: List[int]) -> bool:
                return any(
                    box[0] < other.x2 and box[2] > other.x1 and box[1] < other.y2 and box[3] > other.y1
                    for other in others
                )

            best = next(
                (
                    box
                    for box_width in range(target_width, original_width - 1, -1)
                    for box_height in range(target_height, original_height - 1, -1)
                    for box in [candidate_box(box_width, box_height)]
                    if not overlaps_other(box)
                ),
                None,
            )

            if best is not None and best != region.xyxy.tolist():
                region.xyxy = best
                expanded += 1

        return expanded

    @staticmethod
    def _normalize_render_source(text: str) -> str:
        return "".join(unicodedata.normalize("NFKC", str(text)).split())

    def _suppress_composite_render_regions(
        self, regions: List[TextBlock]
    ) -> List[TextBlock]:
        """Drop exact OCR composites while retaining legitimate nested text."""

        def from_bubble(region: TextBlock) -> bool:
            return bool(getattr(region, "_render_from_bubble", False))

        def duplicate_rank(region: TextBlock):
            return (
                from_bubble(region)
                or (region.block_type == "text" and getattr(region, "bubble_xyxy", None) is not None),
                region.confidence,
            )

        unique: Dict[Tuple[Tuple[int, int, int, int], str], TextBlock] = {}
        for region in regions:
            key = (
                tuple(int(value) for value in region.xyxy),
                self._normalize_render_source(region.text),
            )
            existing = unique.get(key)
            if existing is None or duplicate_rank(region) > duplicate_rank(existing):
                unique[key] = region

        candidates = list(unique.values())
        suppressed = set()
        for first_index, first in enumerate(candidates):
            if first_index in suppressed:
                continue
            first_key = (
                self._normalize_render_source(first.text),
                self._normalize_render_source(first.translated_text),
            )
            if not any(first_key):
                continue
            for second_index in range(first_index + 1, len(candidates)):
                if second_index in suppressed:
                    continue
                second = candidates[second_index]
                second_key = (
                    self._normalize_render_source(second.text),
                    self._normalize_render_source(second.translated_text),
                )
                if first_key != second_key:
                    continue
                overlap_width = max(0, min(first.x2, second.x2) - max(first.x1, second.x1))
                overlap_height = max(0, min(first.y2, second.y2) - max(first.y1, second.y1))
                if overlap_width * overlap_height < min(first.area, second.area) * 0.8:
                    continue
                first_rank = (from_bubble(first), first.area, first.confidence)
                second_rank = (from_bubble(second), second.area, second.confidence)
                suppressed.add(second_index if first_rank >= second_rank else first_index)
                if first_index in suppressed:
                    break

        for parent_index, parent in enumerate(candidates):
            if parent_index in suppressed or from_bubble(parent):
                continue
            parent_source = self._normalize_render_source(parent.text)
            if not parent_source:
                continue

            children = [
                child
                for child_index, child in enumerate(candidates)
                if child_index != parent_index
                and child_index not in suppressed
                and child.area < parent.area
                and self._is_inside(child, parent)
            ]
            if children and self._reading_order_source(parent, children) == parent_source:
                suppressed.add(parent_index)

        return [
            region for index, region in enumerate(candidates) if index not in suppressed
        ]

    def _partition_render_regions(
        self, regions: List[TextBlock]
    ) -> List[TextBlock]:
        """Shrink overlapping render rectangles into pairwise-disjoint regions."""
        ordered = sorted(
            regions,
            key=lambda region: (
                region.y1,
                region.x1,
                region.y2,
                region.x2,
                self._normalize_render_source(region.text),
            ),
        )
        discarded = set()
        minimum_side = 12

        def long_enough(box: List[int], original: List[int]) -> bool:
            return (
                box[2] - box[0] >= min(minimum_side, original[2] - original[0])
                and box[3] - box[1] >= min(minimum_side, original[3] - original[1])
            )

        for first_index, first in enumerate(ordered):
            if first_index in discarded:
                continue
            for second_index in range(first_index + 1, len(ordered)):
                if second_index in discarded:
                    continue
                second = ordered[second_index]
                overlap_x1 = max(first.x1, second.x1)
                overlap_y1 = max(first.y1, second.y1)
                overlap_x2 = min(first.x2, second.x2)
                overlap_y2 = min(first.y2, second.y2)
                if overlap_x2 <= overlap_x1 or overlap_y2 <= overlap_y1:
                    continue

                best = None
                first_box = first.xyxy.tolist()
                second_box = second.xyxy.tolist()
                first_area = max(first.area, 1)
                second_area = max(second.area, 1)

                for axis, start, end in (
                    (0, overlap_x1, overlap_x2),
                    (1, overlap_y1, overlap_y2),
                ):
                    first_is_before = (first.center[axis], first_box[axis], first_index) <= (
                        second.center[axis],
                        second_box[axis],
                        second_index,
                    )
                    for split in range(start, end + 1):
                        candidate_first = list(first_box)
                        candidate_second = list(second_box)
                        before, after = (
                            (candidate_first, candidate_second)
                            if first_is_before
                            else (candidate_second, candidate_first)
                        )
                        before[axis + 2] = min(before[axis + 2], split)
                        after[axis] = max(after[axis], split)

                        if not (long_enough(candidate_first, first_box) and long_enough(candidate_second, second_box)):
                            continue
                        retained_first = (candidate_first[2] - candidate_first[0]) * (candidate_first[3] - candidate_first[1])
                        retained_second = (candidate_second[2] - candidate_second[0]) * (candidate_second[3] - candidate_second[1])
                        score = (
                            min(retained_first / first_area, retained_second / second_area),
                            retained_first / first_area + retained_second / second_area,
                            retained_first + retained_second,
                            axis == 0,
                            -split,
                        )
                        if best is None or score > best[0]:
                            best = (score, candidate_first, candidate_second)

                if best is None:
                    first_rank = (first.confidence, first.area, -first_index)
                    second_rank = (second.confidence, second.area, -second_index)
                    discarded.add(second_index if first_rank >= second_rank else first_index)
                    if first_index in discarded:
                        break
                    continue

                first.xyxy = best[1]
                second.xyxy = best[2]

        return [
            region for index, region in enumerate(ordered) if index not in discarded
        ]

    def _wrap_text_to_lines(
        self, text: str, font, width: int, stroke_width: int = 0
    ) -> List[str]:
        """Wrap using the same pixel measurements used by font fitting."""
        max_width = max(1, int(width))

        def measured_width(value: str) -> int:
            bbox = font.getbbox(value)
            if not bbox:
                return 0
            return bbox[2] - bbox[0] + stroke_width * 2

        def split_oversized_unit(unit: str) -> List[str]:
            if measured_width(unit) <= max_width:
                return [unit]

            chunks: List[str] = []
            current = ""
            for char in unit:
                candidate = current + char
                if current and measured_width(candidate) > max_width:
                    chunks.append(current)
                    current = char
                else:
                    current = candidate
            if current:
                chunks.append(current)
            return chunks or [unit]

        lines: List[str] = []
        for paragraph in str(text).split("\n"):
            if paragraph == "":
                lines.append("")
                continue

            character_wrap = _has_cjk(paragraph) and not any(
                char.isspace() for char in paragraph
            )
            if character_wrap:
                units = list(paragraph)
            else:
                units = []
                for token in paragraph.split():
                    units.extend(
                        re.findall(r".+?[—–](?=.)|.+", token)
                    )
            if not units:
                lines.append("")
                continue

            current = ""
            for unit in units:
                chunks = split_oversized_unit(unit)
                if len(chunks) > 1:
                    if current:
                        lines.append(current)
                    lines.extend(chunks[:-1])
                    current = chunks[-1]
                    continue

                unit = chunks[0]
                if not current:
                    current = unit
                    continue
                separator = (
                    "" if character_wrap or current.endswith(("—", "–")) else " "
                )
                candidate = current + separator + unit
                if measured_width(candidate) <= max_width:
                    current = candidate
                else:
                    lines.append(current)
                    current = unit
            lines.append(current)

        return lines or [""]

    def _draw_vertical_text(
        self,
        draw,
        text,
        position,
        font,
        fill,
        stroke_width=0,
        stroke_fill=None,
        spacing=0,
    ):
        """Draw one character per line for explicit vertical text direction."""
        x, y = position
        step = max(int(getattr(font, "size", 12) + spacing), 1)
        for index, char in enumerate(str(text).replace("\n", "")):
            draw.text(
                (x, y + index * step),
                char,
                font=font,
                fill=fill,
                anchor="ma",
                stroke_width=stroke_width,
                stroke_fill=stroke_fill,
            )

    def _auto_fit_font_size(
        self,
        font_path: Optional[str],
        text: str,
        width: int,
        height: int,
        line_spacing: float,
        align: str,
        stroke_width: int = 0,
        preferred_font_size: int = 16,
        vertical: bool = False,
    ) -> int:
        from PIL import Image, ImageDraw

        if not text:
            return 12

        low = 1
        high = max(low, min(96, int(preferred_font_size)))
        best = 0
        probe = Image.new("L", (1, 1), 0)
        draw = ImageDraw.Draw(probe)

        for _ in range(12):  # ~12 iterations gives sub-pixel precision
            if low > high:
                break
            mid = (low + high) // 2

            font = _get_font_object(font_path, mid)
            spacing = max(0, int((line_spacing - 1.0) * mid))
            candidate_stroke_width = min(
                stroke_width, max(0, int(round(mid * 0.15)))
            )
            if vertical:
                characters = list(str(text).replace("\n", ""))
                glyph_boxes = [
                    draw.textbbox(
                        (0, 0), char, font=font, stroke_width=candidate_stroke_width
                    )
                    for char in characters
                ]
                rendered_width = max(
                    (box[2] - box[0] for box in glyph_boxes), default=0
                )
                rendered_height = len(characters) * max(mid + spacing, 1)
            else:
                words_fit = True
                if not _has_cjk(text):
                    words = str(text).replace("\n", " ").split()
                    words_fit = all(
                        (font.getbbox(word)[2] - font.getbbox(word)[0])
                        + candidate_stroke_width * 2
                        <= width
                        for word in words
                    )
                wrapped = "\n".join(
                    self._wrap_text_to_lines(
                        text, font, width, stroke_width=candidate_stroke_width
                    )
                )
                bbox = draw.multiline_textbbox(
                    (0, 0),
                    wrapped,
                    font=font,
                    spacing=spacing,
                    align=align,
                    stroke_width=candidate_stroke_width,
                )
                rendered_width = bbox[2] - bbox[0]
                rendered_height = bbox[3] - bbox[1]

            if (
                rendered_height <= height
                and rendered_width <= width
                and (vertical or words_fit)
            ):
                best = mid
                low = mid + 1
            else:
                high = mid - 1

        return best
