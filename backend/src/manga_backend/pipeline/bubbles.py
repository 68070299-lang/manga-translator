"""Bubble inference: render bounds and visual text centers for detected dialogue."""

from __future__ import annotations

import logging

import numpy as np

from manga_backend.pipeline.masks import bubble_interior_mask_for_block
from manga_backend.pipeline.regions import (
    clamp_xyxy,
    int_box,
    is_inside,
    longest_word_length,
    reading_order_source,
)
from manga_backend.pipeline.text import normalize_render_source
from manga_backend.utils.textblock import TextBlock

logger = logging.getLogger(__name__)


def infer_bubble_render_bounds(image: np.ndarray, blocks: list[TextBlock]) -> int:
    """Give YOLO dialogue the detected bubble interior as its render region."""
    height, width = image.shape[:2]
    inferred = 0
    for block in blocks:
        if block.block_type != "text" or block.bubble_xyxy is not None:
            continue
        interior, (x1, y1, _x2, _y2) = bubble_interior_mask_for_block(
            image, block
        )
        ys, xs = np.where(interior > 0)
        if not len(xs):
            continue
        bubble_xyxy = [
            int(xs.min() + x1),
            int(ys.min() + y1),
            int(xs.max() + x1 + 1),
            int(ys.max() + y1 + 1),
        ]
        body_center = bubble_body_center(interior, (x1, y1))
        visual_center = visual_bubble_center(body_center, bubble_xyxy)
        block_center = [
            (float(block.x1) + float(block.x2)) / 2.0,
            (float(block.y1) + float(block.y2)) / 2.0,
        ]
        if (
            abs(visual_center[0] - block_center[0]) > max(8.0, block.width * 0.6)
            or abs(visual_center[1] - block_center[1]) > max(8.0, block.height * 0.6)
        ):
            logger.debug(
                "Rejected off-center bubble inference for block %s: %s",
                block.xyxy.tolist(),
                bubble_xyxy,
            )
            continue
        block.bubble_xyxy = bubble_xyxy
        block.bubble_text_center = visual_center
        inferred += 1

    for parent in blocks:
        parent_source = normalize_render_source(parent.text)
        if not parent_source:
            continue
        children = [
            child
            for child in blocks
            if child is not parent
            and child.block_type == "text"
            and child.area < parent.area
            and is_inside(child, parent)
        ]
        if len(children) >= 2 and reading_order_source(parent, children) == parent_source:
            assign_composite_child_regions(parent, parent_source, children, width, height)

    logger.info("Inferred %s YOLO bubble render regions", inferred)
    return inferred


def assign_composite_child_regions(
    parent: TextBlock,
    parent_source: str,
    children: list[TextBlock],
    width: int,
    height: int,
) -> None:
    """Split the area around an OCR composite's parts into one region per part."""
    group_key = (int_box(parent.xyxy), parent_source)
    boxes: dict[int, list[int]] = {}
    for child in children:
        pad_x = max(4, int(round(child.width * 0.2)))
        pad_y = max(4, int(round(child.height * 0.1)))
        box = clamp_xyxy(
            [child.x1 - pad_x, child.y1 - pad_y, child.x2 + pad_x, child.y2 + pad_y],
            width,
            height,
        )
        boxes[id(child)] = box
        child.bubble_xyxy = box
        child.composite_render_group = group_key

    centers_x = [child.center[0] for child in children]
    centers_y = [child.center[1] for child in children]
    axis = 0 if max(centers_x) - min(centers_x) >= max(centers_y) - min(centers_y) else 1
    ordered_children = sorted(
        children,
        key=lambda child: (child.center[axis], child.center[1 - axis]),
    )
    for first, second in zip(ordered_children, ordered_children[1:]):
        first_box = boxes[id(first)]
        second_box = boxes[id(second)]
        overlap_start = max(first_box[axis], second_box[axis])
        overlap_end = min(first_box[axis + 2], second_box[axis + 2])
        if overlap_end <= overlap_start:
            continue

        split = (
            overlap_end
            if longest_word_length(first) >= longest_word_length(second)
            else overlap_start
        )
        first_box[axis + 2] = split
        second_box[axis] = split

    for child in children:
        bx1, by1, bx2, by2 = boxes[id(child)]
        child.bubble_text_center = [(bx1 + bx2) / 2.0, (by1 + by2) / 2.0]


def bubble_body_center(
    interior: np.ndarray, offset: tuple[int, int]
) -> list[float] | None:
    """Return the robust center of a bubble body while ignoring its tail."""
    ys, xs = np.where(interior > 0)
    if not len(xs):
        return None

    def median_center_of_long_spans(positions: np.ndarray, lines: np.ndarray) -> float:
        centers = []
        spans = []
        for line in np.unique(lines):
            line_positions = positions[lines == line]
            centers.append((float(line_positions.min()) + float(line_positions.max()) + 1) / 2)
            spans.append(len(line_positions))
        cutoff = float(np.percentile(spans, 40))
        return np.median([center for center, span in zip(centers, spans) if span >= cutoff])

    return [
        float(median_center_of_long_spans(xs, ys) + offset[0]),
        float(median_center_of_long_spans(ys, xs) + offset[1]),
    ]


def visual_bubble_center(
    body_center: list[float] | None, bubble_xyxy: list[int]
) -> list[float]:
    """Balance contour center with bounds so tail removal cannot over-correct."""
    bx1, by1, bx2, by2 = [float(value) for value in bubble_xyxy]
    bounds_center = [(bx1 + bx2) / 2.0, (by1 + by2) / 2.0]
    if body_center is None:
        return bounds_center
    body_weight = 0.6
    return [
        bounds + (float(body) - bounds) * body_weight
        for bounds, body in zip(bounds_center, body_center)
    ]


def infer_bubble_text_centers(image: np.ndarray, blocks: list[TextBlock]) -> int:
    """Attach contour-aware visual centers to all detected bubble regions."""
    inferred = 0
    for block in blocks:
        if (
            block.block_type != "text"
            or block.bubble_xyxy is None
            or block.bubble_text_center is not None
        ):
            continue
        interior, (x1, y1, _x2, _y2) = bubble_interior_mask_for_block(
            image, block
        )
        center = bubble_body_center(interior, (x1, y1))
        block.bubble_text_center = visual_bubble_center(
            center, block.bubble_xyxy
        )
        inferred += 1
    return inferred