"""Model-free tests for backend pipeline routing."""

import asyncio
import sys
import threading
import time
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import cv2
import numpy as np


BACKEND_DIR = Path(__file__).resolve().parents[1]

from manga_backend.comfy_lanpaint.client import ComfyUIError, ComfyUIUnreachableError
from manga_backend.pipeline import (
    DEFAULT_INPAINTER,
    DetectorUnavailableError,
    InpainterUnavailableError,
    PaddleOCRUnavailableError,
    TranslationPipeline,
)
from manga_backend.pipeline.bubbles import bubble_body_center, infer_bubble_render_bounds
from manga_backend.pipeline.detection import normalize_detector_type, text_block_to_dict
from manga_backend.pipeline.linebreak import break_lines
from manga_backend.pipeline.layout import (
    build_render_regions,
    expand_short_standalone_regions,
)
from manga_backend.pipeline.masks import (
    bubble_interior_mask_for_block,
    contour_safe_mask_for_block,
    create_mask,
    prepare_flux_inpainting,
)
from manga_backend.pipeline import orchestrator as orchestrator_module
from manga_backend.pipeline.orchestrator import erase_candidate, erase_from_translation
from manga_backend.pipeline.fit import (
    MIN_READABLE_FONT_SIZE,
    MIN_WHOLE_WORD_FONT_SIZE,
    fit_text_in_room,
)
from manga_backend.pipeline.rendering import build_rooms, render_text
from manga_backend.pipeline.rooms import box_room
from manga_backend.pipeline.text import has_cjk, has_cjk_letters
from manga_backend.utils.textblock import TextBlock


class FakeOcrEngine:
    def __init__(self):
        self.calls = []

    async def process(self, image, blocks):
        self.calls.append({"image": image, "blocks": blocks})
        return [f"text-{index}" for index, _block in enumerate(blocks)]


class FakePaddleEngine:
    def __init__(self):
        self.initialized = False
        self.calls = []

    async def initialize(self):
        self.initialized = True

    async def recognize_regions(self, image, regions):
        self.calls.append({"image": image, "regions": regions})
        return [f"paddle-{index}" for index, _region in enumerate(regions)]

    async def detect_and_recognize(self, image):
        self.calls.append({"image": image, "full": True})
        return [{"text": "full-paddle"}]


class StaticDetector:
    def __init__(self, blocks):
        self.blocks = blocks

    async def detect(self, _image):
        return self.blocks


class RecordingInpainter:
    last_backend_used = "test"

    def __init__(self):
        self.mask = None

    async def inpaint(self, source, mask):
        self.mask = mask
        return source.copy()


def _pipeline_with_fakes(
    blocks, *, detector_key=None
) -> tuple[TranslationPipeline, RecordingInpainter]:
    pipeline = TranslationPipeline(device="cpu")
    detector = StaticDetector(blocks)
    if detector_key is None:
        pipeline._detectors.default = detector
    else:
        pipeline._detectors.optional[detector_key] = detector
    inpainter = RecordingInpainter()
    pipeline._ocr = FakeOcrEngine()
    pipeline._inpainters.instance = inpainter
    pipeline._inpainters.type = "test"
    pipeline.model_status["inpainter"] = {"status": "loaded", "name": "test"}
    return pipeline, inpainter


class PipelineOcrRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_animetext_constructor_error_updates_status(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.zeros((12, 24, 3), dtype=np.uint8)

        with patch(
            "manga_backend.models.animetext_yolo_detector.AnimeTextYoloDetector",
            side_effect=ValueError("bad AnimeText environment"),
        ):
            with self.assertRaisesRegex(
                DetectorUnavailableError, "bad AnimeText environment"
            ):
                await pipeline.detect(image, detector="animetext-yolo")

        self.assertEqual(
            pipeline.model_status["animetext_yolo"],
            {
                "status": "error",
                "name": "animetext-yolo",
                "error": "bad AnimeText environment",
            },
        )

    async def test_animetext_detector_is_lazy_loaded_and_reused(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.zeros((12, 24, 3), dtype=np.uint8)
        created = []

        class FakeAnimeTextDetector:
            def __init__(self, device):
                self.device = device
                self.initialized = False
                created.append(self)

            async def initialize(self):
                self.initialized = True

            async def detect(self, _image):
                return [TextBlock([1, 2, 10, 11])]

            def dispose(self):
                pass

        with patch(
            "manga_backend.models.animetext_yolo_detector.AnimeTextYoloDetector",
            FakeAnimeTextDetector,
        ):
            first = await pipeline.detect(image, detector="animetext-yolo")
            second = await pipeline.detect(image, detector="animetext")

        self.assertEqual(first, second)
        self.assertEqual(first[0]["bbox"], [1, 2, 10, 11])
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].initialized)
        self.assertEqual(created[0].device, "cpu")
        self.assertEqual(
            pipeline.model_status["animetext_yolo"]["status"], "loaded"
        )

    async def test_translate_routes_through_selected_animetext_detector(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.zeros((12, 24, 3), dtype=np.uint8)

        class FakeAnimeTextDetector:
            async def detect(self, _image):
                return [TextBlock([1, 2, 10, 11])]

        pipeline._detectors.optional["animetext-yolo"] = FakeAnimeTextDetector()
        pipeline._ocr = FakeOcrEngine()
        result = await pipeline.translate(
            image,
            detector="animetext-yolo",
            translator="none",
            inpainter="none",
            render=False,
        )

        self.assertEqual(result["blocks"][0]["bbox"], [1, 2, 10, 11])
        self.assertEqual(result["original_texts"], ["text-0"])

    async def test_advertised_default_inpainter_reuses_preloaded_session(self):
        pipeline = TranslationPipeline(device="cpu")
        active = object()
        pipeline._inpainters.instance = active
        pipeline.model_status["inpainter"]["status"] = "loaded"

        with patch("manga_backend.pipeline.inpainting.Inpainter") as inpainter_constructor:
            selected = await pipeline._inpainters.get(
                pipeline.model_status["inpainter"]["name"]
            )

        self.assertEqual(pipeline._inpainters.type, DEFAULT_INPAINTER)
        self.assertIs(selected, active)
        inpainter_constructor.assert_not_called()

    async def test_ocr_uses_default_engine_and_converts_dict_blocks(self):
        pipeline = TranslationPipeline(device="cpu")
        fake_ocr = FakeOcrEngine()
        pipeline._ocr = fake_ocr
        image = np.zeros((12, 24, 3), dtype=np.uint8)

        texts = await pipeline.ocr(image, [{"bbox": [1, 2, 10, 11], "type": "speech"}])

        self.assertEqual(texts, ["text-0"])
        block = fake_ocr.calls[0]["blocks"][0]
        self.assertIsInstance(block, TextBlock)
        self.assertEqual(block.xyxy.tolist(), [1, 2, 10, 11])
        self.assertEqual(block.block_type, "speech")

    async def test_ocr_routes_paddle_without_requiring_default_ocr_engine(self):
        pipeline = TranslationPipeline(device="cpu")
        fake_paddle = FakePaddleEngine()
        image = np.zeros((12, 24, 3), dtype=np.uint8)

        with patch(
            "manga_backend.pipeline.components.get_paddle_ocr", return_value=fake_paddle
        ) as mock_get_paddle:
            texts = await pipeline.ocr(
                image, [{"bbox": [1, 2, 10, 11]}], ocr="paddle-ocr"
            )

        mock_get_paddle.assert_called_once_with(lang="japan", device="cpu")
        self.assertTrue(fake_paddle.initialized)
        self.assertEqual(texts, ["paddle-0"])
        self.assertEqual(fake_paddle.calls[0]["regions"], [{"bbox": [1, 2, 10, 11]}])
        self.assertEqual(pipeline.model_status["paddle_ocr"]["status"], "loaded")

    async def test_paddle_full_ocr_updates_shared_status(self):
        pipeline = TranslationPipeline(device="cpu")
        fake_paddle = FakePaddleEngine()
        image = np.zeros((12, 24, 3), dtype=np.uint8)

        with patch("manga_backend.pipeline.components.get_paddle_ocr", return_value=fake_paddle):
            blocks = await pipeline.paddle_ocr_full(image, lang="en")

        self.assertEqual(blocks, [{"text": "full-paddle"}])
        self.assertEqual(pipeline.model_status["paddle_ocr"]["status"], "loaded")

    async def test_translate_uses_status_aware_paddle_path(self):
        pipeline = TranslationPipeline(device="cpu")
        fake_paddle = FakePaddleEngine()
        image = np.zeros((12, 24, 3), dtype=np.uint8)

        pipeline._detectors.default = StaticDetector([TextBlock([1, 2, 10, 11])])
        with patch("manga_backend.pipeline.components.get_paddle_ocr", return_value=fake_paddle):
            result = await pipeline.translate(
                image,
                ocr="paddle-ocr",
                inpainter="none",
                render=False,
            )

        self.assertEqual(result["original_texts"], ["paddle-0"])
        self.assertEqual(pipeline.model_status["paddle_ocr"]["status"], "loaded")

    async def test_paddle_recognition_failure_does_not_latch_readiness(self):
        pipeline = TranslationPipeline(device="cpu")
        pipeline.is_initialized = True
        pipeline.model_status["paddle_ocr"]["status"] = "loaded"
        fake_paddle = FakePaddleEngine()
        image = np.zeros((12, 24, 3), dtype=np.uint8)
        regions = [{"bbox": [1, 2, 10, 11]}]
        original = fake_paddle.recognize_regions
        seen_statuses = []

        async def failing(*args, **kwargs):
            seen_statuses.append(pipeline.model_status["paddle_ocr"]["status"])
            raise RuntimeError("ort exploded")

        with patch("manga_backend.pipeline.components.get_paddle_ocr", return_value=fake_paddle):
            fake_paddle.recognize_regions = failing
            with self.assertRaisesRegex(RuntimeError, "ort exploded"):
                await pipeline.ocr(image, regions, ocr="paddle-ocr")
            with self.assertRaisesRegex(RuntimeError, "ort exploded"):
                await pipeline.ocr(image, regions, ocr="paddle-ocr")
            self.assertEqual(seen_statuses, ["loaded", "loaded"])
            self.assertTrue(pipeline.component_ready("paddle_ocr"))

            fake_paddle.recognize_regions = original
            texts = await pipeline.ocr(image, regions, ocr="paddle-ocr")

        self.assertEqual(texts, ["paddle-0"])
        self.assertTrue(pipeline.component_ready("paddle_ocr"))

    async def test_paddle_cancellation_leaves_status_unchanged(self):
        pipeline = TranslationPipeline(device="cpu")
        pipeline.is_initialized = True
        pipeline.model_status["paddle_ocr"]["status"] = "loaded"
        fake_paddle = FakePaddleEngine()
        image = np.zeros((12, 24, 3), dtype=np.uint8)

        async def cancelled(*args, **kwargs):
            raise asyncio.CancelledError()

        fake_paddle.recognize_regions = cancelled
        fake_paddle.detect_and_recognize = cancelled
        with patch("manga_backend.pipeline.components.get_paddle_ocr", return_value=fake_paddle):
            with self.assertRaises(asyncio.CancelledError):
                await pipeline.ocr(image, [{"bbox": [1, 2, 10, 11]}], ocr="paddle-ocr")
            self.assertEqual(pipeline.model_status["paddle_ocr"]["status"], "loaded")
            with self.assertRaises(asyncio.CancelledError):
                await pipeline.paddle_ocr_full(image)
            self.assertEqual(pipeline.model_status["paddle_ocr"]["status"], "loaded")

    async def test_paddle_initialize_failure_marks_error_and_cancel_restores(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.zeros((12, 24, 3), dtype=np.uint8)
        fake_paddle = FakePaddleEngine()

        async def broken_initialize():
            raise RuntimeError("missing model")

        fake_paddle.initialize = broken_initialize
        with patch("manga_backend.pipeline.components.get_paddle_ocr", return_value=fake_paddle):
            with self.assertRaisesRegex(RuntimeError, "missing model"):
                await pipeline.paddle_ocr_full(image)
        self.assertEqual(pipeline.model_status["paddle_ocr"]["status"], "error")

        async def cancelled_initialize():
            raise asyncio.CancelledError()

        pipeline.model_status["paddle_ocr"]["status"] = "not_loaded"
        fake_paddle.initialize = cancelled_initialize
        with patch("manga_backend.pipeline.components.get_paddle_ocr", return_value=fake_paddle):
            with self.assertRaises(asyncio.CancelledError):
                await pipeline.paddle_ocr_full(image)
        self.assertEqual(pipeline.model_status["paddle_ocr"]["status"], "not_loaded")

    async def test_overlapping_paddle_requests_do_not_flip_loaded_status(self):
        pipeline = TranslationPipeline(device="cpu")
        pipeline.is_initialized = True
        pipeline.model_status["paddle_ocr"]["status"] = "loaded"
        fake_paddle = FakePaddleEngine()
        image = np.zeros((12, 24, 3), dtype=np.uint8)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_recognize(img, regions):
            entered.set()
            await release.wait()
            return ["slow"]

        fake_paddle.recognize_regions = slow_recognize
        with patch("manga_backend.pipeline.components.get_paddle_ocr", return_value=fake_paddle):
            task = asyncio.create_task(
                pipeline.ocr(image, [{"bbox": [1, 2, 10, 11]}], ocr="paddle-ocr")
            )
            await entered.wait()
            self.assertTrue(pipeline.component_ready("paddle_ocr"))
            release.set()
            self.assertEqual(await task, ["slow"])

    def test_paddle_engine_is_shared_across_languages(self):
        from manga_backend.pipeline import components as components_module

        created = []

        class Engine:
            def __init__(self, lang, device):
                created.append((lang, device))

        fake_module = types.ModuleType("manga_backend.models.paddle_ocr_onnx")
        fake_module.PaddleOCROnnxEngine = Engine
        with patch.dict(components_module._paddle_ocr_engines, {}, clear=True), patch.dict(
            sys.modules, {"manga_backend.models.paddle_ocr_onnx": fake_module}
        ):
            english = components_module.get_paddle_ocr(lang="en", device="cpu")
            chinese = components_module.get_paddle_ocr(lang="ch", device="cpu")
            gpu = components_module.get_paddle_ocr(lang="en", device="cuda")

        self.assertIs(english, chinese)
        self.assertIsNot(english, gpu)
        self.assertEqual(created, [("en", "cpu"), ("en", "cuda")])

    async def test_initialize_does_not_load_paddle_ocr(self):
        class FakeCore:
            def __init__(self, device):
                self.device = device

            async def initialize(self):
                await asyncio.sleep(0)

        pipeline = TranslationPipeline(device="cpu")
        with patch("manga_backend.pipeline.orchestrator.TextDetector", FakeCore), patch(
            "manga_backend.pipeline.orchestrator.OCREngine", FakeCore
        ), patch("manga_backend.pipeline.components.get_paddle_ocr") as get_paddle:
            await pipeline.initialize()

        get_paddle.assert_not_called()
        self.assertTrue(pipeline.is_ready)
        self.assertEqual(pipeline.model_status["paddle_ocr"]["status"], "not_loaded")
        # Not loaded yet is healthy: the first paddle-ocr request loads it.
        self.assertTrue(pipeline.component_ready("paddle_ocr"))
        self.assertTrue(pipeline.readiness()["paddle_ocr"])

    async def test_failed_paddle_load_is_reported_and_retried_without_affecting_core(self):
        pipeline = TranslationPipeline(device="cpu")
        pipeline.is_initialized = True
        pipeline.model_status["detector"]["status"] = "loaded"
        pipeline.model_status["ocr"]["status"] = "loaded"
        pipeline._ocr = FakeOcrEngine()
        fake_paddle = FakePaddleEngine()
        image = np.zeros((12, 24, 3), dtype=np.uint8)
        blocks = [{"bbox": [1, 2, 10, 11]}]
        attempts = []

        async def flaky_initialize():
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError("download failed")
            fake_paddle.initialized = True

        fake_paddle.initialize = flaky_initialize
        with patch("manga_backend.pipeline.components.get_paddle_ocr", return_value=fake_paddle):
            with self.assertRaises(PaddleOCRUnavailableError) as raised:
                await pipeline.ocr(image, blocks, ocr="paddle-ocr")

            self.assertEqual(raised.exception.status_code, 503)
            self.assertIn("models.paddle_ocr", raised.exception.public_detail)
            self.assertNotIn("download failed", raised.exception.public_detail)
            self.assertEqual(pipeline.model_status["paddle_ocr"]["status"], "error")
            self.assertIn("download failed", pipeline.model_status["paddle_ocr"]["error"])
            self.assertFalse(pipeline.component_ready("paddle_ocr"))
            self.assertFalse(pipeline.readiness()["paddle_ocr"])
            # Only the Paddle routes are affected.
            self.assertTrue(pipeline.is_ready)
            self.assertEqual(await pipeline.ocr(image, blocks), ["text-0"])

            self.assertEqual(
                await pipeline.ocr(image, blocks, ocr="paddle-ocr"), ["paddle-0"]
            )

        self.assertEqual(len(attempts), 2)
        self.assertEqual(pipeline.model_status["paddle_ocr"]["status"], "loaded")
        self.assertNotIn("error", pipeline.model_status["paddle_ocr"])
        self.assertTrue(pipeline.component_ready("paddle_ocr"))

    async def test_concurrent_first_paddle_requests_share_one_engine_and_one_load(self):
        from manga_backend.pipeline import components as components_module

        created = []

        class SlowEngine:
            def __init__(self, lang, device):
                self.loads = 0
                self.lock = asyncio.Lock()
                self.ready = False
                created.append(self)

            async def initialize(self):
                async with self.lock:
                    if self.ready:
                        return
                    await asyncio.sleep(0.02)
                    self.loads += 1
                    self.ready = True

            async def recognize_regions(self, image, regions):
                return ["x"] * len(regions)

            async def detect_and_recognize(self, image):
                return [{"text": "full"}]

        fake_module = types.ModuleType("manga_backend.models.paddle_ocr_onnx")
        fake_module.PaddleOCROnnxEngine = SlowEngine
        pipeline = TranslationPipeline(device="cpu")
        pipeline.is_initialized = True
        image = np.zeros((12, 24, 3), dtype=np.uint8)

        with patch.dict(components_module._paddle_ocr_engines, {}, clear=True), patch.dict(
            sys.modules, {"manga_backend.models.paddle_ocr_onnx": fake_module}
        ):
            results = await asyncio.gather(
                pipeline.ocr(image, [{"bbox": [1, 2, 10, 11]}], ocr="paddle-ocr"),
                pipeline.ocr(image, [{"bbox": [1, 2, 10, 11]}], ocr="paddle-ocr"),
                pipeline.paddle_ocr_full(image),
            )

        self.assertEqual(results, [["x"], ["x"], [{"text": "full"}]])
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].loads, 1)
        self.assertEqual(pipeline.model_status["paddle_ocr"]["status"], "loaded")

    async def test_translation_passes_contour_safe_mask_to_inpainter(self):
        # High noise keeps every fill tier honest, so the block reaches the
        # model and the handed-over mask can be compared.
        rng = np.random.default_rng(4)
        image = rng.integers(128, 255, (60, 100, 3)).astype(np.uint8)
        image[22:30, 25:45] = 0
        block = TextBlock([15, 15, 55, 40])
        pipeline, inpainter = _pipeline_with_fakes([block])

        with patch("manga_backend.pipeline.orchestrator.render_text", return_value=image):
            await pipeline.translate(
                image,
                translator="none",
                inpainter="test",
                render=True,
            )

        np.testing.assert_array_equal(
            inpainter.mask,
            create_mask(image, [block]),
        )

    async def test_translation_uses_rectangular_mask_for_animetext_yolo(self):
        rng = np.random.default_rng(5)
        image = rng.integers(128, 255, (60, 100, 3)).astype(np.uint8)
        image[22:30, 25:45] = 0
        block = TextBlock([15, 15, 55, 40])
        pipeline, inpainter = _pipeline_with_fakes([block], detector_key="animetext-yolo")

        with patch("manga_backend.pipeline.orchestrator.render_text", return_value=image):
            await pipeline.translate(
                image,
                detector="anime-text-yolo",
                translator="none",
                inpainter="test",
                render=True,
            )

        np.testing.assert_array_equal(
            inpainter.mask,
            create_mask(image, [block], rectangular=True),
        )

    async def test_inpainter_changes_outside_the_mask_union_are_discarded(self):
        pipeline = TranslationPipeline(device="cpu")
        rng = np.random.default_rng(6)
        image = rng.integers(128, 255, (60, 100, 3)).astype(np.uint8)
        image[22:30, 25:45] = 0
        block = TextBlock([15, 15, 55, 40])

        class LeakyInpainter:
            last_backend_used = "test"

            async def inpaint(self, source, mask):
                self.mask = mask
                return np.full_like(source, 9)

        inpainter = LeakyInpainter()
        pipeline._detectors.default = StaticDetector([block])
        pipeline._ocr = FakeOcrEngine()
        pipeline._inpainters.instance = inpainter
        pipeline._inpainters.type = "test"
        pipeline.model_status["inpainter"] = {"status": "loaded", "name": "test"}

        with patch(
            "manga_backend.pipeline.orchestrator.render_text", side_effect=lambda i, b, **o: i
        ):
            result = await pipeline.translate(
                image,
                translator="none",
                inpainter="test",
                render=True,
            )

        rendered = result["image"]
        mask_union = inpainter.mask > 0
        np.testing.assert_array_equal(rendered[~mask_union], image[~mask_union])
        self.assertTrue(np.all(rendered[mask_union] == 9))

    async def test_flux_route_composite_preserves_the_white_fill(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((60, 200, 3), 255, dtype=np.uint8)
        # High noise keeps the right block out of every fill tier, so the
        # model mask is non-empty and the model runs.
        rng = np.random.default_rng(7)
        image[:, 100:, :] = rng.integers(128, 255, (60, 100, 3)).astype(np.uint8)
        image[22:30, 25:45] = 0
        image[22:30, 125:145] = 0
        white_block = TextBlock([15, 15, 55, 40])
        textured_block = TextBlock([115, 15, 155, 40])

        class PassthroughInpainter:
            last_backend_used = "flux2_klein_lanpaint"

            async def initialize(self):
                return None

            async def inpaint(self, source, mask):
                self.source = source
                self.mask = mask
                return source.copy()

        inpainter = PassthroughInpainter()
        pipeline._detectors.default = StaticDetector([white_block, textured_block])
        pipeline._ocr = FakeOcrEngine()
        pipeline._inpainters.instance = None
        pipeline._inpainters.type = "flux2_klein_lanpaint"
        pipeline.model_status["inpainter"] = {"status": "not_loaded", "name": "flux2_klein_lanpaint"}

        def create(_inpainter_type, _device):
            return inpainter

        with patch("manga_backend.pipeline.inpainting.create_inpainter", create), patch(
            "manga_backend.pipeline.orchestrator.render_text", side_effect=lambda i, b, **o: i
        ):
            result = await pipeline.translate(
                image,
                translator="none",
                inpainter="flux2_klein_lanpaint",
                render=True,
            )

        rendered = result["image"]
        white_glyphs = np.zeros(image.shape[:2], dtype=bool)
        white_glyphs[22:30, 25:45] = True
        # The fill router repainted the glyphs before the model ran, and the
        # composite keeps that fill inside the mask union.
        self.assertTrue(np.all(inpainter.source[white_glyphs] == 255))
        self.assertTrue(np.all(rendered[white_glyphs] == 255))
        mask_union = (inpainter.mask > 0) | np.any(inpainter.source != image, axis=2)
        np.testing.assert_array_equal(rendered[~mask_union], image[~mask_union])

    async def test_translate_closes_translator_client_after_success_and_failure(self):
        for error in (None, RuntimeError("provider down")):
            pipeline = TranslationPipeline(device="cpu")
            pipeline._detectors.default = types.SimpleNamespace(
                detect=AsyncMock(return_value=[TextBlock(xyxy=[1, 1, 8, 8])])
            )
            pipeline._ocr = types.SimpleNamespace(process=AsyncMock(return_value=["テキスト"]))
            provider = types.SimpleNamespace(
                initialize=AsyncMock(),
                translate=AsyncMock(return_value=["Text"], side_effect=error),
                close=AsyncMock(),
            )

            with patch("manga_backend.pipeline.translation.Translator", return_value=provider):
                if error:
                    with self.assertRaises(RuntimeError):
                        await pipeline.translate(
                            np.zeros((12, 12, 3), dtype=np.uint8), translator="openai", render=False
                        )
                else:
                    result = await pipeline.translate(
                        np.zeros((12, 12, 3), dtype=np.uint8), translator="openai", render=False
                    )
                    self.assertEqual(result["translated_texts"], ["Text"])

            provider.close.assert_awaited_once()

    async def test_inpainter_initialization_is_serialized_and_publishes_ready_instances(
        self,
    ):
        pipeline = TranslationPipeline(device="cpu")
        created = []

        class FakeInpainter:
            def __init__(self, inpainter_type, device):
                self.inpainter_type = inpainter_type
                self.device = device
                self.initialized = False
                self.disposed = False
                created.append(self)

            async def initialize(self):
                await __import__("asyncio").sleep(0)
                self.initialized = True

            def dispose(self):
                self.disposed = True

        with patch("manga_backend.pipeline.inpainting.Inpainter", FakeInpainter):
            first, second = await __import__("asyncio").gather(
                pipeline._inpainters.get("lama"),
                pipeline._inpainters.get("lama_large"),
            )

        self.assertEqual(
            [first.inpainter_type, second.inpainter_type], ["lama", "lama_large"]
        )
        self.assertTrue(all(instance.initialized for instance in created))
        self.assertIs(pipeline._inpainters.instance, second)
        self.assertEqual(pipeline._inpainters.type, "lama_large")
        self.assertTrue(first.disposed)
        self.assertFalse(second.disposed)

    async def test_concurrent_same_type_inpainter_initialization_is_deduplicated(self):
        pipeline = TranslationPipeline(device="cpu")
        created = []

        class FakeInpainter:
            def __init__(self, inpainter_type, device):
                self.inpainter_type = inpainter_type
                self.device = device
                created.append(self)

            async def initialize(self):
                await __import__("asyncio").sleep(0)

        with patch("manga_backend.pipeline.inpainting.Inpainter", FakeInpainter):
            first, second = await __import__("asyncio").gather(
                pipeline._inpainters.get("lama_large"),
                pipeline._inpainters.get("lama_large"),
            )

        self.assertEqual(len(created), 1)
        self.assertIs(first, second)
        self.assertIs(pipeline._inpainters.instance, first)

    async def test_inpainter_initialization_error_is_reported_without_publishing(self):
        pipeline = TranslationPipeline(device="cpu")

        class BrokenInpainter:
            def __init__(self, inpainter_type, device):
                self.disposed = False
                pipeline.broken_candidate = self

            async def initialize(self):
                raise RuntimeError("model load failed")

            def dispose(self):
                self.disposed = True

        with patch("manga_backend.pipeline.inpainting.Inpainter", BrokenInpainter):
            with self.assertRaisesRegex(RuntimeError, "model load failed"):
                await pipeline._inpainters.get("lama")

        self.assertIsNone(pipeline._inpainters.instance)
        self.assertTrue(pipeline.broken_candidate.disposed)
        self.assertEqual(pipeline.model_status["inpainter"]["status"], "error")
        self.assertEqual(pipeline.model_status["inpainter"]["name"], "lama")
        self.assertEqual(
            pipeline.model_status["inpainter"]["error"], "model load failed"
        )

    async def test_readiness_does_not_require_optional_inpainter(self):
        pipeline = TranslationPipeline(device="cpu")
        pipeline.is_initialized = True
        pipeline.model_status["detector"]["status"] = "loaded"
        pipeline.model_status["ocr"]["status"] = "loaded"
        pipeline.model_status["inpainter"]["status"] = "error"

        self.assertTrue(pipeline.is_ready)

        pipeline.model_status["inpainter"]["status"] = "loaded"
        self.assertTrue(pipeline.is_ready)

    async def test_failed_inpainter_replacement_preserves_active_model_and_readiness(
        self,
    ):
        pipeline = TranslationPipeline(device="cpu")
        active = object()
        pipeline._inpainters.instance = active
        pipeline._inpainters.type = "lama_large"
        pipeline.is_initialized = True
        pipeline.model_status["detector"]["status"] = "loaded"
        pipeline.model_status["ocr"]["status"] = "loaded"
        pipeline.model_status["inpainter"] = {"status": "loaded", "name": "lama_large"}

        class BrokenInpainter:
            def __init__(self, inpainter_type, device):
                self.inpainter_type = inpainter_type

            async def initialize(self):
                raise RuntimeError("replacement failed")

        with patch("manga_backend.pipeline.inpainting.Inpainter", BrokenInpainter):
            with self.assertRaisesRegex(RuntimeError, "replacement failed"):
                await pipeline._inpainters.get("lama")

        self.assertIs(pipeline._inpainters.instance, active)
        self.assertEqual(pipeline._inpainters.type, "lama_large")
        self.assertTrue(pipeline.is_ready)
        self.assertEqual(
            pipeline.model_status["inpainter"]["last_load_error"],
            {"name": "lama", "error": "replacement failed"},
        )

        restored = await pipeline._inpainters.get("lama_large")
        self.assertIs(restored, active)
        self.assertTrue(pipeline.is_ready)


class PipelineRenderTests(unittest.TestCase):
    def test_block_serialization_exports_optional_bubble_geometry(self):
        block = TextBlock([20, 30, 40, 60], block_type="text")
        block.bubble_xyxy = [10, 15, 70, 90]
        block.bubble_text_center = [38.5, 51.25]

        payload = text_block_to_dict(block)

        self.assertEqual(payload["bbox"], [20, 30, 40, 60])
        self.assertEqual(payload["bubble_bbox"], [10, 15, 70, 90])
        self.assertEqual(payload["bubble_center"], [38.5, 51.25])

    def test_auto_direction_keeps_translated_latin_text_horizontal(self):
        TranslationPipeline(device="cpu")
        image = np.full((120, 80, 3), 255, dtype=np.uint8)
        block = TextBlock([20, 5, 45, 115], translated_text="TRAIN ME")

        def fail_vertical(*_args, **_kwargs):
            raise AssertionError(
                "auto direction should not stack translated Latin text vertically"
            )

        with patch("manga_backend.pipeline.rendering.draw_vertical_text", fail_vertical):
            rendered = render_text(
                image.copy(),
                [block],
                text_direction="auto",
                bg_opacity=0,
            )

        self.assertFalse(np.array_equal(rendered, image))

    def test_render_text_honors_vertical_direction(self):
        TranslationPipeline(device="cpu")
        image = np.full((90, 90, 3), 255, dtype=np.uint8)
        block = TextBlock([25, 5, 65, 85], translated_text="ABCD")

        horizontal = render_text(
            image.copy(),
            [block],
            text_direction="horizontal",
            bg_opacity=0,
            auto_scale=False,
        )
        vertical = render_text(
            image.copy(),
            [block],
            text_direction="vertical",
            bg_opacity=0,
            auto_scale=False,
        )

        self.assertFalse(np.array_equal(horizontal, vertical))
        self.assertFalse(np.array_equal(vertical, image))

    def test_line_breaking_keeps_words_that_fit_intact(self):
        TranslationPipeline(device="cpu")

        broken = break_lines(
            ["A", "SENSE", "OF", "FAMILIARITY"],
            [140.0],
            lambda value: float(len(value) * 5),
            joiner=lambda parts: " ".join(parts),
        )

        self.assertIsNotNone(broken)
        assert broken is not None
        self.assertEqual(broken.lines, ["A SENSE OF FAMILIARITY"])

    def test_line_breaking_splits_only_oversized_tokens(self):
        TranslationPipeline(device="cpu")

        broken = break_lines(
            ["UNBREAKABLYLONGWORD"],
            [80.0, 80.0, 80.0],
            lambda value: float(len(value) * 10),
            joiner=lambda parts: " ".join(parts),
        )

        self.assertIsNotNone(broken)
        assert broken is not None
        self.assertGreater(len(broken.lines), 1)
        for line in broken.lines:
            self.assertLessEqual(len(line) * 10, 80)

    def test_line_breaking_can_break_at_em_dash_without_adding_space(self):
        TranslationPipeline(device="cpu")

        broken = break_lines(
            ["NICE—", "LIVING"],
            [50.0, 50.0],
            lambda value: float(len(value) * 5),
            joiner=lambda parts: "".join(parts),
        )

        self.assertIsNotNone(broken)
        assert broken is not None
        self.assertEqual(broken.lines, ["NICE—", "LIVING"])

    def test_rtdetr_blocks_sharing_a_bubble_render_as_one_region(self):
        TranslationPipeline(device="cpu")
        first = TextBlock([40, 20, 60, 50], text="A", translated_text="FIRST")
        second = TextBlock([42, 55, 62, 80], text="B", translated_text="SECOND")
        first.bubble_xyxy = [30, 10, 90, 100]
        second.bubble_xyxy = [30, 10, 90, 100]

        regions = build_render_regions([second, first], (120, 120, 3))

        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].translated_text, "FIRST SECOND")
        self.assertEqual(first.xyxy.tolist(), [40, 20, 60, 50])
        self.assertEqual(second.xyxy.tolist(), [42, 55, 62, 80])

    def test_free_space_rooms_do_not_overlap_and_bound_the_drawing(self):
        TranslationPipeline(device="cpu")
        image = np.full((80, 140, 3), 255, dtype=np.uint8)
        left = TextBlock([5, 10, 40, 70], translated_text="UNBREAKABLYLONGWORD")
        right = TextBlock([100, 10, 135, 70], translated_text="ANOTHERLONGWORD")

        rendered = render_text(
            image.copy(),
            [left, right],
            text_direction="horizontal",
            bg_opacity=0,
            auto_scale=False,
        )

        rooms = build_rooms(image, [left, right], [left, right])
        allowed = np.zeros(image.shape[:2], dtype=bool)
        for room in rooms:
            x1, y1, x2, y2 = room.bounds
            allowed[y1:y2, x1:x2] |= room.mask > 0
        left_room, right_room = rooms[0], rooms[1]
        overlap = np.zeros(image.shape[:2], dtype=np.uint8)
        ox1, oy1, ox2, oy2 = left_room.bounds
        overlap[oy1:oy2, ox1:ox2] = left_room.mask > 0
        rx1, ry1, rx2, ry2 = right_room.bounds
        self.assertFalse((overlap[ry1:ry2, rx1:rx2] > 0).any() and (right_room.mask > 0).any())

        changed = np.any(rendered != image, axis=2)
        self.assertTrue(changed.any())
        self.assertFalse((changed & ~allowed).any())

    def test_tiny_short_label_expands_without_overlapping_neighbor(self):
        TranslationPipeline(device="cpu")
        short = TextBlock([120, 691, 142, 724], translated_text="Hey")
        short.render_from_bubble = False
        neighbor = TextBlock([72, 724, 134, 841], translated_text="Hurry up!")
        neighbor.render_from_bubble = True

        expanded = expand_short_standalone_regions(
            [short, neighbor], (902, 1240, 3)
        )

        self.assertEqual(expanded, 1)
        self.assertEqual(short.xyxy.tolist(), [120, 691, 150, 724])
        self.assertEqual(short.y2, neighbor.y1)

    def test_small_bubble_text_is_set_inside_its_room(self):
        TranslationPipeline(device="cpu")
        image = np.full((120, 120, 3), 255, dtype=np.uint8)
        cv2.ellipse(image, (60, 60), (20, 40), 0, 0, 360, (0, 0, 0), 2)
        block = TextBlock(
            [45, 35, 75, 85],
            text="COSMETICS",
            translated_text="COSMETICS?",
        )
        block.bubble_xyxy = [40, 20, 80, 100]

        rendered = render_text(
            image,
            [block],
            bg_opacity=0,
            text_direction="horizontal",
            preferred_font_size=16,
        )

        changed = np.any(rendered != image, axis=2)
        _ys, xs = np.where(changed)
        self.assertTrue(len(xs))
        # The room is the bubble interior; the old overflow rescue grew the
        # box past the outline instead.
        self.assertGreaterEqual(int(xs.min()), 40)
        self.assertLessEqual(int(xs.max()), 79)

    def test_free_text_stays_inside_its_free_space_room(self):
        TranslationPipeline(device="cpu")
        image = np.full((90, 60, 3), 255, dtype=np.uint8)
        block = TextBlock(
            [10, 10, 45, 80], translated_text="UNBREAKABLYLONGWORD"
        )

        rendered = render_text(
            image.copy(),
            [block],
            text_direction="horizontal",
            bg_opacity=0,
            border_size=1,
        )

        rooms = build_rooms(image, [block], [block])
        room = rooms[0]
        x1, y1, x2, y2 = room.bounds
        allowed = np.zeros(image.shape[:2], dtype=bool)
        allowed[y1:y2, x1:x2] = room.mask > 0
        changed = np.any(rendered != image, axis=2)
        self.assertTrue(changed.any())
        self.assertFalse((changed & ~allowed).any())
        self.assertFalse(changed[0, :].any())
        self.assertFalse(changed[-1, :].any())

    def test_distinct_bubbles_are_not_merged(self):
        TranslationPipeline(device="cpu")
        first = TextBlock([10, 10, 30, 30], translated_text="ONE")
        second = TextBlock([20, 20, 40, 40], translated_text="TWO")
        first.bubble_xyxy = [5, 5, 45, 45]
        second.bubble_xyxy = [6, 5, 45, 45]

        regions = build_render_regions([first, second], (60, 60, 3))

        self.assertEqual(len(regions), 2)
        self.assertLessEqual(regions[0].x2, regions[1].x1)

    def test_yolo_exact_composite_is_suppressed_before_partitioning(self):
        TranslationPipeline(device="cpu")
        parent = TextBlock(
            [0, 0, 80, 120], text="WA MIKI", translated_text="Oh, it is Miki"
        )
        right = TextBlock([45, 0, 80, 30], text="WA", translated_text="Oh")
        left = TextBlock([0, 30, 40, 120], text="MIKI", translated_text="Miki")
        right.bubble_xyxy = [42, 0, 90, 40]

        regions = build_render_regions(
            [parent, left, right], (140, 100, 3)
        )

        self.assertEqual(len(regions), 2)
        self.assertNotIn("Oh, it is Miki", [region.translated_text for region in regions])

    def test_same_translation_sfx_inside_bubble_renders_once(self):
        TranslationPipeline(device="cpu")
        sfx = TextBlock(
            [19, 15, 55, 91], text="SAME", translated_text="Same translation"
        )
        text = TextBlock(
            [19, 15, 55, 91], text="SAME", translated_text="Same translation"
        )
        sfx.block_type = "sfx"
        text.bubble_xyxy = [12, 13, 60, 94]

        regions = build_render_regions([sfx, text], (110, 80, 3))

        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].translated_text, "Same translation")
        self.assertTrue(getattr(regions[0], "render_from_bubble", False))

    def test_bubble_group_deduplicates_identical_detector_blocks(self):
        TranslationPipeline(device="cpu")
        first = TextBlock([20, 20, 50, 80], text="SAME", translated_text="Once")
        duplicate = TextBlock(
            [20, 20, 50, 80], text="SAME", translated_text="Once"
        )
        first.bubble_xyxy = [10, 10, 60, 90]
        duplicate.bubble_xyxy = [10, 10, 60, 90]

        regions = build_render_regions([first, duplicate], (100, 100, 3))

        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].translated_text, "Once")

    def test_nonmatching_nested_regions_are_partitioned_not_suppressed(self):
        TranslationPipeline(device="cpu")
        parent = TextBlock([10, 10, 90, 90], text="WHOLE", translated_text="Whole")
        child = TextBlock([30, 30, 70, 70], text="PART", translated_text="Part")
        original_parent = parent.xyxy.copy()
        original_child = child.xyxy.copy()

        regions = build_render_regions([parent, child], (100, 100, 3))

        self.assertEqual(len(regions), 2)
        first, second = regions
        overlap_width = max(0, min(first.x2, second.x2) - max(first.x1, second.x1))
        overlap_height = max(0, min(first.y2, second.y2) - max(first.y1, second.y1))
        self.assertEqual(overlap_width * overlap_height, 0)
        np.testing.assert_array_equal(parent.xyxy, original_parent)
        np.testing.assert_array_equal(child.xyxy, original_child)

    def test_partition_discards_weaker_region_when_no_usable_split_exists(self):
        TranslationPipeline(device="cpu")
        stronger = TextBlock([10, 10, 30, 30], text="A", translated_text="Keep")
        weaker = TextBlock([10, 10, 30, 30], text="B", translated_text="Drop")
        stronger.confidence = 0.9
        weaker.confidence = 0.4

        regions = build_render_regions([weaker, stronger], (50, 50, 3))

        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].translated_text, "Keep")

    def test_render_partition_is_stable_when_input_order_is_reversed(self):
        TranslationPipeline(device="cpu")

        def build(reverse=False):
            blocks = [
                TextBlock([10, 10, 60, 70], text="A", translated_text="One"),
                TextBlock([50, 20, 90, 80], text="B", translated_text="Two"),
                TextBlock([40, 65, 80, 95], text="C", translated_text="Three"),
            ]
            if reverse:
                blocks.reverse()
            return build_render_regions(blocks, (100, 100, 3))

        forward = [(region.text, region.xyxy.tolist()) for region in build()]
        reversed_regions = [
            (region.text, region.xyxy.tolist()) for region in build(reverse=True)
        ]

        self.assertEqual(forward, reversed_regions)

    def test_explicit_background_colour_is_rendered_on_light_paper(self):
        TranslationPipeline(device="cpu")
        image = np.full((80, 120, 3), 200, dtype=np.uint8)
        block = TextBlock([10, 10, 110, 70], translated_text="HELLO")

        rendered = render_text(
            image.copy(),
            [block],
            text_direction="horizontal",
            bg_color="#ff0000",
            bg_opacity=1.0,
        )

        red, green, blue = (int(value) for value in rendered[12, 12])
        self.assertGreater(red, 200)
        self.assertLess(green, 60)
        self.assertLess(blue, 60)

    def test_fit_never_exceeds_its_anchored_target(self):
        TranslationPipeline(device="cpu")

        fit = fit_text_in_room(
            box_room(TextBlock([0, 0, 360, 120])), "THAT.", None, target=16
        )

        self.assertEqual(fit.size, 16)

    def test_fit_uses_the_target_when_it_fits(self):
        TranslationPipeline(device="cpu")

        fit = fit_text_in_room(
            box_room(TextBlock([0, 0, 240, 100])), "HELLO", None, target=16
        )

        self.assertEqual(fit.size, 16)

    def test_english_font_resolves_to_bundled_font_regardless_of_cwd(self):
        import os
        import tempfile

        from manga_backend.pipeline import fonts as fonts_module

        bundled = (BACKEND_DIR / "src" / "manga_backend" / "fonts" / "CCWildWords.ttf").resolve()
        self.assertTrue(bundled.is_file())

        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                os.chdir(temp_dir)
                with patch.dict(fonts_module._font_cache, clear=True):
                    font_path = fonts_module.get_cached_font("english")
            finally:
                os.chdir(original_cwd)

        self.assertIsNotNone(font_path)
        self.assertEqual(Path(font_path).resolve(), bundled)

    def test_fit_reduces_from_the_target_to_the_largest_size_that_fits(self):
        from manga_backend.pipeline import fonts as fonts_module

        TranslationPipeline(device="cpu")
        font_path = fonts_module.get_cached_font("english")

        fit = fit_text_in_room(
            box_room(TextBlock([0, 0, 70, 48])),
            "A translation that needs several wrapped lines",
            font_path,
            target=16,
            base_stroke_width=1,
        )

        self.assertGreaterEqual(fit.size, 4)
        self.assertLess(fit.size, 16)

    def test_fit_hyphenates_a_word_that_cannot_fit_whole(self):
        from manga_backend.pipeline import fonts as fonts_module

        TranslationPipeline(device="cpu")
        font_path = fonts_module.get_cached_font("english")

        fit = fit_text_in_room(
            box_room(TextBlock([0, 0, 56, 120])),
            "Hurry up!",
            font_path,
            target=16,
            base_stroke_width=2,
        )

        # The size is kept and the word is hyphenated, rather than shrinking
        # the whole region to keep the word whole.
        self.assertEqual(fit.size, 16)
        joined = "".join(fit.lines).replace("-", "")
        self.assertEqual(joined, "Hurryup!")
        self.assertTrue(any(line.endswith("-") for line in fit.lines))

    def test_fit_shrinks_a_short_word_instead_of_splitting_it(self):
        from manga_backend.pipeline import fonts as fonts_module

        TranslationPipeline(device="cpu")
        font_path = fonts_module.get_cached_font("english")

        for text in ("Whoa!", "Ah!", "*Pant, pant*"):
            with self.subTest(text=text):
                fit = fit_text_in_room(
                    box_room(TextBlock([0, 0, 48, 120])),
                    text,
                    font_path,
                    target=16,
                    base_stroke_width=2,
                )
                # "W-ho-a!" / "*P-ant," read as typos: the word shrinks whole.
                self.assertFalse(fit.overflow)
                self.assertGreaterEqual(fit.size, 10)
                self.assertFalse(fit.hyphenated)
                self.assertEqual(" ".join(fit.lines), text)

    def test_fit_keeps_a_short_word_whole_below_the_readable_size(self):
        from manga_backend.pipeline import fonts as fonts_module

        TranslationPipeline(device="cpu")
        font_path = fonts_module.get_cached_font("english")

        # A narrow lobe of a cloud balloon: whole, the word only fits under
        # the readable size, and cut anywhere it became "T-/h-/a-/t." at 16.
        for text in ("That.", "Whoa!", "Come.", "*Pant, pant*"):
            with self.subTest(text=text):
                fit = fit_text_in_room(
                    box_room(TextBlock([0, 0, 40, 140])),
                    text,
                    font_path,
                    target=16,
                    base_stroke_width=2,
                )
                self.assertFalse(fit.overflow)
                self.assertFalse(fit.hyphenated)
                self.assertEqual(" ".join(fit.lines), text)
                self.assertGreaterEqual(fit.size, MIN_WHOLE_WORD_FONT_SIZE)
                self.assertLess(fit.size, MIN_READABLE_FONT_SIZE)

        # Too narrow to set it whole at a legible size: cut, not unreadable.
        fit = fit_text_in_room(
            box_room(TextBlock([0, 0, 24, 140])), "That.", font_path, target=16, base_stroke_width=2
        )
        self.assertFalse(fit.overflow)
        self.assertTrue(fit.hyphenated)
        self.assertGreaterEqual(fit.size, MIN_WHOLE_WORD_FONT_SIZE)

    def test_fit_scales_the_stroke_and_preserves_words(self):
        from manga_backend.pipeline import fonts as fonts_module

        TranslationPipeline(device="cpu")
        font_path = fonts_module.get_cached_font("english")

        fit = fit_text_in_room(
            box_room(TextBlock([0, 0, 34, 57])),
            "Must be nice—living so close.",
            font_path,
            target=16,
            base_stroke_width=2,
        )

        self.assertGreater(fit.size, 0)
        self.assertLessEqual(fit.stroke_width, 2)
        self.assertLessEqual(fit.stroke_width, max(0, round(fit.size * 0.15)))
        joined = " ".join(fit.lines)
        self.assertIn("nice—", joined)
        # A dash break carries no space: the next line starts with the word.
        for first, second in zip(fit.lines, fit.lines[1:]):
            if first.endswith("—"):
                self.assertFalse(second.startswith(" "))

    def test_impossible_region_reports_an_overflow_instead_of_dropping(self):
        TranslationPipeline(device="cpu")

        fit = fit_text_in_room(
            box_room(TextBlock([0, 0, 3, 3])),
            "TOO LARGE",
            None,
            target=16,
            base_stroke_width=2,
        )

        self.assertGreater(fit.size, 0)
        self.assertTrue(fit.overflow)

    def test_fit_still_letters_a_narrow_region(self):
        from manga_backend.pipeline import fonts as fonts_module

        TranslationPipeline(device="cpu")
        font_path = fonts_module.get_cached_font("english")

        fit = fit_text_in_room(
            box_room(TextBlock([0, 0, 20, 33])),
            "My husband's an engineer. Not much to look at, though.",
            font_path,
            target=16,
            base_stroke_width=2,
        )

        self.assertGreater(fit.size, 0)
        self.assertLess(fit.size, 6)

    def test_create_mask_follows_rtdetr_bubble_contour_not_its_rectangle(self):
        TranslationPipeline(device="cpu")
        image = np.full((140, 180, 3), 150, dtype=np.uint8)
        cv2.ellipse(image, (90, 70), (55, 45), 0, 0, 360, (255, 255, 255), -1)
        cv2.ellipse(image, (90, 70), (55, 45), 0, 0, 360, (0, 0, 0), 2)
        block = TextBlock([72, 58, 108, 72], translated_text="TEXT")
        block.bubble_xyxy = [33, 23, 147, 117]

        with patch(
            "manga_backend.pipeline.masks.glyph_mask_for_block",
            return_value=(np.zeros((1, 1), dtype=np.uint8), (0, 0, 1, 1)),
        ):
            mask = create_mask(image, [block])

        self.assertEqual(mask[70, 90], 255)
        self.assertEqual(mask[25, 35], 0)
        self.assertEqual(mask[25, 90], 0)
        self.assertEqual(mask[23, 33], 0)

    def test_yolo_mask_is_exact_clipped_detection_rectangle(self):
        TranslationPipeline(device="cpu")
        image = np.zeros((30, 40, 3), dtype=np.uint8)
        block = TextBlock([0, 5, 10, 20])

        mask = create_mask(image, [block], rectangular=True)

        self.assertTrue(np.all(mask[5:20, 0:10] == 255))
        self.assertFalse(np.any(mask[:5]))
        self.assertFalse(np.any(mask[20:]))
        self.assertFalse(np.any(mask[5:20, 10:]))

    def test_animetext_aliases_normalize_to_yolo_rectangle_route(self):
        TranslationPipeline(device="cpu")

        for detector in ("animetext", "animetext_yolo", "anime-text-yolo"):
            self.assertEqual(
                normalize_detector_type(detector),
                "animetext-yolo",
            )

    def test_yolo_block_finds_bubble_contour_without_detector_bubble_box(self):
        TranslationPipeline(device="cpu")
        image = np.full((140, 180, 3), 150, dtype=np.uint8)
        cv2.ellipse(image, (90, 70), (55, 45), 0, 0, 360, (255, 255, 255), -1)
        cv2.ellipse(image, (90, 70), (55, 45), 0, 0, 360, (0, 0, 0), 2)
        block = TextBlock([72, 58, 108, 72], translated_text="TEXT")

        interior, (x1, y1, _x2, _y2) = (
            bubble_interior_mask_for_block(image, block)
        )

        self.assertEqual(interior[70 - y1, 90 - x1], 255)
        self.assertEqual(interior[25 - y1, 35 - x1], 0)

    def test_bubble_contour_ignores_text_connected_near_outline(self):
        TranslationPipeline(device="cpu")
        image = np.full((150, 190, 3), 145, dtype=np.uint8)
        cv2.ellipse(image, (95, 75), (58, 52), 0, 0, 360, (255, 255, 255), -1)
        cv2.ellipse(image, (95, 75), (58, 52), 0, 0, 360, (0, 0, 0), 2)
        image[48:82, 133:139] = 0
        block = TextBlock([82, 42, 140, 100], translated_text="TEXT")

        interior, (x1, y1, _x2, _y2) = (
            bubble_interior_mask_for_block(image, block)
        )

        self.assertEqual(interior[60 - y1, 135 - x1], 255)
        self.assertEqual(interior[20 - y1, 25 - x1], 0)

    def test_yolo_dialogue_infers_bubble_bounds_for_rendering(self):
        TranslationPipeline(device="cpu")
        image = np.full((140, 180, 3), 150, dtype=np.uint8)
        cv2.ellipse(image, (90, 70), (55, 45), 0, 0, 360, (255, 255, 255), -1)
        cv2.ellipse(image, (90, 70), (55, 45), 0, 0, 360, (0, 0, 0), 2)
        dialogue = TextBlock([72, 58, 108, 72], translated_text="OH?")
        sfx = TextBlock([10, 10, 30, 30], block_type="sfx", translated_text="THUD")

        inferred = infer_bubble_render_bounds(image, [dialogue, sfx])

        self.assertEqual(inferred, 1)
        bx1, by1, bx2, by2 = dialogue.bubble_xyxy
        self.assertLess(bx1, 72)
        self.assertLess(by1, 58)
        self.assertGreater(bx2, 108)
        self.assertGreater(by2, 72)
        self.assertIsNone(getattr(sfx, "bubble_xyxy", None))

    def test_yolo_rejects_off_center_ui_aggregate_as_bubble(self):
        TranslationPipeline(device="cpu")
        image = np.full((120, 160, 3), 128, dtype=np.uint8)
        interior = np.zeros((120, 160), dtype=np.uint8)
        interior[10:110, 10:150] = 255
        block = TextBlock([55, 14, 105, 30], translated_text="MESSAGE")

        with patch(
            "manga_backend.pipeline.bubbles.bubble_interior_mask_for_block",
            return_value=(interior, (0, 0, 160, 120)),
        ):
            inferred = infer_bubble_render_bounds(image, [block])

        self.assertEqual(inferred, 0)
        self.assertIsNone(block.bubble_xyxy)

    def test_bubble_body_center_ignores_a_wide_speech_tail(self):
        interior = np.zeros((120, 160), dtype=np.uint8)
        cv2.ellipse(interior, (60, 60), (35, 48), 0, 0, 360, 255, -1)
        cv2.fillPoly(
            interior,
            [np.array([[88, 52], [135, 68], [88, 78]], dtype=np.int32)],
            255,
        )

        center = bubble_body_center(interior, (0, 0))

        self.assertAlmostEqual(center[0], 60.5, delta=1.0)
        self.assertAlmostEqual(center[1], 60.5, delta=1.0)
        self.assertLess(center[0], 70)

    def test_all_render_paths_center_on_bubble_body_not_tail_bounds(self):
        TranslationPipeline(device="cpu")
        image = np.full((120, 160, 3), 255, dtype=np.uint8)
        interior = np.zeros((120, 160), dtype=np.uint8)
        cv2.ellipse(interior, (60, 60), (35, 48), 0, 0, 360, 255, -1)
        cv2.fillPoly(
            interior,
            [np.array([[88, 52], [135, 68], [88, 78]], dtype=np.int32)],
            255,
        )
        block = TextBlock([48, 45, 72, 75], translated_text="CENTER")
        block.bubble_xyxy = [25, 12, 136, 109]

        with patch(
            "manga_backend.pipeline.bubbles.bubble_interior_mask_for_block",
            return_value=(interior, (0, 0, 160, 120)),
        ):
            rendered = render_text(
                image,
                [block],
                bg_opacity=0,
                text_direction="horizontal",
                preferred_font_size=16,
            )

        self.assertAlmostEqual(block.bubble_text_center[0], 68.5, delta=1.0)
        changed = np.any(rendered != image, axis=2)
        _ys, xs = np.where(changed)
        self.assertTrue(len(xs))
        self.assertLess(float(np.median(xs)), 75)

    def test_yolo_composite_children_receive_separate_padded_regions(self):
        TranslationPipeline(device="cpu")
        image = np.full((140, 140, 3), 128, dtype=np.uint8)
        parent = TextBlock([40, 20, 110, 120], text="RIGHTLEFT")
        right = TextBlock(
            [78, 28, 106, 72], text="RIGHT", translated_text="Look"
        )
        left = TextBlock(
            [46, 55, 74, 105], text="LEFT", translated_text="Cosmetics"
        )

        infer_bubble_render_bounds(image, [parent, left, right])

        self.assertEqual(right.bubble_xyxy, [80, 24, 112, 76])
        self.assertEqual(left.bubble_xyxy, [40, 50, 80, 110])
        self.assertEqual(
            right.composite_render_group, left.composite_render_group
        )

        regions = build_render_regions(
            [parent, left, right], image.shape
        )
        self.assertEqual([region.xyxy.tolist() for region in regions], [
            [80, 24, 112, 76],
            [40, 50, 80, 110],
        ])

    def test_primary_glyph_mask_is_clipped_inside_bubble_outline(self):
        TranslationPipeline(device="cpu")
        image = np.full((140, 180, 3), 150, dtype=np.uint8)
        cv2.ellipse(image, (90, 70), (55, 45), 0, 0, 360, (255, 255, 255), -1)
        cv2.ellipse(image, (90, 70), (55, 45), 0, 0, 360, (0, 0, 0), 2)
        image[62:68, 75:105] = 0
        block = TextBlock([60, 22, 120, 92], translated_text="TEXT")
        block.bubble_xyxy = [33, 23, 147, 117]

        mask, (x1, y1, _x2, _y2), strategy = (
            contour_safe_mask_for_block(image, block)
        )

        self.assertEqual(strategy, "bubble-clipped-glyph")
        self.assertEqual(mask[62 - y1, 90 - x1], 255)
        self.assertEqual(mask[25 - y1, 90 - x1], 0)

    def test_bubble_mask_keeps_compact_glyph_touching_detector_edge(self):
        TranslationPipeline(device="cpu")
        image = np.full((100, 130, 3), 150, dtype=np.uint8)
        cv2.ellipse(image, (65, 50), (45, 38), 0, 0, 360, (255, 255, 255), -1)
        cv2.ellipse(image, (65, 50), (45, 38), 0, 0, 360, (0, 0, 0), 2)
        image[42:50, 88:94] = 0
        block = TextBlock([30, 25, 92, 70], translated_text="TEXT")
        interior = np.zeros((100, 130), dtype=np.uint8)
        cv2.ellipse(interior, (65, 50), (42, 35), 0, 0, 360, 255, -1)

        with patch(
            "manga_backend.pipeline.masks.bubble_interior_mask_for_block",
            return_value=(interior, (0, 0, 130, 100)),
        ):
            mask, (x1, y1, _x2, _y2), strategy = (
                contour_safe_mask_for_block(image, block)
        )

        self.assertEqual(strategy, "bubble-clipped-glyph")
        self.assertEqual(mask[46 - y1, 90 - x1], 255)

    def test_flux_hybrid_direct_fills_only_dark_glyphs_on_white(self):
        TranslationPipeline(device="cpu")
        image = np.full((60, 100, 3), 255, dtype=np.uint8)
        image[25:35, 35:65] = 0
        block = TextBlock([33, 23, 67, 37], translated_text="TEXT")

        cleaned, flux_mask, filled_count, model_count, decisions = (
            prepare_flux_inpainting(image, [block])
        )

        self.assertEqual((filled_count, model_count), (1, 0))
        self.assertFalse(np.any(flux_mask))
        self.assertTrue(np.all(cleaned[25:35, 35:65] == 255))
        np.testing.assert_array_equal(cleaned[0:10, 0:10], image[0:10, 0:10])
        self.assertEqual(decisions[0]["tier"], 0)
        self.assertEqual(decisions[0]["tier_name"], "flat")

    def test_flux_rectangular_mode_flat_fills_the_detection_rect(self):
        TranslationPipeline(device="cpu")
        image = np.full((60, 100, 3), 255, dtype=np.uint8)
        image[25:35, 35:65] = 0
        block = TextBlock([30, 20, 70, 40], translated_text="TEXT")

        cleaned, flux_mask, filled_count, model_count, decisions = (
            prepare_flux_inpainting(image, [block], rectangular=True)
        )

        self.assertEqual((filled_count, model_count), (1, 0))
        self.assertFalse(np.any(flux_mask))
        self.assertTrue(np.all(cleaned[25:35, 35:65] == 255))
        np.testing.assert_array_equal(cleaned[0:20, :], image[0:20, :])
        self.assertEqual(decisions[0]["tier"], 0)

    def test_flux_direct_fills_ink_on_white_box_over_dark_background(self):
        # Light glyphs rule: the mask stays on the lettering, so the fill
        # samples the white box around it, not the dark page behind.
        TranslationPipeline(device="cpu")
        image = np.full((80, 120, 3), 40, dtype=np.uint8)
        image[20:60, 30:90] = 255
        image[32:48, 52:68] = 0
        block = TextBlock([30, 20, 90, 60], translated_text="TEXT")

        cleaned, flux_mask, filled_count, model_count, decisions = (
            prepare_flux_inpainting(image, [block])
        )

        self.assertEqual((filled_count, model_count), (1, 0))
        self.assertFalse(np.any(flux_mask))
        self.assertTrue(np.all(cleaned[32:48, 52:68] == 255))
        np.testing.assert_array_equal(cleaned[0:18, :], image[0:18, :])
        self.assertEqual(decisions[0]["tier"], 0)

    def test_flux_keeps_structure_inside_detection_box(self):
        TranslationPipeline(device="cpu")
        image = np.full((80, 120, 3), 255, dtype=np.uint8)
        image[30:36, 40:50] = 0
        image[24:28, 76:84] = 180
        block = TextBlock([30, 20, 90, 60], translated_text="TEXT")
        cleaned, flux_mask, filled_count, model_count, decisions = (
            prepare_flux_inpainting(image, [block])
        )

        self.assertEqual((filled_count, model_count), (1, 0))
        self.assertFalse(np.any(flux_mask))
        self.assertTrue(np.all(cleaned[30:36, 40:50] == 255))
        self.assertTrue(np.any(cleaned[24:28, 76:84] == 180))
        self.assertEqual(decisions[0]["tier"], 0)

    def test_flux_rectangular_mode_lattice_fills_periodic_rect(self):
        # Screentone passes the old white test (H2); the router now fills it
        # with the lattice tier instead of painting it flat or calling a model.
        # The tone extends well past the rectangle, like a real toned panel.
        ys, xs = np.mgrid[4:76, 4:116]
        dots = ((ys - 4) % 7 - 3) ** 2 + ((xs - 4) % 7 - 3) ** 2 <= 2.56
        clean = np.full((80, 120, 3), 255, dtype=np.uint8)
        clean[4:76, 4:116][dots] = 110
        image = clean.copy()
        image[32:48, 52:68] = 0
        block = TextBlock([30, 20, 90, 60], translated_text="TEXT")

        cleaned, flux_mask, filled_count, model_count, decisions = (
            prepare_flux_inpainting(image, [block], rectangular=True)
        )

        self.assertEqual((filled_count, model_count), (1, 0))
        self.assertFalse(np.any(flux_mask))
        np.testing.assert_array_equal(cleaned, clean)
        self.assertEqual(decisions[0]["tier"], 2)

    def test_flux_hybrid_lattice_fills_screentone_not_model(self):
        TranslationPipeline(device="cpu")
        yy, xx = np.indices((60, 100))
        ys, xs = np.mgrid[5:55, 10:90]
        dots = ((ys - 5) % 7 - 3) ** 2 + ((xs - 10) % 7 - 3) ** 2 <= 2.56
        clean = np.full((60, 100, 3), 255, dtype=np.uint8)
        clean[5:55, 10:90][dots] = 110
        image = clean.copy()
        image[25:35, 35:65] = 0
        block = TextBlock([33, 23, 67, 37], translated_text="TEXT")

        cleaned, flux_mask, filled_count, model_count, decisions = (
            prepare_flux_inpainting(image, [block])
        )

        self.assertEqual((filled_count, model_count), (1, 0))
        self.assertFalse(np.any(flux_mask))
        np.testing.assert_array_equal(cleaned, clean)
        self.assertEqual(decisions[0]["tier"], 2)

    def test_flux_masks_light_glyphs_on_dark_ui(self):
        TranslationPipeline(device="cpu")
        image = np.full((60, 120, 3), 110, dtype=np.uint8)
        cv2.putText(
            image,
            "MSG",
            (32, 38),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (245, 245, 245),
            2,
            cv2.LINE_AA,
        )
        block = TextBlock([28, 17, 86, 43], translated_text="TEXT")

        _cleaned, flux_mask, filled_count, model_count, decisions = (
            prepare_flux_inpainting(image, [block])
        )

        self.assertEqual((filled_count, model_count), (1, 0))
        self.assertFalse(np.any(flux_mask))
        self.assertFalse(np.any(flux_mask[0:12, :]))
        self.assertEqual(decisions[0]["tier"], 0)

    def test_flux_rectangular_mode_preserves_bubble_outline_before_model(self):
        # The bubble outline crosses the detection rectangle, so no tier can
        # verify against the mixed ring; the block is queued for the model
        # and the pre-model image keeps the outline untouched.
        TranslationPipeline(device="cpu")
        image = np.full((100, 120, 3), 255, dtype=np.uint8)
        cv2.ellipse(image, (70, 50), (32, 40), 0, 0, 360, (0, 0, 0), 2)
        outline = np.all(image == 0, axis=2)
        image[35:65, 55:75] = 0
        block = TextBlock([35, 10, 78, 90], translated_text="TEXT")

        cleaned, flux_mask, filled_count, model_count, decisions = (
            prepare_flux_inpainting(image, [block], rectangular=True)
        )

        self.assertEqual((filled_count, model_count), (0, 1))
        self.assertTrue(np.any(flux_mask))
        self.assertEqual(decisions[0]["tier"], 4)
        self.assertFalse(np.any(np.all(cleaned[outline] == 255, axis=1)))

    def test_flux_hybrid_preserves_white_region_line_art(self):
        TranslationPipeline(device="cpu")
        image = np.full((60, 100, 3), 255, dtype=np.uint8)
        image[20, 20:80] = 0
        image[27:33, 42:58] = 0
        block = TextBlock([20, 18, 80, 35], translated_text="TEXT")

        cleaned, flux_mask, filled_count, model_count, decisions = (
            prepare_flux_inpainting(image, [block])
        )

        self.assertEqual((filled_count, model_count), (1, 0))
        self.assertFalse(np.any(flux_mask))
        self.assertTrue(np.all(cleaned[20, 20:80] == 0))
        self.assertTrue(np.all(cleaned[27:33, 42:58] == 255))
        self.assertEqual(decisions[0]["tier"], 0)

    def test_unchanged_punctuation_is_not_rendered_again(self):
        TranslationPipeline(device="cpu")
        block = TextBlock([10, 10, 30, 25], text="...", translated_text="...")

        regions = build_render_regions([block], (60, 80, 3))

        self.assertEqual(regions, [])

    def test_translated_punctuation_with_words_is_rendered(self):
        TranslationPipeline(device="cpu")
        block = TextBlock([10, 10, 40, 30], text="？", translated_text="WHAT?")

        regions = build_render_regions([block], (60, 80, 3))

        self.assertEqual(len(regions), 1)


