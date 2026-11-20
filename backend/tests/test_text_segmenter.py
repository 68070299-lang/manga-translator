"""Model-free tests for neural TextSegmenter preprocessing, decoding, and spread merging."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from manga_backend.models.text_segmenter import (
    DEFAULT_INPUT_SIZE,
    LETTERBOX_PAD_VALUE,
    TextSegmentationResult,
    TextSegmenter,
    text_segmenter_disabled,
)
from test_support import writable_temp_directory


def _make_mock_session(run_side_effect=None, input_size: int = DEFAULT_INPUT_SIZE) -> MagicMock:
    session = MagicMock()
    session.get_inputs.return_value = [
        SimpleNamespace(name="images", shape=[1, 3, input_size, input_size], type="tensor(float)")
    ]
    session.get_outputs.return_value = [
        SimpleNamespace(name="blk", shape=[1, 64512, 7], type="tensor(float)"),
        SimpleNamespace(name="seg", shape=[1, 1, input_size, input_size], type="tensor(float)"),
        SimpleNamespace(name="det", shape=[1, 2, input_size, input_size], type="tensor(float)"),
    ]
    session.get_providers.return_value = ["CPUExecutionProvider"]
    if run_side_effect is not None:
        session.run.side_effect = run_side_effect
    return session


class TestTextSegmenterConfigAndDisable:
    def test_rejects_invalid_thresholds(self):
        with pytest.raises(ValueError, match="confidence_threshold"):
            TextSegmenter(confidence_threshold=0.0)
        with pytest.raises(ValueError, match="iou_threshold"):
            TextSegmenter(iou_threshold=1.0)
        with pytest.raises(ValueError, match="max_candidates"):
            TextSegmenter(max_candidates=0)

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "On"])
    def test_text_segmenter_disabled_truthy_values(self, value: str):
        with patch.dict("os.environ", {"TEXT_SEGMENTER_DISABLE": value}):
            assert text_segmenter_disabled() is True

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
    def test_text_segmenter_disabled_falsy_values(self, value: str):
        with patch.dict("os.environ", {"TEXT_SEGMENTER_DISABLE": value}):
            assert text_segmenter_disabled() is False

    @pytest.mark.asyncio
    async def test_disabled_initialize_and_segment_skip_session(self):
        image = np.zeros((120, 160, 3), dtype=np.uint8)
        with (
            patch.dict("os.environ", {"TEXT_SEGMENTER_DISABLE": "true"}),
            patch("manga_backend.models.text_segmenter.download_text_segmenter_onnx") as mock_dl,
            patch("manga_backend.models.text_segmenter.create_onnx_session") as mock_sess,
        ):
            segmenter = TextSegmenter(device="cpu")
            await segmenter.initialize()
            result = await segmenter.segment(image)

        assert not mock_dl.called
        assert not mock_sess.called
        assert segmenter.session is None
        assert isinstance(result, TextSegmentationResult)
        assert result.prob_map.shape == (120, 160)
        assert result.prob_map.dtype == np.float32
        assert float(result.prob_map.max()) == 0.0
        assert result.blocks == []

    def test_result_dataclass_is_frozen(self):
        result = TextSegmentationResult(
            prob_map=np.zeros((10, 10), dtype=np.float32),
            blocks=[],
        )
        assert dataclasses.is_dataclass(result)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.blocks = []  # type: ignore[misc]


class TestLetterboxAndUnletterbox:
    def test_letterbox_preserves_aspect_ratio_and_rgb_normalization(self):
        image = np.zeros((256, 512, 3), dtype=np.uint8)
        image[:, :, 0] = 255  # Red in RGB

        tensor, scale, pad, resized_size = TextSegmenter._letterbox(image, 1024)

        assert tensor.shape == (1, 3, 1024, 1024)
        assert tensor.dtype == np.float32
        assert scale == pytest.approx(2.0)
        assert resized_size == (1024, 512)
        assert pad == (0, 256)
        # Top/bottom pad region is LETTERBOX_PAD_VALUE / 255.0
        expected_pad = np.float32(LETTERBOX_PAD_VALUE / 255.0)
        assert float(tensor[0, 0, 10, 10]) == pytest.approx(expected_pad, abs=1e-6)
        # Content region has R=1.0, G=0.0, B=0.0
        assert float(tensor[0, 0, 300, 100]) == pytest.approx(1.0)
        assert float(tensor[0, 1, 300, 100]) == pytest.approx(0.0)
        assert float(tensor[0, 2, 300, 100]) == pytest.approx(0.0)

    def test_unletterbox_seg_maps_coordinates_and_clamps_range(self):
        # Original landscape image 256x512 -> scale 2.0, resized (1024, 512), pad (0, 256)
        raw_seg = np.zeros((1, 1, 1024, 1024), dtype=np.float32)
        # Put out-of-range drift inside padding & active box to verify cropping + clamping
        raw_seg[0, 0, 0:50, 0:50] = 0.99  # inside top pad -> must be cropped out
        raw_seg[0, 0, 356:456, 200:400] = 0.85  # corresponds to y=[50:100], x=[100:200]
        raw_seg[0, 0, 260, 10] = 1.05
        raw_seg[0, 0, 260, 12] = -0.05
        raw_seg[0, 0, 260, 14] = np.nan

        prob_map = TextSegmenter._unletterbox_seg(
            raw_seg,
            (256, 512),
            pad=(0, 256),
            resized_size=(1024, 512),
        )

        assert prob_map.shape == (256, 512)
        assert prob_map.dtype == np.float32
        assert float(prob_map.min()) >= 0.0
        assert float(prob_map.max()) <= 1.0
        assert float(prob_map[10, 10]) == pytest.approx(0.0)
        assert float(prob_map[75, 150]) == pytest.approx(0.85, abs=1e-4)

    @pytest.mark.asyncio
    async def test_rejects_invalid_image_shapes(self):
        segmenter = TextSegmenter()
        segmenter.session = _make_mock_session()

        with pytest.raises(ValueError, match="HxWx3"):
            await segmenter.segment(np.zeros((64, 64), dtype=np.uint8))
        with pytest.raises(ValueError, match="HxWx3"):
            await segmenter.segment(np.zeros((64, 64, 4), dtype=np.uint8))
        with pytest.raises(ValueError, match="empty"):
            await segmenter.segment(np.zeros((0, 64, 3), dtype=np.uint8))


class TestBlkDecodingAndNms:
    def test_decode_blk_scales_coords_filters_conf_and_runs_nms(self):
        # Image 256x512 -> scale=2.0, pad=(0, 256)
        # Candidate 0: letterbox box [100..200, 356..456] => cx=150, cy=406, w=100, h=100
        #   maps to orig [50..100, 50..100], obj=0.9, cls=(0.1, 0.8) => score=0.72
        # Candidate 1: overlapping duplicate at cx=152, cy=408, w=100, h=100, score=0.56
        # Candidate 2: low-confidence box obj=0.5, cls=(0.6, 0.2) => score=0.30 (< 0.4)
        # Candidate 3: NaN row => ignored
        # Candidate 4: distinct second box [500..700, 556..656] => orig [250..350, 150..200]
        preds = np.array(
            [
                [
                    [150.0, 406.0, 100.0, 100.0, 0.9, 0.1, 0.8],
                    [152.0, 408.0, 100.0, 100.0, 0.8, 0.0, 0.7],
                    [300.0, 300.0, 40.0, 40.0, 0.5, 0.6, 0.2],
                    [np.nan, 400.0, 20.0, 20.0, 0.95, 0.0, 1.0],
                    [600.0, 606.0, 200.0, 100.0, 0.95, 0.0, 0.9],
                ]
            ],
            dtype=np.float32,
        )

        blocks = TextSegmenter._decode_blk(
            preds,
            (256, 512),
            scale=2.0,
            pad=(0, 256),
            confidence_threshold=0.4,
            iou_threshold=0.35,
        )

        assert len(blocks) == 2
        assert blocks[0].xyxy.tolist() == [250, 150, 350, 200]
        assert blocks[0].confidence == pytest.approx(0.95 * 0.9, abs=1e-5)
        assert blocks[0].block_type == "text"
        assert blocks[1].xyxy.tolist() == [50, 50, 100, 100]
        assert blocks[1].confidence == pytest.approx(0.9 * 0.8, abs=1e-5)

    def test_decode_blk_caps_max_candidates(self):
        preds = np.array(
            [
                [
                    [100.0, 100.0, 20.0, 20.0, 0.9, 0.0, 1.0],
                    [300.0, 300.0, 20.0, 20.0, 0.7, 0.0, 1.0],
                    [500.0, 500.0, 20.0, 20.0, 0.5, 0.0, 1.0],
                ]
            ],
            dtype=np.float32,
        )

        blocks = TextSegmenter._decode_blk(
            preds,
            (1024, 1024),
            scale=1.0,
            pad=(0, 0),
            confidence_threshold=0.4,
            iou_threshold=0.35,
            max_candidates=2,
        )

        assert len(blocks) == 2
        assert sorted(round(b.confidence, 1) for b in blocks) == [0.7, 0.9]


class TestSpreadSplitAndSessionLifecycle:
    @pytest.mark.asyncio
    async def test_wide_spread_splits_merges_prob_map_and_deduplicates_blocks(self):
        # 1500x900 triggers spread split: width >= 1000 and 1500/900 = 1.67 >= 1.35
        image = np.full((900, 1500, 3), 255, dtype=np.uint8)
        calls: list[np.ndarray] = []

        def fake_run(output_names, feed):
            assert output_names == ["blk", "seg"]
            tensor = feed["images"]
            calls.append(tensor)
            call_idx = len(calls)

            seg = np.zeros((1, 1, 1024, 1024), dtype=np.float32)
            blk = np.zeros((1, 4, 7), dtype=np.float32)

            if call_idx == 1:
                # Left half: width = 750 + 90 = 840, height = 900
                # scale = 1024/900, resized=(956, 1024), pad=(34, 0)
                seg[0, 0, :, :] = 0.25
                # Seam box at orig x=[720, 770], y=[200, 300] (away from cut x=840)
                s = 1024.0 / 900.0
                px = 34.0
                blk[0, 0] = [745 * s + px, 250 * s, 50 * s, 100 * s, 0.90, 0.0, 1.0]
                # Left-only box at x=[100, 160], y=[100, 200]
                blk[0, 1] = [130 * s + px, 150 * s, 60 * s, 100 * s, 0.85, 0.0, 1.0]
            else:
                # Right half: x starts at right_x1 = 750 - 90 = 660, width = 840, height = 900
                seg[0, 0, :, :] = 0.75
                s = 1024.0 / 900.0
                px = 34.0
                # Duplicate seam box shifted into right half: x=[720-660, 770-660]=[60, 110]
                blk[0, 0] = [85 * s + px, 250 * s, 50 * s, 100 * s, 0.65, 0.0, 1.0]
                # Right-only box at orig x=[1200, 1280] -> local [540, 620]
                blk[0, 1] = [580 * s + px, 450 * s, 80 * s, 120 * s, 0.88, 0.0, 1.0]

            return [blk, seg]

        segmenter = TextSegmenter(device="cpu")
        segmenter.session = _make_mock_session(run_side_effect=fake_run)

        result = await segmenter.segment(image)

        assert len(calls) == 2
        assert result.prob_map.shape == (900, 1500)
        # Left-only [0..660) should be ~0.25; overlap [660..840) takes max(0.25, 0.75)=0.75; right >= 840 is 0.75
        assert float(result.prob_map[450, 200]) == pytest.approx(0.25, abs=1e-4)
        assert float(result.prob_map[450, 750]) == pytest.approx(0.75, abs=1e-4)
        assert float(result.prob_map[450, 1200]) == pytest.approx(0.75, abs=1e-4)
        # Seam duplicate suppressed -> 3 unique blocks
        assert len(result.blocks) == 3
        seam_blocks = [b for b in result.blocks if 700 <= b.x1 <= 740]
        assert len(seam_blocks) == 1
        assert seam_blocks[0].confidence == pytest.approx(0.90, abs=1e-3)

    def test_spread_merge_prefers_unclipped_box_over_cut_edge_fragment(self):
        from manga_backend.utils.textblock import TextBlock

        # Simulate width=1500, left_cut=840, right_cut=660
        # Full block at [780, 100, 1000, 200] in right half -> local x=[120, 340]
        # Clipped fragment in left half at [780, 100, 840, 200] even with higher conf
        left_clipped = TextBlock([780, 100, 840, 200], block_type="text")
        left_clipped.confidence = 0.95
        right_full_local = TextBlock([120, 100, 340, 200], block_type="text")
        right_full_local.confidence = 0.80

        merged = TextSegmenter._merge_spread_blocks(
            [left_clipped],
            [right_full_local],
            width=1500,
            height=900,
            left_cut=840,
            right_cut=660,
            iou_threshold=0.35,
        )

        assert len(merged) == 1
        assert merged[0].xyxy.tolist() == [780, 100, 1000, 200]
        assert merged[0].confidence == pytest.approx(0.80)

    @pytest.mark.asyncio
    async def test_non_spread_page_runs_single_pass(self):
        # 1200x1000 has aspect 1.2 < 1.35 -> single pass
        image = np.zeros((1000, 1200, 3), dtype=np.uint8)
        calls = 0

        def fake_run(_output_names, _feed):
            nonlocal calls
            calls += 1
            return [
                np.zeros((1, 2, 7), dtype=np.float32),
                np.full((1, 1, 1024, 1024), 0.4, dtype=np.float32),
            ]

        segmenter = TextSegmenter(device="cpu")
        segmenter.session = _make_mock_session(run_side_effect=fake_run)

        result = await segmenter.segment(image)

        assert calls == 1
        assert result.prob_map.shape == (1000, 1200)
        assert float(result.prob_map[500, 600]) == pytest.approx(0.4, abs=1e-4)

    @pytest.mark.asyncio
    async def test_initialize_downloads_loads_session_and_dispose_releases_it(self):
        mock_session = _make_mock_session(
            run_side_effect=lambda _names, _feed: [
                np.zeros((1, 1, 7), dtype=np.float32),
                np.zeros((1, 1, 1024, 1024), dtype=np.float32),
            ]
        )

        with writable_temp_directory() as tmpdir:
            model_path = Path(tmpdir, "comic-text-detector.onnx")
            model_path.write_bytes(b"onnx")

            with (
                patch(
                    "manga_backend.models.text_segmenter.download_text_segmenter_onnx",
                    new=AsyncMock(return_value=model_path),
                ) as mock_dl,
                patch(
                    "manga_backend.models.text_segmenter.get_onnx_providers",
                    return_value=["CUDAExecutionProvider", "CPUExecutionProvider"],
                ),
                patch(
                    "manga_backend.models.text_segmenter.create_onnx_session",
                    return_value=mock_session,
                ) as mock_create,
            ):
                segmenter = TextSegmenter(device="cuda")
                await segmenter.initialize()

                mock_dl.assert_awaited_once_with(segmenter.model_dir)
                mock_create.assert_called_once_with(
                    model_path, ["CUDAExecutionProvider", "CPUExecutionProvider"]
                )
                assert segmenter.session is mock_session

                result = await segmenter.segment(np.zeros((100, 100, 3), dtype=np.uint8))
                assert result.prob_map.shape == (100, 100)

                segmenter.dispose()
                assert segmenter.session is None

                with pytest.raises(RuntimeError, match="not initialized"):
                    await segmenter.segment(np.zeros((100, 100, 3), dtype=np.uint8))
