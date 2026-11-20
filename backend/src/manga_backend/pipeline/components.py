"""Component loading and status: lazy components, model_status and readiness."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from manga_backend.pipeline.errors import PaddleOCRUnavailableError
from manga_backend.pipeline.inpainting import DEFAULT_INPAINTER, LAZY_INPAINTERS

if TYPE_CHECKING:
    from manga_backend.models.paddle_ocr_onnx import PaddleOCROnnxEngine

logger = logging.getLogger(__name__)

# Components loaded by the first request that needs them. Startup does not wait
# for them and a failed load only affects the routes that use them.
LAZY_COMPONENTS = frozenset({"paddle_ocr", "text_segmenter"})

ModelStatus = dict[str, dict[str, Any]]

_paddle_ocr_engines: dict[str, PaddleOCROnnxEngine] = {}


class _Initializable(Protocol):
    async def initialize(self) -> None: ...


_EngineT = TypeVar("_EngineT", bound=_Initializable)


def new_model_status() -> ModelStatus:
    return {
        "detector": {"status": "not_loaded", "name": "rtdetr"},
        "animetext_yolo": {"status": "not_loaded", "name": "animetext-yolo"},
        "ocr": {"status": "not_loaded", "name": "manga-ocr"},
        "paddle_ocr": {"status": "not_loaded", "name": "paddle-ocr"},
        "text_segmenter": {"status": "not_loaded", "name": "comic-text-detector"},
        "inpainter": {"status": "not_loaded", "name": DEFAULT_INPAINTER},
    }


def component_ready(
    model_status: ModelStatus,
    component: str,
    *,
    initialized: bool,
    inpainter_type: str,
) -> bool:
    """Report readiness independently so optional failures do not mask healthy routes."""
    if component == "inpainter" and inpainter_type == "none":
        return True
    status = model_status.get(component, {}).get("status")
    # Large Flux runtimes load on first use so health checks remain fast.
    if component == "inpainter" and inpainter_type in LAZY_INPAINTERS:
        return initialized and status in ("loaded", "not_loaded", "loading")
    if component in LAZY_COMPONENTS:
        # Healthy until a load attempt fails; the next request retries it.
        return initialized and status in ("loaded", "not_loaded", "loading")
    return initialized and status == "loaded"


def get_paddle_ocr(lang: str = "japan", device: str = "cpu") -> PaddleOCROnnxEngine:
    """Get or create PaddleOCR ONNX engine (lazy loading)."""
    # The Paddle models are multilingual and the engine ignores `lang`, so share
    # one engine (and one set of ORT sessions) per device.
    if device not in _paddle_ocr_engines:
        from manga_backend.models.paddle_ocr_onnx import PaddleOCROnnxEngine

        _paddle_ocr_engines[device] = PaddleOCROnnxEngine(lang=lang, device=device)
    return _paddle_ocr_engines[device]


async def prepare_paddle_engine(
    model_status: ModelStatus, device: str, lang: str = "japan"
) -> PaddleOCROnnxEngine:
    """Return an initialized Paddle engine, loading it on first use.

    get_paddle_ocr() caches one engine per device without awaiting, and the
    engine serializes initialize() behind its own lock, so concurrent first
    requests share a single load. Only an initialize() failure marks the
    component as error (and the next request retries it). Recognition
    failures and cancellation happen after this returns, so they leave the
    loaded status untouched and concurrent requests keep being admitted.
    """
    status = model_status["paddle_ocr"]
    previous = status.get("status")
    if previous != "loaded":
        status["status"] = "loading"
    try:
        engine = get_paddle_ocr(lang=lang, device=device)
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


async def preload(
    model_status: ModelStatus,
    component: str,
    factory: Callable[[], _EngineT],
    on_created: Callable[[_EngineT], None],
    *,
    required: bool = True,
) -> None:
    """Load one startup model, recording failures without aborting startup."""
    status = model_status[component]
    status["status"] = "loading"
    try:
        engine = factory()
        on_created(engine)
        await engine.initialize()
        status["status"] = "loaded"
        logger.info("%s loaded", component)
    except Exception as error:
        status.update({"status": "error", "error": str(error)})
        log_failure = logger.error if required else logger.warning
        log_failure("Failed to load %s: %s", component, error)
