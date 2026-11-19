"""Text Detection using RT-DETR-v2 ONNX model.

Based on comic-translate's detection implementation.
Model: ogkalu/comic-text-and-bubble-detector from HuggingFace
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import numpy as np
import onnxruntime as ort
from PIL import Image

from manga_backend.paths import MODELS_CACHE_DIR
from manga_backend.utils.device import create_onnx_session, get_onnx_providers
from manga_backend.utils.download import download_pinned_model, verify_cached_model
from manga_backend.utils.textblock import TextBlock

logger = logging.getLogger(__name__)


class TextDetector:
    """RT-DETR-v2 based text and bubble detector using ONNX runtime."""

    def __init__(self, device: str = "cpu") -> None:
        self.device = device
        self.session: ort.InferenceSession | None = None
        self.confidence_threshold = 0.3
        self.repo_id = "ogkalu/comic-text-and-bubble-detector"
        self.model_dir = MODELS_CACHE_DIR / "detection"

    async def initialize(self) -> None:
        """Download and load the detection model."""
        self.model_dir.mkdir(parents=True, exist_ok=True)

        model_path = self.model_dir / "detector.onnx"

        if model_path.exists():
            logger.info("Loading cached detection model...")
            await asyncio.to_thread(
                verify_cached_model, self.repo_id, "detector.onnx", model_path
            )
        else:
            logger.info("Downloading detection model from %s...", self.repo_id)
            model_path = Path(
                await asyncio.to_thread(
                    download_pinned_model,
                    self.repo_id,
                    "detector.onnx",
                    local_dir=self.model_dir,
                )
            )

        if not model_path.exists() or model_path.stat().st_size == 0:
            raise RuntimeError(f"Detection model is missing or empty: {model_path}")

        providers = get_onnx_providers(self.device)
        logger.info("Loading detection model with providers: %s", providers)

        self.session = await asyncio.to_thread(create_onnx_session, model_path, providers)
        logger.info("Detection model loaded successfully")

    async def detect(self, image: np.ndarray) -> list[TextBlock]:
        """Detect text regions in an RGB image."""
        if self.session is None:
            raise RuntimeError("Model not initialized. Call initialize() first.")

        pil_image = Image.fromarray(image)
        orig_w, orig_h = pil_image.size

        resized = np.asarray(pil_image.resize((640, 640)))
        im_data = np.ascontiguousarray(resized.transpose(2, 0, 1), dtype=np.float32)
        im_data *= np.float32(1.0 / 255.0)
        im_data = im_data[np.newaxis, ...]

        outputs = await asyncio.to_thread(
            self.session.run,
            None,
            {
                "images": im_data,
                "orig_target_sizes": np.array([[orig_w, orig_h]], dtype=np.int64)
            }
        )

        labels, boxes, scores = cast(list[np.ndarray], outputs)[:3]

        if labels.ndim == 2:
            labels = labels[0]
        if scores.ndim == 2:
            scores = scores[0]
        if boxes.ndim == 3:
            boxes = boxes[0]

        text_blocks: list[TextBlock] = []
        bubble_boxes: list[list[int]] = []

        for label, box, score in zip(labels, boxes, scores):
            if float(score) < self.confidence_threshold:
                continue

            x1, y1, x2, y2 = map(int, box)
            label_id = int(label)

            if label_id == 0:
                bubble_boxes.append([x1, y1, x2, y2])
            elif label_id in [1, 2]:
                block = TextBlock(
                    xyxy=[x1, y1, x2, y2],
                    block_type="text" if label_id == 1 else "sfx"
                )
                block.confidence = float(score)
                text_blocks.append(block)

        for block in text_blocks:
            for bubble in bubble_boxes:
                if self._is_inside(block.xyxy, bubble):
                    block.bubble_xyxy = bubble
                    break

        logger.info("Detected %s text blocks, %s bubbles", len(text_blocks), len(bubble_boxes))
        return text_blocks

    @staticmethod
    def _is_inside(inner: Sequence[int] | np.ndarray, outer: Sequence[int] | np.ndarray) -> bool:
        """Check if inner bbox is mostly inside outer bbox."""
        ix1, iy1, ix2, iy2 = inner
        ox1, oy1, ox2, oy2 = outer

        inter_x1 = max(ix1, ox1)
        inter_y1 = max(iy1, oy1)
        inter_x2 = min(ix2, ox2)
        inter_y2 = min(iy2, oy2)

        if inter_x1 >= inter_x2 or inter_y1 >= inter_y2:
            return False

        inter_area = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
        inner_area = (ix2 - ix1) * (iy2 - iy1)

        return inter_area > inner_area * 0.5

    def dispose(self) -> None:
        self.session = None
