"""POST /translate: detect, OCR, translate, inpaint and render a page."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, File, Request, UploadFile
from starlette.responses import Response

from manga_backend.api import images
from manga_backend.api.deps import BackendDep, RequestIdDep, TranslateOptionsDep, require_components
from manga_backend.api.errors import raise_endpoint_error
from manga_backend.api.responses import OrjsonResponse
from manga_backend.api.sanitize import sanitize_error
from manga_backend.api.schemas import TranslateTextResponse
from manga_backend.api.validation import (
    VALID_DETECTORS,
    VALID_INPAINTERS,
    VALID_OCR_ENGINES,
    VALID_TRANSLATORS,
    bad_request,
    normalize_choice,
    preflight_translator_inputs,
    required_ocr_components,
    validate_render_options,
)
from manga_backend.pipeline import align_texts

logger = logging.getLogger(__name__)
router = APIRouter()


def normalize_result_arrays(result: Any) -> dict[str, Any]:
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


@router.post("/translate", response_model=None)
async def translate_image(
    request: Request,
    backend: BackendDep,
    request_id: RequestIdDep,
    image: Annotated[UploadFile, File(description="Manga image to translate")],
    options: TranslateOptionsDep,
) -> Response:
    """Translate a manga image.

    Returns the translated image or JSON with extracted text.
    """
    started = time.monotonic()

    try:
        validate_render_options(options)

        detector = normalize_choice(options.detector, VALID_DETECTORS, "detector")
        ocr = normalize_choice(options.ocr, VALID_OCR_ENGINES, "ocr")
        translator = normalize_choice(options.translator, VALID_TRANSLATORS, "translator")
        inpainter = normalize_choice(options.inpainter, VALID_INPAINTERS, "inpainter")
        required_components = ["detector"] if detector == "rtdetr" else []
        required_components.extend(required_ocr_components(ocr))
        pipeline = require_components(backend, *required_components)
        if ocr == "google-ocr" and not options.google_ocr_api_key:
            raise bad_request("google_ocr_api_key is required when ocr is google-ocr")
        preflight_translator_inputs(options, translator, backend.settings)
        async with backend.request_slot():
            img_array = await images.read_upload_image(image, backend.limits)
            height, width = img_array.shape[:2]
            logger.info(
                "translate start request_id=%s content_type=%s width=%s height=%s detector=%s ocr=%s translator=%s inpainter=%s render=%s target_lang=%s",
                request_id,
                image.content_type,
                width,
                height,
                detector,
                ocr,
                translator,
                inpainter,
                options.render,
                options.target_lang,
            )

            result = normalize_result_arrays(
                await pipeline.translate(
                    image=img_array,
                    target_lang=options.target_lang,
                    detector=detector,
                    ocr=ocr,
                    translator=translator,
                    inpainter=inpainter,
                    render=options.render,
                    api_key=options.api_key or None,
                    model=options.model or None,
                    google_ocr_api_key=options.google_ocr_api_key or None,
                    google_ocr_language_hint=options.google_ocr_language_hint or None,
                    line_spacing=options.line_spacing,
                    border_size=options.border_size,
                    text_color=options.text_color,
                    border_color=options.border_color,
                    bg_color=options.bg_color,
                    bg_opacity=options.bg_opacity,
                    text_alignment=options.text_alignment,
                    text_direction=options.text_direction,
                    uppercase=options.uppercase,
                    auto_scale=options.auto_scale,
                    preferred_font_size=options.font_size,
                    local_llm_base_url=options.local_llm_base_url or None,
                    local_llm_api_key=options.local_llm_api_key or None,
                    verify_translation=options.verify_translation,
                )
            )

        if options.render and result.get("image") is not None:
            payload = await asyncio.to_thread(images.encode_png, result["image"])
            inpainter_used = result.get("inpainter_used", inpainter)
            inpainting_fallback = bool(result.get("inpainting_fallback", False))
            logger.info(
                "translate success request_id=%s response_type=image/png output_bytes=%s "
                "inpainter_used=%s inpainting_fallback=%s duration=%.3fs",
                request_id,
                len(payload),
                inpainter_used,
                inpainting_fallback,
                time.monotonic() - started,
            )
            return Response(
                content=payload,
                media_type="image/png",
                headers={"X-Inpainter-Used": inpainter_used},
            )

        text = TranslateTextResponse(
            blocks=result.get("blocks", []),
            original_texts=result.get("original_texts", []),
            translated_texts=result.get("translated_texts", []),
        )
        logger.info(
            "translate success request_id=%s response_type=application/json output_blocks=%s duration=%.3fs",
            request_id,
            len(text.blocks),
            time.monotonic() - started,
        )
        return OrjsonResponse(text)

    except Exception as error:
        status_code = getattr(error, "status_code", 500)
        sensitive_values = options.secrets
        logger.error(
            "translate failed request_id=%s status=%s error=%s duration=%.3fs",
            request_id,
            status_code,
            sanitize_error(error, sensitive_values),
            time.monotonic() - started,
        )
        raise_endpoint_error(
            backend,
            "Translation failed",
            error,
            request_id=request_id,
            path="/translate",
            sensitive_values=sensitive_values,
        )
