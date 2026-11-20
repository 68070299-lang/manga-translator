"""Input validation for the multipart routes; failures are HTTP 400s with fixed messages."""

from __future__ import annotations

import re
import unicodedata

from fastapi import HTTPException

from manga_backend.api.schemas import TranslateOptions
from manga_backend.config import TRANSLATOR_API_KEY_ENV_VARS, Settings
from manga_backend.models.translator import validate_local_llm_base_url

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
VALID_TEXT_ALIGNMENTS = frozenset({"left", "center", "right"})
VALID_TEXT_DIRECTIONS = frozenset({"auto", "horizontal", "vertical"})
VALID_DETECTORS = {
    "default": "rtdetr",
    "rtdetr": "rtdetr",
    "animetext": "animetext-yolo",
    "animetext-yolo": "animetext-yolo",
    "animetext_yolo": "animetext-yolo",
    "anime-text-yolo": "animetext-yolo",
}
VALID_OCR_ENGINES = {
    "manga-ocr": "manga-ocr",
    "mangaocr": "manga-ocr",
    "paddle-ocr": "paddle-ocr",
    "paddleocr": "paddle-ocr",
    "paddle-vl": "paddle-ocr",
    "paddlevl": "paddle-ocr",
}
VALID_TRANSLATORS = {
    "": "none",
    "none": "none",
    "gemini": "gemini",
    "google": "gemini",
    "openai": "openai",
    "openrouter": "openrouter",
    "local": "local",
}
VALID_INPAINTERS = {
    "": "none",
    "none": "none",
    "lama": "lama_large",
    "lama_large": "lama_large",
    "flux2_klein_lanpaint": "flux2_klein_lanpaint",
    "flux2-klein-lanpaint": "flux2_klein_lanpaint",
    "flux_lanpaint": "flux2_klein_lanpaint",
    "lanpaint": "flux2_klein_lanpaint",
    "flux2_klein_q4": "flux2_klein_q4",
    "flux2-klein-q4": "flux2_klein_q4",
    "flux_klein_q4": "flux2_klein_q4",
    "q4": "flux2_klein_q4",
}
VALID_PADDLE_LANGS = frozenset({"japan", "ch", "en", "korean"})
MAX_TARGET_LANG_LENGTH = 64
MAX_MODEL_NAME_LENGTH = 128
MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._:/@+-]+$")


def bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def validate_render_options(options: TranslateOptions) -> None:
    """Validate render settings before they reach PIL rendering code."""
    if not 0.5 <= options.line_spacing <= 3.0:
        raise bad_request("line_spacing must be between 0.5 and 3.0")
    if not 0 <= options.border_size <= 20:
        raise bad_request("border_size must be between 0 and 20")
    if not 0 <= options.bg_opacity <= 1:
        raise bad_request("bg_opacity must be between 0 and 1")
    if not 6 <= options.font_size <= 96:
        raise bad_request("font_size must be between 6 and 96")

    for field_name, value in (
        ("text_color", options.text_color),
        ("border_color", options.border_color),
        ("bg_color", options.bg_color),
    ):
        if not HEX_COLOR_RE.match(value):
            raise bad_request(f"{field_name} must be a #RRGGBB hex color")

    if options.text_alignment not in VALID_TEXT_ALIGNMENTS:
        raise bad_request("text_alignment must be left, center, or right")
    if options.text_direction not in VALID_TEXT_DIRECTIONS:
        raise bad_request("text_direction must be auto, horizontal, or vertical")


def normalize_choice(value: str, choices: dict[str, str], field_name: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in choices:
        allowed = ", ".join(sorted(key or "none" for key in choices))
        raise bad_request(f"{field_name} must be one of: {allowed}")
    return choices[normalized]


def normalize_paddle_lang(lang: str) -> str:
    normalized = lang.strip().lower()
    if normalized not in VALID_PADDLE_LANGS:
        allowed = ", ".join(sorted(VALID_PADDLE_LANGS))
        raise bad_request(f"lang must be one of: {allowed}")
    return normalized


def required_ocr_components(ocr: str) -> list[str]:
    """Return local components required by the selected OCR engine."""
    if ocr == "paddle-ocr":
        return ["paddle_ocr"]
    if ocr == "manga-ocr":
        return ["ocr"]
    return []


def _reject_control_characters(field_name: str, value: str) -> None:
    if any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in value):
        raise bad_request(f"{field_name} must not contain control characters")


def validate_target_lang(target_lang: str) -> None:
    if not target_lang.strip():
        raise bad_request("target_lang must not be empty")
    if len(target_lang) > MAX_TARGET_LANG_LENGTH:
        raise bad_request(f"target_lang must be at most {MAX_TARGET_LANG_LENGTH} characters")
    _reject_control_characters("target_lang", target_lang)


def validate_model_name(model: str) -> None:
    if len(model) > MAX_MODEL_NAME_LENGTH:
        raise bad_request(f"model must be at most {MAX_MODEL_NAME_LENGTH} characters")
    if model and not MODEL_NAME_RE.fullmatch(model):
        raise bad_request("model may only contain letters, digits and . _ : / @ + -")


def preflight_translator_inputs(
    options: TranslateOptions, translator: str, settings: Settings
) -> None:
    """Reject bad translator inputs before any detector or OCR work starts.

    `translator` is the already-normalized provider name.
    """
    validate_target_lang(options.target_lang)
    validate_model_name(options.model)

    if translator == "local":
        base_url = options.local_llm_base_url.strip() or settings.local_llm_base_url
        try:
            validate_local_llm_base_url(base_url)
        except ValueError as error:
            raise bad_request(str(error)) from error
    elif translator in TRANSLATOR_API_KEY_ENV_VARS:
        env_var = TRANSLATOR_API_KEY_ENV_VARS[translator]
        if not options.api_key.strip() and not settings.translator_api_key(translator):
            raise bad_request(
                f"api_key is required when translator is {translator} (or set {env_var} on the backend)"
            )
