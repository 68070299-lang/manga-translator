"""Neural text segmentation and block detection using comic-text-detector ONNX.

Model: mayocream/comic-text-detector-onnx (dmMaze/comic-text-detector export)
Outputs:
    - blk: [1, 64512, 7] YOLOv5-style boxes [cx, cy, w, h, obj, cls0, cls1]
    - seg: [1, 1, 1024, 1024] post-sigmoid pixel-level text probability map
    - det: [1, 2, 1024, 1024] DBNet text-line maps (unused here)
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import cv2
import numpy as np
import onnxruntime as ort

from manga_backend.paths import MODELS_CACHE_DIR
from manga_backend.utils.device import create_onnx_session, get_onnx_providers
from manga_backend.utils.download import download_text_segmenter_onnx
from manga_backend.utils.textblock import TextBlock

logger = logging.getLogger(__name__)

DEFAULT_INPUT_SIZE = 1024
DEFAULT_CONF_THRESHOLD = 0.4
DEFAULT_IOU_THRESHOLD = 0.35
DEFAULT_MAX_CANDIDATES = 1000
LETTERBOX_PAD_VALUE = 114

SPREAD_MIN_WIDTH = 1000
SPREAD_MIN_ASPECT = 1.35
SPREAD_OVERLAP_RATIO = 0.06
SPREAD_MIN_OVERLAP_PX = 48

_DISABLED_VALUES = frozenset({"1", "true", "yes", "on"})


def text_segmenter_disabled() -> bool:
    """Return True when neural text segmentation is disabled by TEXT_SEGMENTER_DISABLE."""
    return os.getenv("TEXT_SEGMENTER_DISABLE", "").strip().lower() in _DISABLED_VALUES


@dataclass(frozen=True, slots=True)
class TextSegmentationResult:
    """Neural text segmentation probability map and detected text blocks."""

    prob_map: np.ndarray
    blocks: list[TextBlock]


class TextSegmenter:
    """ONNX Runtime wrapper for mayocream/comic-text-detector-onnx."""

    def __init__(
        self,
        device: str = "cpu",
        *,
        confidence_threshold: float = DEFAULT_CONF_THRESHOLD,
        iou_threshold: float = DEFAULT_IOU_THRESHOLD,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
    ) -> None:
        if not 0.0 < confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be in (0, 1]")
        if not 0.0 < iou_threshold < 1.0:
            raise ValueError("iou_threshold must be in (0, 1)")
        if max_candidates <= 0:
            raise ValueError("max_candidates must be positive")

        self.device = device
        self.confidence_threshold = confidence_threshold
        self.iou_threshold = iou_threshold
        self.max_candidates = max_candidates
        self.model_dir = MODELS_CACHE_DIR / "text_segmenter"
        self.session: ort.InferenceSession | None = None
        self.input_size = DEFAULT_INPUT_SIZE
        self.input_name = "images"
        self.blk_output_name = "blk"
        self.seg_output_name = "seg"
        self._inference_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Download (if needed), verify, and load the ONNX text segmenter session."""
        if text_segmenter_disabled():
            logger.info("Text segmenter disabled via TEXT_SEGMENTER_DISABLE")
            self.session = None
            return

        model_path = await download_text_segmenter_onnx(self.model_dir)
        providers = get_onnx_providers(self.device)
        logger.info("Loading text segmenter model with providers: %s", providers)
        session = await asyncio.to_thread(create_onnx_session, model_path, providers)

        inputs = session.get_inputs()
        outputs = session.get_outputs()
        if len(inputs) != 1 or len(inputs[0].shape) != 4:
            raise RuntimeError("Text segmenter must have one 4D image input tensor")
        if len(outputs) < 2:
            raise RuntimeError("Text segmenter must expose at least 'blk' and 'seg' outputs")

        self.input_name = inputs[0].name
        in_h, in_w = inputs[0].shape[-2:]
        if isinstance(in_h, int) and isinstance(in_w, int) and in_h > 0 and in_w > 0:
            if in_h != in_w:
                raise RuntimeError("Text segmenter image input must be square")
            self.input_size = in_h

        output_names = {out.name for out in outputs}
        if "blk" in output_names and "seg" in output_names:
            self.blk_output_name = "blk"
            self.seg_output_name = "seg"
        else:
            self.blk_output_name = outputs[0].name
            self.seg_output_name = outputs[1].name

        self.session = session
        logger.info("Text segmenter loaded with providers %s", session.get_providers())

    async def segment(self, image: np.ndarray) -> TextSegmentationResult:
        """Segment text pixels and detect text blocks in an RGB HxWx3 uint8 image."""
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("TextSegmenter expects an RGB HxWx3 image")
        height, width = image.shape[:2]
        if height <= 0 or width <= 0:
            raise ValueError("TextSegmenter cannot process an empty image")

        if text_segmenter_disabled():
            return TextSegmentationResult(
                prob_map=np.zeros((height, width), dtype=np.float32),
                blocks=[],
            )

        if self.session is None:
            raise RuntimeError("Model not initialized. Call initialize() first.")

        rgb = (
            image
            if image.dtype == np.uint8
            else np.clip(image, 0, 255).astype(np.uint8)
        )

        if width >= SPREAD_MIN_WIDTH and (width / height) >= SPREAD_MIN_ASPECT:
            return await self._segment_spread(rgb)
        return await self._segment_single(rgb)

    async def _segment_single(self, image: np.ndarray) -> TextSegmentationResult:
        height, width = image.shape[:2]
        tensor, scale, pad, resized_size = self._letterbox(image, self.input_size)
        blk_raw, seg_raw = await self._run_session(tensor)
        prob_map = self._unletterbox_seg(
            seg_raw, (height, width), pad=pad, resized_size=resized_size
        )
        blocks = self._decode_blk(
            blk_raw,
            (height, width),
            scale=scale,
            pad=pad,
            confidence_threshold=self.confidence_threshold,
            iou_threshold=self.iou_threshold,
            max_candidates=self.max_candidates,
        )
        return TextSegmentationResult(prob_map=prob_map, blocks=blocks)

    async def _segment_spread(self, image: np.ndarray) -> TextSegmentationResult:
        height, width = image.shape[:2]
        mid = width // 2
        overlap = max(SPREAD_MIN_OVERLAP_PX, int(round(width * SPREAD_OVERLAP_RATIO)))
        left_x2 = min(width, mid + overlap)
        right_x1 = max(0, mid - overlap)

        left_res = await self._segment_single(image[:, :left_x2])
        right_res = await self._segment_single(image[:, right_x1:])

        prob_map = np.zeros((height, width), dtype=np.float32)
        prob_map[:, :left_x2] = left_res.prob_map
        prob_map[:, right_x1:] = np.maximum(prob_map[:, right_x1:], right_res.prob_map)

        blocks = self._merge_spread_blocks(
            left_res.blocks,
            right_res.blocks,
            width=width,
            height=height,
            left_cut=left_x2,
            right_cut=right_x1,
            iou_threshold=self.iou_threshold,
        )
        return TextSegmentationResult(prob_map=prob_map, blocks=blocks)

    async def _run_session(self, tensor: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        session = self.session
        if session is None:
            raise RuntimeError("Model not initialized. Call initialize() first.")

        async with self._inference_lock:
            inference = asyncio.create_task(
                asyncio.to_thread(
                    session.run,
                    [self.blk_output_name, self.seg_output_name],
                    {self.input_name: tensor},
                )
            )
            try:
                outputs = await asyncio.shield(inference)
            except asyncio.CancelledError:
                await inference
                raise

        if len(outputs) < 2:
            raise RuntimeError(f"Expected at least 2 outputs from session, got {len(outputs)}")
        return cast(np.ndarray, outputs[0]), cast(np.ndarray, outputs[1])

    @staticmethod
    def _letterbox(
        image: np.ndarray, input_size: int
    ) -> tuple[np.ndarray, float, tuple[int, int], tuple[int, int]]:
        """Resize image preserving aspect ratio and pad to input_size x input_size."""
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("TextSegmenter expects an RGB HxWx3 image")
        height, width = image.shape[:2]
        if height <= 0 or width <= 0:
            raise ValueError("TextSegmenter cannot process an empty image")

        scale = min(input_size / width, input_size / height)
        resized_width = max(1, int(round(width * scale)))
        resized_height = max(1, int(round(height * scale)))
        if (resized_width, resized_height) != (width, height):
            resized = cv2.resize(
                image, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR
            )
        else:
            resized = image

        pad_x = (input_size - resized_width) // 2
        pad_y = (input_size - resized_height) // 2
        canvas = np.full(
            (input_size, input_size, 3), LETTERBOX_PAD_VALUE, dtype=np.uint8
        )
        canvas[
            pad_y : pad_y + resized_height,
            pad_x : pad_x + resized_width,
        ] = resized
        tensor = np.ascontiguousarray(
            canvas.transpose(2, 0, 1), dtype=np.float32
        ) / np.float32(255.0)
        return tensor[np.newaxis, ...], scale, (pad_x, pad_y), (resized_width, resized_height)

    @staticmethod
    def _unletterbox_seg(
        seg_output: np.ndarray,
        image_shape: tuple[int, int],
        *,
        pad: tuple[int, int],
        resized_size: tuple[int, int],
    ) -> np.ndarray:
        """Crop padding from raw seg output and resize back to (height, width) float32 [0, 1]."""
        arr = np.asarray(seg_output, dtype=np.float32)
        if arr.ndim == 4:
            if arr.shape[0] != 1 or arr.shape[1] != 1:
                raise RuntimeError(f"Unexpected seg output shape: {arr.shape}")
            arr = arr[0, 0]
        elif arr.ndim == 3:
            if arr.shape[0] != 1:
                raise RuntimeError(f"Unexpected seg output shape: {arr.shape}")
            arr = arr[0]
        elif arr.ndim != 2:
            raise RuntimeError(f"Unexpected seg output shape: {arr.shape}")

        pad_x, pad_y = pad
        resized_width, resized_height = resized_size
        cropped = arr[pad_y : pad_y + resized_height, pad_x : pad_x + resized_width]

        height, width = image_shape
        if cropped.shape != (height, width):
            unletterboxed = cv2.resize(
                cropped, (width, height), interpolation=cv2.INTER_LINEAR
            )
        else:
            unletterboxed = cropped

        cleaned = np.nan_to_num(unletterboxed, nan=0.0, posinf=1.0, neginf=0.0)
        return np.clip(cleaned, 0.0, 1.0).astype(np.float32, copy=False)

    @classmethod
    def _decode_blk(
        cls,
        predictions: np.ndarray,
        image_shape: tuple[int, int],
        *,
        scale: float,
        pad: tuple[int, int],
        confidence_threshold: float,
        iou_threshold: float,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
    ) -> list[TextBlock]:
        """Decode [1, N, 7] YOLO-style blk predictions with confidence scaling and NMS."""
        values = np.asarray(predictions, dtype=np.float32)
        if values.ndim == 3:
            if values.shape[0] != 1:
                raise RuntimeError(f"Unexpected blk output shape: {values.shape}")
            values = values[0]
        elif values.ndim != 2:
            raise RuntimeError(f"Unexpected blk output shape: {values.shape}")

        if values.shape[1] < 6:
            raise RuntimeError(f"Unexpected blk feature dimension: {values.shape}")

        values = values[np.isfinite(values).all(axis=1)]
        if not len(values):
            return []

        scores = values[:, 4] * values[:, 5:].max(axis=1)
        keep_mask = scores >= confidence_threshold
        values = values[keep_mask]
        scores = scores[keep_mask]
        if not len(values):
            return []

        if len(values) > max_candidates:
            top_idx = np.argpartition(scores, -max_candidates)[-max_candidates:]
            values = values[top_idx]
            scores = scores[top_idx]

        cx, cy, bw, bh = values[:, 0], values[:, 1], values[:, 2], values[:, 3]
        boxes = np.stack((cx - bw / 2.0, cy - bh / 2.0, cx + bw / 2.0, cy + bh / 2.0), axis=1)
        keep = cls._nms(boxes, scores, iou_threshold)

        height, width = image_shape
        pad_x, pad_y = pad
        blocks: list[TextBlock] = []
        for idx in keep:
            bx1, by1, bx2, by2 = boxes[idx]
            x1 = max(0.0, min(float(width), (float(bx1) - pad_x) / scale))
            y1 = max(0.0, min(float(height), (float(by1) - pad_y) / scale))
            x2 = max(0.0, min(float(width), (float(bx2) - pad_x) / scale))
            y2 = max(0.0, min(float(height), (float(by2) - pad_y) / scale))
            xyxy = [
                int(np.floor(x1)),
                int(np.floor(y1)),
                int(np.ceil(x2)),
                int(np.ceil(y2)),
            ]
            if xyxy[2] <= xyxy[0] or xyxy[3] <= xyxy[1]:
                continue
            block = TextBlock(xyxy=xyxy, block_type="text")
            block.confidence = float(scores[idx])
            blocks.append(block)
        return blocks

    @staticmethod
    def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
        order = np.argsort(scores)[::-1]
        keep: list[int] = []
        while order.size:
            current = int(order[0])
            keep.append(current)
            if order.size == 1:
                break
            rest = order[1:]
            ix1 = np.maximum(boxes[current, 0], boxes[rest, 0])
            iy1 = np.maximum(boxes[current, 1], boxes[rest, 1])
            ix2 = np.minimum(boxes[current, 2], boxes[rest, 2])
            iy2 = np.minimum(boxes[current, 3], boxes[rest, 3])
            intersection = np.maximum(0.0, ix2 - ix1) * np.maximum(0.0, iy2 - iy1)
            current_area = max(
                0.0,
                float(boxes[current, 2] - boxes[current, 0])
                * float(boxes[current, 3] - boxes[current, 1]),
            )
            rest_area = np.maximum(0.0, boxes[rest, 2] - boxes[rest, 0]) * np.maximum(
                0.0, boxes[rest, 3] - boxes[rest, 1]
            )
            union = current_area + rest_area - intersection
            iou = np.divide(
                intersection,
                union,
                out=np.zeros_like(intersection),
                where=union > 0,
            )
            order = rest[iou <= threshold]
        return keep

    @classmethod
    def _merge_spread_blocks(
        cls,
        left_blocks: Sequence[TextBlock],
        right_blocks: Sequence[TextBlock],
        *,
        width: int,
        height: int,
        left_cut: int,
        right_cut: int,
        iou_threshold: float,
    ) -> list[TextBlock]:
        """Shift right-half blocks into full-page coordinates and NMS-deduplicate."""
        shifted_right: list[TextBlock] = []
        for block in right_blocks:
            x1 = max(0, min(width, block.x1 + right_cut))
            y1 = max(0, min(height, block.y1))
            x2 = max(x1, min(width, block.x2 + right_cut))
            y2 = max(y1, min(height, block.y2))
            if x2 <= x1 or y2 <= y1:
                continue
            shifted = TextBlock(xyxy=[x1, y1, x2, y2], block_type=block.block_type)
            shifted.confidence = block.confidence
            shifted_right.append(shifted)

        # Prefer boxes not clipped at an internal crop cut boundary, then rank by confidence.
        candidates: list[tuple[bool, float, TextBlock]] = []
        for block in left_blocks:
            clipped = left_cut < width and block.x2 >= left_cut - 2
            candidates.append((not clipped, float(block.confidence), block))
        for block in shifted_right:
            clipped = right_cut > 0 and block.x1 <= right_cut + 2
            candidates.append((not clipped, float(block.confidence), block))

        if not candidates:
            return []

        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        kept: list[TextBlock] = []
        for _unclipped, _conf, candidate in candidates:
            if any(
                cls._is_overlapping_duplicate(
                    candidate, existing, iou_threshold=iou_threshold
                )
                for existing in kept
            ):
                continue
            kept.append(candidate)
        return kept

    @staticmethod
    def _is_overlapping_duplicate(
        a: TextBlock,
        b: TextBlock,
        *,
        iou_threshold: float,
        contain_threshold: float = 0.80,
    ) -> bool:
        ix1 = max(a.x1, b.x1)
        iy1 = max(a.y1, b.y1)
        ix2 = min(a.x2, b.x2)
        iy2 = min(a.y2, b.y2)
        if ix2 <= ix1 or iy2 <= iy1:
            return False
        inter = (ix2 - ix1) * (iy2 - iy1)
        area_a = a.area
        area_b = b.area
        union = area_a + area_b - inter
        if union > 0 and (inter / union) > iou_threshold:
            return True
        min_area = min(area_a, area_b)
        return min_area > 0 and (inter / min_area) >= contain_threshold

    def dispose(self) -> None:
        """Release the loaded ONNX Runtime session."""
        if self.session is not None:
            del self.session
            self.session = None
