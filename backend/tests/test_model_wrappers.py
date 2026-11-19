"""Model-free tests for backend model wrapper helper behavior."""

import asyncio
import subprocess
import threading
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import numpy as np


BACKEND_DIR = Path(__file__).resolve().parents[1]

from manga_backend.models.detector import TextDetector
from manga_backend.models.inpainter import Inpainter
from manga_backend.models.flux_klein_inpainter import FluxKleinInpainter
from test_support import writable_temp_directory
from manga_backend.models.ocr import OCREngine
from manga_backend.models.paddle_ocr_onnx import PaddleOCROnnxEngine, _sort_box_points

from manga_backend.utils.download import MODEL_PINS
from manga_backend.utils.textblock import TextBlock

class FakeInput:
    def __init__(self, name):
        self.name = name


class FakeInpaintSession:
    def get_inputs(self):
        return [FakeInput("image"), FakeInput("mask")]

    def run(self, _outputs, inputs):
        image_tensor = inputs["image"]
        return [np.zeros_like(image_tensor, dtype=np.float32) + 0.5]


class FakeOcrEncoderSession:
    def __init__(self):
        self.calls = 0

    def run(self, _outputs, inputs):
        self.calls += 1
        batch_size = inputs["pixels"].shape[0]
        return [np.zeros((batch_size, 197, 4), dtype=np.float32)]


class FakeOcrDecoderSession:
    def __init__(self):
        self.calls = 0

    def run(self, _outputs, inputs):
        self.calls += 1
        token_ids = inputs["tokens"]
        batch_size, sequence_length = token_ids.shape
        logits = np.zeros((batch_size, sequence_length, 8), dtype=np.float32)
        if sequence_length == 1:
            for index in range(batch_size):
                logits[index, -1, 5 + index] = 1.0
        else:
            logits[:, -1, OCREngine.EOS_TOKEN] = 1.0
        return [logits]


def run_blocked_calls(coroutine_factories, started, release, before_release=None):
    "Run blocked inference calls and deterministically prove the loop can advance."
    watchdog_fired = threading.Event()

    def unblock_on_timeout():
        watchdog_fired.set()
        release.set()

    watchdog = threading.Timer(3.0, unblock_on_timeout)
    watchdog.start()

    async def run_requests():
        tasks = [
            asyncio.create_task(coroutine_factory())
            for coroutine_factory in coroutine_factories
        ]
        started_in_time = await asyncio.to_thread(started.wait, 2.0)
        await asyncio.sleep(0)
        observation = before_release() if before_release else None
        responsive = started_in_time and not watchdog_fired.is_set()
        release.set()
        results = await asyncio.gather(*tasks)
        return responsive, observation, results

    try:
        return asyncio.run(run_requests())
    finally:
        release.set()
        watchdog.cancel()
        watchdog.join()


