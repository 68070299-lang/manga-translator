"""Upload validation, image decoding and PNG encoding."""

from __future__ import annotations

import asyncio
import io

import numpy as np
from fastapi import HTTPException, UploadFile
from PIL import Image

from manga_backend.config import Limits

ALLOWED_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
PNG_COMPRESS_LEVEL = 2


async def read_upload_image(image: UploadFile, limits: Limits) -> np.ndarray:
    """Validate and decode an uploaded image as an RGB numpy array."""
    if image.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid image type: {image.content_type}")

    contents = await image.read(limits.max_upload_size + 1)
    if len(contents) > limits.max_upload_size:
        raise HTTPException(status_code=413, detail="File too large")

    return await asyncio.to_thread(decode_image, contents, limits.max_image_pixels)


def decode_image(contents: bytes, max_image_pixels: int) -> np.ndarray:
    """Decode image bytes to RGB; slow enough on full pages to need a worker thread."""
    try:
        img = Image.open(io.BytesIO(contents))
        width, height = img.size
        if width <= 0 or height <= 0:
            raise HTTPException(status_code=400, detail="Invalid image dimensions")
        if width * height > max_image_pixels:
            raise HTTPException(status_code=413, detail="Image dimensions too large")
        img = img.convert("RGB")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid image data") from exc

    return np.array(img)


def encode_png(image: np.ndarray) -> bytes:
    """Encode a rendered page for a worker thread.

    Level 2 took 35-55% less time than Pillow's default level 6 on synthetic manga
    pages for 2-6% more bytes; level 1 grew noisy pages by over 30%.
    """
    buffer = io.BytesIO()
    Image.fromarray(image).save(buffer, format="PNG", compress_level=PNG_COMPRESS_LEVEL)
    return buffer.getvalue()
