"""POST /ocr and POST /paddle-ocr: text recognition."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile

from manga_backend.api import images
from manga_backend.api.deps import BackendDep, RequestIdDep, require_components
from manga_backend.api.errors import endpoint_errors
from manga_backend.api.responses import OrjsonResponse
from manga_backend.api.schemas import OcrResponse, PaddleOcrResponse
from manga_backend.api.validation import (
    VALID_OCR_ENGINES,
    normalize_choice,
    normalize_paddle_lang,
    required_ocr_components,
)

router = APIRouter()


@router.post("/ocr", response_model=OcrResponse, response_class=OrjsonResponse)
async def ocr_image(
    request: Request,
    backend: BackendDep,
    request_id: RequestIdDep,
    image: Annotated[UploadFile, File(description="Image to OCR")],
    ocr: Annotated[
        str, Form(description="OCR model: manga-ocr or paddle-ocr")
    ] = "manga-ocr",
) -> OcrResponse:
    """Extract text from an image."""
    with endpoint_errors(
        backend,
        "OCR failed",
        request_id=request_id,
        path=request.url.path,
    ):
        engine = normalize_choice(ocr, VALID_OCR_ENGINES, "ocr")
        pipeline = require_components(backend, "detector", *required_ocr_components(engine))
        async with backend.request_slot():
            img_array = await images.read_upload_image(image, backend.limits)

            blocks = await pipeline.detect(img_array)
            texts = await pipeline.ocr(img_array, blocks, ocr=engine)

        return OcrResponse(texts=texts)


@router.post("/paddle-ocr", response_model=PaddleOcrResponse, response_class=OrjsonResponse)
async def paddle_ocr_full(
    request: Request,
    backend: BackendDep,
    request_id: RequestIdDep,
    image: Annotated[UploadFile, File(description="Image for PaddleOCR")],
    lang: Annotated[str, Form(description="Language: japan, ch, en, korean")] = "japan",
) -> PaddleOcrResponse:
    """Full PaddleOCR pipeline: detection and recognition in one call (PP-OCRv5)."""
    with endpoint_errors(
        backend,
        "PaddleOCR failed",
        request_id=request_id,
        path=request.url.path,
        sensitive_values=[],
    ):
        language = normalize_paddle_lang(lang)
        pipeline = require_components(backend, "paddle_ocr")
        async with backend.request_slot():
            img_array = await images.read_upload_image(image, backend.limits)
            results = await pipeline.paddle_ocr_full(img_array, lang=language)

        return PaddleOcrResponse(blocks=results, total=len(results))
