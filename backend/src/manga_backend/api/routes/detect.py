"""POST /detect: text-region detection."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile

from manga_backend.api import images
from manga_backend.api.deps import BackendDep, RequestIdDep, require_components
from manga_backend.api.errors import endpoint_errors
from manga_backend.api.responses import OrjsonResponse
from manga_backend.api.schemas import DetectedBlock, DetectResponse
from manga_backend.api.validation import VALID_DETECTORS, normalize_choice

router = APIRouter()


@router.post("/detect", response_model=DetectResponse, response_class=OrjsonResponse)
async def detect_text(
    request: Request,
    backend: BackendDep,
    request_id: RequestIdDep,
    image: Annotated[UploadFile, File(description="Image to detect text in")],
    detector: Annotated[str, Form(description="Detection model")] = "rtdetr",
) -> DetectResponse:
    """Detect text regions in an image."""
    with endpoint_errors(
        backend,
        "Detection failed",
        request_id=request_id,
        path=request.url.path,
        sensitive_values=[],
    ):
        detector = normalize_choice(detector, VALID_DETECTORS, "detector")
        required_components = ["detector"] if detector == "rtdetr" else []
        pipeline = require_components(backend, *required_components)
        async with backend.request_slot():
            img_array = await images.read_upload_image(image, backend.limits)
            blocks = await pipeline.detect(img_array, detector=detector)

        return DetectResponse(
            blocks=[
                DetectedBlock(bbox=block["bbox"], type=block.get("type", "text"))
                for block in blocks
            ]
        )
