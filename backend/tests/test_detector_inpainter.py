"""Tests for Phase 3 spread-split detection, SFX fusion, and boundary-aware LaMa."""

from __future__ import annotations

import asyncio
import os
import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import numpy as np
import onnxruntime as ort

from manga_backend.comfy_lanpaint.crop import build_crop_regions
from manga_backend.models.detector import (
    SPREAD_MIN_ASPECT,
    SPREAD_MIN_OVERLAP_PX,
    SPREAD_MIN_WIDTH,
    SPREAD_OVERLAP_RATIO,
    TextDetector,
    fuse_ctd_sfx_blocks,
)
from manga_backend.models.inpainter import (
    LAMA_MASK_GROWTH,
    Inpainter,
    _expand_crop_to_square,
)
from manga_backend.utils.textblock import TextBlock


class _ScriptedDetectorSession:
    """Fake RT-DETR session returning preconfigured outputs per orig_target_sizes."""

    def __init__(self, script_fn):
        self._script_fn = script_fn
        self.calls: list[dict[str, object]] = []

    def run(self, _outputs, inputs):
        orig_sizes = inputs["orig_target_sizes"].tolist()
        img_tensor = inputs["images"]
        self.calls.append(
            {
                "orig_target_sizes": orig_sizes,
                "image_shape": img_tensor.shape,
                "image_dtype": img_tensor.dtype,
            }
        )
        labels, boxes, scores = self._script_fn(len(self.calls) - 1, orig_sizes[0])
        return [
            np.asarray(labels, dtype=np.int64),
            np.asarray(boxes, dtype=np.float32),
            np.asarray(scores, dtype=np.float32),
        ]


