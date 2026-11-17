"""
TextBlock data structure for storing detection and OCR results.
"""

from typing import List, Optional, Any
import numpy as np


def _validate_bbox(xyxy: List[int]) -> np.ndarray:
    arr = np.array(xyxy, dtype=np.int32)
    if arr.shape != (4,):
        raise ValueError(f"Bounding box must have exactly 4 values, got shape {arr.shape}")
    arr[0] = max(0, int(arr[0]))
    arr[1] = max(0, int(arr[1]))
    arr[2] = max(arr[0], int(arr[2]))
    arr[3] = max(arr[1], int(arr[3]))
    return arr


def _validate_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        return str(value)
    return value


class TextBlock:
    def __init__(
        self,
        xyxy: List[int],
        block_type: str = "text",
        text: str = "",
        translated_text: str = "",
    ):
        self._xyxy = _validate_bbox(xyxy)
        self.block_type = block_type
        self.text = _validate_text(text)
        self.translated_text = _validate_text(translated_text)
        self.bubble_xyxy: Optional[List[int]] = None
        self.confidence: float = 1.0
        self.language: str = "ja"

    @property
    def xyxy(self) -> np.ndarray:
        return self._xyxy

    @xyxy.setter
    def xyxy(self, value: Any):
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
    def center(self) -> tuple:
        return ((self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2)

    def __repr__(self) -> str:
        text_preview = self.text[:20] if self.text else ""
        return f"TextBlock(xyxy={self._xyxy.tolist()}, type={self.block_type}, text={text_preview}...)"