class OcrWrapperTests(unittest.TestCase):
    def test_find_name_is_case_insensitive(self):
        engine = OCREngine()

        result = engine._find_name(["PIXEL_VALUES", "TOKEN_IDS"], ["pixel_values"])

        self.assertEqual(result, "PIXEL_VALUES")

    def test_preprocess_returns_normalized_encoder_tensor(self):
        engine = OCREngine()
        image = np.full((12, 24, 3), 255, dtype=np.uint8)

        result = engine._preprocess(image)

        self.assertEqual(result.shape, (1, 3, 224, 224))
        self.assertEqual(result.dtype, np.float32)
        self.assertTrue(np.allclose(result, 1.0))

    def test_decode_skips_special_and_out_of_range_tokens(self):
        engine = OCREngine()
        engine.vocab = ["pad", "x", "bos", "eos", "skip", "ã‚", "ã„"]

        result = engine._decode([2, 5, 999, 6, 3])

        self.assertEqual(result, "ã‚ã„")

    def test_generate_batch_amortizes_encoder_and_decoder_runs(self):
        engine = OCREngine(device="cuda")
        engine.encoder = FakeOcrEncoderSession()
        engine.decoder = FakeOcrDecoderSession()
        engine.encoder_image_input = "pixels"
        engine.decoder_token_input = "tokens"
        engine.decoder_encoder_input = "hidden"
        images = np.zeros((2, 3, 224, 224), dtype=np.float32)

        result = engine._generate_batch(images)

        self.assertEqual(result, [[engine.BOS_TOKEN, 5, engine.EOS_TOKEN], [engine.BOS_TOKEN, 6, engine.EOS_TOKEN]])
        self.assertEqual(engine.encoder.calls, 1)
        self.assertEqual(engine.decoder.calls, 2)

    def generate_with_script(self, scripts):
        """Greedy-decode crops whose next tokens come from per-crop scripts.

        A script is a token list (EOS follows its end) or a function of the step.
        The encoder tags each crop's hidden state with the crop's index, so the
        decoder knows which crops are still in the batch, records them per call
        and checks that every row carries exactly that crop's token history.
        Returns the decoded sequences and the decoder.
        """
        test = self

        def scripted_token(crop, step):
            script = scripts[crop]
            if callable(script):
                return script(step)
            return script[step] if step < len(script) else OCREngine.EOS_TOKEN

        class TaggingEncoder:
            def run(self, _outputs, inputs):
                batch_size = inputs["pixels"].shape[0]
                hidden = np.zeros((batch_size, 4, 4), dtype=np.float32)
                hidden[:, 0, 0] = np.arange(batch_size)
                return [hidden]

        class ScriptedDecoder:
            def __init__(self):
                self.calls = 0
                self.rows = []

            def run(self, _outputs, inputs):
                self.calls += 1
                tokens = inputs["tokens"]
                crops = inputs["hidden"][:, 0, 0].astype(int).tolist()
                self.rows.append(crops)
                step = tokens.shape[1] - 1
                logits = np.zeros((len(crops), step + 1, 16), dtype=np.float32)
                for row, crop in enumerate(crops):
                    history = [scripted_token(crop, earlier) for earlier in range(step)]
                    test.assertEqual(tokens[row].tolist(), [OCREngine.BOS_TOKEN, *history])
                    logits[row, -1, scripted_token(crop, step)] = 1.0
                return [logits]

        engine = OCREngine(device="cuda")
        engine.encoder = TaggingEncoder()
        engine.decoder = ScriptedDecoder()
        engine.encoder_image_input = "pixels"
        engine.decoder_token_input = "tokens"
        engine.decoder_encoder_input = "hidden"
        images = np.zeros((len(scripts), 3, 8, 8), dtype=np.float32)
        return engine._generate_batch(images), engine.decoder

    def test_long_single_token_run_keeps_the_prefix_and_four_copies(self):
        # Like うおおおお... with 30 お: the scream keeps its shape instead of becoming うお.
        result, decoder = self.generate_with_script([[5, 6] + [7] * 30])

        self.assertEqual(result, [[OCREngine.BOS_TOKEN, 5, 6, 7, 7, 7, 7]])
        # Decoding stopped once 20 copies were read, not at the end of the run.
        self.assertEqual(decoder.calls, 2 + OCREngine.REPEAT_LIMIT)

    def test_runaway_without_end_is_cut_to_four_copies(self):
        result, decoder = self.generate_with_script([lambda step: 7])

        self.assertEqual(result, [[OCREngine.BOS_TOKEN, 7, 7, 7, 7]])
        self.assertEqual(decoder.calls, OCREngine.REPEAT_LIMIT)

    def test_repeated_two_token_cycle_keeps_four_copies_of_the_cycle(self):
        result, decoder = self.generate_with_script([[5] + [8, 9] * 12 + [6]])

        self.assertEqual(result, [[OCREngine.BOS_TOKEN, 5, 8, 9, 8, 9, 8, 9, 8, 9]])
        self.assertEqual(decoder.calls, 1 + OCREngine.REPEAT_LIMIT)

    def test_run_just_below_the_limit_is_kept_whole_and_decoding_continues(self):
        run = [9] * 19
        result, decoder = self.generate_with_script([[5] + run + [6]])

        self.assertEqual(result, [[OCREngine.BOS_TOKEN, 5, *run, 6, OCREngine.EOS_TOKEN]])
        self.assertEqual(decoder.calls, len(run) + 3)

    def test_batch_keeps_decoding_other_crops_after_one_stops_on_repetition(self):
        varied = list(range(5, 16)) * 3  # period 11: never treated as a runaway
        scripts = [[5, 6] + [7] * 30, varied, [12]]

        result, decoder = self.generate_with_script(scripts)

        self.assertEqual(result, [
            [OCREngine.BOS_TOKEN, 5, 6, 7, 7, 7, 7],
            [OCREngine.BOS_TOKEN, *varied, OCREngine.EOS_TOKEN],
            [OCREngine.BOS_TOKEN, 12, OCREngine.EOS_TOKEN],
        ])
        # Crop 2 leaves on EOS, crop 0 on the repetition stop, crop 1 runs to EOS.
        self.assertEqual(decoder.rows, [[0, 1, 2]] * 2 + [[0, 1]] * 20 + [[1]] * 12)

    def test_postprocess_spells_out_the_ellipsis_character(self):
        engine = OCREngine()

        result = engine._postprocess("え……")

        self.assertNotIn("…", result)
        self.assertEqual(result, engine._postprocess("え......"))

    def test_ocr_batch_honors_bounded_chunk_size(self):
        engine = OCREngine(device="cuda")
        engine.batch_size = 2
        engine.encoder = FakeOcrEncoderSession()
        engine.decoder = FakeOcrDecoderSession()
        engine.encoder_image_input = "pixels"
        engine.decoder_token_input = "tokens"
        engine.decoder_encoder_input = "hidden"
        engine.vocab = ["", "", "", "", "", "A", "B", "C"]
        images = [np.zeros((12, 24, 3), dtype=np.uint8) for _ in range(3)]

        result = engine._ocr_batch(images)

        self.assertEqual(result, [engine._postprocess(value) for value in ["A", "B", "A"]])
        self.assertEqual(engine.encoder.calls, 2)
        self.assertEqual(engine.decoder.calls, 4)

    def test_process_offloads_and_serializes_inference(self):
        engine = OCREngine()
        engine.encoder = object()
        engine.decoder = object()
        state_lock = threading.Lock()
        state = {"active": 0, "max_active": 0, "threads": set()}
        started = threading.Event()
        release = threading.Event()

        def blocking_ocr(images):
            with state_lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
                state["threads"].add(threading.get_ident())
            started.set()
            try:
                if not release.wait(5.0):
                    raise TimeoutError('OCR inference test was not released')
            finally:
                with state_lock:
                    state["active"] -= 1
            return ["text"] * len(images)

        engine._ocr_batch = blocking_ocr
        image = np.zeros((12, 24, 3), dtype=np.uint8)
        blocks = [TextBlock([0, 0, 12, 12])]
        main_thread = threading.get_ident()

        responsive, lock_held, results = run_blocked_calls(
            [lambda: engine.process(image, blocks), lambda: engine.process(image, blocks)],
            started, release, before_release=engine._inference_lock.locked,
        )

        self.assertTrue(responsive)
        self.assertEqual(results, [["text"], ["text"]])
        self.assertEqual(state["max_active"], 1)
        self.assertTrue(lock_held)
        self.assertNotIn(main_thread, state["threads"])

    def test_process_falls_back_per_region_when_batch_fails(self):
        engine = OCREngine()
        engine.encoder = object()
        engine.decoder = object()
        calls = []

        def fail_batch(_images):
            raise RuntimeError("batch failed")

        def individual(_image):
            calls.append(len(calls))
            if len(calls) == 2:
                raise RuntimeError("region failed")
            return "text"

        engine._ocr_batch = fail_batch
        engine._ocr_single = individual
        image = np.zeros((12, 24, 3), dtype=np.uint8)
        blocks = [TextBlock([0, 0, 12, 12]), TextBlock([12, 0, 24, 12])]

        result = asyncio.run(engine.process(image, blocks))

        self.assertEqual(result, ["text", ""])
        self.assertEqual(blocks[0].text, "text")
        self.assertEqual(blocks[1].text, "")