class PipelineUnavailableHintTests(unittest.IsolatedAsyncioTestCase):
    async def get_inpainter_error(self, inpainter_type, module_name=None):
        pipeline = TranslationPipeline(device="cpu")

        class BrokenInpainter:
            def __init__(self, *args, **kwargs):
                pass

            async def initialize(self):
                raise RuntimeError("failed at C:\\private\\model.onnx")

        modules = {}
        if module_name:
            modules[module_name] = types.SimpleNamespace(
                FluxLanPaintInpainter=BrokenInpainter,
                FluxKleinInpainter=BrokenInpainter,
            )
        with patch.dict(sys.modules, modules), patch(
            "manga_backend.pipeline.inpainting.Inpainter", BrokenInpainter
        ):
            with self.assertRaises(InpainterUnavailableError) as raised:
                await pipeline._inpainters.get(inpainter_type)
        return raised.exception

    async def test_lanpaint_failure_hint_is_selected_by_requested_option(self):
        error = await self.get_inpainter_error(
            "flux2_klein_lanpaint", "manga_backend.models.flux_lanpaint_inpainter"
        )

        self.assertIn("start ComfyUI", error.public_detail)
        self.assertIn("choose LaMa", error.public_detail)
        self.assertNotIn("private", error.public_detail)
        self.assertIn("private", str(error))

    async def test_q4_failure_hint_names_the_runtime_settings(self):
        error = await self.get_inpainter_error(
            "flux2_klein_q4", "manga_backend.models.flux_klein_inpainter"
        )

        self.assertIn("SD_CPP_CLI / SD_CPP_MODELS_DIR", error.public_detail)

    async def test_lama_failure_keeps_the_generic_detail(self):
        error = await self.get_inpainter_error("lama_large")

        self.assertEqual(
            error.public_detail, "Requested inpainter runtime is unavailable"
        )

    async def test_animetext_failure_hint_is_path_free(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.zeros((12, 24, 3), dtype=np.uint8)

        with patch(
            "manga_backend.models.animetext_yolo_detector.AnimeTextYoloDetector",
            side_effect=ValueError("missing C:\\private\\animetext.pt"),
        ):
            with self.assertRaises(DetectorUnavailableError) as raised:
                await pipeline.detect(image, detector="animetext")

        self.assertIn("ANIMETEXT_YOLO_MODEL", raised.exception.public_detail)
        self.assertIn("Hugging Face", raised.exception.public_detail)
        self.assertNotIn("private", raised.exception.public_detail)

    def test_errors_without_an_option_keep_the_generic_detail(self):
        self.assertEqual(
            DetectorUnavailableError("x").public_detail,
            "Requested detector runtime is unavailable",
        )
        self.assertEqual(
            InpainterUnavailableError("x").public_detail,
            "Requested inpainter runtime is unavailable",
        )


class InpainterFallbackTests(unittest.IsolatedAsyncioTestCase):
    """LanPaint needs ComfyUI; without it a page still translates through LaMa."""

    def make_pipeline(self):
        pipeline = TranslationPipeline(device="cpu")
        attempts = []

        class LamaInpainter:
            async def initialize(self):
                pass

            async def inpaint(self, source, mask):
                return source.copy()

        async def get_inpainter(inpainter_type):
            attempts.append(inpainter_type)
            if inpainter_type == "flux2_klein_lanpaint":
                raise InpainterUnavailableError(
                    "ComfyUI is down", inpainter=inpainter_type
                )
            return LamaInpainter()

        pipeline._inpainters.get = get_inpainter
        return pipeline, attempts

    async def test_unavailable_lanpaint_falls_back_to_lama(self):
        pipeline, attempts = self.make_pipeline()

        _model, loaded = await pipeline._inpainters.get_or_fallback("flux2_klein_lanpaint")

        self.assertEqual(loaded, "lama_large")
        self.assertEqual(attempts, ["flux2_klein_lanpaint", "lama_large"])

    async def test_failed_runtime_is_not_probed_again_until_the_retry_time(self):
        pipeline, attempts = self.make_pipeline()

        await pipeline._inpainters.get_or_fallback("flux2_klein_lanpaint")
        await pipeline._inpainters.get_or_fallback("flux2_klein_lanpaint")
        self.assertEqual(attempts, ["flux2_klein_lanpaint", "lama_large", "lama_large"])

        pipeline._inpainters.retry_at["flux2_klein_lanpaint"] = 0.0
        await pipeline._inpainters.get_or_fallback("flux2_klein_lanpaint")
        self.assertEqual(attempts[-2:], ["flux2_klein_lanpaint", "lama_large"])

    async def test_fallback_can_be_switched_off(self):
        pipeline, attempts = self.make_pipeline()

        with patch.dict("os.environ", {"INPAINTER_FALLBACK": "off"}):
            with self.assertRaises(InpainterUnavailableError):
                await pipeline._inpainters.get_or_fallback("flux2_klein_lanpaint")

        self.assertEqual(attempts, ["flux2_klein_lanpaint"])

    async def test_inpainters_without_a_fallback_still_fail(self):
        pipeline = TranslationPipeline(device="cpu")

        async def get_inpainter(inpainter_type):
            raise InpainterUnavailableError("missing", inpainter=inpainter_type)

        pipeline._inpainters.get = get_inpainter

        for inpainter_type in ("lama_large", "flux2_klein_q4"):
            with self.assertRaises(InpainterUnavailableError):
                await pipeline._inpainters.get_or_fallback(inpainter_type)

    async def test_translate_reports_the_inpainter_that_actually_ran(self):
        pipeline, _attempts = self.make_pipeline()
        rng = np.random.default_rng(8)
        image = rng.integers(128, 255, (80, 120, 3)).astype(np.uint8)
        image[30:38, 40:70] = 0
        block = TextBlock([30, 22, 80, 46])

        pipeline._detectors.default = StaticDetector([block])
        pipeline._ocr = FakeOcrEngine()

        with patch("manga_backend.pipeline.orchestrator.render_text", return_value=image):
            result = await pipeline.translate(
                image,
                translator="none",
                inpainter="flux2_klein_lanpaint",
                render=True,
            )

        self.assertEqual(result["inpainter_used"], "lama_large")
        self.assertTrue(result["inpainting_fallback"])

    async def test_failed_fallback_keeps_the_lanpaint_setup_hint(self):
        pipeline = TranslationPipeline(device="cpu")
        lama_errors = []

        async def get_inpainter(inpainter_type):
            error = InpainterUnavailableError(
                f"{inpainter_type} failed", inpainter=inpainter_type
            )
            if inpainter_type == "lama_large":
                lama_errors.append(error)
            raise error

        pipeline._inpainters.get = get_inpainter

        # First request probes ComfyUI; the second is inside the retry window.
        for _attempt in range(2):
            with self.assertRaises(InpainterUnavailableError) as raised:
                await pipeline._inpainters.get_or_fallback("flux2_klein_lanpaint")

            self.assertIn("start ComfyUI", raised.exception.public_detail)
            self.assertIs(raised.exception.__cause__, lama_errors[-1])
        self.assertEqual(len(lama_errors), 2)


class LostInpainterRuntimeTests(unittest.IsolatedAsyncioTestCase):
    """ComfyUI can stop after LanPaint loaded; pages must not turn into 500s."""

    def make_pipeline(self, lama_fails=False):
        pipeline = TranslationPipeline(device="cpu")
        self.daemon_up = True
        self.job_error = None
        self.lanpaints = []
        self.lamas = []
        test = self

        class FakeLanPaint:
            def __init__(self, device="cpu"):
                self.disposed = False
                self.last_backend_used = "flux2_klein_lanpaint"
                test.lanpaints.append(self)

            async def initialize(self):
                if not test.daemon_up:
                    raise RuntimeError("ComfyUI LanPaint is unavailable")

            async def inpaint(self, source, mask):
                if not test.daemon_up:
                    raise ComfyUIUnreachableError("Cannot connect to 127.0.0.1:8188")
                if test.job_error is not None:
                    raise test.job_error
                return source.copy()

            def dispose(self):
                self.disposed = True

        class FakeLama:
            def __init__(self, inpainter_type, device):
                self.disposed = False
                test.lamas.append(self)

            async def initialize(self):
                if lama_fails:
                    raise RuntimeError("LaMa is not downloaded and the machine is offline")

            async def inpaint(self, source, mask):
                return source.copy()

            def dispose(self):
                self.disposed = True

        self.enterContext(
            patch.dict(
                sys.modules,
                {
                    "manga_backend.models.flux_lanpaint_inpainter": types.SimpleNamespace(
                        FluxLanPaintInpainter=FakeLanPaint
                    )
                },
            )
        )
        self.enterContext(patch("manga_backend.pipeline.inpainting.Inpainter", FakeLama))

        rng = np.random.default_rng(9)
        self.image = rng.integers(128, 255, (80, 120, 3)).astype(np.uint8)
        self.image[30:38, 40:70] = 0
        block = TextBlock([30, 22, 80, 46])

        pipeline._detectors.default = StaticDetector([block])
        pipeline._ocr = FakeOcrEngine()
        return pipeline

    async def translate(self, pipeline):
        with patch("manga_backend.pipeline.orchestrator.render_text", return_value=self.image):
            return await pipeline.translate(
                self.image,
                translator="none",
                inpainter="flux2_klein_lanpaint",
                render=True,
            )

    async def test_lost_runtime_falls_back_to_lama_and_is_probed_again_later(self):
        pipeline = self.make_pipeline()
        first = await self.translate(pipeline)
        self.assertEqual(first["inpainter_used"], "flux2_klein_lanpaint")
        lost = self.lanpaints[0]

        self.daemon_up = False
        result = await self.translate(pipeline)

        self.assertEqual(result["inpainter_used"], "lama_large")
        self.assertTrue(result["inpainting_fallback"])
        self.assertTrue(lost.disposed)
        self.assertIs(pipeline._inpainters.instance, self.lamas[0])
        self.assertEqual(
            pipeline.model_status["inpainter"],
            {"status": "loaded", "name": "lama_large"},
        )
        self.assertGreater(
            pipeline._inpainters.retry_at["flux2_klein_lanpaint"], time.monotonic()
        )

        # Inside the retry window ComfyUI is left alone.
        again = await self.translate(pipeline)
        self.assertEqual(again["inpainter_used"], "lama_large")
        self.assertEqual(len(self.lanpaints), 1)

        self.daemon_up = True
        pipeline._inpainters.retry_at["flux2_klein_lanpaint"] = 0.0
        back = await self.translate(pipeline)

        self.assertEqual(back["inpainter_used"], "flux2_klein_lanpaint")
        self.assertFalse(back["inpainting_fallback"])
        self.assertEqual(len(self.lanpaints), 2)
        self.assertTrue(self.lamas[0].disposed)

    async def test_lost_runtime_without_fallback_is_a_503_with_the_comfyui_hint(self):
        pipeline = self.make_pipeline()
        with patch.dict("os.environ", {"INPAINTER_FALLBACK": "off"}):
            await self.translate(pipeline)
            lost = self.lanpaints[0]

            self.daemon_up = False
            with self.assertRaises(InpainterUnavailableError) as raised:
                await self.translate(pipeline)

            self.assertEqual(raised.exception.status_code, 503)
            self.assertIn("start ComfyUI", raised.exception.public_detail)
            self.assertIsInstance(raised.exception.__cause__, ComfyUIUnreachableError)
            self.assertTrue(lost.disposed)
            self.assertIsNone(pipeline._inpainters.instance)
            self.assertEqual(pipeline.model_status["inpainter"]["status"], "error")
            self.assertEqual(self.lamas, [])

            # The next request probes ComfyUI again instead of reusing the instance.
            self.daemon_up = True
            result = await self.translate(pipeline)

        self.assertEqual(result["inpainter_used"], "flux2_klein_lanpaint")
        self.assertEqual(len(self.lanpaints), 2)

    async def test_failed_lanpaint_jobs_are_not_turned_into_a_fallback(self):
        pipeline = self.make_pipeline()
        await self.translate(pipeline)
        loaded = self.lanpaints[0]
        self.job_error = ComfyUIError("ComfyUI job failed: CUDA out of memory")

        with self.assertRaises(ComfyUIError) as raised:
            await self.translate(pipeline)

        self.assertIs(raised.exception, self.job_error)
        self.assertIs(pipeline._inpainters.instance, loaded)
        self.assertFalse(loaded.disposed)
        self.assertEqual(pipeline._inpainters.retry_at, {})
        self.assertEqual(self.lamas, [])

    async def test_lost_runtime_with_a_failed_fallback_keeps_the_comfyui_hint(self):
        pipeline = self.make_pipeline(lama_fails=True)
        await self.translate(pipeline)
        lost = self.lanpaints[0]

        self.daemon_up = False
        with self.assertRaises(InpainterUnavailableError) as raised:
            await self.translate(pipeline)

        self.assertIn("start ComfyUI", raised.exception.public_detail)
        self.assertIsInstance(raised.exception.__cause__, InpainterUnavailableError)
        self.assertTrue(lost.disposed)
        # The dead instance is not restored as the "previous" inpainter.
        self.assertIsNone(pipeline._inpainters.instance)

        self.daemon_up = True
        pipeline._inpainters.retry_at["flux2_klein_lanpaint"] = 0.0
        result = await self.translate(pipeline)
        self.assertEqual(result["inpainter_used"], "flux2_klein_lanpaint")
        self.assertEqual(len(self.lanpaints), 2)


class InpainterWarmUpTests(unittest.IsolatedAsyncioTestCase):
    """Startup loads the inpainter the first page will need."""

    def make_pipeline(self, inpainter_type=DEFAULT_INPAINTER, fail=False):
        pipeline = TranslationPipeline(device="cpu")
        pipeline._inpainters.type = inpainter_type
        loaded = []

        async def get_inpainter(requested):
            loaded.append(requested)
            if fail:
                raise InpainterUnavailableError("no model", inpainter=requested)
            return object()

        pipeline._inpainters.get = get_inpainter
        return pipeline, loaded

    async def test_default_inpainter_warms_its_lama_stand_in(self):
        pipeline, loaded = self.make_pipeline()

        await pipeline.warm_up()

        self.assertEqual(loaded, ["lama_large"])

    async def test_strict_mode_does_not_load_a_stand_in(self):
        pipeline, loaded = self.make_pipeline()

        with patch.dict("os.environ", {"INPAINTER_FALLBACK": "off"}):
            await pipeline.warm_up()

        self.assertEqual(loaded, [])

    async def test_nothing_is_loaded_for_none_or_an_already_loaded_inpainter(self):
        none_pipeline, none_loaded = self.make_pipeline("none")
        ready_pipeline, ready_loaded = self.make_pipeline("lama_large")
        ready_pipeline._inpainters.instance = object()

        await none_pipeline.warm_up()
        await ready_pipeline.warm_up()

        self.assertEqual(none_loaded + ready_loaded, [])

    async def test_a_failed_warm_up_does_not_raise(self):
        pipeline, loaded = self.make_pipeline(fail=True)

        await pipeline.warm_up()

        self.assertEqual(loaded, ["lama_large"])


class PipelineEventLoopTests(unittest.TestCase):
    """CPU-heavy mask and render steps must not stall the event loop."""

    def run_translate_with_blocked_step(self, step_name):
        started = threading.Event()
        release = threading.Event()
        image = np.full((60, 100, 3), 255, dtype=np.uint8)
        block = TextBlock([15, 15, 55, 40])

        pipeline = TranslationPipeline(device="cpu")
        pipeline._detectors.default = StaticDetector([block])
        pipeline._ocr = FakeOcrEngine()

        # An empty mask skips inpainter initialization entirely.
        outputs = {
            "prepare_flux_inpainting": (
                np.full((60, 100, 3), 255, dtype=np.uint8),
                np.zeros(image.shape[:2], dtype=np.uint8),
                0,
                0,
                [],
            ),
            "render_text": image,
        }

        def blocked_step(*_args, **_kwargs):
            started.set()
            release.wait(5.0)
            return outputs[step_name]

        watchdog_fired = threading.Event()

        def unblock_on_timeout():
            watchdog_fired.set()
            release.set()

        watchdog = threading.Timer(3.0, unblock_on_timeout)
        watchdog.start()

        async def run():
            overrides = {
                name: (lambda *_args, _name=name, **_kwargs: outputs[_name])
                for name in outputs
            }
            overrides[step_name] = blocked_step
            with patch.multiple("manga_backend.pipeline.orchestrator", **overrides):
                task = asyncio.create_task(
                    pipeline.translate(
                        image,
                        translator="none",
                        inpainter="lama_large",
                        render=True,
                    )
                )
                started_in_time = await asyncio.to_thread(started.wait, 2.0)

                async def health_like():
                    await asyncio.sleep(0)
                    return "ok"

                health = await asyncio.wait_for(health_like(), 1.0)
                responsive = started_in_time and not watchdog_fired.is_set()
                release.set()
                await task
            return responsive, health

        try:
            return asyncio.run(run())
        finally:
            release.set()
            watchdog.cancel()
            watchdog.join()

    def test_mask_building_does_not_block_the_event_loop(self):
        responsive, health = self.run_translate_with_blocked_step("prepare_flux_inpainting")

        self.assertTrue(responsive)
        self.assertEqual(health, "ok")

    def test_text_rendering_does_not_block_the_event_loop(self):
        responsive, health = self.run_translate_with_blocked_step("render_text")

        self.assertTrue(responsive)
        self.assertEqual(health, "ok")


class RenderedTextVerificationTests(unittest.IsolatedAsyncioTestCase):
    def test_cjk_letters_ignore_full_width_latin_and_punctuation(self):
        for text in ("Ｒｅａｄｙ．", "ＯＫ！？", "。", "「…」", "１２３", "Ready.", ""):
            with self.subTest(text=text):
                self.assertFalse(has_cjk_letters(text))
        for text in ("それは、", "ＱｕｒｒｙになってＭＥ？", "漢字", "々", "ｱｲｳ", "한국어"):
            with self.subTest(text=text):
                self.assertTrue(has_cjk_letters(text))
        # Font choice, wrapping and the translate decision keep the wider check.
        self.assertTrue(has_cjk("Ｒｅａｄｙ．"))


class FontObjectCacheTests(unittest.TestCase):
    def test_concurrent_requests_load_each_font_once(self):
        from manga_backend.pipeline import fonts as fonts_module

        calls = []
        barrier = threading.Barrier(6)

        def slow_truetype(path, size):
            calls.append((path, size))
            time.sleep(0.05)
            return object()

        def worker(results):
            barrier.wait()
            results.append(fonts_module.get_font_object("fake.ttf", 17))

        results = []
        with patch.dict(fonts_module._font_object_cache, clear=True), patch(
            "PIL.ImageFont.truetype", slow_truetype
        ):
            threads = [
                threading.Thread(target=worker, args=(results,)) for _ in range(6)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(calls, [("fake.ttf", 17)])
        self.assertEqual(len({id(font) for font in results}), 1)


class FakeTranslatorClient:
    """Records what the translate step asked the provider for."""

    calls: list[tuple[str, list[str], object]] = []
    translate_result: object = None
    shorten_result: object = None
    shorten_error: Exception | None = None

    def __init__(self, **_kwargs):
        pass

    async def initialize(self):
        pass

    async def close(self):
        pass

    async def translate(self, texts, target_lang="English", *, budgets=None, page_image=None):
        type(self).calls.append(("translate", list(texts), list(budgets or []), page_image))
        return list(self.translate_result or [])

    async def shorten(self, texts, limits, target_lang="English"):
        type(self).calls.append(("shorten", list(texts), list(limits)))
        if self.shorten_error is not None:
            raise self.shorten_error
        return list(self.shorten_result or [])


class TranslationReaskTests(unittest.IsolatedAsyncioTestCase):
    """Length budgets reach the prompt; an unfit unit gets exactly one re-ask."""

    def setUp(self):
        FakeTranslatorClient.calls = []
        FakeTranslatorClient.translate_result = ["A much too long line here", "Short"]
        FakeTranslatorClient.shorten_result = ["Short one", "Also short"]
        FakeTranslatorClient.shorten_error = None
        patcher = patch(
            "manga_backend.pipeline.translation.Translator", FakeTranslatorClient
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_step(self, *, budgets, fits, page_image=None):
        from manga_backend.pipeline.translation import TranslationReport, translate_texts

        report = TranslationReport()
        result = asyncio.run(
            translate_texts(
                ["一", "二"],
                "English",
                translator="local",
                api_key=None,
                model=None,
                local_llm_base_url=None,
                local_llm_api_key=None,
                budgets=budgets,
                page_image=page_image,
                fits=fits,
                report=report,
            )
        )
        return result, report

    def test_budgets_reach_the_prompt_and_no_reask_happens_when_everything_fits(self):
        result, report = self.run_step(budgets=[24, None], fits=lambda index, text: True)

        self.assertEqual(result, ["A much too long line here", "Short"])
        translate_call = FakeTranslatorClient.calls[0]
        self.assertEqual(translate_call[0], "translate")
        self.assertEqual(translate_call[2], [24, None])
        self.assertEqual(len(FakeTranslatorClient.calls), 1)
        self.assertEqual(report.requests, 1)
        self.assertEqual(report.reask_units, 0)
        self.assertEqual(report.budgeted, 1)

    def test_one_reask_shortens_only_the_unfit_units(self):
        result, report = self.run_step(
            budgets=[12, 30],
            fits=lambda index, text: index != 0,
        )

        self.assertEqual(result, ["Short one", "Short"])
        self.assertEqual(
            [call[0] for call in FakeTranslatorClient.calls], ["translate", "shorten"]
        )
        self.assertEqual(FakeTranslatorClient.calls[1][1], ["A much too long line here"])
        self.assertEqual(FakeTranslatorClient.calls[1][2], [12])
        self.assertEqual(report.reask_units, 1)
        self.assertEqual(report.reask_shortened, 1)
        self.assertEqual(report.requests, 2)

    def test_a_second_failure_is_not_reasked_again(self):
        FakeTranslatorClient.translate_result = ["still far too long", "Short"]
        FakeTranslatorClient.shorten_result = ["also far too long"]

        result, report = self.run_step(budgets=[6, 30], fits=lambda index, text: index != 0)

        # The shortened version is used as it is; the renderer does what it can.
        self.assertEqual(result, ["also far too long", "Short"])
        self.assertEqual([call[0] for call in FakeTranslatorClient.calls], ["translate", "shorten"])
        self.assertEqual(report.reask_units, 1)
        self.assertEqual(report.reask_shortened, 1)

    def test_a_failed_reask_keeps_the_first_translations(self):
        FakeTranslatorClient.shorten_error = RuntimeError("provider down")

        result, report = self.run_step(budgets=[12, 30], fits=lambda index, text: index != 0)

        self.assertEqual(result, ["A much too long line here", "Short"])
        self.assertTrue(report.reask_failed)
        self.assertEqual(report.requests, 2)

    def test_an_empty_shortened_item_does_not_replace_the_translation(self):
        FakeTranslatorClient.shorten_result = [""]

        result, _report = self.run_step(budgets=[12, 30], fits=lambda index, text: index != 0)

        self.assertEqual(result, ["A much too long line here", "Short"])

    def test_page_image_is_handed_to_the_provider(self):
        _result, report = self.run_step(
            budgets=[12, 30], fits=lambda index, text: True, page_image=b"png"
        )

        self.assertEqual(FakeTranslatorClient.calls[0][3], b"png")
        self.assertTrue(report.page_image)

    def test_no_units_gives_no_translations(self):
        FakeTranslatorClient.translate_result = []
        from manga_backend.pipeline.translation import translate_texts

        result = asyncio.run(
            translate_texts(
                [],
                "English",
                translator="local",
                api_key=None,
                model=None,
                local_llm_base_url=None,
                local_llm_api_key=None,
            )
        )

        self.assertEqual(result, [])
        self.assertEqual([call[0] for call in FakeTranslatorClient.calls], ["translate"])
        self.assertEqual(FakeTranslatorClient.calls[0][1], [])


class TranslationConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """Erasing runs beside translation; the observable order is unchanged."""

    def build_pipeline(self):
        image = np.full((120, 160, 3), 255, dtype=np.uint8)
        blocks = [
            TextBlock([10, 10, 60, 40]),
            TextBlock([80, 10, 130, 40]),
            TextBlock([10, 80, 60, 110]),
        ]
        pipeline = TranslationPipeline(device="cpu")
        pipeline._detectors.default = types.SimpleNamespace(
            detect=AsyncMock(return_value=list(blocks))
        )
        pipeline._run_ocr = AsyncMock(return_value=["一", "……", "三"])
        return pipeline, image, blocks

    def install(self, pipeline, *, mask_started=None, translate_waits_for_mask=False):
        events: list[str] = []
        calls: dict[str, list[Any]] = {
            "mask": [],
            "mask_translations": [],
            "render": [],
            "translate": [],
        }
        image_holder: dict[str, np.ndarray] = {}

        def fake_prepare(image, blocks, rectangular=False):
            events.append("mask-start")
            if mask_started is not None:
                mask_started.set()
            calls["mask"].append(list(blocks))
            calls["mask_translations"].append([block.translated_text for block in blocks])
            image_holder["mask_input"] = image
            return image.copy(), np.zeros(image.shape[:2], dtype=np.uint8), 0, 0, []

        async def fake_translate(texts, target_lang, **kwargs):
            events.append("translate-start")
            calls["translate"].append(list(texts))
            if translate_waits_for_mask and mask_started is not None:
                started = await asyncio.to_thread(mask_started.wait, 5.0)
                self.assertTrue(started, "the erase should start while translation runs")
            events.append("translate-end")
            # Like a real provider (and the harness stub): punctuation-only
            # text comes back unchanged, so the erase set does not move.
            return [
                text if text.strip() and not any(ch.isalnum() for ch in text) else f"T{index}"
                for index, text in enumerate(texts)
            ]

        def fake_render(image, blocks, **options):
            events.append("render")
            calls["render"].append(list(blocks))
            image_holder["render_input"] = image
            output = image.copy()
            output[0, 0] = [1, 2, 3]
            return output

        stack = ExitStack()
        overrides = {
            "translate_texts": fake_translate,
            "prepare_flux_inpainting": fake_prepare,
            "render_text": fake_render,
        }
        for name, replacement in overrides.items():
            stack.enter_context(patch.object(orchestrator_module, name, replacement))
        self.addCleanup(stack.close)
        return events, calls, image_holder

    async def run_pipeline(self, pipeline, image, *, translate_waits_for_mask=False):
        mask_started = threading.Event()
        events, calls, image_holder = self.install(
            pipeline,
            mask_started=mask_started,
            translate_waits_for_mask=translate_waits_for_mask,
        )
        result = await pipeline.translate(
            image,
            translator="local",
            inpainter="lama_large",
            render=True,
            verify_translation=False,
        )
        return result, events, calls, image_holder

    async def test_erasing_starts_while_the_provider_translates(self):
        pipeline, image, blocks = self.build_pipeline()

        _result, events, calls, _inputs = await self.run_pipeline(
            pipeline, image, translate_waits_for_mask=True
        )

        self.assertIn("mask-start", events)
        self.assertLess(events.index("mask-start"), events.index("translate-end"))
        # The mask ran before the translation was assigned, and only the blocks
        # that will be erased went in: punctuation-only text is left alone.
        self.assertTrue(all(not text for text in calls["mask_translations"][0]))
        self.assertEqual([block.text for block in calls["mask"][0]], ["一", "三"])
        self.assertEqual(len(calls["mask"]), 1)

    async def test_fast_and_slow_provider_runs_are_byte_identical(self):
        pipeline, image, _blocks = self.build_pipeline()
        slow, _slow_events, slow_calls, slow_inputs = await self.run_pipeline(
            pipeline, image, translate_waits_for_mask=True
        )
        pipeline, image, _blocks = self.build_pipeline()
        fast, _fast_events, fast_calls, fast_inputs = await self.run_pipeline(pipeline, image)

        self.assertEqual(slow["translated_texts"], fast["translated_texts"])
        self.assertEqual(slow["original_texts"], fast["original_texts"])
        self.assertEqual(
            [list(block["bbox"]) for block in slow["blocks"]],
            [list(block["bbox"]) for block in fast["blocks"]],
        )
        self.assertTrue(np.array_equal(slow["image"], fast["image"]))
        self.assertEqual(
            [(block.text, list(block.xyxy)) for block in slow_calls["mask"][0]],
            [(block.text, list(block.xyxy)) for block in fast_calls["mask"][0]],
        )
        self.assertTrue(
            np.array_equal(slow_inputs["render_input"], fast_inputs["render_input"])
        )

    async def test_an_empty_translation_rebuilds_the_mask_for_the_reduced_set(self):
        pipeline, image, blocks = self.build_pipeline()

        async def dropping_translate(texts, target_lang, **kwargs):
            # The provider answers, then one of the erased units comes back empty.
            return ["T0", "T1", ""]

        mask_started = threading.Event()
        events, calls, _inputs = self.install(
            pipeline, mask_started=mask_started, translate_waits_for_mask=False
        )
        with patch.object(orchestrator_module, "translate_texts", dropping_translate):
            result = await pipeline.translate(
                image,
                translator="local",
                inpainter="lama_large",
                render=True,
                verify_translation=False,
            )

        self.assertEqual(result["translated_texts"], ["T0", "T1", ""])
        # The source-side erase started before the provider answered, with the
        # blocks whose OCR text can be rendered; the translation then changed
        # the set (the punctuation block was rewritten, the erased one came
        # back empty), so the mask is rebuilt for the final set.
        self.assertEqual([block.text for block in calls["mask"][0]], ["一", "三"])
        self.assertEqual([block.text for block in calls["mask"][1]], ["一", "……"])
        self.assertEqual(len(calls["mask"]), 2)

    async def test_concurrent_erase_records_residual_check_and_skips_step6_reocr(self):
        pipeline, image, _blocks = self.build_pipeline()
        # Draw thin lettering strokes inside the two erased blocks ([80, 10, 130, 40]
        # and [10, 80, 60, 110]) so the real fill router + residual check run on both.
        image[18:22, 90:120] = 0
        image[88:92, 20:50] = 0
        ocr_mock = AsyncMock(return_value=["一", "……", "三"])
        pipeline._ocr = types.SimpleNamespace(process=ocr_mock)

        async def fake_translate(texts, target_lang, **kwargs):
            return ["One", "……", "Three"]

        with patch.object(orchestrator_module, "translate_texts", fake_translate):
            result = await pipeline.translate(
                image,
                translator="local",
                inpainter="lama_large",
                render=True,
                verify_translation=True,
            )

        self.assertTrue(result["fill_decisions"])
        for decision in result["fill_decisions"]:
            self.assertIn("residual_flag", decision)
            self.assertIn("escalated", decision)
        # Step 6 re-OCR is off the production path even when verify_translation=True.
        ocr_mock.assert_not_awaited()


class EraseFilterTests(unittest.TestCase):
    """The empty/punctuation filter is the same rule as before the restructure."""

    def test_source_side_candidates_skip_empty_and_punctuation_only_blocks(self):
        cases = {
            "一": True,
            "……": False,
            "。": False,
            "！？": False,
            "": False,
            "   ": False,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(erase_candidate(TextBlock([0, 0, 10, 10], text=text)), expected)

    def test_translation_side_filter_is_unchanged(self):
        def block(text, translated):
            return TextBlock([0, 0, 10, 10], text=text, translated_text=translated)

        self.assertTrue(erase_from_translation(block("一", "One")))
        self.assertFalse(erase_from_translation(block("一", "")))
        self.assertFalse(erase_from_translation(block("……", "……")))
        self.assertTrue(erase_from_translation(block("……", "...")))
        self.assertTrue(erase_from_translation(block("……", "Hm")))


class TextSegmenterAndRetryPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_segmenter_load_falls_back_gracefully(self):
        pipeline = TranslationPipeline(device="cpu")
        pipeline.is_initialized = True
        with patch(
            "manga_backend.pipeline.orchestrator.TextSegmenter.initialize",
            new=AsyncMock(side_effect=RuntimeError("offline")),
        ):
            seg = await pipeline._get_text_segmenter()

        self.assertIsNone(seg)
        self.assertEqual(pipeline.model_status["text_segmenter"]["status"], "error")
        self.assertIn("offline", pipeline.model_status["text_segmenter"]["error"])

    async def test_segmenter_feeds_cache_and_fuses_sfx_in_translate(self):
        from manga_backend.models.text_segmenter import TextSegmentationResult
        from manga_backend.pipeline import masks as masks_module

        pipeline = TranslationPipeline(device="cpu")
        pipeline.is_initialized = True
        image = np.full((120, 160, 3), 255, dtype=np.uint8)
        image[20:50, 20:60] = 0
        rtdetr_block = TextBlock([15, 15, 65, 55], block_type="text")
        sfx_block = TextBlock([95, 20, 145, 70], block_type="sfx")
        sfx_block.confidence = 0.85

        seg_map = np.zeros((120, 160), dtype=np.float32)
        seg_map[22:48, 22:58] = 0.9
        seg_map[25:65, 100:140] = 0.9

        fake_seg = types.SimpleNamespace(
            session=object(),
            segment=AsyncMock(
                return_value=TextSegmentationResult(prob_map=seg_map, blocks=[sfx_block])
            ),
            dispose=lambda: None,
        )
        pipeline._text_segmenter = fake_seg
        pipeline._detectors.default = types.SimpleNamespace(
            detect=AsyncMock(return_value=[rtdetr_block])
        )
        pipeline._run_ocr = AsyncMock(side_effect=lambda _i, blks, *_a: ["台詞"] * len(blks))

        seen_seg = []
        orig_prepare = orchestrator_module.prepare_flux_inpainting

        def capture_prepare(img, blks, rectangular=False):
            seen_seg.append(masks_module.current_text_seg_map(img))
            return orig_prepare(img, blks, rectangular=rectangular)

        with (
            patch.object(orchestrator_module, "prepare_flux_inpainting", capture_prepare),
            patch.object(orchestrator_module, "render_text", side_effect=lambda i, b, **o: i),
        ):
            result = await pipeline.translate(
                image,
                detector="rtdetr",
                translator="none",
                inpainter="lama_large",
                render=True,
            )

        self.assertEqual(len(result["blocks"]), 2)
        self.assertIs(seen_seg[0], seg_map)

    async def test_post_model_residual_triggers_one_retry_inpaint_call(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((120, 120, 3), 240, dtype=np.uint8)
        cv2.line(image, (0, 32), (120, 42), (120, 120, 120), 4)
        cv2.line(image, (0, 82), (120, 74), (105, 105, 105), 3)
        cv2.circle(image, (30, 62), 18, (130, 130, 130), 3)
        cv2.circle(image, (92, 58), 16, (115, 115, 115), 3)
        image[48:68, 52:58] = 0
        image[48:54, 52:72] = 0
        image[58:63, 52:70] = 0
        block = TextBlock([40, 40, 82, 76], block_type="sfx")

        inpaint_calls: list[np.ndarray] = []

        class TwoPassInpainter:
            last_backend_used = "lama_large"

            async def inpaint(self, source, mask):
                inpaint_calls.append(mask.copy())
                if len(inpaint_calls) == 1:
                    # First pass barely touches the top edge of the stroke so residue re-flags.
                    out = source.copy()
                    out[48:50, 52:72] = 200
                    return out
                out = source.copy()
                out[mask > 0] = 240
                return out

        pipeline._detectors.default = types.SimpleNamespace(
            detect=AsyncMock(return_value=[block])
        )
        pipeline._ocr = types.SimpleNamespace(process=AsyncMock(return_value=["ドン"]))
        pipeline._inpainters.instance = TwoPassInpainter()
        pipeline._inpainters.type = "lama_large"
        pipeline.model_status["inpainter"] = {"status": "loaded", "name": "lama_large"}

        with patch.object(
            orchestrator_module, "render_text", side_effect=lambda i, b, **o: i
        ):
            result = await pipeline.translate(
                image,
                translator="none",
                inpainter="lama_large",
                render=True,
            )

        self.assertEqual(len(inpaint_calls), 2)
        self.assertEqual(result.get("model_retry_regions"), 1)


if __name__ == "__main__":
    unittest.main()