class DetectorSpreadTests(unittest.TestCase):
    def test_single_page_runs_one_inference_call_and_picks_tightest_bubble(self):
        detector = TextDetector()
        session = _ScriptedDetectorSession(
            lambda _idx, _size: (
                [0, 0, 1, 1],
                [
                    [40, 40, 400, 400],
                    [80, 80, 220, 220],
                    [100, 100, 200, 200],
                    [300, 300, 340, 340],
                ],
                [0.90, 0.85, 0.80, 0.25],  # last is below default threshold 0.3
            )
        )
        detector.session = cast(ort.InferenceSession, session)
        image = np.zeros((1100, 800, 3), dtype=np.uint8)

        blocks = asyncio.run(detector.detect(image))

        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0]["orig_target_sizes"], [[800, 1100]])
        self.assertEqual(session.calls[0]["image_shape"], (1, 3, 640, 640))
        self.assertEqual(session.calls[0]["image_dtype"], np.float32)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].xyxy.tolist(), [100, 100, 200, 200])
        self.assertEqual(blocks[0].bubble_xyxy, [80, 80, 220, 220])

    def test_non_spread_dimensions_never_split(self):
        for height, width in ((500, SPREAD_MIN_WIDTH - 1), (1400, 1200)):
            with self.subTest(height=height, width=width):
                detector = TextDetector()
                session = _ScriptedDetectorSession(lambda _idx, _size: ([], [], []))
                detector.session = cast(ort.InferenceSession, session)
                image = np.zeros((height, width, 3), dtype=np.uint8)

                asyncio.run(detector.detect(image))

                self.assertEqual(len(session.calls), 1)
                self.assertEqual(session.calls[0]["orig_target_sizes"], [[width, height]])

    def test_wide_spread_splits_remaps_dedups_seam_and_picks_tightest_bubble(self):
        orig_w, orig_h = 1525, 912
        self.assertGreaterEqual(orig_w, SPREAD_MIN_WIDTH)
        self.assertGreaterEqual(orig_w / orig_h, SPREAD_MIN_ASPECT)
        mid = orig_w // 2
        overlap = max(SPREAD_MIN_OVERLAP_PX, int(round(orig_w * SPREAD_OVERLAP_RATIO)))
        left_w = mid + overlap  # 854
        right_x_offset = mid - overlap  # 670
        right_w = orig_w - right_x_offset  # 855

        def scripted(call_idx, size):
            if call_idx == 0:
                self.assertEqual(size, [left_w, orig_h])
                return (
                    [0, 0, 0, 1, 1, 2],
                    [
                        [50, 50, 420, 420],      # outer left bubble
                        [80, 80, 220, 220],      # tighter left bubble
                        [780, 280, 854, 380],    # clipped seam bubble
                        [100, 100, 200, 200],    # left text block
                        [790, 300, 850, 360],    # seam text (cx=820, 34px from left cut)
                        [700, 500, 740, 540],    # same coords as right text, different class
                    ],
                    [0.90, 0.90, 0.85, 0.88, 0.95, 0.70],
                )
            if call_idx == 1:
                self.assertEqual(size, [right_w, orig_h])
                return (
                    [0, 1, 1, 1],
                    [
                        [110, 280, 240, 380],  # full seam bubble -> [780, 280, 910, 380]
                        [120, 300, 180, 360],  # seam text -> [790, 300, 850, 360] (150px from right cut)
                        [300, 400, 420, 500],  # right text -> [970, 400, 1090, 500]
                        [820, 880, 900, 950],  # overshoots -> clamped to [1490, 880, 1525, 912]
                    ],
                    [0.90, 0.80, 0.92, 0.75],
                )
            self.assertEqual(size, [orig_w, orig_h])
            return (
                [0, 1, 1],
                [
                    [1140, 80, 1260, 360],   # full-page-only tall bubble
                    [1153, 92, 1241, 337],   # full-page-only tall SFX/scream
                    [972, 402, 1088, 498],   # duplicate of right half block -> suppressed
                ],
                [0.85, 0.38, 0.99],
            )

        detector = TextDetector()
        session = _ScriptedDetectorSession(scripted)
        detector.session = cast(ort.InferenceSession, session)
        image = np.zeros((orig_h, orig_w, 3), dtype=np.uint8)

        with patch.dict(os.environ, {"DETECTOR_DISABLE_SPREAD_SPLIT": "0"}):
            blocks = asyncio.run(detector.detect(image))

        self.assertEqual(len(session.calls), 3)
        coords = {tuple(b.xyxy.tolist()): b for b in blocks}
        self.assertEqual(len(blocks), 6)

        left_block = coords[(100, 100, 200, 200)]
        self.assertEqual(left_block.bubble_xyxy, [80, 80, 220, 220])

        seam_block = coords[(790, 300, 850, 360)]
        # Right crop wins near right side of seam despite lower confidence (0.80 vs 0.95).
        self.assertAlmostEqual(seam_block.confidence, 0.80, places=5)
        self.assertEqual(seam_block.bubble_xyxy, [780, 280, 910, 380])

        self.assertIn((700, 500, 740, 540), coords)
        self.assertEqual(coords[(700, 500, 740, 540)].block_type, "sfx")
        self.assertIn((970, 400, 1090, 500), coords)
        self.assertIn((1490, 880, orig_w, orig_h), coords)
        self.assertIn((1153, 92, 1241, 337), coords)
        self.assertEqual(coords[(1153, 92, 1241, 337)].bubble_xyxy, [1140, 80, 1260, 360])

    def test_seam_duplicate_with_equal_cut_distance_tiebreaks_by_confidence(self):
        orig_w, orig_h = 1525, 912
        right_x_offset = (orig_w // 2) - max(
            SPREAD_MIN_OVERLAP_PX, int(round(orig_w * SPREAD_OVERLAP_RATIO))
        )
        # Centered around mid = 762 -> equal 92px distance from both crop cuts.
        left_box = [732, 200, 792, 260]
        right_box = [732 - right_x_offset, 200, 792 - right_x_offset, 260]

        def scripted(call_idx, _size):
            if call_idx == 0:
                return ([1], [left_box], [0.65])
            if call_idx == 1:
                return ([1], [right_box], [0.91])
            return ([], [], [])

        detector = TextDetector()
        detector.session = cast(
            ort.InferenceSession, _ScriptedDetectorSession(scripted)
        )
        blocks = asyncio.run(detector.detect(np.zeros((orig_h, orig_w, 3), dtype=np.uint8)))

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].xyxy.tolist(), left_box)
        self.assertAlmostEqual(blocks[0].confidence, 0.91, places=5)

    def test_disable_spread_split_env_forces_single_inference(self):
        detector = TextDetector()
        session = _ScriptedDetectorSession(lambda _idx, _size: ([], [], []))
        detector.session = cast(ort.InferenceSession, session)
        image = np.zeros((912, 1525, 3), dtype=np.uint8)

        with patch.dict(os.environ, {"DETECTOR_DISABLE_SPREAD_SPLIT": "1"}):
            asyncio.run(detector.detect(image))

        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0]["orig_target_sizes"], [[1525, 912]])

    def test_cross_class_duplicates_collapse_and_upgrade_inside_bubble(self):
        detector = TextDetector()
        session = _ScriptedDetectorSession(
            lambda _idx, _size: (
                [0, 2, 1, 2, 1],
                [
                    [60, 60, 240, 240],    # bubble enclosing first pair
                    [100, 100, 180, 180],  # sfx inside bubble (higher conf)
                    [100, 100, 180, 180],  # duplicate text inside bubble (lower conf)
                    [300, 100, 380, 180],  # free sfx outside bubble (higher conf)
                    [302, 102, 378, 178],  # duplicate text outside bubble (IoU >= 0.70)
                ],
                [0.95, 0.88, 0.55, 0.90, 0.60],
            )
        )
        detector.session = cast(ort.InferenceSession, session)
        image = np.zeros((500, 500, 3), dtype=np.uint8)

        blocks = asyncio.run(detector.detect(image))

        self.assertEqual(len(blocks), 2)
        by_x1 = {b.x1: b for b in blocks}
        self.assertEqual(by_x1[100].block_type, "text")
        self.assertEqual(by_x1[100].bubble_xyxy, [60, 60, 240, 240])
        self.assertEqual(by_x1[300].block_type, "sfx")
        self.assertIsNone(by_x1[300].bubble_xyxy)