class InpainterWrapperTests(unittest.TestCase):
    def test_initialize_none_marks_engine_initialized_without_model_session(self):
        inpainter = Inpainter(inpainter_type="none")

        asyncio.run(inpainter.initialize())

        self.assertTrue(inpainter._initialized)
        self.assertIsNone(inpainter.session)

    def test_inpaint_handles_one_pixel_images_without_reflect_padding_failure(self):
        inpainter = Inpainter()
        inpainter.session = FakeInpaintSession()
        image = np.full((1, 1, 3), 255, dtype=np.uint8)
        mask = np.full((1, 1), 255, dtype=np.uint8)

        result = asyncio.run(inpainter.inpaint(image, mask))

        self.assertEqual(result.shape, (1, 1, 3))
        self.assertEqual(result.dtype, np.uint8)
        self.assertTrue(np.all(result == 127))

    def test_inpaint_rejects_mask_shape_mismatch(self):
        inpainter = Inpainter()
        inpainter.session = FakeInpaintSession()
        image = np.zeros((8, 8, 3), dtype=np.uint8)
        mask = np.zeros((4, 4), dtype=np.uint8)

        with self.assertRaises(ValueError):
            asyncio.run(inpainter.inpaint(image, mask))

    def test_normalize_mask_uses_binary_channel(self):
        inpainter = Inpainter()
        mask = np.array([[0, 128], [255, 1]], dtype=np.uint8)

        result = inpainter._normalize_mask(mask)

        self.assertEqual(result.shape, (1, 2, 2))
        self.assertEqual(result.tolist(), [[[0.0, 1.0], [1.0, 1.0]]])

    def test_flux_q4_commits_only_pixels_inside_binary_mask(self):
        inpainter = FluxKleinInpainter()
        inpainter._initialized = True
        image = np.arange(8 * 8 * 3, dtype=np.uint8).reshape(8, 8, 3)
        mask = np.zeros((8, 8), dtype=np.uint8)
        mask[2:6, 3:5] = 255
        generated = np.full_like(image, 240)

        with patch(
            "manga_backend.models.flux_klein_inpainter.asyncio.to_thread",
            new=AsyncMock(return_value=generated),
        ):
            result = asyncio.run(inpainter.inpaint(image, mask))

        np.testing.assert_array_equal(result[mask == 0], image[mask == 0])
        np.testing.assert_array_equal(result[mask > 0], generated[mask > 0])


    def test_inpaint_offloads_and_serializes_inference(self):
        class BlockingSession(FakeInpaintSession):
            def __init__(self):
                self.active = 0
                self.max_active = 0
                self.threads = set()
                self.lock = threading.Lock()
                self.started = threading.Event()
                self.release = threading.Event()

            def run(self, outputs, inputs):
                with self.lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                    self.threads.add(threading.get_ident())
                self.started.set()
                try:
                    if not self.release.wait(5.0):
                        raise TimeoutError("Inpainter inference test was not released")
                    return super().run(outputs, inputs)
                finally:
                    with self.lock:
                        self.active -= 1

        inpainter = Inpainter()
        session = BlockingSession()
        inpainter.session = session
        image = np.zeros((8, 8, 3), dtype=np.uint8)
        mask = np.zeros((8, 8), dtype=np.uint8)
        mask[2:4, 2:4] = 255
        main_thread = threading.get_ident()

        responsive, lock_held, results = run_blocked_calls(
            [lambda: inpainter.inpaint(image, mask), lambda: inpainter.inpaint(image, mask)],
            session.started, session.release, before_release=inpainter._inference_lock.locked,
        )

        self.assertTrue(responsive)
        self.assertTrue(lock_held)
        self.assertEqual(len(results), 2)
        self.assertEqual(session.max_active, 1)
        self.assertNotIn(main_thread, session.threads)

