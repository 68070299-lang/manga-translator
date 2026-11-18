"""Model-free tests for AnimeText YOLO preprocessing and decoding."""

import asyncio
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError


BACKEND_DIR = Path(__file__).resolve().parents[1]

from manga_backend.models.animetext_yolo_detector import AnimeTextYoloDetector
from scripts.export_animetext_yolo import validate_onnx_parity
from test_support import writable_temp_directory


class AnimeTextYoloDetectorTests(unittest.TestCase):
    def test_rejects_invalid_runtime_thresholds(self):
        with patch.dict("os.environ", {"ANIMETEXT_YOLO_CONFIDENCE": "0"}):
            with self.assertRaisesRegex(ValueError, "CONFIDENCE"):
                AnimeTextYoloDetector()
        with patch.dict("os.environ", {"ANIMETEXT_YOLO_IOU": "1"}):
            with self.assertRaisesRegex(ValueError, "IOU"):
                AnimeTextYoloDetector()

    def test_letterbox_preserves_aspect_ratio_and_rgb_values(self):
        image = np.zeros((100, 200, 3), dtype=np.uint8)
        image[:, :, 0] = 255

        tensor, scale, pad = AnimeTextYoloDetector._letterbox(image, 640)

        self.assertEqual(tensor.shape, (1, 3, 640, 640))
        self.assertEqual(tensor.dtype, np.float32)
        self.assertAlmostEqual(scale, 3.2)
        self.assertEqual(pad, (0, 160))
        self.assertEqual(float(tensor[0, 0, 200, 100]), 1.0)
        self.assertEqual(float(tensor[0, 1, 200, 100]), 0.0)

    def test_decode_maps_letterbox_coordinates_and_suppresses_duplicates(self):
        predictions = np.array(
            [
                [
                    [160.0, 161.0],
                    [240.0, 241.0],
                    [192.0, 192.0],
                    [96.0, 96.0],
                    [0.9, 0.8],
                ]
            ],
            dtype=np.float32,
        )

        blocks = AnimeTextYoloDetector._decode_predictions(
            predictions,
            image_shape=(100, 200),
            scale=3.2,
            pad=(0, 160),
            confidence_threshold=0.3,
            iou_threshold=0.35,
        )

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].xyxy.tolist(), [20, 10, 80, 40])
        self.assertAlmostEqual(blocks[0].confidence, 0.9)
        self.assertEqual(blocks[0].block_type, "text")

    def test_decode_filters_low_confidence_predictions(self):
        predictions = np.array(
            [[[100.0], [100.0], [20.0], [20.0], [0.1]]], dtype=np.float32
        )

        blocks = AnimeTextYoloDetector._decode_predictions(
            predictions,
            image_shape=(640, 640),
            scale=1.0,
            pad=(0, 0),
            confidence_threshold=0.3,
            iou_threshold=0.35,
        )

        self.assertEqual(blocks, [])

    def test_decode_caps_candidates_before_nms(self):
        predictions = np.array(
            [
                [
                    [50.0, 150.0, 250.0],
                    [50.0, 150.0, 250.0],
                    [20.0, 20.0, 20.0],
                    [20.0, 20.0, 20.0],
                    [0.9, 0.8, 0.7],
                ]
            ],
            dtype=np.float32,
        )

        blocks = AnimeTextYoloDetector._decode_predictions(
            predictions,
            image_shape=(640, 640),
            scale=1.0,
            pad=(0, 0),
            confidence_threshold=0.1,
            iou_threshold=0.35,
            max_candidates=2,
        )

        self.assertEqual(len(blocks), 2)
        self.assertEqual(
            sorted(round(block.confidence, 1) for block in blocks), [0.8, 0.9]
        )

    def test_output_contract_accepts_only_raw_one_class_predictions(self):
        AnimeTextYoloDetector._validate_output_shape([1, 5, 8400])
        AnimeTextYoloDetector._validate_output_shape([1, 5, "candidates"])

        for shape in (
            [1, 6, 300],
            [1, 84, 8400],
            [1, 8400, 5],
            [8400, 5],
            [1, 5, 0],
        ):
            with self.subTest(shape=shape):
                with self.assertRaisesRegex(RuntimeError, r"\[1, 5, N\]"):
                    AnimeTextYoloDetector._validate_output_shape(shape)

    def test_decode_rejects_transposed_or_post_nms_outputs(self):
        for predictions in (
            np.zeros((1, 6, 300), dtype=np.float32),
            np.zeros((1, 8400, 5), dtype=np.float32),
            np.zeros((300, 6), dtype=np.float32),
        ):
            with self.subTest(shape=predictions.shape):
                with self.assertRaisesRegex(RuntimeError, "output shape"):
                    AnimeTextYoloDetector._decode_predictions(
                        predictions,
                        image_shape=(640, 640),
                        scale=1.0,
                        pad=(0, 0),
                        confidence_threshold=0.3,
                        iou_threshold=0.35,
                    )

    def test_use_gpu_forces_cuda_to_the_front(self):
        detector = AnimeTextYoloDetector(device="cpu")
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]

        with (
            patch.dict("os.environ", {"USE_GPU": "true"}),
            patch(
                "manga_backend.models.animetext_yolo_detector.get_onnx_providers",
                return_value=providers,
            ) as get_providers,
        ):
            self.assertEqual(detector._runtime_providers(), providers)

        get_providers.assert_called_once_with("cuda")

    def test_use_gpu_rejects_silent_cpu_fallback(self):
        detector = AnimeTextYoloDetector(device="cpu")
        with (
            patch.dict("os.environ", {"USE_GPU": "true"}),
            patch(
                "manga_backend.models.animetext_yolo_detector.get_onnx_providers",
                return_value=["CPUExecutionProvider"],
            ),
            self.assertRaisesRegex(RuntimeError, "CUDAExecutionProvider"),
        ):
            detector._runtime_providers()

    def test_export_parity_validator_rejects_numerical_drift(self):
        reference = np.array([[1.0, 2.0]], dtype=np.float32)
        self.assertEqual(validate_onnx_parity(reference, reference.copy()), 0.0)

        with self.assertRaises(AssertionError):
            validate_onnx_parity(
                reference,
                np.array([[1.0, 2.1]], dtype=np.float32),
            )