class FuseCtdSfxTests(unittest.TestCase):
    @staticmethod
    def _block(xyxy, conf=0.8, block_type="text"):
        block = TextBlock(xyxy=xyxy, block_type=block_type)
        block.confidence = conf
        return block

    def test_adds_high_seg_sfx_and_rejects_overlaps_low_seg_and_duplicates(self):
        existing = [self._block([50, 50, 150, 150], conf=0.95, block_type="text")]
        seg_map = np.zeros((400, 400), dtype=np.float32)
        seg_map[220:260, 220:280] = 0.85  # high segmentation coverage inside [210, 210, 290, 270]
        seg_map[55:60, 305:310] = 0.90    # only 25/3600 < 0.06 inside [300, 50, 360, 110]
        seg_map[305:355, 215:275] = 0.90  # high seg, but candidate conf is below 0.60

        candidates = [
            self._block([210, 210, 290, 270], conf=0.72, block_type="text"),
            self._block([212, 212, 288, 268], conf=0.65, block_type="sfx"),  # dup of above
            self._block([60, 60, 140, 140], conf=0.95, block_type="sfx"),    # inside existing
            self._block([100, 50, 200, 150], conf=0.90, block_type="sfx"),   # IoU=0.33 >= 0.20
            self._block([300, 50, 360, 110], conf=0.90, block_type="sfx"),   # pure art (low seg)
            self._block([210, 300, 280, 360], conf=0.59, block_type="sfx"),  # low conf
            self._block([10, 300, 25, 315], conf=0.90, block_type="sfx"),    # area 225 < 256
        ]

        fused = fuse_ctd_sfx_blocks(existing, candidates, seg_map=seg_map)

        self.assertEqual(len(fused), 2)
        self.assertIs(fused[0], existing[0])
        self.assertEqual(fused[1].xyxy.tolist(), [210, 210, 290, 270])
        self.assertEqual(fused[1].block_type, "sfx")
        self.assertAlmostEqual(fused[1].confidence, 0.72, places=5)
        self.assertEqual(candidates[0].block_type, "text")  # input candidate not mutated

    def test_handles_missing_or_invalid_seg_map(self):
        existing = [self._block([0, 0, 40, 40])]
        candidates = [self._block([100, 100, 140, 130], conf=0.75, block_type="text")]

        fused_none = fuse_ctd_sfx_blocks(existing, candidates, seg_map=None)
        fused_empty = fuse_ctd_sfx_blocks(
            existing, candidates, seg_map=np.empty((0, 0), dtype=np.float32)
        )

        self.assertEqual(len(fused_none), 2)
        self.assertEqual(fused_none[1].block_type, "sfx")
        self.assertEqual(len(fused_empty), 2)
        self.assertEqual(fused_empty[1].block_type, "sfx")


class _BoundaryInspectSession:
    """Fake LaMa session recording crops/masks and filling a constant value."""

    def __init__(self, fill_value: int = 200):
        self.fill_value = fill_value
        self.calls: list[tuple[np.ndarray, np.ndarray]] = []

    def get_inputs(self):
        return [SimpleNamespace(name="image"), SimpleNamespace(name="mask")]

    def run(self, _outputs, inputs, _options=None):
        img_tensor = inputs["image"]
        mask_tensor = inputs["mask"]
        self.calls.append((img_tensor.copy(), mask_tensor.copy()))
        out = np.full_like(img_tensor, self.fill_value / 255.0, dtype=np.float32)
        return [out]


