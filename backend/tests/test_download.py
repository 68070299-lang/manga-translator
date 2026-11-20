"""Model-free tests for model download pinning and checksum verification."""

import hashlib
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]

from test_support import writable_temp_directory
from manga_backend.utils.download import (
    MODEL_PINS,
    TEXT_SEGMENTER_FILENAME,
    TEXT_SEGMENTER_REPO,
    download_lama_onnx,
    download_manga_ocr_onnx,
    download_pinned_model,
    download_text_segmenter_onnx,
    verify_cached_model,
    verify_sha256,
)


REPO = "ogkalu/lama-manga-onnx-dynamic"
FILENAME = "lama-manga-dynamic.onnx"
PAYLOAD = b"pinned-model-bytes"
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()
# Gated on Hugging Face: anonymous API calls hide its sha256s, so it may stay unpinned.
GATED_REPO = "deepghs/AnimeText_yolo"
REVISION_RE = re.compile(r"\A[0-9a-f]{40}\Z")
SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")


def pin(revision=None, sha256=None):
    return {(REPO, FILENAME): {"revision": revision, "sha256": sha256}}


class ModelPinTableTests(unittest.TestCase):
    def test_pin_table_has_an_entry_for_every_downloaded_model(self):
        for key in (
            ("mayocream/manga-ocr-onnx", "encoder_model.onnx"),
            ("mayocream/manga-ocr-onnx", "decoder_model.onnx"),
            ("mayocream/manga-ocr-onnx", "vocab.txt"),
            (REPO, FILENAME),
            ("ogkalu/comic-text-and-bubble-detector", "detector.onnx"),
            ("mayocream/comic-text-detector-onnx", "comic-text-detector.onnx"),
            ("xberg-io/paddleocr-onnx-models", "PP-OCRv5_server_det_infer.onnx"),
            ("xberg-io/paddleocr-onnx-models", "rec/chinese/model.onnx"),
            ("PaddlePaddle/PP-OCRv5_server_rec", "inference.yml"),
            ("deepghs/AnimeText_yolo", "yolo12m_animetext/model.onnx"),
        ):
            with self.subTest(key=key):
                self.assertIn(key, MODEL_PINS)
                self.assertEqual(set(MODEL_PINS[key]), {"revision", "sha256"})

    def test_only_the_gated_animetext_repo_may_be_unpinned(self):
        for (repo_id, filename), value in MODEL_PINS.items():
            with self.subTest(repo_id=repo_id, filename=filename):
                self.assertEqual(set(value), {"revision", "sha256"})
                if repo_id == GATED_REPO and value == {"revision": None, "sha256": None}:
                    continue
                self.assertIsInstance(value["revision"], str)
                self.assertIsInstance(value["sha256"], str)
                self.assertRegex(value["revision"], REVISION_RE)
                self.assertRegex(value["sha256"], SHA256_RE)


class VerifySha256Tests(unittest.TestCase):
    def test_matching_hash_keeps_file(self):
        with writable_temp_directory() as tmpdir:
            path = Path(tmpdir, FILENAME)
            path.write_bytes(PAYLOAD)

            verify_sha256(str(path), PAYLOAD_SHA.upper())

            self.assertTrue(path.exists())

    def test_mismatch_deletes_file_and_raises(self):
        with writable_temp_directory() as tmpdir:
            path = Path(tmpdir, FILENAME)
            path.write_bytes(b"tampered")

            with self.assertRaisesRegex(
                RuntimeError,
                r"Model checksum mismatch for lama-manga-dynamic\.onnx; "
                r"re-download or update MODEL_PINS",
            ):
                verify_sha256(str(path), PAYLOAD_SHA)

            self.assertFalse(path.exists())

    def test_no_expected_hash_skips_check(self):
        with writable_temp_directory() as tmpdir:
            path = Path(tmpdir, FILENAME)
            path.write_bytes(b"anything")

            verify_sha256(str(path), None)
            with patch.dict(MODEL_PINS, pin(), clear=False):
                verify_cached_model(REPO, FILENAME, str(path))

            self.assertTrue(path.exists())


