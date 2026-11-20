"""Pipeline errors. Each carries the HTTP status and sanitized detail the API reports."""

from __future__ import annotations


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

    def __init__(self, message: str = "", inpainter: str | None = None) -> None:
        super().__init__(message)
        hint = INPAINTER_UNAVAILABLE_HINTS.get(inpainter or "")
        if hint:
            self.public_detail = hint


class DetectorUnavailableError(RuntimeError):
    """The requested optional detector runtime could not be initialized."""

    status_code = 503
    public_detail = "Requested detector runtime is unavailable"

    def __init__(self, message: str = "", detector: str | None = None) -> None:
        super().__init__(message)
        hint = DETECTOR_UNAVAILABLE_HINTS.get(detector or "")
        if hint:
            self.public_detail = hint


class PaddleOCRUnavailableError(RuntimeError):
    """PaddleOCR could not be loaded (model download or runtime failure)."""

    status_code = 503
    public_detail = (
        "PaddleOCR is unavailable (its models failed to download or load); "
        "see models.paddle_ocr in /health"
    )
