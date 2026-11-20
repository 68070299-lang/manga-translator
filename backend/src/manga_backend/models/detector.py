"""Text Detection using RT-DETR-v2 ONNX model.

Based on comic-translate's detection implementation.
Model: ogkalu/comic-text-and-bubble-detector from HuggingFace
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterable, Sequence
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

SPREAD_MIN_WIDTH = 1000
SPREAD_MIN_ASPECT = 1.35
SPREAD_OVERLAP_RATIO = 0.06
SPREAD_MIN_OVERLAP_PX = 48


def _box_area(box: Sequence[int] | np.ndarray) -> int:
    return max(0, int(box[2]) - int(box[0])) * max(0, int(box[3]) - int(box[1]))


def _box_iou_and_areas(
    a: Sequence[int] | np.ndarray, b: Sequence[int] | np.ndarray
) -> tuple[float, int, int, int]:
    area_a = _box_area(a)
    area_b = _box_area(b)
    ix1 = max(int(a[0]), int(b[0]))
    iy1 = max(int(a[1]), int(b[1]))
    ix2 = min(int(a[2]), int(b[2]))
    iy2 = min(int(a[3]), int(b[3]))
    inter = (ix2 - ix1) * (iy2 - iy1) if ix2 > ix1 and iy2 > iy1 else 0
    union = area_a + area_b - inter
    iou = float(inter) / float(union) if union > 0 else 0.0
    return iou, inter, area_a, area_b


def _is_duplicate_box(
    a: Sequence[int] | np.ndarray,
    b: Sequence[int] | np.ndarray,
    *,
    iou_threshold: float,
    contain_threshold: float | None,
) -> bool:
    iou, inter, area_a, area_b = _box_iou_and_areas(a, b)
    if iou >= iou_threshold:
        return True
    if contain_threshold is None:
        return False
    min_area = min(area_a, area_b)
    return min_area > 0 and (float(inter) / float(min_area)) >= contain_threshold


def _clamp_xyxy(box: Sequence[int] | np.ndarray, width: int, height: int) -> list[int]:
    x1 = max(0, min(width, int(box[0])))
    y1 = max(0, min(height, int(box[1])))
    x2 = max(x1, min(width, int(box[2])))
    y2 = max(y1, min(height, int(box[3])))
    return [x1, y1, x2, y2]


def _cut_boundary_clearance(
    box: Sequence[int] | np.ndarray, side: str, left_cut: int, right_cut: int
) -> float:
    """Distance from a box's horizontal center to its crop's cut boundary."""
    cx = (int(box[0]) + int(box[2])) * 0.5
    if side == "left":
        return abs(float(left_cut) - cx)
    return abs(cx - float(right_cut))


def _greedy_dedup(candidates: Iterable[TextBlock]) -> list[TextBlock]:
    kept: list[TextBlock] = []
    for candidate in candidates:
        if any(
            existing.block_type == candidate.block_type
            and _is_duplicate_box(
                candidate.xyxy,
                existing.xyxy,
                iou_threshold=0.45,
                contain_threshold=0.80,
            )
            for existing in kept
        ):
            continue
        kept.append(candidate)
    return kept


def _dedup_same_side_blocks(blocks: list[TextBlock]) -> list[TextBlock]:
    return _greedy_dedup(sorted(blocks, key=lambda b: b.confidence, reverse=True))


def _merge_spread_text_blocks(
    left_blocks: list[TextBlock],
    right_blocks: list[TextBlock],
    *,
    left_cut: int,
    right_cut: int,
) -> list[TextBlock]:
    """Merge left/right detections, preferring detections farther from the cut edge near the seam."""
    tagged: list[tuple[TextBlock, str]] = [
        *((b, "left") for b in _dedup_same_side_blocks(left_blocks)),
        *((b, "right") for b in _dedup_same_side_blocks(right_blocks)),
    ]
    # Across left vs right crops, boxes overlapping near the seam rank by distance
    # from their crop's cut boundary, tie-broken by confidence.
    tagged.sort(
        key=lambda item: (
            _cut_boundary_clearance(item[0].xyxy, item[1], left_cut, right_cut),
            item[0].confidence,
        ),
        reverse=True,
    )
    return _greedy_dedup(block for block, _side in tagged)


def _dedup_same_side_bubbles(bubbles: list[list[int]]) -> list[list[int]]:
    kept: list[list[int]] = []
    for candidate in bubbles:
        if any(
            _is_duplicate_box(
                candidate, existing, iou_threshold=0.50, contain_threshold=None
            )
            for existing in kept
        ):
            continue
        kept.append(candidate)
    return kept


def _merge_spread_bubbles(
    left_bubbles: list[list[int]],
    right_bubbles: list[list[int]],
    *,
    left_cut: int,
    right_cut: int,
) -> list[list[int]]:
    """Merge left/right bubble boxes, keeping unclipped bubbles over cut fragments."""
    tagged: list[tuple[list[int], str]] = [
        *((b, "left") for b in _dedup_same_side_bubbles(left_bubbles)),
        *((b, "right") for b in _dedup_same_side_bubbles(right_bubbles)),
    ]
    tagged.sort(
        key=lambda item: (
            _cut_boundary_clearance(item[0], item[1], left_cut, right_cut),
            _box_area(item[0]),
        ),
        reverse=True,
    )
    kept: list[tuple[list[int], str]] = []
    for candidate, side in tagged:
        if any(
            _is_duplicate_box(
                candidate,
                existing,
                iou_threshold=0.50,
                contain_threshold=0.80 if side != existing_side else None,
            )
            for existing, existing_side in kept
        ):
            continue
        kept.append((candidate, side))
    return [bubble for bubble, _side in kept]


def _append_full_spread_blocks(
    split_blocks: list[TextBlock], full_blocks: list[TextBlock]
) -> list[TextBlock]:
    """Keep full-page-only spread detections without overriding sharper half-page boxes."""
    merged = list(split_blocks)
    for cand in sorted(full_blocks, key=lambda b: b.confidence, reverse=True):
        duplicate = False
        for ex in merged:
            iou, inter, cand_area, ex_area = _box_iou_and_areas(cand.xyxy, ex.xyxy)
            min_area = min(cand_area, ex_area)
            same_class = cand.block_type == ex.block_type
            if same_class:
                if iou >= 0.45 or (min_area > 0 and float(inter) / float(min_area) >= 0.75):
                    duplicate = True
                    break
            elif iou >= 0.55:
                duplicate = True
                break
        if not duplicate:
            merged.append(cand)
    return merged


def _append_full_spread_bubbles(
    split_bubbles: list[list[int]], full_bubbles: list[list[int]]
) -> list[list[int]]:
    """Keep full-page-only spread bubble boxes without overriding unclipped half-page bubbles."""
    merged = list(split_bubbles)
    for cand in full_bubbles:
        if any(
            _is_duplicate_box(cand, ex, iou_threshold=0.50, contain_threshold=0.80)
            for ex in merged
        ):
            continue
        merged.append(cand)
    return merged


def _dedup_cross_class_blocks(
    blocks: list[TextBlock], bubble_boxes: list[list[int]]
) -> list[TextBlock]:
    """Collapse overlapping cross-class (text vs sfx) duplicates for the same text region."""
    ordered = sorted(
        blocks, key=lambda b: float(getattr(b, "confidence", 0.0)), reverse=True
    )
    kept: list[TextBlock] = []
    for cand in ordered:
        duplicate = False
        for ex in kept:
            if cand.block_type != ex.block_type:
                iou, _inter, _cand_area, _ex_area = _box_iou_and_areas(
                    cand.xyxy, ex.xyxy
                )
                if iou >= 0.70:
                    if ex.block_type != "text" and any(
                        TextDetector._is_inside(ex.xyxy, bubble)
                        for bubble in bubble_boxes
                    ):
                        ex.block_type = "text"
                    duplicate = True
                    break
        if not duplicate:
            kept.append(cand)
    return kept


def fuse_ctd_sfx_blocks(
    existing: list[TextBlock],
    candidates: list[TextBlock],
    seg_map: np.ndarray | None = None,
    *,
    min_conf: float = 0.60,
    min_area: int = 256,
) -> list[TextBlock]:
    """Append high-confidence, segmentation-backed SFX candidates that do not overlap existing blocks."""
    seg_2d: np.ndarray | None = None
    if seg_map is not None:
        arr = np.asarray(seg_map)
        if arr.ndim == 3 and arr.shape[2] == 1:
            arr = arr[:, :, 0]
        elif arr.ndim == 3 and arr.shape[0] == 1:
            arr = arr[0]
        if arr.ndim == 2 and arr.shape[0] > 0 and arr.shape[1] > 0:
            seg_2d = arr

    ordered = sorted(
        candidates,
        key=lambda cand: float(getattr(cand, "confidence", 0.0)),
        reverse=True,
    )
    accepted_sfx: list[TextBlock] = []
    for cand in ordered:
        conf = float(getattr(cand, "confidence", 0.0))
        if conf < min_conf:
            continue

        x1, y1, x2, y2 = cand.x1, cand.y1, cand.x2, cand.y2
        w = x2 - x1
        h = y2 - y1
        cand_area = w * h
        if w <= 0 or h <= 0 or cand_area < min_area or max(w, h) < 12:
            continue

        overlaps_existing = False
        for ex in existing:
            iou, inter, _cand_area, _ex_area = _box_iou_and_areas(cand.xyxy, ex.xyxy)
            if iou >= 0.20 or (float(inter) / float(cand_area)) >= 0.35:
                overlaps_existing = True
                break
        if overlaps_existing:
            continue

        if seg_2d is not None:
            seg_h, seg_w = seg_2d.shape
            sy1 = max(0, min(seg_h, y1))
            sy2 = max(sy1, min(seg_h, y2))
            sx1 = max(0, min(seg_w, x1))
            sx2 = max(sx1, min(seg_w, x2))
            if sy2 <= sy1 or sx2 <= sx1:
                continue
            if float(np.mean(seg_2d[sy1:sy2, sx1:sx2] >= 0.40)) < 0.06:
                continue

        if any(
            _is_duplicate_box(
                cand.xyxy, acc.xyxy, iou_threshold=0.45, contain_threshold=0.80
            )
            for acc in accepted_sfx
        ):
            continue

        sfx_block = TextBlock(
            xyxy=[x1, y1, x2, y2],
            block_type="sfx",
            text=cand.text,
            translated_text=cand.translated_text,
        )
        sfx_block.confidence = conf
        sfx_block.language = cand.language
        if cand.bubble_xyxy is not None:
            sfx_block.bubble_xyxy = list(cand.bubble_xyxy)
        accepted_sfx.append(sfx_block)

    return list(existing) + accepted_sfx


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

    async def _detect_crop(
        self, pil_image: Image.Image
    ) -> tuple[list[TextBlock], list[list[int]]]:
        """Run RT-DETR inference on a single PIL crop without bubble assignment."""
        session = self.session
        if session is None:
            raise RuntimeError("Model not initialized. Call initialize() first.")

        orig_w, orig_h = pil_image.size

        resized = np.asarray(pil_image.resize((640, 640)))
        im_data = np.ascontiguousarray(resized.transpose(2, 0, 1), dtype=np.float32)
        im_data *= np.float32(1.0 / 255.0)
        im_data = im_data[np.newaxis, ...]

        outputs = await asyncio.to_thread(
            session.run,
            None,
            {
                "images": im_data,
                "orig_target_sizes": np.array([[orig_w, orig_h]], dtype=np.int64),
            },
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
                    block_type="text" if label_id == 1 else "sfx",
                )
                block.confidence = float(score)
                text_blocks.append(block)

        return text_blocks, bubble_boxes

    async def detect(self, image: np.ndarray) -> list[TextBlock]:
        """Detect text regions in an RGB image."""
        if self.session is None:
            raise RuntimeError("Model not initialized. Call initialize() first.")

        pil_image = Image.fromarray(image)
        orig_w, orig_h = pil_image.size

        disable_split = os.getenv("DETECTOR_DISABLE_SPREAD_SPLIT", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        is_spread = (
            not disable_split
            and orig_w >= SPREAD_MIN_WIDTH
            and (orig_w / max(1, orig_h)) >= SPREAD_MIN_ASPECT
        )

        if is_spread:
            mid = orig_w // 2
            overlap = max(
                SPREAD_MIN_OVERLAP_PX, int(round(orig_w * SPREAD_OVERLAP_RATIO))
            )
            left_x2 = min(orig_w, mid + overlap)
            right_x_offset = max(0, mid - overlap)

            left_crop = pil_image.crop((0, 0, left_x2, orig_h))
            right_crop = pil_image.crop((right_x_offset, 0, orig_w, orig_h))

            left_blocks, left_bubbles = await self._detect_crop(left_crop)
            right_blocks, right_bubbles = await self._detect_crop(right_crop)
            full_blocks, full_bubbles = await self._detect_crop(pil_image)

            for block in left_blocks:
                block.xyxy = _clamp_xyxy(block.xyxy, orig_w, orig_h)
            left_bubbles = [_clamp_xyxy(b, orig_w, orig_h) for b in left_bubbles]

            for block in right_blocks:
                bx1, by1, bx2, by2 = block.xyxy.tolist()
                block.xyxy = _clamp_xyxy(
                    [bx1 + right_x_offset, by1, bx2 + right_x_offset, by2],
                    orig_w,
                    orig_h,
                )
            right_bubbles = [
                _clamp_xyxy(
                    [b[0] + right_x_offset, b[1], b[2] + right_x_offset, b[3]],
                    orig_w,
                    orig_h,
                )
                for b in right_bubbles
            ]

            for block in full_blocks:
                block.xyxy = _clamp_xyxy(block.xyxy, orig_w, orig_h)
            full_bubbles = [_clamp_xyxy(b, orig_w, orig_h) for b in full_bubbles]

            text_blocks = _append_full_spread_blocks(
                _merge_spread_text_blocks(
                    left_blocks,
                    right_blocks,
                    left_cut=left_x2,
                    right_cut=right_x_offset,
                ),
                full_blocks,
            )
            bubble_boxes = _append_full_spread_bubbles(
                _merge_spread_bubbles(
                    left_bubbles,
                    right_bubbles,
                    left_cut=left_x2,
                    right_cut=right_x_offset,
                ),
                full_bubbles,
            )
        else:
            text_blocks, bubble_boxes = await self._detect_crop(pil_image)

        text_blocks = _dedup_cross_class_blocks(text_blocks, bubble_boxes)

        for block in text_blocks:
            best_bubble: list[int] | None = None
            best_area: int | None = None
            for bubble in bubble_boxes:
                if self._is_inside(block.xyxy, bubble):
                    area = _box_area(bubble)
                    if best_area is None or area < best_area:
                        best_area = area
                        best_bubble = bubble
            if best_bubble is not None:
                block.bubble_xyxy = best_bubble

        logger.info(
            "Detected %s text blocks, %s bubbles", len(text_blocks), len(bubble_boxes)
        )
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
