"""Model-free tests for backend utility modules."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np


BACKEND_DIR = Path(__file__).resolve().parents[1]

from manga_backend.utils.device import create_onnx_session, get_device, get_onnx_providers
from test_support import writable_temp_directory
from manga_backend.utils.download import (
    MANGA_OCR_REPO,
    MODEL_PINS,
    download_lama_onnx,
    download_manga_ocr_onnx,
)
from manga_backend.utils.textblock import TextBlock


class DeviceUtilityTests(unittest.TestCase):
    def test_get_device_returns_cpu_when_gpu_disabled(self):
        self.assertEqual(get_device(use_gpu=False), "cpu")

    def test_get_device_chooses_available_gpu_provider(self):
        with patch("manga_backend.utils.device.ort.get_available_providers", return_value=["DmlExecutionProvider"]):
            self.assertEqual(get_device(use_gpu=True), "directml")

    def test_get_device_prefers_cuda_over_directml(self):
        available = ["DmlExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]

        with patch("manga_backend.utils.device.ort.get_available_providers", return_value=available):
            self.assertEqual(get_device(use_gpu=True), "cuda")

    def test_get_device_falls_back_to_cpu_when_provider_query_fails(self):
        with patch("manga_backend.utils.device.ort.get_available_providers", side_effect=RuntimeError("boom")):
            self.assertEqual(get_device(use_gpu=True), "cpu")

    def test_get_onnx_providers_respects_requested_directml_device(self):
        available = ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"]

        with patch("manga_backend.utils.device.ort.get_available_providers", return_value=available):
            self.assertEqual(get_onnx_providers("directml"), ["DmlExecutionProvider", "CPUExecutionProvider"])

    def test_get_onnx_providers_respects_requested_tensorrt_device(self):
        available = ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]

        with patch("manga_backend.utils.device.ort.get_available_providers", return_value=available):
            self.assertEqual(get_onnx_providers("tensorrt"), ["TensorrtExecutionProvider", "CPUExecutionProvider"])

    def test_get_onnx_providers_falls_back_when_requested_provider_missing(self):
        with patch("manga_backend.utils.device.ort.get_available_providers", return_value=["CPUExecutionProvider"]):
            self.assertEqual(get_onnx_providers("cuda"), ["CPUExecutionProvider"])

    def test_get_onnx_providers_auto_orders_accelerators_before_cpu(self):
        available = ["CPUExecutionProvider", "CUDAExecutionProvider", "TensorrtExecutionProvider"]

        with patch("manga_backend.utils.device.ort.get_available_providers", return_value=available):
            self.assertEqual(
                get_onnx_providers(),
                ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"],
            )

    def test_create_onnx_session_rejects_silent_accelerator_fallback(self):
        session = MagicMock()
        session.get_providers.return_value = ["CPUExecutionProvider"]

        with patch("manga_backend.utils.device.ort.InferenceSession", return_value=session):
            with self.assertRaisesRegex(RuntimeError, "CUDAExecutionProvider failed to initialize"):
                create_onnx_session("model.onnx", ["CUDAExecutionProvider", "CPUExecutionProvider"])

    def test_create_onnx_session_accepts_active_accelerator(self):
        session = MagicMock()
        session.get_providers.return_value = ["CUDAExecutionProvider", "CPUExecutionProvider"]

        with patch("manga_backend.utils.device.ort.InferenceSession", return_value=session):
            self.assertIs(
                create_onnx_session("model.onnx", ["CUDAExecutionProvider", "CPUExecutionProvider"]),
                session,
            )

    def created_providers(self, providers, env=None):
        captured = {}
        session = MagicMock()
        session.get_providers.return_value = list(providers)

        def fake_session(_path, _opts, providers=None):
            captured["providers"] = providers
            return session

        environ = {"ORT_CUDNN_CONV_ALGO_SEARCH": ""}
        environ.update(env or {})
        with (
            patch.dict("os.environ", environ),
            patch("manga_backend.utils.device.preload_onnx_gpu_dependencies"),
            patch("manga_backend.utils.device.ort.InferenceSession", side_effect=fake_session),
        ):
            create_onnx_session("model.onnx", providers)
        return captured["providers"]

    def test_create_onnx_session_avoids_per_shape_cudnn_benchmarking(self):
        providers = self.created_providers(["CUDAExecutionProvider", "CPUExecutionProvider"])

        self.assertEqual(
            providers,
            [
                (
                    "CUDAExecutionProvider",
                    {
                        "cudnn_conv_algo_search": "HEURISTIC",
                        "arena_extend_strategy": "kSameAsRequested",
                    },
                ),
                "CPUExecutionProvider",
            ],
        )

    def test_create_onnx_session_cudnn_search_env_override(self):
        chosen = self.created_providers(
            ["CUDAExecutionProvider"], env={"ORT_CUDNN_CONV_ALGO_SEARCH": "exhaustive"}
        )
        invalid = self.created_providers(
            ["CUDAExecutionProvider"], env={"ORT_CUDNN_CONV_ALGO_SEARCH": "fastest"}
        )

        self.assertEqual(chosen[0][1]["cudnn_conv_algo_search"], "EXHAUSTIVE")
        self.assertEqual(invalid[0][1]["cudnn_conv_algo_search"], "HEURISTIC")

    def test_create_onnx_session_leaves_other_providers_untouched(self):
        providers = ["DmlExecutionProvider", "CPUExecutionProvider"]

        self.assertEqual(self.created_providers(providers), providers)

    def create_session_options(self, providers, env=None, cpu_count=8):
        captured = {}
        session = MagicMock()
        session.get_providers.return_value = [
            provider[0] if isinstance(provider, tuple) else provider
            for provider in providers
        ]

        def fake_session(_path, opts, providers=None):
            captured["opts"] = opts
            return session

        environ = {"ORT_INTRA_OP_THREADS": ""}
        environ.update(env or {})
        with (
            patch.dict("os.environ", environ),
            patch("manga_backend.utils.device.os.cpu_count", return_value=cpu_count),
            patch("manga_backend.utils.device.ort.InferenceSession", side_effect=fake_session),
        ):
            create_onnx_session("model.onnx", providers)
        return captured["opts"]

    def test_create_onnx_session_cpu_default_uses_half_the_cores(self):
        opts = self.create_session_options(["CPUExecutionProvider"], cpu_count=8)

        self.assertEqual(opts.intra_op_num_threads, 4)
        self.assertEqual(opts.inter_op_num_threads, 1)

    def test_create_onnx_session_cpu_default_is_capped_and_at_least_one(self):
        self.assertEqual(
            self.create_session_options(["CPUExecutionProvider"], cpu_count=64).intra_op_num_threads,
            8,
        )
        self.assertEqual(
            self.create_session_options(["CPUExecutionProvider"], cpu_count=1).intra_op_num_threads,
            1,
        )
        self.assertEqual(
            self.create_session_options(["CPUExecutionProvider"], cpu_count=None).intra_op_num_threads,
            1,
        )

    def test_create_onnx_session_env_override_wins(self):
        opts = self.create_session_options(
            ["CPUExecutionProvider"], env={"ORT_INTRA_OP_THREADS": "3"}, cpu_count=8
        )

        self.assertEqual(opts.intra_op_num_threads, 3)
        self.assertEqual(opts.inter_op_num_threads, 1)

    def test_create_onnx_session_gpu_provider_stays_single_threaded(self):
        providers = ["DmlExecutionProvider", "CPUExecutionProvider"]

        opts = self.create_session_options(providers, cpu_count=8)
        overridden = self.create_session_options(
            providers, env={"ORT_INTRA_OP_THREADS": "2"}, cpu_count=8
        )

        self.assertEqual(opts.intra_op_num_threads, 1)
        self.assertEqual(overridden.intra_op_num_threads, 2)

    def test_create_onnx_session_invalid_env_falls_back_to_default(self):
        for value in ("0", "-2", "many", "1.5"):
            with self.subTest(value=value):
                opts = self.create_session_options(
                    ["CPUExecutionProvider"],
                    env={"ORT_INTRA_OP_THREADS": value},
                    cpu_count=8,
                )
                self.assertEqual(opts.intra_op_num_threads, 4)


class TextBlockUtilityTests(unittest.TestCase):
    def test_text_block_normalizes_bbox_and_text_values(self):
        block = TextBlock([-5, 10, 3, 1], text=None, translated_text=123)

        self.assertEqual(block.xyxy.tolist(), [0, 10, 3, 10])
        self.assertEqual(block.text, "")
        self.assertEqual(block.translated_text, "123")
        self.assertEqual(block.width, 3)
        self.assertEqual(block.height, 0)
        self.assertEqual(block.area, 0)


class DownloadUtilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # These tests use placeholder bytes, so lift the real checksum pins for
        # the files they create. test_download.py covers pinned verification.
        unpinned = {"revision": None, "sha256": None}
        pins = patch.dict(
            MODEL_PINS,
            {
                key: dict(unpinned)
                for key in MODEL_PINS
                if key[0] in (MANGA_OCR_REPO, "ogkalu/lama-manga-onnx-dynamic")
            },
        )
        pins.start()
        self.addCleanup(pins.stop)

    async def test_manga_ocr_download_uses_existing_files_without_network(self):
        with writable_temp_directory() as tmpdir:
            for filename in ("encoder_model.onnx", "decoder_model.onnx", "vocab.txt"):
                Path(tmpdir, filename).write_bytes(b"model")

            progress = []
            with patch("manga_backend.utils.download.hf_hub_download") as mock_download:
                paths = await download_manga_ocr_onnx(tmpdir, lambda current, total: progress.append((current, total)))

            self.assertFalse(mock_download.called)
            self.assertEqual(progress, [(1, 1)])
            self.assertEqual(paths["encoder"], str(Path(tmpdir, "encoder_model.onnx")))
            self.assertEqual(paths["decoder"], str(Path(tmpdir, "decoder_model.onnx")))
            self.assertEqual(paths["vocab"], str(Path(tmpdir, "vocab.txt")))

    async def test_manga_ocr_downloads_missing_files_with_patched_hub(self):
        calls = []

        def fake_download(repo_id, filename, local_dir, local_dir_use_symlinks, **kwargs):
            calls.append((repo_id, filename, local_dir, local_dir_use_symlinks))
            path = Path(local_dir, filename)
            path.write_bytes(b"model")
            return str(path)

        with writable_temp_directory() as tmpdir:
            with patch("manga_backend.utils.download.hf_hub_download", side_effect=fake_download):
                paths = await download_manga_ocr_onnx(tmpdir)

            self.assertEqual(
                [filename for _, filename, _, _ in calls],
                ["encoder_model.onnx", "decoder_model.onnx", "vocab.txt"],
            )
            self.assertTrue(all(Path(path).exists() for path in paths.values()))

    async def test_manga_ocr_download_failure_is_wrapped(self):
        with writable_temp_directory() as tmpdir:
            with patch("manga_backend.utils.download.hf_hub_download", side_effect=RuntimeError("offline")):
                with self.assertRaisesRegex(RuntimeError, "Could not download manga-ocr ONNX model"):
                    await download_manga_ocr_onnx(tmpdir)

    async def test_lama_download_uses_existing_file_without_network(self):
        with writable_temp_directory() as tmpdir:
            model_path = Path(tmpdir, "lama-manga-dynamic.onnx")
            model_path.write_bytes(b"model")

            progress = []
            with patch("manga_backend.utils.download.hf_hub_download") as mock_download:
                result = await download_lama_onnx(tmpdir, lambda current, total: progress.append((current, total)))

            self.assertFalse(mock_download.called)
            self.assertEqual(progress, [(1, 1)])
            self.assertEqual(result, str(model_path))

    async def test_lama_download_rejects_empty_existing_file(self):
        with writable_temp_directory() as tmpdir:
            Path(tmpdir, "lama-manga-dynamic.onnx").touch()

            with self.assertRaisesRegex(RuntimeError, "is empty"):
                await download_lama_onnx(tmpdir)


if __name__ == "__main__":
    unittest.main()