class RecordingInpaintSession(FakeInpaintSession):
    def __init__(self):
        self.shapes = []
        self.batch_sizes = []
        self.masked_pixels = []

    def run(self, outputs, inputs):
        self.shapes.append(inputs["image"].shape[2:])
        self.batch_sizes.append(inputs["image"].shape[0])
        self.masked_pixels.append(int(inputs["mask"].sum()))
        return super().run(outputs, inputs)


class InpainterTileTests(unittest.TestCase):
    """LaMa runs on aligned contextual crops, not the full page.

    A crop too large for the device pixel budget still falls back to equal
    tiles, so GPU pages with page-scale masks keep working.
    """

    def inpaint(self, image, mask, max_pixels=None):
        inpainter = Inpainter()
        inpainter.session = RecordingInpaintSession()
        inpainter.max_pixels = max_pixels
        result = asyncio.run(inpainter.inpaint(image, mask))
        return result, inpainter.session

    def test_small_mask_runs_one_crop_instead_of_the_whole_page(self):
        rng = np.random.default_rng(0)
        image = rng.integers(0, 255, (1000, 1280, 3), dtype=np.uint8)
        mask = np.zeros((1000, 1280), dtype=np.uint8)
        mask[700:730, 500:560] = 255

        result, session = self.inpaint(image, mask, max_pixels=1_300_000)

        self.assertEqual(len(session.shapes), 1)
        height, width = session.shapes[0]
        self.assertEqual((height % 8, width % 8), (0, 0))
        self.assertLess(height * width, 1000 * 1280)
        np.testing.assert_array_equal(result[mask == 0], image[mask == 0])
        self.assertTrue(np.all(result[mask > 0] == 127))

    def test_crop_carries_at_least_64_px_of_context(self):
        rng = np.random.default_rng(2)
        image = rng.integers(0, 255, (600, 600, 3), dtype=np.uint8)
        mask = np.zeros((600, 600), dtype=np.uint8)
        mask[290:310, 290:310] = 255

        result, session = self.inpaint(image, mask)

        # A 20 px glyph asks for a 64 px margin, so the aligned crop is at
        # least (20 + 2 * 64) px per side before padding.
        self.assertEqual(len(session.shapes), 1)
        height, width = session.shapes[0]
        self.assertGreaterEqual(height, 152)
        self.assertGreaterEqual(width, 152)
        np.testing.assert_array_equal(result[mask == 0], image[mask == 0])
        self.assertTrue(np.all(result[mask > 0] == 127))

    def test_equal_shaped_crops_share_one_batched_run(self):
        rng = np.random.default_rng(3)
        image = rng.integers(0, 255, (1000, 1280, 3), dtype=np.uint8)
        mask = np.zeros((1000, 1280), dtype=np.uint8)
        mask[100:140, 200:240] = 255
        mask[700:740, 900:940] = 255

        result, session = self.inpaint(image, mask)

        self.assertEqual(len(session.shapes), 1)
        self.assertEqual(session.batch_sizes, [2])
        self.assertEqual(session.masked_pixels, [2 * 40 * 40])
        np.testing.assert_array_equal(result[mask == 0], image[mask == 0])
        self.assertTrue(np.all(result[mask > 0] == 127))

    def test_page_scale_mask_is_split_into_equal_tiles_under_the_limit(self):
        image = np.zeros((3000, 2400, 3), dtype=np.uint8)
        mask = np.full((3000, 2400), 255, dtype=np.uint8)

        result, session = self.inpaint(image, mask, max_pixels=1_000_000)

        self.assertEqual(len(set(session.shapes)), 1)
        height, width = session.shapes[0]
        self.assertLessEqual(height * width, 1_000_000)
        self.assertLess(height, 3000)
        self.assertLess(width, 2400)
        self.assertTrue(np.all(result == 127))

    def test_odd_sized_page_is_padded_and_cropped_back(self):
        image = np.zeros((1001, 603, 3), dtype=np.uint8)
        mask = np.zeros((1001, 603), dtype=np.uint8)
        mask[990:1001, 590:603] = 255

        result, session = self.inpaint(image, mask, max_pixels=400_000)

        self.assertEqual(result.shape, image.shape)
        for height, width in session.shapes:
            self.assertEqual((height % 8, width % 8), (0, 0))
        self.assertTrue(np.all(result[mask > 0] == 127))
        self.assertTrue(np.all(result[mask == 0] == 0))

    def test_empty_mask_skips_the_model(self):
        image = np.full((64, 64, 3), 9, dtype=np.uint8)

        result, session = self.inpaint(image, np.zeros((64, 64), dtype=np.uint8))

        self.assertEqual(session.shapes, [])
        np.testing.assert_array_equal(result, image)
        self.assertIsNot(result, image)

    def test_gpu_square_buckets_batch_mismatched_crops_into_one_run(self):
        rng = np.random.default_rng(7)
        image = rng.integers(0, 255, (1000, 1280, 3), dtype=np.uint8)
        mask = np.zeros((1000, 1280), dtype=np.uint8)
        # Two differently-sized regions that both fit inside the 256x256 GPU bucket.
        mask[100:130, 200:240] = 255
        mask[700:748, 900:920] = 255

        inpainter = Inpainter(device="cuda")
        inpainter.session = RecordingInpaintSession()
        inpainter.max_pixels = 1_000_000
        result = asyncio.run(inpainter.inpaint(image, mask))

        self.assertEqual(inpainter.session.shapes, [(256, 256)])
        self.assertEqual(inpainter.session.batch_sizes, [2])
        np.testing.assert_array_equal(result[mask == 0], image[mask == 0])
        self.assertTrue(np.all(result[mask > 0] == 127))


