"""AnimeText YOLO12 text-block detector using ONNX Runtime."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import numpy as np
import onnxruntime as ort
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError
from PIL import Image

from manga_backend.paths import MODELS_CACHE_DIR
from manga_backend.utils.boxes import nms, unletterbox_blocks
from manga_backend.utils.device import (
    PROVIDER_BY_DEVICE,
    Provider,
    create_onnx_session,
    get_onnx_providers,
)
from manga_backend.utils.download import download_pinned_model, verify_cached_model
from manga_backend.utils.textblock import TextBlock

logger = logging.getLogger(__name__)
MODELS_DIR = MODELS_CACHE_DIR / "animetext_yolo"
OFFICIAL_REPO = "deepghs/AnimeText_yolo"
PUBLISHED_THRESHOLDS = {
    "n": 0.269,
    "s": 0.272,
    "m": 0.299,
    "l": 0.347,
    "x": 0.425,
}


class AnimeTextYoloDetector:
    """Detect rectangular anime text blocks without changing the mask pipeline."""

    def __init__(self, device: str = "cpu") -> None:
        self.device = device
        self.variant = os.getenv("ANIMETEXT_YOLO_VARIANT", "m").strip().lower()
        if self.variant not in PUBLISHED_THRESHOLDS:
            raise ValueError(f"Unsupported AnimeText YOLO variant: {self.variant}")
        self.confidence_threshold = float(
            os.getenv(
                "ANIMETEXT_YOLO_CONFIDENCE",
                str(PUBLISHED_THRESHOLDS[self.variant]),
            )
        )
        self.iou_threshold = float(os.getenv("ANIMETEXT_YOLO_IOU", "0.35"))
        self.configured_input_size = os.getenv(
            "ANIMETEXT_YOLO_INPUT_SIZE", ""
        ).strip()
        self.input_size = 640
        self.max_candidates = int(
            os.getenv("ANIMETEXT_YOLO_MAX_CANDIDATES", "1000")
        )
        if not 0 < self.confidence_threshold <= 1:
            raise ValueError("ANIMETEXT_YOLO_CONFIDENCE must be greater than 0 and at most 1")
        if not 0 < self.iou_threshold < 1:
            raise ValueError("ANIMETEXT_YOLO_IOU must be greater than 0 and less than 1")
        if self.max_candidates <= 0:
            raise ValueError("ANIMETEXT_YOLO_MAX_CANDIDATES must be positive")
        self.session: ort.InferenceSession | None = None
        self.input_name = "images"
        self.output_name = "predictions"
        self._inference_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Load a local export, or the official gated ONNX when authorized."""
        configured_path = os.getenv("ANIMETEXT_YOLO_MODEL", "").strip()
        remote_filename = f"yolo12{self.variant}_animetext/model.onnx"
        if configured_path:
            # A user-supplied local export is used as-is and never replaced by a download.
            model_path = Path(configured_path).expanduser()
            if not model_path.exists():
                raise RuntimeError(
                    "ANIMETEXT_YOLO_MODEL does not point to an existing ONNX file"
                )
        else:
            exported_path = MODELS_DIR / f"yolo12{self.variant}_animetext.onnx"
            downloaded_path = MODELS_DIR / remote_filename
            if exported_path.exists():
                model_path = exported_path
            elif downloaded_path.exists():
                model_path = downloaded_path
                await asyncio.to_thread(
                    verify_cached_model, OFFICIAL_REPO, remote_filename, str(model_path)
                )
            else:
                logger.info("Downloading AnimeText YOLO%s from %s", self.variant, OFFICIAL_REPO)
                try:
                    downloaded = await asyncio.to_thread(
                        download_pinned_model,
                        OFFICIAL_REPO,
                        remote_filename,
                        local_dir=str(MODELS_DIR),
                    )
                except Exception as error:
                    if self._is_auth_failure(error):
                        raise RuntimeError(
                            f"AnimeText YOLO{self.variant} could not be downloaded from "
                            f"{OFFICIAL_REPO}: the repository is gated or requires Hugging "
                            "Face authentication. Authenticate with Hugging Face (accept "
                            "the repo terms, then log in or set HF_TOKEN), or export a "
                            "local ONNX with scripts/export_animetext_yolo.py and set "
                            "ANIMETEXT_YOLO_MODEL to it."
                        ) from error
                    raise
                model_path = Path(downloaded)
        if not model_path.is_file() or model_path.stat().st_size == 0:
            raise RuntimeError(
                f"AnimeText YOLO model is missing or empty: {model_path.name}"
            )

        providers = self._runtime_providers()
        session = await asyncio.to_thread(
            create_onnx_session, str(model_path), providers
        )
        self.session = session
        inputs = session.get_inputs()
        outputs = session.get_outputs()
        if len(inputs) != 1 or len(inputs[0].shape) != 4:
            raise RuntimeError("AnimeText YOLO must have one four-dimensional image input")
        if len(outputs) != 1:
            raise RuntimeError("AnimeText YOLO must have exactly one output tensor")
        self._validate_output_shape(outputs[0].shape)
        input_height, input_width = inputs[0].shape[-2:]
        if isinstance(input_height, int) and isinstance(input_width, int):
            if input_height != input_width:
                raise RuntimeError("AnimeText YOLO image input must be square")
            self.input_size = input_height
            if (
                self.configured_input_size
                and int(self.configured_input_size) != self.input_size
            ):
                raise ValueError(
                    "ANIMETEXT_YOLO_INPUT_SIZE does not match the fixed ONNX input"
                )
        elif self.configured_input_size:
            self.input_size = int(self.configured_input_size)
        if self.input_size <= 0:
            raise ValueError("ANIMETEXT_YOLO_INPUT_SIZE must be positive")
        self.input_name = inputs[0].name
        self.output_name = outputs[0].name
        logger.info(
            "AnimeText YOLO%s loaded with providers %s",
            self.variant,
            session.get_providers(),
        )

    async def detect(self, image: np.ndarray) -> list[TextBlock]:
        session = self.session
        if session is None:
            raise RuntimeError("Model not initialized. Call initialize() first.")
        tensor, scale, pad = self._letterbox(image, self.input_size)
        async with self._inference_lock:
            inference = asyncio.create_task(
                asyncio.to_thread(
                    session.run,
                    [self.output_name],
                    {self.input_name: tensor},
                )
            )
            try:
                output = await asyncio.shield(inference)
            except asyncio.CancelledError:
                await inference
                raise
        blocks = self._decode_predictions(
            cast(np.ndarray, output[0]),
            image.shape[:2],
            scale,
            pad,
            self.confidence_threshold,
            self.iou_threshold,
            self.max_candidates,
        )
        logger.info("AnimeText YOLO detected %d text blocks", len(blocks))
        return blocks

    @staticmethod
    def _is_auth_failure(error: BaseException) -> bool:
        if isinstance(error, GatedRepoError):
            return True
        if isinstance(error, HfHubHTTPError):
            status = getattr(getattr(error, "response", None), "status_code", None)
            return status in (401, 403)
        return False

    def _runtime_providers(self) -> list[Provider]:
        requested_device = (
            "cuda"
            if os.getenv("USE_GPU", "false").strip().lower() == "true"
            else self.device
        )
        providers = get_onnx_providers(requested_device)
        normalized_device = requested_device.strip().lower()
        expected = PROVIDER_BY_DEVICE.get(normalized_device)
        first = providers[0][0] if providers and isinstance(providers[0], tuple) else (
            providers[0] if providers else None
        )
        if expected and first != expected:
            raise RuntimeError(
                f"Requested AnimeText YOLO provider {expected} is unavailable"
            )
        return providers

    @staticmethod
    def _validate_output_shape(shape: Sequence[Any]) -> None:
        if len(shape) != 3 or shape[0] != 1 or shape[1] != 5:
            raise RuntimeError(
                f"AnimeText YOLO output must have shape [1, 5, N], got {shape}"
            )
        candidates = shape[2]
        if isinstance(candidates, int):
            valid_candidates = candidates > 0
        else:
            valid_candidates = isinstance(candidates, str) and bool(candidates.strip())
        if not valid_candidates:
            raise RuntimeError(
                f"AnimeText YOLO output must have shape [1, 5, N], got {shape}"
            )

    @staticmethod
    def _letterbox(
        image: np.ndarray, input_size: int
    ) -> tuple[np.ndarray, float, tuple[int, int]]:
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("AnimeText YOLO expects an RGB HxWx3 image")
        height, width = image.shape[:2]
        if height <= 0 or width <= 0:
            raise ValueError("AnimeText YOLO cannot process an empty image")

        scale = min(input_size / width, input_size / height)
        resized_width = max(1, int(round(width * scale)))
        resized_height = max(1, int(round(height * scale)))
        resized = np.asarray(
            Image.fromarray(image).resize(
                (resized_width, resized_height), Image.Resampling.BILINEAR
            )
        )
        pad_x = (input_size - resized_width) // 2
        pad_y = (input_size - resized_height) // 2
        canvas = np.full((input_size, input_size, 3), 114, dtype=np.uint8)
        canvas[
            pad_y : pad_y + resized_height,
            pad_x : pad_x + resized_width,
        ] = resized
        tensor = np.transpose(canvas.astype(np.float32) / 255.0, (2, 0, 1))
        return np.ascontiguousarray(tensor[None]), scale, (pad_x, pad_y)

    @classmethod
    def _decode_predictions(
        cls,
        predictions: np.ndarray,
        image_shape: tuple[int, int],
        scale: float,
        pad: tuple[int, int],
        confidence_threshold: float,
        iou_threshold: float,
        max_candidates: int = 1000,
    ) -> list[TextBlock]:
        values = np.asarray(predictions, dtype=np.float32)
        if values.ndim != 3 or values.shape[0] != 1 or values.shape[1] != 5:
            raise RuntimeError(f"Unexpected AnimeText YOLO output shape: {values.shape}")
        values = values[0].T

        values = values[np.isfinite(values).all(axis=1)]
        values = values[values[:, 4] >= confidence_threshold]
        if not len(values):
            return []
        if len(values) > max_candidates:
            highest = np.argpartition(values[:, 4], -max_candidates)[-max_candidates:]
            values = values[highest]
        centers = values[:, :2]
        sizes = values[:, 2:4]
        boxes = np.concatenate((centers - sizes / 2, centers + sizes / 2), axis=1)
        scores = values[:, 4]
        keep = nms(boxes, scores, iou_threshold)

        return unletterbox_blocks(boxes, scores, keep, image_shape, scale, pad)

    def dispose(self) -> None:
        if self.session is not None:
            del self.session
            self.session = None
