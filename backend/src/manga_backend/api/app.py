"""Application factory for the Manga Translation API.

A local FastAPI server that runs text detection, OCR, inpainting and translation
with ONNX Runtime (no PyTorch or build tools required). Based on:
- comic-translate (ogkalu2/comic-translate)
- manga-image-translator (zyddnys/manga-image-translator)

Run it with `python -m manga_backend` or `uvicorn manga_backend.main:app`.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from manga_backend.api.auth import EXTENSION_ORIGIN_RE
from manga_backend.api.lifespan import default_pipeline_factory, lifespan
from manga_backend.api.middleware import RequestGuardMiddleware
from manga_backend.api.responses import OrjsonResponse
from manga_backend.api.routes import detect, health, ocr, translate
from manga_backend.api.state import BackendState, PipelineFactory
from manga_backend.config import Limits, Settings, load_settings
from manga_backend.pipeline import TranslationPipeline


def create_app(
    settings: Settings | None = None,
    *,
    limits: Limits | None = None,
    pipeline: TranslationPipeline | None = None,
    pipeline_factory: PipelineFactory | None = None,
) -> FastAPI:
    """Build the API.

    `pipeline` injects a pre-built pipeline that is used as-is (never initialized
    or disposed by the lifespan); `pipeline_factory` replaces how the lifespan
    builds the real one.
    """
    resolved = settings if settings is not None else load_settings()
    backend = BackendState(
        resolved,
        limits if limits is not None else Limits(),
        pipeline=pipeline,
        pipeline_factory=pipeline_factory or default_pipeline_factory,
    )
    app = FastAPI(
        title="Manga Translation API",
        description="Translate manga images using OCR and LLM translation",
        version="1.0.0",
        lifespan=lifespan,
        default_response_class=OrjsonResponse,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.backend = backend

    # Added first so it sits inside the request guard: rejected requests carry no CORS headers.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(resolved.allowed_origins),
        allow_origin_regex=EXTENSION_ORIGIN_RE.pattern,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestGuardMiddleware, backend=backend)

    for module in (health, translate, detect, ocr):
        app.include_router(module.router)
    return app