class InpainterBoundaryTests(unittest.TestCase):
    def _build_rimmed_test_case(self):
        image = np.full((320, 320, 3), 40, dtype=np.uint8)
        mask = np.zeros((320, 320), dtype=np.uint8)
        mask[100:140, 100:140] = 255
        # Bright 1 px anti-aliased fringe immediately outside the binary mask.
        image[99, 99:141] = 255
        image[140, 99:141] = 255
        image[99:141, 99] = 255
        image[99:141, 140] = 255
        return image, mask

    def _assert_boundary_aware(self, device: str, max_pixels: int | None, expected_shape: tuple[int, int] | None):
        image, mask = self._build_rimmed_test_case()
        session = _BoundaryInspectSession(fill_value=200)
        inpainter = Inpainter(device=device)
        inpainter.session = cast(ort.InferenceSession, session)
        inpainter.max_pixels = max_pixels

        out = asyncio.run(inpainter.inpaint(image, mask))

        self.assertEqual(LAMA_MASK_GROWTH, 2)
        self.assertEqual(len(session.calls), 1)
        img_tensor, mask_tensor = session.calls[0]
        if expected_shape is not None:
            self.assertEqual(img_tensor.shape[2:], expected_shape)

        # Every bright 1 px rim pixel (image == 1.0) inside the crop is hidden by dilated mask.
        rim_pixels = (img_tensor[0, 0] > 0.99)
        self.assertTrue(np.any(rim_pixels))
        self.assertTrue(np.all(mask_tensor[0, 0][rim_pixels] == 1.0))
        # A 40x40 mask dilated by LAMA_MASK_GROWTH=2 becomes 44x44 = 1936 pixels.
        self.assertEqual(int(mask_tensor.sum()), 44 * 44)

        # Strictly zero pixels changed outside the original binary mask.
        self.assertEqual(np.count_nonzero((out != image) & (mask[:, :, None] == 0)), 0)

        # Every masked pixel is replaced cleanly without bleeding dark original rims back in.
        self.assertTrue(np.all(out[mask > 0] == 200))

    def test_cpu_unbucketed_dilates_generation_mask_keeps_zero_outside_and_commits_cleanly(self):
        self._assert_boundary_aware(device="cpu", max_pixels=None, expected_shape=None)

    def test_cuda_bucketed_dilates_generation_mask_keeps_zero_outside_and_commits_cleanly(self):
        self._assert_boundary_aware(
            device="cuda", max_pixels=1_000_000, expected_shape=(256, 256)
        )

    def test_oversized_tiled_dilates_generation_mask_and_keeps_zero_outside(self):
        image = np.full((700, 700, 3), 40, dtype=np.uint8)
        mask = np.zeros((700, 700), dtype=np.uint8)
        mask[100:600, 100:600] = 255
        image[99, 99:601] = 255
        image[600, 99:601] = 255
        image[99:601, 99] = 255
        image[99:601, 600] = 255

        session = _BoundaryInspectSession(fill_value=200)
        inpainter = Inpainter(device="cpu")
        inpainter.session = cast(ort.InferenceSession, session)
        inpainter.max_pixels = 250_000

        out = asyncio.run(inpainter.inpaint(image, mask))

        self.assertGreater(len(session.calls), 1)
        for img_tensor, mask_tensor in session.calls:
            rim_pixels = img_tensor[0, 0] > 0.99
            if np.any(rim_pixels):
                self.assertTrue(np.all(mask_tensor[0, 0][rim_pixels] == 1.0))
        self.assertEqual(np.count_nonzero((out != image) & (mask[:, :, None] == 0)), 0)
        self.assertTrue(np.all(out[mask > 0] == 200))

    def test_expand_crop_to_square_mask_growth_backward_compatible(self):
        image, mask = self._build_rimmed_test_case()
        [region] = build_crop_regions(
            image, mask, context=32, multiple=8, mask_growth=LAMA_MASK_GROWTH, max_crop_pixels=None
        )

        _, un_dilated, _ = _expand_crop_to_square(image, mask, region, 256)
        _, dilated, _ = _expand_crop_to_square(
            image, mask, region, 256, mask_growth=LAMA_MASK_GROWTH
        )

        self.assertEqual(int(np.count_nonzero(un_dilated)), 40 * 40)
        self.assertEqual(int(np.count_nonzero(dilated)), 44 * 44)


if __name__ == "__main__":
    unittest.main()
