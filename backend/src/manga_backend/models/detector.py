"""
Text Detection using RT-DETR-v2 ONNX model.

Based on comic-translate's detection implementation.
Model: ogkalu/comic-text-and-bubble-detector from HuggingFace
"""

import asyncio
import os
import logging
from typing import List
import numpy as np
from PIL import Image

from manga_backend.utils.device import get_onnx_providers, create_onnx_session
from manga_backend.utils.download import download_pinned_model, verify_cached_model
from manga_backend.utils.textblock import TextBlock
from manga_backend.paths import MODELS_CACHE_DIR

logger = logging.getLogger(__name__)

MODELS_DIR = str(MODELS_CACHE_DIR)


class TextDetector:
    """RT-DETR-v2 based text and bubble detector using ONNX runtime."""

    def __init__(self, device: str = "cpu"):
        self.device = device
        self.session = None
        self.confidence_threshold = 0.3
        self.repo_id = "ogkalu/comic-text-and-bubble-detector"
        self.model_dir = os.path.join(MODELS_DIR, "detection")

    async def initialize(self):
        """Download and load the detection model."""
        os.makedirs(self.model_dir, exist_ok=True)

        model_path = os.path.join(self.model_dir, "detector.onnx")

        if os.path.exists(model_path):
            logger.info("Loading cached detection model...")
            await asyncio.to_thread(
                verify_cached_model, self.repo_id, "detector.onnx", model_path
            )
        else:
            logger.info(f"Downloading detection model from {self.repo_id}...")
            model_path = await asyncio.to_thread(
                download_pinned_model,
                self.repo_id,
                "detector.onnx",
                local_dir=self.model_dir
            )

        if not os.path.exists(model_path) or os.path.getsize(model_path) == 0:
            raise RuntimeError(f"Detection model is missing or empty: {model_path}")

        providers = get_onnx_providers(self.device)
        logger.info(f"Loading detection model with providers: {providers}")

        self.session = await asyncio.to_thread(create_onnx_session, model_path, providers)
        logger.info("Detection model loaded successfully")

    async def detect(self, image: np.ndarray) -> List[TextBlock]:
        """Detect text regions in an RGB image."""
        if self.session is None:
            raise RuntimeError("Model not initialized. Call initialize() first.")

        pil_image = Image.fromarray(image)
        orig_w, orig_h = pil_image.size

        arr = np.asarray(pil_image.resize((640, 640)), dtype=np.float32) / 255.0
        im_data = np.transpose(arr, (2, 0, 1))[np.newaxis, ...]

        outputs = await asyncio.to_thread(
            self.session.run,
            None,
            {
                "images": im_data,
                "orig_target_sizes": np.array([[orig_w, orig_h]], dtype=np.int64)
            }
        )

        labels, boxes, scores = outputs[:3]

        if labels.ndim == 2:
            labels = labels[0]
        if scores.ndim == 2:
            scores = scores[0]
        if boxes.ndim == 3:
            boxes = boxes[0]

        text_blocks = []
        bubble_boxes = []

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

        logger.info(f"Detected {len(text_blocks)} text blocks, {len(bubble_boxes)} bubbles")
        return text_blocks

    def _is_inside(self, inner: List[int], outer: List[int]) -> bool:
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

    def dispose(self):
        self.session = None