class DownloadPinnedModelTests(unittest.TestCase):
    def fake_download(self, calls, payload=PAYLOAD):
        def download(**kwargs):
            calls.append(kwargs)
            path = Path(kwargs["local_dir"], kwargs["filename"])
            path.write_bytes(payload)
            return str(path)

        return download

    def test_pinned_revision_is_forwarded(self):
        calls = []
        with writable_temp_directory() as tmpdir:
            with (
                patch.dict(MODEL_PINS, pin(revision="abc123"), clear=False),
                patch("manga_backend.utils.download.hf_hub_download", side_effect=self.fake_download(calls)),
            ):
                download_pinned_model(REPO, FILENAME, local_dir=tmpdir)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["revision"], "abc123")
        self.assertEqual(calls[0]["repo_id"], REPO)
        self.assertEqual(calls[0]["filename"], FILENAME)

    def test_unpinned_download_is_unchanged_and_unchecked(self):
        calls = []
        with writable_temp_directory() as tmpdir:
            with (
                patch.dict(MODEL_PINS, pin(), clear=False),
                patch("manga_backend.utils.download.hf_hub_download", side_effect=self.fake_download(calls, b"x")),
            ):
                path = download_pinned_model(REPO, FILENAME, local_dir=tmpdir)

            self.assertTrue(Path(path).exists())

        self.assertNotIn("revision", calls[0])

    def test_unknown_repo_file_is_unpinned(self):
        calls = []
        with writable_temp_directory() as tmpdir:
            with (
                patch.dict(MODEL_PINS, pin(), clear=False),
                patch("manga_backend.utils.download.hf_hub_download", side_effect=self.fake_download(calls)),
            ):
                download_pinned_model(REPO, FILENAME, local_dir=tmpdir)
                download_pinned_model("someone/else", FILENAME, local_dir=tmpdir)

        self.assertTrue(all("revision" not in call for call in calls))

    def test_downloaded_file_with_wrong_hash_is_deleted(self):
        calls = []
        with writable_temp_directory() as tmpdir:
            with (
                patch.dict(MODEL_PINS, pin(sha256=PAYLOAD_SHA), clear=False),
                patch("manga_backend.utils.download.hf_hub_download", side_effect=self.fake_download(calls, b"bad")),
            ):
                with self.assertRaisesRegex(RuntimeError, "Model checksum mismatch for"):
                    download_pinned_model(REPO, FILENAME, local_dir=tmpdir)

            self.assertFalse(Path(tmpdir, FILENAME).exists())

    def test_downloaded_file_with_matching_hash_is_kept(self):
        calls = []
        with writable_temp_directory() as tmpdir:
            with (
                patch.dict(MODEL_PINS, pin(sha256=PAYLOAD_SHA), clear=False),
                patch("manga_backend.utils.download.hf_hub_download", side_effect=self.fake_download(calls)),
            ):
                path = download_pinned_model(REPO, FILENAME, local_dir=tmpdir)

            self.assertTrue(Path(path).exists())


