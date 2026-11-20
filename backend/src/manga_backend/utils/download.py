"""Model download utilities.

Downloads ONNX models from HuggingFace Hub.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

from huggingface_hub import hf_hub_download

logger = logging.getLogger(__name__)


class ModelPin(TypedDict):
    revision: str | None
    sha256: str | None


# Optional per-file pins: {(repo_id, filename): {"revision": commit, "sha256": hex}}.
# None means "not pinned" and keeps the previous behaviour (latest revision, no
# checksum). Fill these in from a trusted download to make downloads reproducible.
# Pins are keyed by the default repos; a repo overridden through the environment
# is never pinned.
_UNPINNED: ModelPin = {"revision": None, "sha256": None}
MODEL_PINS: dict[tuple[str, str], ModelPin] = {
    # manga-ocr, LaMa and RT-DETR files pinned to the Hugging Face commit whose files
    # match the cached models_cache copies byte for byte (sha256 = LFS oid; vocab.txt
    # downloaded at that commit and hashed).
    ("mayocream/manga-ocr-onnx", "encoder_model.onnx"): {
        "revision": "24b12778d85800835e2ca409236de281b8ab7b9f",
        "sha256": "15fa8155fe9bc1a7d25d9bb353debaa4def033d0174e907dbd2dd6d995def85f",
    },
    ("mayocream/manga-ocr-onnx", "decoder_model.onnx"): {
        "revision": "24b12778d85800835e2ca409236de281b8ab7b9f",
        "sha256": "ef7765261e9d1cdc34d89356986c2bbc2a082897f753a89605ae80fdfa61f5e8",
    },
    ("mayocream/manga-ocr-onnx", "vocab.txt"): {
        "revision": "24b12778d85800835e2ca409236de281b8ab7b9f",
        "sha256": "5cb5c5586d98a2f331d9f8828e4586479b0611bfba5d8c3b6dadffc84d6a36a3",
    },
    ("ogkalu/lama-manga-onnx-dynamic", "lama-manga-dynamic.onnx"): {
        "revision": "ee4ed4a8447b6730fc41d34f90876b6c48af925a",
        "sha256": "de31ffa5ba26916b8ea35319f6c12151ff9654d4261bccf0583a69bb095315f9",
    },
    ("ogkalu/comic-text-and-bubble-detector", "detector.onnx"): {
        "revision": "16e8a622f91fabc6b5b65c96d32d1183f8843546",
        "sha256": "065744e91c0594ad8663aa8b870ce3fb27222942eded5a3cc388ce23421bd195",
    },
    ("mayocream/comic-text-detector-onnx", "comic-text-detector.onnx"): {
        "revision": "a5d67ec772adef819ef5b0e7aa701fcf4c8bf74a",
        "sha256": "1a86ace74961413cbd650002e7bb4dcec4980ffa21b2f19b86933372071d718f",
    },
    # PaddleOCR files pinned from verified downloads: the recognition ONNX matches
    # the official PaddlePaddle/PP-OCRv5_server_rec weights numerically.
    ("xberg-io/paddleocr-onnx-models", "PP-OCRv5_server_det_infer.onnx"): {
        "revision": "bc5ec866cf0e798e667808dfa51b0ba8ad0dafc8",
        "sha256": "127edf0182bb3d218ad59476377b02ca90296cfb4cc85df55042d671a3e53aeb",
    },
    ("xberg-io/paddleocr-onnx-models", "rec/chinese/model.onnx"): {
        "revision": "bc5ec866cf0e798e667808dfa51b0ba8ad0dafc8",
        "sha256": "26fa4f47060f58e25962b9af6beaee05c8182b90e026c4ecc6db165d9dfdc38a",
    },
    ("PaddlePaddle/PP-OCRv5_server_rec", "inference.yml"): {
        "revision": "b26c3587fda8da3c8ec0ce357214b4d661ff1558",
        "sha256": "2c719dba044c4e2228aef8ff92f5f575394d75d24c16de096a33b7cfd902f66d",
    },
    # AnimeText stays unpinned: the repo is gated and hides its sha256s without auth.
    **{
        ("deepghs/AnimeText_yolo", f"yolo12{variant}_animetext/model.onnx"): ModelPin(**_UNPINNED)
        for variant in ("n", "s", "m", "l", "x")
    },
}


class ModelChecksumError(RuntimeError):
    """A model file did not match its pinned sha256."""


def get_model_pin(repo_id: str, filename: str) -> ModelPin:
    pin = MODEL_PINS.get((repo_id, filename)) or {}
    return {"revision": pin.get("revision"), "sha256": pin.get("sha256")}


def verify_sha256(path: str | Path, expected: str | None) -> None:
    """Check a file against a pinned sha256; delete it and raise on mismatch."""
    if not expected:
        return
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != expected.strip().lower():
        filename = path.name
        try:
            path.unlink()
        except OSError as error:
            logger.warning("Could not delete %s after checksum mismatch: %s", filename, error)
        raise ModelChecksumError(
            f"Model checksum mismatch for {filename}; re-download or update MODEL_PINS"
        )


def verify_cached_model(repo_id: str, filename: str, path: str | Path) -> None:
    """Verify an already-downloaded file when a sha256 is pinned for it."""
    verify_sha256(path, get_model_pin(repo_id, filename)["sha256"])


def download_pinned_model(repo_id: str, filename: str, **kwargs: Any) -> str:
    """hf_hub_download that forwards a pinned revision and verifies a pinned sha256."""
    pin = get_model_pin(repo_id, filename)
    if pin["revision"]:
        kwargs.setdefault("revision", pin["revision"])
    path = hf_hub_download(repo_id=repo_id, filename=filename, **kwargs)
    verify_sha256(path, pin["sha256"])
    return path


def _is_nonempty(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


MANGA_OCR_REPO = "mayocream/manga-ocr-onnx"
MANGA_OCR_FILES = {
    "encoder": "encoder_model.onnx",
    "decoder": "decoder_model.onnx",
    "vocab": "vocab.txt",
}
TEXT_SEGMENTER_REPO = "mayocream/comic-text-detector-onnx"
TEXT_SEGMENTER_FILENAME = "comic-text-detector.onnx"


async def download_manga_ocr_onnx(
    model_dir: str | Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, Path]:
    model_dir = Path(model_dir)
    await asyncio.to_thread(model_dir.mkdir, parents=True, exist_ok=True)

    paths = {label: model_dir / filename for label, filename in MANGA_OCR_FILES.items()}
    downloaded: set[str] = set()

    try:
        for label, filename in MANGA_OCR_FILES.items():
            if not await asyncio.to_thread(paths[label].exists):
                logger.info("Downloading manga-ocr %s...", label)
                await asyncio.to_thread(
                    download_pinned_model,
                    MANGA_OCR_REPO,
                    filename,
                    local_dir=model_dir,
                    local_dir_use_symlinks=False,
                )
                downloaded.add(filename)
    except ModelChecksumError:
        raise
    except Exception as error:
        logger.error("Failed to download manga-ocr model from %s: %s", MANGA_OCR_REPO, error)
        raise RuntimeError(
            "Could not download manga-ocr ONNX model from mayocream/manga-ocr-onnx. "
            "Please check your network or manually download the model."
        ) from error

    for label, path in paths.items():
        if not await asyncio.to_thread(_is_nonempty, path):
            raise RuntimeError(f"Download failed: {label} at {path} is empty or missing")

    # Freshly downloaded files were verified by download_pinned_model; check the rest.
    for label, filename in MANGA_OCR_FILES.items():
        if filename not in downloaded:
            await asyncio.to_thread(verify_cached_model, MANGA_OCR_REPO, filename, paths[label])

    if progress_callback:
        progress_callback(1, 1)

    return paths


async def _download_single_onnx(
    model_dir: str | Path,
    *,
    repo_id: str,
    filename: str,
    label: str,
    error_message: str,
    progress_callback: Callable[[int, int], None] | None = None,
) -> Path:
    model_dir = Path(model_dir)
    await asyncio.to_thread(model_dir.mkdir, parents=True, exist_ok=True)

    model_path = model_dir / filename
    cached = await asyncio.to_thread(model_path.exists)

    if not cached:
        try:
            logger.info("Downloading %s to %s...", label, model_path)
            await asyncio.to_thread(
                download_pinned_model,
                repo_id,
                filename,
                local_dir=model_dir,
                local_dir_use_symlinks=False,
            )
            logger.info("%s download complete", label)
        except ModelChecksumError:
            raise
        except Exception as error:
            logger.error("Failed to download %s from %s: %s", label, repo_id, error)
            raise RuntimeError(error_message) from error

    if not await asyncio.to_thread(_is_nonempty, model_path):
        raise RuntimeError(f"Download failed: {model_path} is empty or missing")

    if cached:
        await asyncio.to_thread(verify_cached_model, repo_id, filename, model_path)

    if progress_callback:
        progress_callback(1, 1)

    return model_path


async def download_lama_onnx(
    model_dir: str | Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> Path:
    return await _download_single_onnx(
        model_dir,
        repo_id="ogkalu/lama-manga-onnx-dynamic",
        filename="lama-manga-dynamic.onnx",
        label="LaMa inpainting model",
        error_message="Could not download LaMa inpainting model.",
        progress_callback=progress_callback,
    )


async def download_text_segmenter_onnx(
    model_dir: str | Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> Path:
    """Download and checksum-verify the comic-text-detector ONNX model."""
    return await _download_single_onnx(
        model_dir,
        repo_id=TEXT_SEGMENTER_REPO,
        filename=TEXT_SEGMENTER_FILENAME,
        label="text segmenter model",
        error_message="Could not download text segmenter ONNX model.",
        progress_callback=progress_callback,
    )
