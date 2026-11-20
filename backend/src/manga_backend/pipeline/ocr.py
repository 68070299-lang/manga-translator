"""OCR routing across manga-ocr and PaddleOCR."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

import numpy as np

from manga_backend.models.ocr import OCREngine
from manga_backend.pipeline.translation import align_texts
from manga_backend.utils.textblock import TextBlock

PADDLE_OCR_NAMES = frozenset({"paddle-ocr", "paddleocr", "paddle-vl", "paddlevl"})


class PaddleRegionRecognizer(Protocol):
    async def recognize_regions(
        self, image: np.ndarray, regions: list[dict[str, Any]]
    ) -> Sequence[Any] | None: ...


def blocks_from_payload(blocks: Sequence[TextBlock | Mapping[str, Any]]) -> list[TextBlock]:
    """Accept TextBlocks or API-shaped dicts ({"bbox"|"xyxy", "type"})."""
    text_blocks: list[TextBlock] = []
    for block in blocks:
        if isinstance(block, TextBlock):
            text_blocks.append(block)
            continue
        bbox = block.get("bbox", block.get("xyxy", [0, 0, 0, 0]))
        text_blocks.append(TextBlock(xyxy=bbox, block_type=block.get("type", "text")))
    return text_blocks


async def run_ocr(
    image: np.ndarray,
    blocks: list[TextBlock],
    ocr: str,
    *,
    local_engine: OCREngine | None,
    prepare_paddle_engine: Callable[[], Awaitable[PaddleRegionRecognizer]],
) -> list[str]:
    """Recognize exactly one string per block with the requested engine."""
    if ocr in PADDLE_OCR_NAMES:
        paddle_engine = await prepare_paddle_engine()
        # PaddleOCR can do its own detection, but we use pre-detected regions
        values = await paddle_engine.recognize_regions(
            image, [{"bbox": block.xyxy.tolist()} for block in blocks]
        )
    else:
        if local_engine is None:
            raise RuntimeError("OCR not initialized")
        values = await local_engine.process(image, blocks)
    return align_texts(values, len(blocks), "OCR")
