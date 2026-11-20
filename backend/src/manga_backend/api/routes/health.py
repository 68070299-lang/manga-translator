"""Liveness and readiness endpoints."""

from __future__ import annotations

import time
from collections.abc import Callable

from fastapi import APIRouter

from manga_backend.api.deps import BackendDep
from manga_backend.api.responses import OrjsonResponse
from manga_backend.api.sanitize import scrub_model_status
from manga_backend.api.schemas import HealthResponse, RootResponse

router = APIRouter()


@router.get("/", response_model=RootResponse, response_class=OrjsonResponse)
async def root() -> RootResponse:
    """Health check endpoint."""
    return RootResponse(status="ok", message="Manga Translation API is running")


@router.get("/health", response_model=HealthResponse, response_class=OrjsonResponse)
async def health(backend: BackendDep) -> HealthResponse:
    """Readiness check with model status; 'loading' while models initialize in the background."""
    pipeline = backend.pipeline
    loading = backend.pipeline_is_loading()
    models_loaded = not loading and pipeline is not None and pipeline.is_ready
    readiness_fn: Callable[[], dict[str, bool]] | None = (
        getattr(pipeline, "readiness", None) if pipeline else None
    )
    components: dict[str, bool] = (
        readiness_fn()
        if callable(readiness_fn)
        else {
            name: details.get("status") == "loaded"
            for name, details in (pipeline.model_status if pipeline else {}).items()
            if isinstance(details, dict)
        }
    )
    return HealthResponse(
        status="loading" if loading else ("ok" if models_loaded else "degraded"),
        models_loaded=models_loaded,
        device=pipeline.device if pipeline else "unknown",
        models=scrub_model_status(pipeline.model_status) if pipeline else {},
        components=components,
        uptime_seconds=round(time.monotonic() - backend.started_at, 3),
        active_translate_requests=backend.active_requests,
        config=backend.sanitized_config(),
        last_error=backend.last_error,
    )
