"""TextBlock data structure for storing detection and OCR results."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

Box = tuple[int, int, int, int]
CompositeGroup = tuple[Box, str]


def _validate_bbox(xyxy: Sequence[Any] | NDArray[Any]) -> NDArray[np.int32]:
    arr = np.array(xyxy, dtype=np.int32)
    if arr.shape != (4,):
        raise ValueError(f"Bounding box must have exactly 4 values, got shape {arr.shape}")
    arr[0] = max(0, int(arr[0]))
    arr[1] = max(0, int(arr[1]))
    arr[2] = max(arr[0], int(arr[2]))
    arr[3] = max(arr[1], int(arr[3]))
    return arr


def _validate_text(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        return str(value)
    return value


@dataclass(slots=True, eq=False, repr=False, init=False)
class TextBlock:
    """One detected text region plus its OCR, translation and layout hints.

    Blocks compare by identity because they are mutated while moving through
    the pipeline. ``xyxy`` is validated (clamped to non-negative, ordered
    corners) on every assignment.
    """

    _xyxy: NDArray[np.int32]
    block_type: str
    text: str
    translated_text: str
    bubble_xyxy: list[int] | None
    bubble_text_center: list[float] | None
    confidence: float
    language: str
    verified_text: str | None
    verification_passed: bool | None
    # Layout hints written while building render regions.
    render_from_bubble: bool
    composite_render_group: CompositeGroup | None

    def __init__(
        self,
        xyxy: Sequence[Any] | NDArray[Any],
        block_type: str = "text",
        text: str = "",
        translated_text: str = "",
    ) -> None:
        self._xyxy = _validate_bbox(xyxy)
        self.block_type = block_type
        self.text = _validate_text(text)
        self.translated_text = _validate_text(translated_text)
        self.bubble_xyxy = None
        self.bubble_text_center = None
        self.confidence = 1.0
        self.language = "ja"
        self.verified_text = None
        self.verification_passed = None
        self.render_from_bubble = False
        self.composite_render_group = None

    @property
    def xyxy(self) -> NDArray[np.int32]:
        return self._xyxy

    @xyxy.setter
    def xyxy(self, value: Sequence[Any] | NDArray[Any]) -> None:
        self._xyxy = _validate_bbox(value)

    @property
    def x1(self) -> int:
        return int(self._xyxy[0])

    @property
    def y1(self) -> int:
        return int(self._xyxy[1])

    @property
    def x2(self) -> int:
        return int(self._xyxy[2])

    @property
    def y2(self) -> int:
        return int(self._xyxy[3])

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def center(self) -> tuple[int, int]:
        return ((self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2)

    def __repr__(self) -> str:
        text_preview = self.text[:20] if self.text else ""
        return f"TextBlock(xyxy={self._xyxy.tolist()}, type={self.block_type}, text={text_preview}...)"
