"""Application lifespan: background model initialization and shutdown cleanup."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI

from manga_backend.api.sanitize import sanitize_error
from manga_backend.api.state import BackendState
from manga_backend.pipeline import TranslationPipeline
from manga_backend.utils.device import get_device

logger = logging.getLogger(__name__)


def default_pipeline_factory(use_gpu: bool) -> TranslationPipeline:
    return TranslationPipeline(device=get_device(use_gpu=use_gpu))


async def initialize_pipeline(target: TranslationPipeline) -> None:
    """Load the startup models, then the inpainter the first page will need.

    Creating an ONNX session holds the interpreter lock, so the server cannot
    answer while a model loads (LaMa takes 10-20 s). Doing that load here keeps
    it inside the "loading" phase instead of freezing the first translation.
    """
    await target.initialize()
    warm_up: Callable[[], Awaitable[None]] | None = getattr(target, "warm_up", None)
    if callable(warm_up):
        await warm_up()


def _on_pipeline_init_done(backend: BackendState, task: asyncio.Task[None]) -> None:
    """Record a failed background initialization so /health can report it."""
    if task.cancelled():
        return
    error = task.exception()
    pipeline = backend.pipeline
    if error is None:
        logger.info("Pipeline initialized on device: %s", getattr(pipeline, "device", "unknown"))
        return
    logger.error("Pipeline initialization failed error=%s", sanitize_error(error))
    backend.record_last_error("startup", "startup", error, 500)
    statuses = getattr(pipeline, "model_status", None)
    if isinstance(statuses, dict):
        for details in statuses.values():
            if isinstance(details, dict) and details.get("status") == "loading":
                details["status"] = "error"
                details["error"] = sanitize_error(error)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start model initialization in the background so / and /health answer immediately."""
    backend: BackendState = app.state.backend
    injected_pipeline = backend.pipeline is not None
    init_task: asyncio.Task[None] | None = None
    backend.pipeline_init_task = None

    if backend.settings.testing:
        logger.warning(
            "MANGA_TRANSLATOR_TESTING=1: backend authentication is DISABLED and "
            "backend/.env is not loaded. Never use this mode outside tests."
        )

    if backend.pipeline is not None:
        # A pre-built (test) pipeline is used as-is; it is not initialized or disposed here.
        logger.info("Using pre-initialized translation pipeline")
    else:
        logger.info("Initializing translation pipeline in the background...")
        backend.pipeline = backend.pipeline_factory(backend.settings.use_gpu)
        init_task = asyncio.create_task(
            initialize_pipeline(backend.pipeline), name="pipeline-initialize"
        )
        init_task.add_done_callback(partial(_on_pipeline_init_done, backend))
        backend.pipeline_init_task = init_task

    try:
        yield
    finally:
        logger.info("Shutting down pipeline...")
        if init_task is not None and not init_task.done():
            init_task.cancel()
            # Cancellation is expected; any real failure was recorded by the done callback.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await init_task
        backend.pipeline_init_task = None
        if not injected_pipeline and backend.pipeline is not None:
            dispose = getattr(backend.pipeline, "dispose", None)
            if callable(dispose):
                dispose()
            backend.pipeline = None