class InpainterPixelLimitTests(unittest.TestCase):
    def resolve(self, device, total_gib, env=""):
        from manga_backend.models import inpainter as inpainter_module

        with patch.dict("os.environ", {"LAMA_MAX_PIXELS": env}), patch.object(
            inpainter_module, "_gpu_total_gib", return_value=total_gib
        ):
            return inpainter_module.resolve_max_pixels(device)

    def test_cpu_has_no_limit(self):
        self.assertIsNone(self.resolve("cpu", 6.0))

    def test_limit_follows_gpu_memory(self):
        six, twelve = self.resolve("cuda", 6.0), self.resolve("cuda", 12.0)

        self.assertTrue(1_100_000 <= six <= 1_250_000)
        self.assertGreater(twelve, 2 * six)

    def test_small_or_unknown_gpu_gets_a_safe_default(self):
        self.assertEqual(self.resolve("cuda", 2.0), 400_000)
        self.assertEqual(self.resolve("cuda", None), 1_000_000)

    def test_env_override_wins_and_zero_removes_the_limit(self):
        self.assertEqual(self.resolve("cuda", 6.0, env="750000"), 750_000)
        self.assertIsNone(self.resolve("cuda", 6.0, env="0"))
        self.assertEqual(self.resolve("cpu", 6.0, env="750000"), 750_000)

    def test_invalid_env_value_is_ignored(self):
        self.assertEqual(self.resolve("cuda", None, env="lots"), 1_000_000)

    def test_gpu_memory_is_queried_once_across_initializations(self):
        from manga_backend.models import inpainter as inpainter_module

        inpainter_module._gpu_total_gib.cache_clear()
        self.addCleanup(inpainter_module._gpu_total_gib.cache_clear)
        nvidia_smi = subprocess.CompletedProcess([], 0, stdout="6144\n", stderr="")
        limits = []
        with writable_temp_directory() as model_dir, patch.dict(
            "os.environ", {"LAMA_MAX_PIXELS": ""}
        ), patch.object(
            inpainter_module, "download_lama_onnx", new=AsyncMock(return_value="lama.onnx")
        ), patch.object(
            inpainter_module, "get_onnx_providers", return_value=["CPUExecutionProvider"]
        ), patch.object(
            inpainter_module, "create_onnx_session", return_value=FakeInpaintSession()
        ), patch.object(
            inpainter_module.subprocess, "run", return_value=nvidia_smi
        ) as run:
            for _ in range(2):
                inpainter = Inpainter(device="cuda")
                inpainter.model_dir = Path(model_dir)
                asyncio.run(inpainter.initialize())
                limits.append(inpainter.max_pixels)

        run.assert_called_once()
        # 6 GiB: (6 - 2.5 reserved) / 3 GiB per megapixel.
        self.assertEqual(limits, [1_166_666, 1_166_666])


