"""Translation pipeline package.

Modules: errors (public exceptions), components (status and lazy loading),
detection, ocr, translation, inpainting, masks (inpaint masks), bubbles,
layout, fonts, rendering and orchestrator (TranslationPipeline).
"""

from manga_backend.pipeline.components import LAZY_COMPONENTS
from manga_backend.pipeline.errors import (
    DetectorUnavailableError,
    InpainterUnavailableError,
    PaddleOCRUnavailableError,
)
from manga_backend.pipeline.inpainting import DEFAULT_INPAINTER
from manga_backend.pipeline.orchestrator import TranslationPipeline
from manga_backend.pipeline.translation import align_texts

__all__ = [
    "DEFAULT_INPAINTER",
    "LAZY_COMPONENTS",
    "DetectorUnavailableError",
    "InpainterUnavailableError",
    "PaddleOCRUnavailableError",
    "TranslationPipeline",
    "align_texts",
]
