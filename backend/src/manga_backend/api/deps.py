"""FastAPI dependencies shared by the routers."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Form, HTTPException, Request

from manga_backend.api.schemas import TranslateOptions
from manga_backend.api.state import BackendState
from manga_backend.pipeline import DEFAULT_INPAINTER, LAZY_COMPONENTS, TranslationPipeline

REQUEST_ID_HEADER = "X-Request-ID"


def get_backend(request: Request) -> BackendState:
    return request.app.state.backend


def get_request_id(request: Request) -> str:
    request_id = request.headers.get(REQUEST_ID_HEADER) or getattr(request.state, "request_id", "")
    return request_id or uuid.uuid4().hex


BackendDep = Annotated[BackendState, Depends(get_backend)]
RequestIdDep = Annotated[str, Depends(get_request_id)]


def require_components(backend: BackendState, *components: str) -> TranslationPipeline:
    """Require only the components needed by an endpoint.

    Lazy components (PaddleOCR) are loaded by the request itself, so they are
    not required to be loaded here and a failed load is retried, not latched.
    """
    pipeline = backend.pipeline
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline not initialized")
    if backend.pipeline_is_loading():
        raise HTTPException(
            status_code=503, detail="Backend component unavailable: models are still loading"
        )
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


def translate_options(
    target_lang: Annotated[str, Form(description="Target language")] = "English",
    detector: Annotated[str, Form(description="Detection model")] = "rtdetr",
    ocr: Annotated[
        str, Form(description="OCR model: manga-ocr, paddle-ocr, google-ocr")
    ] = "manga-ocr",
    translator: Annotated[
        str, Form(description="Translation: none, gemini, openai, openrouter, local")
    ] = "none",
    inpainter: Annotated[str, Form(description="Inpainting model")] = DEFAULT_INPAINTER,
    render: Annotated[bool, Form(description="Render text on image")] = True,
    api_key: Annotated[
        str, Form(description="API key for translation (Gemini/OpenAI/OpenRouter/local)")
    ] = "",
    model: Annotated[str, Form(description="Model name for translation API")] = "",
    google_ocr_api_key: Annotated[str, Form(description="Google Cloud Vision API key")] = "",
    google_ocr_language_hint: Annotated[str, Form(description="Language hint for Google OCR")] = "",
    local_llm_base_url: Annotated[
        str, Form(description="Base URL for local LLM (llama.cpp/vLLM)")
    ] = "",
    local_llm_api_key: Annotated[str, Form(description="API key for local LLM (if needed)")] = "",
    line_spacing: Annotated[
        float, Form(description="Line spacing multiplier for rendered text")
    ] = 1.2,
    border_size: Annotated[float, Form(description="Text stroke/border size in pixels")] = 2.0,
    text_color: Annotated[str, Form(description="Text color (hex)")] = "#000000",
    border_color: Annotated[str, Form(description="Text border/stroke color (hex)")] = "#ffffff",
    bg_color: Annotated[str, Form(description="Background fill color (hex)")] = "#ffffff",
    bg_opacity: Annotated[float, Form(description="Background fill opacity (0-1)")] = 0.0,
    text_alignment: Annotated[
        str, Form(description="Text alignment: left, center, right")
    ] = "center",
    text_direction: Annotated[
        str, Form(description="Text direction: auto, horizontal, vertical")
    ] = "auto",
    uppercase: Annotated[bool, Form(description="Convert text to uppercase")] = False,
    auto_scale: Annotated[bool, Form(description="Auto-fit font size to bubble")] = True,
    font_size: Annotated[
        int,
        Form(description="Preferred maximum font size; auto-scale selects the largest fit"),
    ] = 16,
    verify_translation: Annotated[
        bool, Form(description="Re-OCR rendered text to verify translation")
    ] = False,
) -> TranslateOptions:
    """Collect the POST /translate multipart form fields."""
    return TranslateOptions(
        target_lang=target_lang,
        detector=detector,
        ocr=ocr,
        translator=translator,
        inpainter=inpainter,
        render=render,
        api_key=api_key,
        model=model,
        google_ocr_api_key=google_ocr_api_key,
        google_ocr_language_hint=google_ocr_language_hint,
        local_llm_base_url=local_llm_base_url,
        local_llm_api_key=local_llm_api_key,
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
        font_size=font_size,
        verify_translation=verify_translation,
    )


TranslateOptionsDep = Annotated[TranslateOptions, Depends(translate_options)]