class DetectorWrapperTests(unittest.TestCase):
    def test_detect_offloads_inference(self):
        started = threading.Event()
        release = threading.Event()

        class BlockingSession:
            def __init__(self):
                self.threads = set()

            def run(self, _outputs, _inputs):
                self.threads.add(threading.get_ident())
                started.set()
                if not release.wait(5.0):
                    raise TimeoutError("Detector inference test was not released")
                return [
                    np.array([1], dtype=np.int64),
                    np.array([[1, 2, 10, 11]], dtype=np.float32),
                    np.array([0.9], dtype=np.float32),
                ]

        detector = TextDetector()
        session = BlockingSession()
        detector.session = session
        image = np.zeros((12, 24, 3), dtype=np.uint8)
        main_thread = threading.get_ident()

        responsive, _observation, results = run_blocked_calls(
            [lambda: detector.detect(image)],
            started,
            release,
        )

        self.assertTrue(responsive)
        self.assertEqual(len(results[0]), 1)
        self.assertEqual(results[0][0].xyxy.tolist(), [1, 2, 10, 11])
        self.assertNotIn(main_thread, session.threads)


    def test_is_inside_requires_majority_overlap(self):
        detector = TextDetector()

        self.assertTrue(detector._is_inside([2, 2, 8, 8], [0, 0, 10, 10]))
        self.assertFalse(detector._is_inside([8, 8, 18, 18], [0, 0, 10, 10]))

    def test_initialize_downloads_through_pinned_helper(self):
        detector = TextDetector()
        calls = []

        def fake_download(**kwargs):
            calls.append(kwargs)
            path = Path(kwargs["local_dir"], kwargs["filename"])
            path.write_bytes(b"model")
            return str(path)

        pins = {(detector.repo_id, "detector.onnx"): {"revision": "rev9", "sha256": None}}
        with writable_temp_directory() as tmpdir:
            detector.model_dir = Path(tmpdir)
            with (
                patch.dict(MODEL_PINS, pins, clear=False),
                patch("manga_backend.utils.download.hf_hub_download", side_effect=fake_download),
                patch("manga_backend.models.detector.get_onnx_providers", return_value=["CPUExecutionProvider"]),
                patch("manga_backend.models.detector.create_onnx_session", return_value=object()) as create,
            ):
                asyncio.run(detector.initialize())

        self.assertEqual(calls[0]["revision"], "rev9")
        self.assertEqual(Path(calls[0]["local_dir"]), Path(tmpdir))
        create.assert_called_once()

    def test_initialize_rejects_cached_model_with_wrong_checksum(self):
        detector = TextDetector()
        pins = {(detector.repo_id, "detector.onnx"): {"revision": None, "sha256": "0" * 64}}
        with writable_temp_directory() as tmpdir:
            detector.model_dir = Path(tmpdir)
            model_path = Path(tmpdir, "detector.onnx")
            model_path.write_bytes(b"model")
            with (
                patch.dict(MODEL_PINS, pins, clear=False),
                patch("manga_backend.models.detector.create_onnx_session") as create,
            ):
                with self.assertRaisesRegex(RuntimeError, "Model checksum mismatch"):
                    asyncio.run(detector.initialize())

            self.assertFalse(model_path.exists())
        create.assert_not_called()


