"""Model-free tests for backend pipeline routing."""

import asyncio
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import cv2
import numpy as np


BACKEND_DIR = Path(__file__).resolve().parents[1]

from manga_backend.comfy_lanpaint.client import ComfyUIError, ComfyUIUnreachableError
from manga_backend.pipeline import (
    DEFAULT_INPAINTER,
    DetectorUnavailableError,
    GOOGLE_OCR_CONCURRENCY,
    GoogleOCRError,
    InpainterUnavailableError,
    PaddleOCRUnavailableError,
    TranslationPipeline,
    text_block_to_dict,
)
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

        pipeline._optional_detectors["animetext-yolo"] = FakeAnimeTextDetector()
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
        pipeline._inpainter = active
        pipeline.model_status["inpainter"]["status"] = "loaded"

        with patch("manga_backend.pipeline.Inpainter") as inpainter_constructor:
            selected = await pipeline._get_inpainter(
                pipeline.model_status["inpainter"]["name"]
            )

        self.assertEqual(pipeline._inpainter_type, DEFAULT_INPAINTER)
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
            "manga_backend.pipeline.get_paddle_ocr", return_value=fake_paddle
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

        with patch("manga_backend.pipeline.get_paddle_ocr", return_value=fake_paddle):
            blocks = await pipeline.paddle_ocr_full(image, lang="en")

        self.assertEqual(blocks, [{"text": "full-paddle"}])
        self.assertEqual(pipeline.model_status["paddle_ocr"]["status"], "loaded")

    async def test_translate_uses_status_aware_paddle_path(self):
        pipeline = TranslationPipeline(device="cpu")
        fake_paddle = FakePaddleEngine()
        image = np.zeros((12, 24, 3), dtype=np.uint8)

        class FakeDetector:
            async def detect(self, _image):
                return [TextBlock([1, 2, 10, 11])]

        pipeline._detector = FakeDetector()
        with patch("manga_backend.pipeline.get_paddle_ocr", return_value=fake_paddle):
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

        with patch("manga_backend.pipeline.get_paddle_ocr", return_value=fake_paddle):
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
        with patch("manga_backend.pipeline.get_paddle_ocr", return_value=fake_paddle):
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
        with patch("manga_backend.pipeline.get_paddle_ocr", return_value=fake_paddle):
            with self.assertRaisesRegex(RuntimeError, "missing model"):
                await pipeline.paddle_ocr_full(image)
        self.assertEqual(pipeline.model_status["paddle_ocr"]["status"], "error")

        async def cancelled_initialize():
            raise asyncio.CancelledError()

        pipeline.model_status["paddle_ocr"]["status"] = "not_loaded"
        fake_paddle.initialize = cancelled_initialize
        with patch("manga_backend.pipeline.get_paddle_ocr", return_value=fake_paddle):
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
        with patch("manga_backend.pipeline.get_paddle_ocr", return_value=fake_paddle):
            task = asyncio.create_task(
                pipeline.ocr(image, [{"bbox": [1, 2, 10, 11]}], ocr="paddle-ocr")
            )
            await entered.wait()
            self.assertTrue(pipeline.component_ready("paddle_ocr"))
            release.set()
            self.assertEqual(await task, ["slow"])

    def test_paddle_engine_is_shared_across_languages(self):
        import manga_backend.pipeline as pipeline_module

        created = []

        class Engine:
            def __init__(self, lang, device):
                created.append((lang, device))

        fake_module = types.ModuleType("manga_backend.models.paddle_ocr_onnx")
        fake_module.PaddleOCROnnxEngine = Engine
        with patch.dict(pipeline_module._paddle_ocr_engines, {}, clear=True), patch.dict(
            sys.modules, {"manga_backend.models.paddle_ocr_onnx": fake_module}
        ):
            english = pipeline_module.get_paddle_ocr(lang="en", device="cpu")
            chinese = pipeline_module.get_paddle_ocr(lang="ch", device="cpu")
            gpu = pipeline_module.get_paddle_ocr(lang="en", device="cuda")

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
        with patch("manga_backend.pipeline.TextDetector", FakeCore), patch(
            "manga_backend.pipeline.OCREngine", FakeCore
        ), patch("manga_backend.pipeline.get_paddle_ocr") as get_paddle:
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
        with patch("manga_backend.pipeline.get_paddle_ocr", return_value=fake_paddle):
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
        import manga_backend.pipeline as pipeline_module

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

        with patch.dict(pipeline_module._paddle_ocr_engines, {}, clear=True), patch.dict(
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
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((60, 100, 3), 255, dtype=np.uint8)
        image[22:30, 25:45] = 0
        block = TextBlock([15, 15, 55, 40])

        class FakeDetector:
            async def detect(self, _image):
                return [block]

        class FakeInpainter:
            last_backend_used = "test"

            async def inpaint(self, source, mask):
                self.mask = mask
                return source.copy()

        inpainter = FakeInpainter()
        pipeline._detector = FakeDetector()
        pipeline._ocr = FakeOcrEngine()
        pipeline._inpainter = inpainter
        pipeline._inpainter_type = "test"
        pipeline.model_status["inpainter"] = {"status": "loaded", "name": "test"}

        with patch.object(pipeline, "_render_text", return_value=image):
            await pipeline.translate(
                image,
                translator="none",
                inpainter="test",
                render=True,
            )

        np.testing.assert_array_equal(
            inpainter.mask,
            pipeline._create_mask(image, [block]),
        )

    async def test_translation_uses_rectangular_mask_for_animetext_yolo(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((60, 100, 3), 255, dtype=np.uint8)
        block = TextBlock([15, 15, 55, 40])

        class FakeDetector:
            async def detect(self, _image):
                return [block]

        class FakeInpainter:
            last_backend_used = "test"

            async def inpaint(self, source, mask):
                self.mask = mask
                return source.copy()

        inpainter = FakeInpainter()
        pipeline._optional_detectors["animetext-yolo"] = FakeDetector()
        pipeline._ocr = FakeOcrEngine()
        pipeline._inpainter = inpainter
        pipeline._inpainter_type = "test"
        pipeline.model_status["inpainter"] = {"status": "loaded", "name": "test"}

        with patch.object(pipeline, "_render_text", return_value=image):
            await pipeline.translate(
                image,
                detector="anime-text-yolo",
                translator="none",
                inpainter="test",
                render=True,
            )

        np.testing.assert_array_equal(
            inpainter.mask,
            pipeline._create_mask(image, [block], rectangular=True),
        )

    async def test_ocr_routes_google_with_api_key_and_language_hint(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.zeros((12, 24, 3), dtype=np.uint8)
        pipeline._google_ocr = AsyncMock(return_value=["google-text"])

        texts = await pipeline.ocr(
            image,
            [{"bbox": [1, 2, 10, 11]}],
            ocr="google-ocr",
            google_ocr_api_key="vision-key",
            google_ocr_language_hint="ja",
        )

        self.assertEqual(texts, ["google-text"])
        pipeline._google_ocr.assert_awaited_once()
        _image, blocks, api_key, language_hint = pipeline._google_ocr.await_args.args
        self.assertEqual(blocks[0].xyxy.tolist(), [1, 2, 10, 11])
        self.assertEqual(api_key, "vision-key")
        self.assertEqual(language_hint, "ja")

    async def test_translate_closes_translator_client_after_success_and_failure(self):
        for error in (None, RuntimeError("provider down")):
            pipeline = TranslationPipeline(device="cpu")
            pipeline._detector = types.SimpleNamespace(
                detect=AsyncMock(return_value=[TextBlock(xyxy=[1, 1, 8, 8])])
            )
            pipeline._ocr = types.SimpleNamespace(process=AsyncMock(return_value=["テキスト"]))
            provider = types.SimpleNamespace(
                initialize=AsyncMock(),
                translate=AsyncMock(return_value=["Text"], side_effect=error),
                close=AsyncMock(),
            )

            with patch("manga_backend.pipeline.Translator", return_value=provider):
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

    def _fake_google_session(self, respond):
        """Build a ClientSession stand-in whose post() awaits respond(request_number)."""
        state = types.SimpleNamespace(active=0, peak=0, calls=0)

        class FakeResponse:
            def __init__(self, status, body):
                self.status = status
                self.body = body

            async def json(self):
                return self.body

        class FakePost:
            async def __aenter__(self):
                state.calls += 1
                number = state.calls
                state.active += 1
                state.peak = max(state.peak, state.active)
                try:
                    status, body = await respond(number)
                finally:
                    state.active -= 1
                return FakeResponse(status, body)

            async def __aexit__(self, *_exc):
                return False

        class FakeSession:
            def __init__(self, *_args, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            def post(self, *_args, **_kwargs):
                return FakePost()

        return FakeSession, state

    async def test_google_ocr_runs_concurrently_within_limit_and_keeps_block_order(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.zeros((40, 40, 3), dtype=np.uint8)
        blocks = [TextBlock(xyxy=[0, 0, 10, 10]) for _ in range(GOOGLE_OCR_CONCURRENCY * 2)]
        blocks.insert(1, TextBlock(xyxy=[5, 5, 5, 5]))  # empty crop makes no request

        async def respond(number):
            # Later requests finish first, so order must not come from completion.
            await asyncio.sleep(0.02 / number)
            return 200, {"responses": [{"textAnnotations": [{"description": f" text-{number} "}]}]}

        session, state = self._fake_google_session(respond)
        with patch("aiohttp.ClientSession", session):
            texts = await pipeline._google_ocr(image, blocks, "vision-key")

        self.assertEqual(len(texts), len(blocks))
        self.assertEqual(texts[1], "")
        self.assertTrue(all(text.startswith("text-") for i, text in enumerate(texts) if i != 1))
        self.assertEqual(texts[0], "text-1")
        self.assertEqual(texts[2], "text-2")
        self.assertEqual(state.peak, GOOGLE_OCR_CONCURRENCY)

    async def test_google_ocr_failure_cancels_remaining_requests_and_stays_sanitized(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.zeros((40, 40, 3), dtype=np.uint8)
        blocks = [TextBlock(xyxy=[0, 0, 10, 10]) for _ in range(GOOGLE_OCR_CONCURRENCY * 3)]

        async def respond(number):
            if number == 1:
                return 403, {"error": "vision-key leaked"}
            await asyncio.sleep(30)
            return 200, {"responses": [{}]}

        session, state = self._fake_google_session(respond)
        with patch("aiohttp.ClientSession", session):
            with self.assertRaises(GoogleOCRError) as raised:
                await asyncio.wait_for(pipeline._google_ocr(image, blocks, "vision-key"), timeout=5)

        self.assertNotIn("vision-key", str(raised.exception))
        self.assertEqual(state.active, 0)
        self.assertLessEqual(state.calls, GOOGLE_OCR_CONCURRENCY + 1)

    async def test_ocr_rejects_google_without_api_key_as_client_error(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.zeros((12, 24, 3), dtype=np.uint8)

        with self.assertRaisesRegex(ValueError, "google_ocr_api_key"):
            await pipeline.ocr(image, [{"bbox": [1, 2, 10, 11]}], ocr="google-ocr")

    async def test_google_ocr_skips_zero_area_regions(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.zeros((12, 24, 3), dtype=np.uint8)
        blocks = [
            TextBlock([5, 5, 5, 8]),
            TextBlock([8, 8, 4, 10]),
            TextBlock([30, 30, 40, 40]),
        ]

        texts = await pipeline._google_ocr(image, blocks, api_key="vision-key")

        self.assertEqual(texts, ["", "", ""])

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

        with patch("manga_backend.pipeline.Inpainter", FakeInpainter):
            first, second = await __import__("asyncio").gather(
                pipeline._get_inpainter("lama"),
                pipeline._get_inpainter("lama_large"),
            )

        self.assertEqual(
            [first.inpainter_type, second.inpainter_type], ["lama", "lama_large"]
        )
        self.assertTrue(all(instance.initialized for instance in created))
        self.assertIs(pipeline._inpainter, second)
        self.assertEqual(pipeline._inpainter_type, "lama_large")
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

        with patch("manga_backend.pipeline.Inpainter", FakeInpainter):
            first, second = await __import__("asyncio").gather(
                pipeline._get_inpainter("lama_large"),
                pipeline._get_inpainter("lama_large"),
            )

        self.assertEqual(len(created), 1)
        self.assertIs(first, second)
        self.assertIs(pipeline._inpainter, first)

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

        with patch("manga_backend.pipeline.Inpainter", BrokenInpainter):
            with self.assertRaisesRegex(RuntimeError, "model load failed"):
                await pipeline._get_inpainter("lama")

        self.assertIsNone(pipeline._inpainter)
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
        pipeline._inpainter = active
        pipeline._inpainter_type = "lama_large"
        pipeline.is_initialized = True
        pipeline.model_status["detector"]["status"] = "loaded"
        pipeline.model_status["ocr"]["status"] = "loaded"
        pipeline.model_status["inpainter"] = {"status": "loaded", "name": "lama_large"}

        class BrokenInpainter:
            def __init__(self, inpainter_type, device):
                self.inpainter_type = inpainter_type

            async def initialize(self):
                raise RuntimeError("replacement failed")

        with patch("manga_backend.pipeline.Inpainter", BrokenInpainter):
            with self.assertRaisesRegex(RuntimeError, "replacement failed"):
                await pipeline._get_inpainter("lama")

        self.assertIs(pipeline._inpainter, active)
        self.assertEqual(pipeline._inpainter_type, "lama_large")
        self.assertTrue(pipeline.is_ready)
        self.assertEqual(
            pipeline.model_status["inpainter"]["last_load_error"],
            {"name": "lama", "error": "replacement failed"},
        )

        restored = await pipeline._get_inpainter("lama_large")
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
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((120, 80, 3), 255, dtype=np.uint8)
        block = TextBlock([20, 5, 45, 115], translated_text="TRAIN ME")

        def fail_vertical(*_args, **_kwargs):
            raise AssertionError(
                "auto direction should not stack translated Latin text vertically"
            )

        pipeline._draw_vertical_text = fail_vertical

        rendered = pipeline._render_text(
            image.copy(),
            [block],
            text_direction="auto",
            bg_opacity=0,
        )

        self.assertFalse(np.array_equal(rendered, image))

    def test_render_text_honors_vertical_direction(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((90, 90, 3), 255, dtype=np.uint8)
        block = TextBlock([25, 5, 65, 85], translated_text="ABCD")

        horizontal = pipeline._render_text(
            image.copy(),
            [block],
            text_direction="horizontal",
            bg_opacity=0,
            auto_scale=False,
        )
        vertical = pipeline._render_text(
            image.copy(),
            [block],
            text_direction="vertical",
            bg_opacity=0,
            auto_scale=False,
        )

        self.assertFalse(np.array_equal(horizontal, vertical))
        self.assertFalse(np.array_equal(vertical, image))

    def test_latin_wrapping_keeps_words_that_fit_intact(self):
        from PIL import ImageFont

        pipeline = TranslationPipeline(device="cpu")

        lines = pipeline._wrap_text_to_lines(
            "A SENSE OF FAMILIARITY", ImageFont.load_default(), width=90
        )

        self.assertIn("FAMILIARITY", lines)
        self.assertNotIn("F", lines)

    def test_latin_wrapping_splits_only_oversized_tokens(self):
        from PIL import ImageFont

        pipeline = TranslationPipeline(device="cpu")
        font = ImageFont.load_default()

        lines = pipeline._wrap_text_to_lines(
            "UNBREAKABLYLONGWORD", font, width=30, stroke_width=1
        )

        self.assertGreater(len(lines), 1)
        for line in lines:
            bbox = font.getbbox(line)
            self.assertLessEqual(bbox[2] - bbox[0] + 2, 30)

    def test_latin_wrapping_can_break_at_em_dash_without_adding_space(self):
        from PIL import ImageFont

        pipeline = TranslationPipeline(device="cpu")
        font = ImageFont.load_default(size=12)
        word_width = max(
            font.getbbox("NICE—")[2] - font.getbbox("NICE—")[0],
            font.getbbox("LIVING")[2] - font.getbbox("LIVING")[0],
        )

        lines = pipeline._wrap_text_to_lines(
            "NICE—LIVING", font, width=word_width
        )

        self.assertEqual(lines, ["NICE—", "LIVING"])

    def test_rtdetr_blocks_sharing_a_bubble_render_as_one_region(self):
        pipeline = TranslationPipeline(device="cpu")
        first = TextBlock([40, 20, 60, 50], text="A", translated_text="FIRST")
        second = TextBlock([42, 55, 62, 80], text="B", translated_text="SECOND")
        first.bubble_xyxy = [30, 10, 90, 100]
        second.bubble_xyxy = [30, 10, 90, 100]

        regions = pipeline._build_render_regions([second, first], (120, 120, 3))

        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].translated_text, "FIRST SECOND")
        self.assertEqual(first.xyxy.tolist(), [40, 20, 60, 50])
        self.assertEqual(second.xyxy.tolist(), [42, 55, 62, 80])

    def test_yolo_regions_are_clipped_and_cannot_draw_into_neighbors(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((80, 140, 3), 255, dtype=np.uint8)
        left = TextBlock([5, 10, 40, 70], translated_text="UNBREAKABLYLONGWORD")
        right = TextBlock([100, 10, 135, 70], translated_text="ANOTHERLONGWORD")

        rendered = pipeline._render_text(
            image.copy(),
            [left, right],
            text_direction="horizontal",
            bg_opacity=0,
            auto_scale=False,
        )

        changed = np.any(rendered != image, axis=2)
        allowed = np.zeros(changed.shape, dtype=bool)
        allowed[10:70, 5:40] = True
        allowed[10:70, 100:135] = True
        self.assertTrue(changed.any())
        self.assertFalse(changed[~allowed].any())
        self.assertFalse(changed[:, 40:100].any())

    def test_tiny_short_label_expands_without_overlapping_neighbor(self):
        pipeline = TranslationPipeline(device="cpu")
        short = TextBlock([120, 691, 142, 724], translated_text="Hey")
        short._render_from_bubble = False
        neighbor = TextBlock([72, 724, 134, 841], translated_text="Hurry up!")
        neighbor._render_from_bubble = True

        expanded = pipeline._expand_short_standalone_regions(
            [short, neighbor], (902, 1240, 3)
        )

        self.assertEqual(expanded, 1)
        self.assertEqual(short.xyxy.tolist(), [120, 691, 150, 724])
        self.assertEqual(short.y2, neighbor.y1)

    def test_small_bubble_text_can_overflow_to_reach_readable_font(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((120, 120, 3), 255, dtype=np.uint8)
        block = TextBlock(
            [45, 35, 75, 85],
            text="COSMETICS",
            translated_text="COSMETICS?",
        )
        block.bubble_xyxy = [40, 20, 80, 100]

        rendered = pipeline._render_text(
            image,
            [block],
            bg_opacity=0,
            text_direction="horizontal",
            preferred_font_size=16,
        )

        changed = np.any(rendered != image, axis=2)
        _ys, xs = np.where(changed)
        self.assertTrue(len(xs))
        self.assertTrue(xs.min() < 42 or xs.max() >= 78)

    def test_narrow_text_is_fitted_without_touching_clip_boundary(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((90, 60, 3), 255, dtype=np.uint8)
        block = TextBlock(
            [10, 10, 45, 80], translated_text="UNBREAKABLYLONGWORD"
        )

        rendered = pipeline._render_text(
            image.copy(),
            [block],
            text_direction="horizontal",
            bg_opacity=0,
            border_size=1,
        )

        changed = np.any(rendered != image, axis=2)
        self.assertTrue(changed.any())
        self.assertFalse(changed[10, 10:45].any())
        self.assertFalse(changed[79, 10:45].any())
        self.assertFalse(changed[10:80, 10].any())
        self.assertFalse(changed[10:80, 44].any())

    def test_distinct_bubbles_are_not_merged(self):
        pipeline = TranslationPipeline(device="cpu")
        first = TextBlock([10, 10, 30, 30], translated_text="ONE")
        second = TextBlock([20, 20, 40, 40], translated_text="TWO")
        first.bubble_xyxy = [5, 5, 45, 45]
        second.bubble_xyxy = [6, 5, 45, 45]

        regions = pipeline._build_render_regions([first, second], (60, 60, 3))

        self.assertEqual(len(regions), 2)
        self.assertLessEqual(regions[0].x2, regions[1].x1)

    def test_yolo_exact_composite_is_suppressed_before_partitioning(self):
        pipeline = TranslationPipeline(device="cpu")
        parent = TextBlock(
            [0, 0, 80, 120], text="WA MIKI", translated_text="Oh, it is Miki"
        )
        right = TextBlock([45, 0, 80, 30], text="WA", translated_text="Oh")
        left = TextBlock([0, 30, 40, 120], text="MIKI", translated_text="Miki")
        right.bubble_xyxy = [42, 0, 90, 40]

        regions = pipeline._build_render_regions(
            [parent, left, right], (140, 100, 3)
        )

        self.assertEqual(len(regions), 2)
        self.assertNotIn("Oh, it is Miki", [region.translated_text for region in regions])

    def test_same_translation_sfx_inside_bubble_renders_once(self):
        pipeline = TranslationPipeline(device="cpu")
        sfx = TextBlock(
            [19, 15, 55, 91], text="SAME", translated_text="Same translation"
        )
        text = TextBlock(
            [19, 15, 55, 91], text="SAME", translated_text="Same translation"
        )
        sfx.block_type = "sfx"
        text.bubble_xyxy = [12, 13, 60, 94]

        regions = pipeline._build_render_regions([sfx, text], (110, 80, 3))

        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].translated_text, "Same translation")
        self.assertTrue(getattr(regions[0], "_render_from_bubble", False))

    def test_bubble_group_deduplicates_identical_detector_blocks(self):
        pipeline = TranslationPipeline(device="cpu")
        first = TextBlock([20, 20, 50, 80], text="SAME", translated_text="Once")
        duplicate = TextBlock(
            [20, 20, 50, 80], text="SAME", translated_text="Once"
        )
        first.bubble_xyxy = [10, 10, 60, 90]
        duplicate.bubble_xyxy = [10, 10, 60, 90]

        regions = pipeline._build_render_regions([first, duplicate], (100, 100, 3))

        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].translated_text, "Once")

    def test_nonmatching_nested_regions_are_partitioned_not_suppressed(self):
        pipeline = TranslationPipeline(device="cpu")
        parent = TextBlock([10, 10, 90, 90], text="WHOLE", translated_text="Whole")
        child = TextBlock([30, 30, 70, 70], text="PART", translated_text="Part")
        original_parent = parent.xyxy.copy()
        original_child = child.xyxy.copy()

        regions = pipeline._build_render_regions([parent, child], (100, 100, 3))

        self.assertEqual(len(regions), 2)
        first, second = regions
        overlap_width = max(0, min(first.x2, second.x2) - max(first.x1, second.x1))
        overlap_height = max(0, min(first.y2, second.y2) - max(first.y1, second.y1))
        self.assertEqual(overlap_width * overlap_height, 0)
        np.testing.assert_array_equal(parent.xyxy, original_parent)
        np.testing.assert_array_equal(child.xyxy, original_child)

    def test_partition_discards_weaker_region_when_no_usable_split_exists(self):
        pipeline = TranslationPipeline(device="cpu")
        stronger = TextBlock([10, 10, 30, 30], text="A", translated_text="Keep")
        weaker = TextBlock([10, 10, 30, 30], text="B", translated_text="Drop")
        stronger.confidence = 0.9
        weaker.confidence = 0.4

        regions = pipeline._build_render_regions([weaker, stronger], (50, 50, 3))

        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].translated_text, "Keep")

    def test_render_partition_is_stable_when_input_order_is_reversed(self):
        pipeline = TranslationPipeline(device="cpu")

        def build(reverse=False):
            blocks = [
                TextBlock([10, 10, 60, 70], text="A", translated_text="One"),
                TextBlock([50, 20, 90, 80], text="B", translated_text="Two"),
                TextBlock([40, 65, 80, 95], text="C", translated_text="Three"),
            ]
            if reverse:
                blocks.reverse()
            return pipeline._build_render_regions(blocks, (100, 100, 3))

        forward = [(region.text, region.xyxy.tolist()) for region in build()]
        reversed_regions = [
            (region.text, region.xyxy.tolist()) for region in build(reverse=True)
        ]

        self.assertEqual(forward, reversed_regions)

    def test_explicit_white_background_is_rendered(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.zeros((80, 120, 3), dtype=np.uint8)
        block = TextBlock([10, 10, 110, 70], translated_text="HELLO")

        rendered = pipeline._render_text(
            image.copy(),
            [block],
            text_direction="horizontal",
            bg_color="#ffffff",
            bg_opacity=0.9,
        )

        self.assertGreater(float(rendered[12, 12].mean()), 200)

    def test_short_latin_text_does_not_scale_to_title_size(self):
        pipeline = TranslationPipeline(device="cpu")

        font_size = pipeline._auto_fit_font_size(
            font_path=None,
            text="THAT.",
            width=360,
            height=120,
            line_spacing=1.2,
            align="center",
        )

        self.assertLessEqual(font_size, 28)

    def test_auto_fit_uses_preferred_size_when_it_fits(self):
        pipeline = TranslationPipeline(device="cpu")

        font_size = pipeline._auto_fit_font_size(
            font_path=None,
            text="HELLO",
            width=240,
            height=100,
            line_spacing=1.2,
            align="center",
            preferred_font_size=16,
        )

        self.assertEqual(font_size, 16)

    def test_english_font_resolves_to_bundled_font_regardless_of_cwd(self):
        import os
        import tempfile

        import manga_backend.pipeline as pipeline_module

        bundled = (BACKEND_DIR / "src" / "manga_backend" / "fonts" / "CCWildWords.ttf").resolve()
        self.assertTrue(bundled.is_file())

        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                os.chdir(temp_dir)
                with patch.dict(pipeline_module._font_cache, clear=True):
                    font_path = pipeline_module._get_cached_font("english")
            finally:
                os.chdir(original_cwd)

        self.assertIsNotNone(font_path)
        self.assertEqual(Path(font_path).resolve(), bundled)

    def test_auto_fit_reduces_from_preferred_size_to_largest_fit(self):
        import manga_backend.pipeline as pipeline_module

        pipeline = TranslationPipeline(device="cpu")
        font_path = pipeline_module._get_cached_font("english")

        font_size = pipeline._auto_fit_font_size(
            font_path=font_path,
            text="A translation that needs several wrapped lines",
            width=70,
            height=48,
            line_spacing=1.2,
            align="center",
            stroke_width=1,
            preferred_font_size=16,
        )

        self.assertGreaterEqual(font_size, 6)
        self.assertLess(font_size, 16)

    def test_auto_fit_reduces_size_instead_of_splitting_short_words(self):
        import manga_backend.pipeline as pipeline_module

        pipeline = TranslationPipeline(device="cpu")
        font_path = pipeline_module._get_cached_font("english")

        font_size = pipeline._auto_fit_font_size(
            font_path=font_path,
            text="Hurry up!",
            width=56,
            height=120,
            line_spacing=1.2,
            align="center",
            stroke_width=2,
            preferred_font_size=16,
        )

        self.assertGreater(font_size, 0)
        self.assertLess(font_size, 16)

    def test_font_fit_scales_stroke_and_preserves_words(self):
        import manga_backend.pipeline as pipeline_module

        pipeline = TranslationPipeline(device="cpu")
        font_path = pipeline_module._get_cached_font("english")

        font_size = pipeline._auto_fit_font_size(
            font_path=font_path,
            text="Must be nice—living so close.",
            width=34,
            height=57,
            line_spacing=1.2,
            align="center",
            stroke_width=2,
        )

        self.assertEqual(font_size, 5)

    def test_impossible_region_still_reports_no_font_fit(self):
        pipeline = TranslationPipeline(device="cpu")

        font_size = pipeline._auto_fit_font_size(
            font_path=None,
            text="TOO LARGE",
            width=3,
            height=3,
            line_spacing=1.2,
            align="center",
            stroke_width=2,
        )

        self.assertEqual(font_size, 0)

    def test_auto_fit_can_use_a_tiny_readable_fallback_in_narrow_regions(self):
        import manga_backend.pipeline as pipeline_module

        pipeline = TranslationPipeline(device="cpu")
        font_path = pipeline_module._get_cached_font("english")

        font_size = pipeline._auto_fit_font_size(
            font_path=font_path,
            text="My husband's an engineer. Not much to look at, though.",
            width=20,
            height=33,
            line_spacing=1.2,
            align="center",
            stroke_width=2,
            preferred_font_size=16,
        )

        self.assertGreater(font_size, 0)
        self.assertLess(font_size, 6)

    def test_create_mask_follows_rtdetr_bubble_contour_not_its_rectangle(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((140, 180, 3), 150, dtype=np.uint8)
        cv2.ellipse(image, (90, 70), (55, 45), 0, 0, 360, (255, 255, 255), -1)
        cv2.ellipse(image, (90, 70), (55, 45), 0, 0, 360, (0, 0, 0), 2)
        block = TextBlock([72, 58, 108, 72], translated_text="TEXT")
        block.bubble_xyxy = [33, 23, 147, 117]

        with patch.object(
            pipeline,
            "_glyph_mask_for_block",
            return_value=(np.zeros((1, 1), dtype=np.uint8), (0, 0, 1, 1)),
        ):
            mask = pipeline._create_mask(image, [block])

        self.assertEqual(mask[70, 90], 255)
        self.assertEqual(mask[25, 35], 0)
        self.assertEqual(mask[25, 90], 0)
        self.assertEqual(mask[23, 33], 0)

    def test_yolo_mask_is_exact_clipped_detection_rectangle(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.zeros((30, 40, 3), dtype=np.uint8)
        block = TextBlock([0, 5, 10, 20])

        mask = pipeline._create_mask(image, [block], rectangular=True)

        self.assertTrue(np.all(mask[5:20, 0:10] == 255))
        self.assertFalse(np.any(mask[:5]))
        self.assertFalse(np.any(mask[20:]))
        self.assertFalse(np.any(mask[5:20, 10:]))

    def test_animetext_aliases_normalize_to_yolo_rectangle_route(self):
        pipeline = TranslationPipeline(device="cpu")

        for detector in ("animetext", "animetext_yolo", "anime-text-yolo"):
            self.assertEqual(
                pipeline._normalize_detector_type(detector),
                "animetext-yolo",
            )

    def test_yolo_block_finds_bubble_contour_without_detector_bubble_box(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((140, 180, 3), 150, dtype=np.uint8)
        cv2.ellipse(image, (90, 70), (55, 45), 0, 0, 360, (255, 255, 255), -1)
        cv2.ellipse(image, (90, 70), (55, 45), 0, 0, 360, (0, 0, 0), 2)
        block = TextBlock([72, 58, 108, 72], translated_text="TEXT")

        interior, (x1, y1, _x2, _y2) = (
            pipeline._bubble_interior_mask_for_block(image, block)
        )

        self.assertEqual(interior[70 - y1, 90 - x1], 255)
        self.assertEqual(interior[25 - y1, 35 - x1], 0)

    def test_bubble_contour_ignores_text_connected_near_outline(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((150, 190, 3), 145, dtype=np.uint8)
        cv2.ellipse(image, (95, 75), (58, 52), 0, 0, 360, (255, 255, 255), -1)
        cv2.ellipse(image, (95, 75), (58, 52), 0, 0, 360, (0, 0, 0), 2)
        image[48:82, 133:139] = 0
        block = TextBlock([82, 42, 140, 100], translated_text="TEXT")

        interior, (x1, y1, _x2, _y2) = (
            pipeline._bubble_interior_mask_for_block(image, block)
        )

        self.assertEqual(interior[60 - y1, 135 - x1], 255)
        self.assertEqual(interior[20 - y1, 25 - x1], 0)

    def test_yolo_dialogue_infers_bubble_bounds_for_rendering(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((140, 180, 3), 150, dtype=np.uint8)
        cv2.ellipse(image, (90, 70), (55, 45), 0, 0, 360, (255, 255, 255), -1)
        cv2.ellipse(image, (90, 70), (55, 45), 0, 0, 360, (0, 0, 0), 2)
        dialogue = TextBlock([72, 58, 108, 72], translated_text="OH?")
        sfx = TextBlock([10, 10, 30, 30], block_type="sfx", translated_text="THUD")

        inferred = pipeline._infer_bubble_render_bounds(image, [dialogue, sfx])

        self.assertEqual(inferred, 1)
        bx1, by1, bx2, by2 = dialogue.bubble_xyxy
        self.assertLess(bx1, 72)
        self.assertLess(by1, 58)
        self.assertGreater(bx2, 108)
        self.assertGreater(by2, 72)
        self.assertIsNone(getattr(sfx, "bubble_xyxy", None))

    def test_yolo_rejects_off_center_ui_aggregate_as_bubble(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((120, 160, 3), 128, dtype=np.uint8)
        interior = np.zeros((120, 160), dtype=np.uint8)
        interior[10:110, 10:150] = 255
        block = TextBlock([55, 14, 105, 30], translated_text="MESSAGE")

        with patch.object(
            pipeline,
            "_bubble_interior_mask_for_block",
            return_value=(interior, (0, 0, 160, 120)),
        ):
            inferred = pipeline._infer_bubble_render_bounds(image, [block])

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

        center = TranslationPipeline._bubble_body_center(interior, (0, 0))

        self.assertAlmostEqual(center[0], 60.5, delta=1.0)
        self.assertAlmostEqual(center[1], 60.5, delta=1.0)
        self.assertLess(center[0], 70)

    def test_all_render_paths_center_on_bubble_body_not_tail_bounds(self):
        pipeline = TranslationPipeline(device="cpu")
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

        with patch.object(
            pipeline,
            "_bubble_interior_mask_for_block",
            return_value=(interior, (0, 0, 160, 120)),
        ):
            rendered = pipeline._render_text(
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
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((140, 140, 3), 128, dtype=np.uint8)
        parent = TextBlock([40, 20, 110, 120], text="RIGHTLEFT")
        right = TextBlock(
            [78, 28, 106, 72], text="RIGHT", translated_text="Look"
        )
        left = TextBlock(
            [46, 55, 74, 105], text="LEFT", translated_text="Cosmetics"
        )

        pipeline._infer_bubble_render_bounds(image, [parent, left, right])

        self.assertEqual(right.bubble_xyxy, [80, 24, 112, 76])
        self.assertEqual(left.bubble_xyxy, [40, 50, 80, 110])
        self.assertEqual(
            right._composite_render_group, left._composite_render_group
        )

        regions = pipeline._build_render_regions(
            [parent, left, right], image.shape
        )
        self.assertEqual([region.xyxy.tolist() for region in regions], [
            [80, 24, 112, 76],
            [40, 50, 80, 110],
        ])

    def test_primary_glyph_mask_is_clipped_inside_bubble_outline(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((140, 180, 3), 150, dtype=np.uint8)
        cv2.ellipse(image, (90, 70), (55, 45), 0, 0, 360, (255, 255, 255), -1)
        cv2.ellipse(image, (90, 70), (55, 45), 0, 0, 360, (0, 0, 0), 2)
        image[62:68, 75:105] = 0
        block = TextBlock([60, 22, 120, 92], translated_text="TEXT")
        block.bubble_xyxy = [33, 23, 147, 117]

        mask, (x1, y1, _x2, _y2), strategy = (
            pipeline._contour_safe_mask_for_block(image, block)
        )

        self.assertEqual(strategy, "bubble-clipped-glyph")
        self.assertEqual(mask[62 - y1, 90 - x1], 255)
        self.assertEqual(mask[25 - y1, 90 - x1], 0)

    def test_bubble_mask_keeps_compact_glyph_touching_detector_edge(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((100, 130, 3), 150, dtype=np.uint8)
        cv2.ellipse(image, (65, 50), (45, 38), 0, 0, 360, (255, 255, 255), -1)
        cv2.ellipse(image, (65, 50), (45, 38), 0, 0, 360, (0, 0, 0), 2)
        image[42:50, 88:94] = 0
        block = TextBlock([30, 25, 92, 70], translated_text="TEXT")
        interior = np.zeros((100, 130), dtype=np.uint8)
        cv2.ellipse(interior, (65, 50), (42, 35), 0, 0, 360, 255, -1)

        with patch.object(
            pipeline,
            "_bubble_interior_mask_for_block",
            return_value=(interior, (0, 0, 130, 100)),
        ):
            mask, (x1, y1, _x2, _y2), strategy = (
                pipeline._contour_safe_mask_for_block(image, block)
        )

        self.assertEqual(strategy, "bubble-clipped-glyph")
        self.assertEqual(mask[46 - y1, 90 - x1], 255)

    def test_flux_hybrid_direct_fills_only_dark_glyphs_on_white(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((60, 100, 3), 255, dtype=np.uint8)
        image[25:35, 35:65] = 0
        block = TextBlock([33, 23, 67, 37], translated_text="TEXT")

        cleaned, flux_mask, white_count, textured_count = (
            pipeline._prepare_flux_inpainting(image, [block])
        )

        self.assertEqual(white_count, 1)
        self.assertEqual(textured_count, 0)
        self.assertFalse(np.any(flux_mask))
        self.assertTrue(np.all(cleaned[25:35, 35:65] == 255))
        np.testing.assert_array_equal(cleaned[0:10, 0:10], image[0:10, 0:10])

    def test_flux_yolo_white_region_direct_fills_only_glyphs(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((60, 100, 3), 255, dtype=np.uint8)
        image[25:35, 35:65] = 0
        block = TextBlock([30, 20, 70, 40], translated_text="TEXT")

        cleaned, flux_mask, white_count, textured_count = (
            pipeline._prepare_flux_inpainting(
                image, [block], rectangular=True
            )
        )

        self.assertEqual((white_count, textured_count), (1, 0))
        self.assertFalse(np.any(flux_mask))
        self.assertTrue(np.all(cleaned[25:35, 35:65] == 255))
        np.testing.assert_array_equal(cleaned[0:20, :], image[0:20, :])

    def test_flux_yolo_glyph_mask_direct_fills_white_interior(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((80, 120, 3), 40, dtype=np.uint8)
        image[20:60, 30:90] = 255
        image[32:48, 52:68] = 0
        block = TextBlock([30, 20, 90, 60], translated_text="TEXT")

        cleaned, flux_mask, white_count, textured_count = (
            pipeline._prepare_flux_inpainting(
                image, [block], rectangular=True
            )
        )

        self.assertEqual((white_count, textured_count), (1, 0))
        self.assertFalse(np.any(flux_mask))
        self.assertTrue(np.all(cleaned[32:48, 52:68] == 255))
        np.testing.assert_array_equal(cleaned[0:18, :], image[0:18, :])

    def test_flux_yolo_uses_glyph_mask_within_detection_box(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((80, 120, 3), 255, dtype=np.uint8)
        image[30:36, 40:50] = 0
        image[24:28, 76:84] = 180
        block = TextBlock([30, 20, 90, 60], translated_text="TEXT")
        cleaned, flux_mask, white_count, textured_count = (
            pipeline._prepare_flux_inpainting(
                image, [block], rectangular=True
            )
        )

        self.assertEqual((white_count, textured_count), (1, 0))
        self.assertFalse(np.any(flux_mask))
        self.assertTrue(np.all(cleaned[30:36, 40:50] == 255))
        self.assertTrue(np.any(cleaned[24:28, 76:84] == 180))

    def test_flux_yolo_keeps_textured_glyphs_for_flux(self):
        pipeline = TranslationPipeline(device="cpu")
        yy, xx = np.indices((80, 120))
        pattern = np.where((xx + yy) % 2 == 0, 160, 205).astype(np.uint8)
        image = np.repeat(pattern[:, :, None], 3, axis=2)
        image[32:48, 52:68] = 0
        block = TextBlock([30, 20, 90, 60], translated_text="TEXT")
        cleaned, flux_mask, white_count, textured_count = (
            pipeline._prepare_flux_inpainting(
                image, [block], rectangular=True
            )
        )

        self.assertEqual((white_count, textured_count), (0, 1))
        self.assertTrue(np.all(flux_mask[32:48, 52:68] == 255))
        self.assertLess(np.count_nonzero(flux_mask), 60 * 40)
        np.testing.assert_array_equal(cleaned, image)

    def test_flux_yolo_masks_light_glyphs_on_dark_ui(self):
        pipeline = TranslationPipeline(device="cpu")
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

        _cleaned, flux_mask, white_count, textured_count = (
            pipeline._prepare_flux_inpainting(
                image, [block], rectangular=True
            )
        )

        self.assertEqual((white_count, textured_count), (0, 1))
        self.assertTrue(np.any(flux_mask[18:42, 30:84]))
        self.assertFalse(np.any(flux_mask[0:12, :]))

    def test_flux_yolo_preserves_bubble_outline_crossing_detection_box(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((100, 120, 3), 255, dtype=np.uint8)
        cv2.ellipse(image, (70, 50), (32, 40), 0, 0, 360, (0, 0, 0), 2)
        outline = np.all(image == 0, axis=2)
        image[35:65, 55:75] = 0
        block = TextBlock([35, 10, 78, 90], translated_text="TEXT")

        _cleaned, flux_mask, white_count, textured_count = (
            pipeline._prepare_flux_inpainting(
                image, [block], rectangular=True
            )
        )

        self.assertEqual((white_count, textured_count), (1, 0))
        self.assertFalse(np.any(flux_mask))
        self.assertFalse(np.any(np.all(_cleaned[outline] == 255, axis=1)))

    def test_flux_hybrid_queues_glyph_mask_for_textured_region(self):
        pipeline = TranslationPipeline(device="cpu")
        yy, xx = np.indices((60, 100))
        pattern = np.where((xx + yy) % 2 == 0, 160, 205).astype(np.uint8)
        image = np.repeat(pattern[:, :, None], 3, axis=2)
        image[25:35, 35:65] = 0
        block = TextBlock([33, 23, 67, 37], translated_text="TEXT")

        cleaned, flux_mask, white_count, textured_count = (
            pipeline._prepare_flux_inpainting(image, [block])
        )

        self.assertEqual(white_count, 0)
        self.assertEqual(textured_count, 1)
        self.assertTrue(np.any(flux_mask[23:37, 33:67]))
        self.assertLess(np.count_nonzero(flux_mask), 34 * 14)
        np.testing.assert_array_equal(cleaned, image)

    def test_flux_hybrid_preserves_white_region_line_art(self):
        pipeline = TranslationPipeline(device="cpu")
        image = np.full((60, 100, 3), 255, dtype=np.uint8)
        image[20, 20:80] = 0
        image[27:33, 42:58] = 0
        block = TextBlock([20, 18, 80, 35], translated_text="TEXT")

        cleaned, flux_mask, white_count, textured_count = (
            pipeline._prepare_flux_inpainting(image, [block])
        )

        self.assertEqual((white_count, textured_count), (1, 0))
        self.assertFalse(np.any(flux_mask))
        self.assertTrue(np.all(cleaned[20, 20:80] == 0))
        self.assertTrue(np.all(cleaned[27:33, 42:58] == 255))

    def test_unchanged_punctuation_is_not_rendered_again(self):
        pipeline = TranslationPipeline(device="cpu")
        block = TextBlock([10, 10, 30, 25], text="...", translated_text="...")

        regions = pipeline._build_render_regions([block], (60, 80, 3))

        self.assertEqual(regions, [])

    def test_translated_punctuation_with_words_is_rendered(self):
        pipeline = TranslationPipeline(device="cpu")
        block = TextBlock([10, 10, 40, 30], text="？", translated_text="WHAT?")

        regions = pipeline._build_render_regions([block], (60, 80, 3))

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
            "manga_backend.pipeline.Inpainter", BrokenInpainter
        ):
            with self.assertRaises(InpainterUnavailableError) as raised:
                await pipeline._get_inpainter(inpainter_type)
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

        pipeline._get_inpainter = get_inpainter
        return pipeline, attempts

    async def test_unavailable_lanpaint_falls_back_to_lama(self):
        pipeline, attempts = self.make_pipeline()

        _model, loaded = await pipeline._get_inpainter_or_fallback("flux2_klein_lanpaint")

        self.assertEqual(loaded, "lama_large")
        self.assertEqual(attempts, ["flux2_klein_lanpaint", "lama_large"])

    async def test_failed_runtime_is_not_probed_again_until_the_retry_time(self):
        pipeline, attempts = self.make_pipeline()

        await pipeline._get_inpainter_or_fallback("flux2_klein_lanpaint")
        await pipeline._get_inpainter_or_fallback("flux2_klein_lanpaint")
        self.assertEqual(attempts, ["flux2_klein_lanpaint", "lama_large", "lama_large"])

        pipeline._inpainter_retry_at["flux2_klein_lanpaint"] = 0.0
        await pipeline._get_inpainter_or_fallback("flux2_klein_lanpaint")
        self.assertEqual(attempts[-2:], ["flux2_klein_lanpaint", "lama_large"])

    async def test_fallback_can_be_switched_off(self):
        pipeline, attempts = self.make_pipeline()

        with patch.dict("os.environ", {"INPAINTER_FALLBACK": "off"}):
            with self.assertRaises(InpainterUnavailableError):
                await pipeline._get_inpainter_or_fallback("flux2_klein_lanpaint")

        self.assertEqual(attempts, ["flux2_klein_lanpaint"])

    async def test_inpainters_without_a_fallback_still_fail(self):
        pipeline = TranslationPipeline(device="cpu")

        async def get_inpainter(inpainter_type):
            raise InpainterUnavailableError("missing", inpainter=inpainter_type)

        pipeline._get_inpainter = get_inpainter

        for inpainter_type in ("lama_large", "flux2_klein_q4"):
            with self.assertRaises(InpainterUnavailableError):
                await pipeline._get_inpainter_or_fallback(inpainter_type)

    async def test_translate_reports_the_inpainter_that_actually_ran(self):
        pipeline, _attempts = self.make_pipeline()
        image = np.full((80, 120, 3), 128, dtype=np.uint8)
        image[30:38, 40:70] = 0
        block = TextBlock([30, 22, 80, 46])

        class FakeDetector:
            async def detect(self, _image):
                return [block]

        pipeline._detector = FakeDetector()
        pipeline._ocr = FakeOcrEngine()

        with patch.object(pipeline, "_render_text", return_value=image):
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

        pipeline._get_inpainter = get_inpainter

        # First request probes ComfyUI; the second is inside the retry window.
        for _attempt in range(2):
            with self.assertRaises(InpainterUnavailableError) as raised:
                await pipeline._get_inpainter_or_fallback("flux2_klein_lanpaint")

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
        self.enterContext(patch("manga_backend.pipeline.Inpainter", FakeLama))

        self.image = np.full((80, 120, 3), 128, dtype=np.uint8)
        self.image[30:38, 40:70] = 0
        block = TextBlock([30, 22, 80, 46])

        class FakeDetector:
            async def detect(self, _image):
                return [block]

        pipeline._detector = FakeDetector()
        pipeline._ocr = FakeOcrEngine()
        return pipeline

    async def translate(self, pipeline):
        with patch.object(pipeline, "_render_text", return_value=self.image):
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
        self.assertIs(pipeline._inpainter, self.lamas[0])
        self.assertEqual(
            pipeline.model_status["inpainter"],
            {"status": "loaded", "name": "lama_large"},
        )
        self.assertGreater(
            pipeline._inpainter_retry_at["flux2_klein_lanpaint"], time.monotonic()
        )

        # Inside the retry window ComfyUI is left alone.
        again = await self.translate(pipeline)
        self.assertEqual(again["inpainter_used"], "lama_large")
        self.assertEqual(len(self.lanpaints), 1)

        self.daemon_up = True
        pipeline._inpainter_retry_at["flux2_klein_lanpaint"] = 0.0
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
            self.assertIsNone(pipeline._inpainter)
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
        self.assertIs(pipeline._inpainter, loaded)
        self.assertFalse(loaded.disposed)
        self.assertEqual(pipeline._inpainter_retry_at, {})
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
        self.assertIsNone(pipeline._inpainter)

        self.daemon_up = True
        pipeline._inpainter_retry_at["flux2_klein_lanpaint"] = 0.0
        result = await self.translate(pipeline)
        self.assertEqual(result["inpainter_used"], "flux2_klein_lanpaint")
        self.assertEqual(len(self.lanpaints), 2)


class InpainterWarmUpTests(unittest.IsolatedAsyncioTestCase):
    """Startup loads the inpainter the first page will need."""

    def make_pipeline(self, inpainter_type=DEFAULT_INPAINTER, fail=False):
        pipeline = TranslationPipeline(device="cpu")
        pipeline._inpainter_type = inpainter_type
        loaded = []

        async def get_inpainter(requested):
            loaded.append(requested)
            if fail:
                raise InpainterUnavailableError("no model", inpainter=requested)
            return object()

        pipeline._get_inpainter = get_inpainter
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
        ready_pipeline._inpainter = object()

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

        class FakeDetector:
            async def detect(self, _image):
                return [block]

        pipeline = TranslationPipeline(device="cpu")
        pipeline._detector = FakeDetector()
        pipeline._ocr = FakeOcrEngine()

        # An empty mask skips inpainter initialization entirely.
        outputs = {
            "_create_mask": np.zeros(image.shape[:2], dtype=np.uint8),
            "_render_text": image,
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
            with patch.multiple(pipeline, **overrides):
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
        responsive, health = self.run_translate_with_blocked_step("_create_mask")

        self.assertTrue(responsive)
        self.assertEqual(health, "ok")

    def test_text_rendering_does_not_block_the_event_loop(self):
        responsive, health = self.run_translate_with_blocked_step("_render_text")

        self.assertTrue(responsive)
        self.assertEqual(health, "ok")


class FontObjectCacheTests(unittest.TestCase):
    def test_concurrent_requests_load_each_font_once(self):
        import manga_backend.pipeline as pipeline_module

        calls = []
        barrier = threading.Barrier(6)

        def slow_truetype(path, size):
            calls.append((path, size))
            time.sleep(0.05)
            return object()

        def worker(results):
            barrier.wait()
            results.append(pipeline_module._get_font_object("fake.ttf", 17))

        results = []
        with patch.dict(pipeline_module._font_object_cache, clear=True), patch(
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


if __name__ == "__main__":
    unittest.main()