class FakeValue:
    def __init__(self, name, shape):
        self.name = name
        self.shape = shape


class FakeYoloSession:
    def __init__(self, input_shape=(1, 3, 640, 640), output_shape=(1, 5, 8400)):
        self.inputs = [FakeValue("images", list(input_shape))]
        self.outputs = [FakeValue("predictions", list(output_shape))]

    def get_inputs(self):
        return self.inputs

    def get_outputs(self):
        return self.outputs

    def get_providers(self):
        return ["CPUExecutionProvider"]


def http_error(error_class, status_code):
    response = MagicMock()
    response.status_code = status_code
    response.headers = {}
    return error_class("access denied", response=response)


class AnimeTextYoloInitializeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(
            "os.environ",
            {
                "ANIMETEXT_YOLO_MODEL": "",
                "ANIMETEXT_YOLO_VARIANT": "m",
                "ANIMETEXT_YOLO_INPUT_SIZE": "",
                "ANIMETEXT_YOLO_CONFIDENCE": "0.299",
                "USE_GPU": "false",
            },
        )
        env.start()
        self.addCleanup(env.stop)

    async def initialize(self, models_dir, session=None, downloader=None, **env):
        created = []

        def fake_create(path, providers):
            created.append((path, providers))
            return session or FakeYoloSession()

        with (
            patch("manga_backend.models.animetext_yolo_detector.MODELS_DIR", Path(models_dir)),
            patch("manga_backend.models.animetext_yolo_detector.create_onnx_session", side_effect=fake_create),
            patch(
                "manga_backend.utils.download.hf_hub_download",
                side_effect=downloader or AssertionError("unexpected download"),
            ),
            patch.dict("os.environ", env),
        ):
            detector = AnimeTextYoloDetector()
            await detector.initialize()
        return detector, created

    @staticmethod
    def fake_downloader(calls, payload=b"onnx"):
        def download(**kwargs):
            calls.append(kwargs)
            path = Path(kwargs["local_dir"], kwargs["filename"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            return str(path)

        return download

    async def test_configured_model_is_used_without_download(self):
        with writable_temp_directory() as tmpdir:
            model = Path(tmpdir, "custom.onnx")
            model.write_bytes(b"onnx")

            detector, created = await self.initialize(
                Path(tmpdir, "cache"), ANIMETEXT_YOLO_MODEL=str(model)
            )

        self.assertEqual(created[0][0], str(model))
        self.assertEqual(detector.input_size, 640)
        self.assertEqual(detector.input_name, "images")
        self.assertEqual(detector.output_name, "predictions")

    async def test_configured_model_that_is_missing_is_not_replaced_by_download(self):
        with writable_temp_directory() as tmpdir:
            with self.assertRaisesRegex(RuntimeError, "ANIMETEXT_YOLO_MODEL") as raised:
                await self.initialize(
                    Path(tmpdir, "cache"),
                    ANIMETEXT_YOLO_MODEL=str(Path(tmpdir, "missing.onnx")),
                )

        self.assertNotIn(tmpdir, str(raised.exception))

    async def test_empty_model_file_is_rejected(self):
        with writable_temp_directory() as tmpdir:
            model = Path(tmpdir, "empty.onnx")
            model.touch()

            with self.assertRaisesRegex(RuntimeError, "missing or empty") as raised:
                await self.initialize(Path(tmpdir, "cache"), ANIMETEXT_YOLO_MODEL=str(model))

        self.assertNotIn(tmpdir, str(raised.exception))

    async def test_exported_model_in_cache_short_circuits_download(self):
        with writable_temp_directory() as tmpdir:
            Path(tmpdir, "yolo12m_animetext.onnx").write_bytes(b"onnx")

            _detector, created = await self.initialize(tmpdir)

        self.assertEqual(created[0][0], str(Path(tmpdir, "yolo12m_animetext.onnx")))

    async def test_downloaded_model_lands_in_models_cache_and_is_reused(self):
        calls = []
        with writable_temp_directory() as tmpdir:
            _detector, created = await self.initialize(
                tmpdir, downloader=self.fake_downloader(calls)
            )
            expected = Path(tmpdir, "yolo12m_animetext", "model.onnx")

            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["repo_id"], "deepghs/AnimeText_yolo")
            self.assertEqual(calls[0]["filename"], "yolo12m_animetext/model.onnx")
            self.assertEqual(Path(calls[0]["local_dir"]), Path(tmpdir))
            self.assertEqual(created[0][0], str(expected))

            # A second initialize finds the cached file and does not download again.
            await self.initialize(tmpdir)
            self.assertEqual(len(calls), 1)

    async def test_empty_download_is_rejected(self):
        with writable_temp_directory() as tmpdir:
            with self.assertRaisesRegex(RuntimeError, "missing or empty"):
                await self.initialize(
                    tmpdir, downloader=self.fake_downloader([], payload=b"")
                )

    async def test_gated_repo_failure_explains_how_to_recover(self):
        for error in (
            http_error(GatedRepoError, 403),
            http_error(HfHubHTTPError, 401),
            http_error(HfHubHTTPError, 403),
        ):
            with self.subTest(error=type(error).__name__, status=error.response.status_code):
                with writable_temp_directory() as tmpdir:
                    with self.assertRaises(RuntimeError) as raised:
                        await self.initialize(tmpdir, downloader=error)

                message = str(raised.exception)
                self.assertIn("ANIMETEXT_YOLO_MODEL", message)
                self.assertIn("scripts/export_animetext_yolo.py", message)
                self.assertIn("Hugging Face", message)
                self.assertIn("authenticat", message)
                self.assertNotIn(tmpdir, message)
                self.assertIs(raised.exception.__cause__, error)

    async def test_non_auth_download_failures_are_not_relabelled(self):
        for error in (http_error(HfHubHTTPError, 500), ConnectionError("offline")):
            with self.subTest(error=type(error).__name__):
                with writable_temp_directory() as tmpdir:
                    with self.assertRaises(type(error)) as raised:
                        await self.initialize(tmpdir, downloader=error)

                self.assertIs(raised.exception, error)

    async def test_wrong_output_shape_is_rejected(self):
        with writable_temp_directory() as tmpdir:
            Path(tmpdir, "yolo12m_animetext.onnx").write_bytes(b"onnx")

            with self.assertRaisesRegex(RuntimeError, r"\[1, 5, N\]"):
                await self.initialize(
                    tmpdir, session=FakeYoloSession(output_shape=(1, 84, 8400))
                )

    async def test_non_square_fixed_input_is_rejected(self):
        with writable_temp_directory() as tmpdir:
            Path(tmpdir, "yolo12m_animetext.onnx").write_bytes(b"onnx")

            with self.assertRaisesRegex(RuntimeError, "square"):
                await self.initialize(
                    tmpdir, session=FakeYoloSession(input_shape=(1, 3, 640, 480))
                )

    async def test_configured_input_size_must_match_fixed_onnx_input(self):
        with writable_temp_directory() as tmpdir:
            Path(tmpdir, "yolo12m_animetext.onnx").write_bytes(b"onnx")

            with self.assertRaisesRegex(ValueError, "ANIMETEXT_YOLO_INPUT_SIZE"):
                await self.initialize(tmpdir, ANIMETEXT_YOLO_INPUT_SIZE="512")

    async def test_dynamic_input_uses_configured_size(self):
        with writable_temp_directory() as tmpdir:
            Path(tmpdir, "yolo12m_animetext.onnx").write_bytes(b"onnx")

            detector, _created = await self.initialize(
                tmpdir,
                session=FakeYoloSession(input_shape=(1, 3, "h", "w")),
                ANIMETEXT_YOLO_INPUT_SIZE="512",
            )

        self.assertEqual(detector.input_size, 512)

    async def test_dispose_releases_session(self):
        with writable_temp_directory() as tmpdir:
            Path(tmpdir, "yolo12m_animetext.onnx").write_bytes(b"onnx")
            detector, _created = await self.initialize(tmpdir)

        detector.dispose()

        self.assertIsNone(detector.session)


class AnimeTextYoloConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_inference_holds_lock_until_worker_finishes(self):
        started = threading.Event()
        release = threading.Event()

        class BlockingSession:
            def __init__(self):
                self.calls = 0
                self.active = 0
                self.max_active = 0
                self.guard = threading.Lock()

            def run(self, *_args):
                with self.guard:
                    self.calls += 1
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                    started.set()
                release.wait(timeout=2)
                with self.guard:
                    self.active -= 1
                return [np.zeros((1, 5, 1), dtype=np.float32)]

        detector = AnimeTextYoloDetector()
        detector.session = BlockingSession()
        image = np.zeros((8, 8, 3), dtype=np.uint8)
        first = asyncio.create_task(detector.detect(image))
        self.assertTrue(await asyncio.to_thread(started.wait, 1))
        first.cancel()
        second = asyncio.create_task(detector.detect(image))
        await asyncio.sleep(0.05)

        try:
            self.assertEqual(detector.session.calls, 1)
            self.assertFalse(second.done())
        finally:
            release.set()

        with self.assertRaises(asyncio.CancelledError):
            await first
        await second
        self.assertEqual(detector.session.calls, 2)
        self.assertEqual(detector.session.max_active, 1)


if __name__ == "__main__":
    unittest.main()
