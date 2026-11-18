"""Detector selection, lazy optional detectors and block serialization."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

import numpy as np

from manga_backend.models.detector import TextDetector
from manga_backend.pipeline.errors import DetectorUnavailableError
from manga_backend.utils.textblock import TextBlock

logger = logging.getLogger(__name__)

_DETECTOR_ALIASES = {
    "": "rtdetr",
    "default": "rtdetr",
    "rtdetr": "rtdetr",
    "animetext": "animetext-yolo",
    "anime-text-yolo": "animetext-yolo",
    "animetext-yolo": "animetext-yolo",
}


class BlockDetector(Protocol):
    async def detect(self, image: np.ndarray) -> list[TextBlock]: ...


def normalize_detector_type(detector_type: str) -> str:
    """Return the canonical detector name used by routing and masking."""
    normalized = detector_type.strip().lower().replace("_", "-")
    selected = _DETECTOR_ALIASES.get(normalized)
    if selected is None:
        raise ValueError(f"Unsupported detector: {detector_type}")
    return selected


def text_block_to_dict(block: TextBlock) -> dict[str, Any]:
    """Serialize a TextBlock using the API response shape."""
    payload: dict[str, Any] = {"bbox": block.xyxy.tolist(), "type": block.block_type}
    if block.bubble_xyxy is not None:
        payload["bubble_bbox"] = [int(value) for value in block.bubble_xyxy]
    if block.bubble_text_center is not None:
        payload["bubble_center"] = [float(value) for value in block.bubble_text_center]
    return payload


class DetectorRegistry:
    """Holds the startup RT-DETR detector and lazily created optional ones."""

    def __init__(self, device: str, model_status: dict[str, dict[str, Any]]) -> None:
        self.device = device
        self.default: TextDetector | None = None
        self.optional: dict[str, BlockDetector] = {}
        self._model_status = model_status
        self._initialization_lock = asyncio.Lock()

    async def get(self, detector_type: str) -> BlockDetector:
        """Return RT-DETR or lazily initialize AnimeText YOLO."""
        selected = normalize_detector_type(detector_type)
        if selected == "rtdetr":
            if self.default is None:
                raise RuntimeError("RT-DETR detector not initialized")
            return self.default

        existing = self.optional.get(selected)
        if existing is not None:
            return existing
        async with self._initialization_lock:
            existing = self.optional.get(selected)
            if existing is not None:
                return existing

            from manga_backend.models.animetext_yolo_detector import (
                AnimeTextYoloDetector,
            )

            status = self._model_status["animetext_yolo"]
            status.update({"status": "loading", "name": selected})
            candidate = None
            try:
                candidate = AnimeTextYoloDetector(device=self.device)
                await candidate.initialize()
            except Exception as error:
                dispose = getattr(candidate, "dispose", None) if candidate else None
                if callable(dispose):
                    dispose()
                status.update({"status": "error", "error": str(error)})
                raise DetectorUnavailableError(
                    f"Failed to initialize {selected}: {error}", detector=selected
                ) from error
            self.optional[selected] = candidate
            status.update({"status": "loaded", "name": selected})
            status.pop("error", None)
            return candidate

    def dispose(self) -> None:
        """Release every loaded detector runtime."""
        seen: set[int] = set()
        for instance in (self.default, *self.optional.values()):
            if instance is None or id(instance) in seen:
                continue
            seen.add(id(instance))
            dispose = getattr(instance, "dispose", None)
            if callable(dispose):
                dispose()
        self.optional.clear()
