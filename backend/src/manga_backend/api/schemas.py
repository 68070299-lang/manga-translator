"""Request and response models for the extension-facing HTTP contract.

Field names, field order and nullability are part of the contract with
extension/lib/api.*: models are dumped in declaration order and None stays null.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    model_config = ConfigDict(frozen=True)


# --- Health -----------------------------------------------------------------


class RootResponse(ApiModel):
    status: str
    message: str


class SanitizedConfig(ApiModel):
    allowed_browser_origins_count: int
    max_upload_size: int
    max_image_pixels: int
    use_gpu: bool


class LastError(ApiModel):
    request_id: str
    path: str
    status: int
    error: str
    timestamp: float


class HealthResponse(ApiModel):
    status: str
    models_loaded: bool
    device: str
    models: dict[str, Any]
    components: dict[str, bool]
    uptime_seconds: float
    active_translate_requests: int
    config: SanitizedConfig
    last_error: LastError | None


# --- Translate --------------------------------------------------------------


class TranslateOptions(ApiModel):
    """Multipart form fields of POST /translate (defaults live in the form dependency)."""

    target_lang: str
    detector: str
    ocr: str
    translator: str
    inpainter: str
    render: bool
    api_key: str
    model: str
    google_ocr_api_key: str
    google_ocr_language_hint: str
    local_llm_base_url: str
    local_llm_api_key: str
    line_spacing: float
    border_size: float
    text_color: str
    border_color: str
    bg_color: str
    bg_opacity: float
    text_alignment: str
    text_direction: str
    uppercase: bool
    auto_scale: bool
    font_size: int
    verify_translation: bool

    @property
    def secrets(self) -> list[str]:
        """Values that must never appear in logs or error reports."""
        return [self.api_key, self.google_ocr_api_key, self.local_llm_api_key]


class TranslateTextResponse(ApiModel):
    """JSON body of POST /translate when no rendered image is returned."""

    blocks: list[dict[str, Any]]
    original_texts: list[str]
    translated_texts: list[str]


# --- Detect / OCR -----------------------------------------------------------


class DetectedBlock(ApiModel):
    bbox: list[int | float]
    type: str


class DetectResponse(ApiModel):
    blocks: list[DetectedBlock]


class OcrResponse(ApiModel):
    texts: list[str]


class PaddleOcrResponse(ApiModel):
    blocks: list[dict[str, Any]]
    total: int
