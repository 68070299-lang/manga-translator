"""Shared helpers that turn detector output (boxes + scores) into TextBlocks."""

from __future__ import annotations

import numpy as np

from manga_backend.utils.textblock import TextBlock


def nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
    """Greedy non-maximum suppression; returns kept indices in score order."""
    order = np.argsort(scores)[::-1]
    keep: list[int] = []
    while order.size:
        current = int(order[0])
        keep.append(current)
        if order.size == 1:
            break
        rest = order[1:]
        ix1 = np.maximum(boxes[current, 0], boxes[rest, 0])
        iy1 = np.maximum(boxes[current, 1], boxes[rest, 1])
        ix2 = np.minimum(boxes[current, 2], boxes[rest, 2])
        iy2 = np.minimum(boxes[current, 3], boxes[rest, 3])
        intersection = np.maximum(0.0, ix2 - ix1) * np.maximum(0.0, iy2 - iy1)
        current_area = max(
            0.0,
            float(boxes[current, 2] - boxes[current, 0])
            * float(boxes[current, 3] - boxes[current, 1]),
        )
        rest_area = np.maximum(0.0, boxes[rest, 2] - boxes[rest, 0]) * np.maximum(
            0.0, boxes[rest, 3] - boxes[rest, 1]
        )
        union = current_area + rest_area - intersection
        iou = np.divide(
            intersection,
            union,
            out=np.zeros_like(intersection),
            where=union > 0,
        )
        order = rest[iou <= threshold]
    return keep


def unletterbox_blocks(
    boxes: np.ndarray,
    scores: np.ndarray,
    keep: list[int],
    image_shape: tuple[int, int],
    scale: float,
    pad: tuple[int, int],
) -> list[TextBlock]:
    """Map letterboxed boxes back to image coordinates and build text blocks."""
    height, width = image_shape
    pad_x, pad_y = pad
    blocks: list[TextBlock] = []
    for idx in keep:
        bx1, by1, bx2, by2 = boxes[idx]
        x1 = max(0.0, min(float(width), (float(bx1) - pad_x) / scale))
        y1 = max(0.0, min(float(height), (float(by1) - pad_y) / scale))
        x2 = max(0.0, min(float(width), (float(bx2) - pad_x) / scale))
        y2 = max(0.0, min(float(height), (float(by2) - pad_y) / scale))
        xyxy = [
            int(np.floor(x1)),
            int(np.floor(y1)),
            int(np.ceil(x2)),
            int(np.ceil(y2)),
        ]
        if xyxy[2] <= xyxy[0] or xyxy[3] <= xyxy[1]:
            continue
        block = TextBlock(xyxy=xyxy, block_type="text")
        block.confidence = float(scores[idx])
        blocks.append(block)
    return blocks