class PaddleOcrWrapperTests(unittest.TestCase):
    def test_sort_box_points_orders_clockwise_from_top_left(self):
        points = np.array([[10, 10], [0, 0], [10, 0], [0, 10]], dtype=np.float32)

        result = _sort_box_points(points)

        self.assertEqual(result.tolist(), [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]])

    def test_recognition_postprocess_ctc_decodes_repeated_indices(self):
        engine = PaddleOCROnnxEngine()
        engine.char_dict = ["blank", "A", "B"]
        output = np.array([[
            [0.9, 0.1, 0.0],
            [0.0, 0.8, 0.2],
            [0.0, 0.7, 0.3],
            [0.9, 0.1, 0.0],
            [0.0, 0.2, 0.8],
        ]], dtype=np.float32)

        result = engine._postprocess_rec_batch(output)

        self.assertEqual(result[0][0], "AB")
        self.assertGreater(result[0][1], 0)

    def test_model_download_forwards_pinned_revision_and_verifies_cache(self):
        key = ("xberg-io/paddleocr-onnx-models", "PP-OCRv5_server_det_infer.onnx")
        calls = []

        def fake_download(**kwargs):
            calls.append(kwargs)
            path = Path(kwargs["local_dir"], kwargs["filename"])
            path.write_bytes(b"yml")
            return str(path)

        with writable_temp_directory() as tmpdir:
            engine = PaddleOCROnnxEngine()
            engine.models_dir = Path(tmpdir)
            with (
                patch.dict(MODEL_PINS, {key: {"revision": "rev3", "sha256": None}}, clear=False),
                patch("manga_backend.utils.download.hf_hub_download", side_effect=fake_download),
            ):
                downloaded = engine._download_model_sync(key[1], key[0])
            self.assertEqual(calls[0]["revision"], "rev3")
            self.assertEqual(downloaded, Path(tmpdir, key[1]))

            with patch.dict(MODEL_PINS, {key: {"revision": None, "sha256": "0" * 64}}, clear=False):
                with self.assertRaisesRegex(RuntimeError, "Model checksum mismatch"):
                    engine._download_model_sync(key[1], key[0])
            self.assertFalse(Path(tmpdir, key[1]).exists())

    def test_recognition_preprocess_uses_half_normalization_and_aspect_width(self):
        engine = PaddleOCROnnxEngine()
        white = np.full((24, 96, 3), 255, dtype=np.uint8)
        black = np.zeros((24, 96, 3), dtype=np.uint8)

        white_input = engine._preprocess_rec(white)
        black_input = engine._preprocess_rec(black)

        self.assertEqual(white_input.shape, (3, 48, 192))
        self.assertEqual(white_input.dtype, np.float32)
        self.assertAlmostEqual(float(white_input.max()), 1.0, places=5)
        self.assertAlmostEqual(float(black_input.min()), -1.0, places=5)

    def test_tall_crops_are_turned_sideways_before_recognition(self):
        engine = PaddleOCROnnxEngine()
        tall = np.zeros((90, 20, 3), dtype=np.uint8)
        tall[:10] = 255  # the top of a vertical column
        wide = np.zeros((20, 90, 3), dtype=np.uint8)

        turned = engine._upright(tall)

        self.assertEqual(turned.shape[:2], (20, 90))
        # Reading top to bottom becomes reading left to right.
        self.assertTrue((turned[:, :10] == 255).all())
        self.assertTrue((turned[:, 10:] == 0).all())
        self.assertIs(engine._upright(wide), wide)

    def test_unclip_grows_a_box_symmetrically(self):
        box = np.array([[10, 10], [110, 10], [110, 30], [10, 30]], dtype=np.float32)

        grown = PaddleOCROnnxEngine._unclip(box)

        distance = 100 * 20 * 1.5 / (2 * (100 + 20))
        self.assertAlmostEqual(float(grown[:, 0].min()), 10 - distance, places=3)
        self.assertAlmostEqual(float(grown[:, 0].max()), 110 + distance, places=3)
        self.assertAlmostEqual(float(grown[:, 1].min()), 10 - distance, places=3)
        self.assertAlmostEqual(float(grown[:, 1].max()), 30 + distance, places=3)

    def test_crops_are_batched_by_width_and_results_keep_input_order(self):
        engine = PaddleOCROnnxEngine()
        engine.char_dict = ["blank", "A", "B", "C"]
        batches = []

        class FakeInput:
            name = "x"

        class FakeSession:
            def get_inputs(self):
                return [FakeInput()]

            def run(self, _outputs, feeds):
                batch = feeds["x"]
                batches.append(batch.shape)
                steps = batch.shape[3] // 8
                out = np.zeros((batch.shape[0], steps, 4), dtype=np.float32)
                out[:, :, 0] = 1.0
                for row in range(batch.shape[0]):
                    # Class = which of three crop widths this row holds.
                    widest = float(np.abs(batch[row, 0, 0]).nonzero()[0].max() + 1)
                    out[row, 0] = [0, 0, 0, 0]
                    out[row, 0, 1 if widest < 200 else 2 if widest < 400 else 3] = 1.0
                return [out]

        engine.rec_session = FakeSession()
        crops = [
            np.full((48, 480, 3), 200, dtype=np.uint8),   # wide  -> C
            np.full((48, 96, 3), 200, dtype=np.uint8),    # short -> A
            np.full((48, 288, 3), 200, dtype=np.uint8),   # mid   -> B
        ]

        results = engine._recognize_crops(crops)

        self.assertEqual([text for text, _score in results], ["C", "A", "B"])
        self.assertEqual(batches, [(3, 3, 48, 480)])

    def test_default_models_are_pinned_and_use_the_working_recognition_source(self):
        import manga_backend.models.paddle_ocr_onnx as paddle_module

        sources = [
            (paddle_module.PADDLEOCR_DET_REPO, paddle_module.PADDLEOCR_DET_FILE),
            (paddle_module.PADDLEOCR_ONNX_REPO, paddle_module.PADDLEOCR_REC_FILE),
            (paddle_module.PADDLEOCR_DICT_REPO, paddle_module.PADDLEOCR_DICT_FILE),
        ]
        for source in sources:
            with self.subTest(source=source):
                self.assertNotIn("AIPLUX", source[0])
                self.assertIn(source, MODEL_PINS)
                self.assertTrue(MODEL_PINS[source]["sha256"])
                self.assertTrue(MODEL_PINS[source]["revision"])

    def test_cached_nested_model_path_is_found_and_checksum_verified(self):
        key = ("xberg-io/paddleocr-onnx-models", "rec/chinese/model.onnx")
        with writable_temp_directory() as tmpdir:
            engine = PaddleOCROnnxEngine()
            engine.models_dir = Path(tmpdir)
            model = Path(tmpdir, "rec", "chinese", "model.onnx")
            model.parent.mkdir(parents=True)
            model.write_bytes(b"onnx")
            with patch("manga_backend.utils.download.hf_hub_download") as download:
                with patch.dict(MODEL_PINS, {key: {"revision": None, "sha256": None}}):
                    path = engine._download_model_sync(key[1], key[0])
                self.assertEqual(Path(path), model)
                download.assert_not_called()
                # The shipped pin rejects a file that is not the verified model.
                with self.assertRaisesRegex(RuntimeError, "Model checksum mismatch"):
                    engine._download_model_sync(key[1], key[0])
            self.assertFalse(model.exists())

    def test_load_char_dict_reads_embedded_character_dict(self):
        with writable_temp_directory() as tmpdir:
            config_path = Path(tmpdir) / "rec.yml"
            config_path.write_text("character_dict:\n  - A\n  - B\n", encoding="utf-8")

            engine = PaddleOCROnnxEngine()
            result = engine._load_char_dict(str(config_path))

        # blank first, then the dictionary, then the ASCII space PaddleOCR appends.
        self.assertEqual(result, ["blank", "A", "B", " "])

    def test_region_recognition_offloads_and_serializes_inference(self):
        engine = PaddleOCROnnxEngine()
        engine._initialized = True
        state_lock = threading.Lock()
        state = {"active": 0, "max_active": 0, "threads": set()}
        started = threading.Event()
        release = threading.Event()

        def blocking_recognition(_image, _regions):
            with state_lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
                state["threads"].add(threading.get_ident())
            started.set()
            try:
                if not release.wait(5.0):
                    raise TimeoutError("Paddle OCR inference test was not released")
            finally:
                with state_lock:
                    state["active"] -= 1
            return ["text"]

        engine._recognize_regions_sync = blocking_recognition
        image = np.zeros((8, 8, 3), dtype=np.uint8)
        regions = [{"bbox": [0, 0, 8, 8]}]
        main_thread = threading.get_ident()

        responsive, lock_held, results = run_blocked_calls(
            [lambda: engine.recognize_regions(image, regions), lambda: engine.recognize_regions(image, regions)],
            started, release, before_release=engine._inference_lock.locked,
        )

        self.assertTrue(responsive)
        self.assertTrue(lock_held)
        self.assertEqual(results, [["text"], ["text"]])
        self.assertEqual(state["max_active"], 1)
        self.assertNotIn(main_thread, state["threads"])


if __name__ == "__main__":
    unittest.main()
