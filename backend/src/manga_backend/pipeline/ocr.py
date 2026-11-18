"""OCR routing across manga-ocr, PaddleOCR and Google Vision."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

import aiohttp
import numpy as np
from PIL import Image

from manga_backend.models.ocr import OCREngine
from manga_backend.pipeline.errors import GoogleOCRError, GoogleOCRTimeoutError
from manga_backend.pipeline.translation import align_texts
from manga_backend.utils.textblock import TextBlock

logger = logging.getLogger(__name__)

GOOGLE_OCR_CONCURRENCY = 6
GOOGLE_OCR_URL = "https://vision.googleapis.com/v1/images:annotate"
GOOGLE_OCR_TIMEOUT_SECONDS = 30

PADDLE_OCR_NAMES = frozenset({"paddle-ocr", "paddleocr", "paddle-vl", "paddlevl"})
GOOGLE_OCR_NAMES = frozenset({"google-ocr", "googleocr", "google"})


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
    google_ocr_api_key: str | None,
    google_ocr_language_hint: str | None,
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
    elif ocr in GOOGLE_OCR_NAMES:
        if not google_ocr_api_key:
            raise ValueError("google_ocr_api_key is required when ocr is google-ocr")
        values = await google_ocr(
            image, blocks, google_ocr_api_key, google_ocr_language_hint
        )
    else:
        if local_engine is None:
            raise RuntimeError("OCR not initialized")
        values = await local_engine.process(image, blocks)
    return align_texts(values, len(blocks), "OCR")


def _upstream_status_error(status: int) -> GoogleOCRError:
    # Google's response body is never included: it can echo credentials or request data.
    if status in (401, 403):
        detail = "Google OCR authentication failed"
    elif status == 429:
        detail = "Google OCR quota exceeded"
    elif status >= 500:
        detail = "Google OCR service unavailable"
    else:
        detail = "Google OCR request rejected"
    logger.warning("Google OCR upstream returned status=%s", status)
    return GoogleOCRError(detail)


def _annotation_text(payload: object) -> str:
    """Return the full-text annotation of the first response, or "" when none."""
    if not isinstance(payload, dict):
        raise GoogleOCRError("Google OCR returned an invalid response")
    responses = payload.get("responses")
    first_response = responses[0] if isinstance(responses, list) and responses else {}
    annotations = (
        first_response.get("textAnnotations", [])
        if isinstance(first_response, dict)
        else []
    )
    if annotations:
        return annotations[0].get("description", "").strip()
    # A valid response with no text is a successful OCR result.
    return ""


def _encode_png_base64(region: np.ndarray) -> str:
    buffer = io.BytesIO()
    Image.fromarray(region).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


async def google_ocr(
    image: np.ndarray,
    blocks: list[TextBlock],
    api_key: str,
    language_hint: str | None = None,
) -> list[str]:
    """Perform OCR using Google Cloud Vision API."""
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    limiter = asyncio.Semaphore(GOOGLE_OCR_CONCURRENCY)

    timeout = aiohttp.ClientTimeout(total=GOOGLE_OCR_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:

        async def recognize(block: TextBlock) -> str:
            x1, y1, x2, y2 = (int(v) for v in block.xyxy)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(image.shape[1], x2), min(image.shape[0], y2)

            if x2 <= x1 or y2 <= y1:
                return ""

            img_base64 = await asyncio.to_thread(_encode_png_base64, image[y1:y2, x1:x2])

            image_context: dict[str, Any] = {}
            if language_hint:
                image_context["languageHints"] = [language_hint]

            request_body = {
                "requests": [
                    {
                        "image": {"content": img_base64},
                        "features": [{"type": "TEXT_DETECTION"}],
                        "imageContext": image_context,
                    }
                ]
            }

            try:
                async with session.post(
                    GOOGLE_OCR_URL, headers=headers, json=request_body
                ) as response:
                    if response.status != 200:
                        raise _upstream_status_error(response.status)

                    try:
                        result = await response.json()
                    except (TypeError, ValueError, aiohttp.ContentTypeError) as error:
                        logger.warning(
                            "Google OCR returned invalid JSON: %s",
                            error.__class__.__name__,
                        )
                        raise GoogleOCRError(
                            "Google OCR returned an invalid response"
                        ) from error
                    return _annotation_text(result)
            except (asyncio.TimeoutError, aiohttp.ServerTimeoutError) as error:
                logger.warning("Google OCR request timed out")
                raise GoogleOCRTimeoutError() from error
            except aiohttp.ClientError as error:
                logger.warning(
                    "Google OCR network request failed: %s", error.__class__.__name__
                )
                raise GoogleOCRError() from error

        async def recognize_limited(block: TextBlock) -> str:
            async with limiter:
                return await recognize(block)

        # Results keep block order; the first failure stops the remaining requests.
        tasks = [asyncio.create_task(recognize_limited(block)) for block in blocks]
        try:
            return list(await asyncio.gather(*tasks))
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