class DownloadWorkflowPinTests(unittest.IsolatedAsyncioTestCase):
    async def test_lama_cached_file_is_verified_and_removed_on_mismatch(self):
        with writable_temp_directory() as tmpdir:
            model_path = Path(tmpdir, FILENAME)
            model_path.write_bytes(b"tampered")

            with (
                patch.dict(MODEL_PINS, pin(sha256=PAYLOAD_SHA), clear=False),
                patch("manga_backend.utils.download.hf_hub_download") as mock_download,
            ):
                with self.assertRaisesRegex(RuntimeError, "Model checksum mismatch"):
                    await download_lama_onnx(tmpdir)

            self.assertFalse(mock_download.called)
            self.assertFalse(model_path.exists())

    async def test_lama_cached_file_without_pin_is_not_hashed(self):
        with writable_temp_directory() as tmpdir:
            model_path = Path(tmpdir, FILENAME)
            model_path.write_bytes(b"anything")

            with (
                patch.dict(MODEL_PINS, pin(), clear=False),
                patch("manga_backend.utils.download.verify_sha256") as mock_verify,
            ):
                result = await download_lama_onnx(tmpdir)

            self.assertEqual(result, model_path)
            mock_verify.assert_called_once_with(model_path, None)

    async def test_lama_download_forwards_revision_and_surfaces_checksum_error(self):
        calls = []

        def fake_download(**kwargs):
            calls.append(kwargs)
            path = Path(kwargs["local_dir"], kwargs["filename"])
            path.write_bytes(b"bad")
            return str(path)

        with writable_temp_directory() as tmpdir:
            with (
                patch.dict(MODEL_PINS, pin(revision="rev1", sha256=PAYLOAD_SHA), clear=False),
                patch("manga_backend.utils.download.hf_hub_download", side_effect=fake_download),
            ):
                with self.assertRaisesRegex(RuntimeError, "Model checksum mismatch"):
                    await download_lama_onnx(tmpdir)

            self.assertFalse(Path(tmpdir, FILENAME).exists())

        self.assertEqual(calls[0]["revision"], "rev1")

    async def test_manga_ocr_cached_file_with_wrong_hash_is_removed(self):
        key = ("mayocream/manga-ocr-onnx", "decoder_model.onnx")
        pins = {
            ("mayocream/manga-ocr-onnx", "encoder_model.onnx"): {"revision": None, "sha256": None},
            key: {"revision": None, "sha256": PAYLOAD_SHA},
            ("mayocream/manga-ocr-onnx", "vocab.txt"): {"revision": None, "sha256": None},
        }
        with writable_temp_directory() as tmpdir:
            for filename in ("encoder_model.onnx", "decoder_model.onnx", "vocab.txt"):
                Path(tmpdir, filename).write_bytes(b"model")

            with (
                patch.dict(MODEL_PINS, pins, clear=False),
                patch("manga_backend.utils.download.hf_hub_download") as mock_download,
            ):
                with self.assertRaisesRegex(RuntimeError, "Model checksum mismatch for decoder_model.onnx"):
                    await download_manga_ocr_onnx(tmpdir)

            self.assertFalse(mock_download.called)
            self.assertFalse(Path(tmpdir, "decoder_model.onnx").exists())
            self.assertTrue(Path(tmpdir, "encoder_model.onnx").exists())

    async def test_text_segmenter_cached_file_is_verified_and_removed_on_mismatch(self):
        pins = {(TEXT_SEGMENTER_REPO, TEXT_SEGMENTER_FILENAME): {"revision": "rev1", "sha256": PAYLOAD_SHA}}
        with writable_temp_directory() as tmpdir:
            model_path = Path(tmpdir, TEXT_SEGMENTER_FILENAME)
            model_path.write_bytes(b"tampered")

            with (
                patch.dict(MODEL_PINS, pins, clear=False),
                patch("manga_backend.utils.download.hf_hub_download") as mock_download,
            ):
                with self.assertRaisesRegex(RuntimeError, "Model checksum mismatch"):
                    await download_text_segmenter_onnx(tmpdir)

            self.assertFalse(mock_download.called)
            self.assertFalse(model_path.exists())

    async def test_text_segmenter_download_forwards_revision_and_verifies_hash(self):
        calls = []
        progress = []
        pins = {(TEXT_SEGMENTER_REPO, TEXT_SEGMENTER_FILENAME): {"revision": "rev1", "sha256": PAYLOAD_SHA}}

        def fake_download(**kwargs):
            calls.append(kwargs)
            path = Path(kwargs["local_dir"], kwargs["filename"])
            path.write_bytes(PAYLOAD)
            return str(path)

        with writable_temp_directory() as tmpdir:
            with (
                patch.dict(MODEL_PINS, pins, clear=False),
                patch("manga_backend.utils.download.hf_hub_download", side_effect=fake_download),
            ):
                result = await download_text_segmenter_onnx(
                    tmpdir, lambda current, total: progress.append((current, total))
                )

            self.assertEqual(result, Path(tmpdir, TEXT_SEGMENTER_FILENAME))
            self.assertTrue(result.exists())

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["repo_id"], TEXT_SEGMENTER_REPO)
        self.assertEqual(calls[0]["filename"], TEXT_SEGMENTER_FILENAME)
        self.assertEqual(calls[0]["revision"], "rev1")
        self.assertEqual(progress, [(1, 1)])


if __name__ == "__main__":
    unittest.main()
