"""Render-region layout: merge detected blocks into regions and make them disjoint."""

from __future__ import annotations

import re
from collections.abc import Callable

import numpy as np

from manga_backend.pipeline.regions import (
    clamp_xyxy,
    int_box,
    suppress_composite_render_regions,
)
from manga_backend.pipeline.text import (
    has_cjk,
    is_unchanged_punctuation,
    normalize_render_source,
)
from manga_backend.utils.textblock import Box, TextBlock

# fit_box(width, height) -> (padding, inner_width, inner_height, font_size)
FitLayout = tuple[int, int, int, int]
FitBox = Callable[[int, int], FitLayout]


def build_render_regions(
    blocks: list[TextBlock], image_shape: tuple[int, ...]
) -> list[TextBlock]:
    """Build non-mutating render regions while preserving detector/OCR blocks."""
    height, width = image_shape[:2]
    bubble_groups: dict[Box, list[TextBlock]] = {}
    ordered_items: list[tuple[Box | None, list[TextBlock]]] = []

    for block in suppress_composite_render_regions(blocks):
        translated = block.translated_text
        if translated.strip() == "":
            continue
        if is_unchanged_punctuation(block.text, translated):
            continue

        bubble = block.bubble_xyxy
        bubble_key: Box | None = None
        if block.block_type == "text" and bubble is not None:
            try:
                clamped = clamp_xyxy(int_box(bubble), width, height)
                if clamped[2] > clamped[0] and clamped[3] > clamped[1]:
                    bubble_key = int_box(clamped)
            except (TypeError, ValueError):
                bubble_key = None

        if bubble_key is None:
            ordered_items.append((None, [block]))
        elif bubble_key in bubble_groups:
            bubble_groups[bubble_key].append(block)
        else:
            bubble_groups[bubble_key] = [block]
            ordered_items.append((bubble_key, bubble_groups[bubble_key]))

    regions: list[TextBlock] = []
    for bubble_key, grouped in ordered_items:
        region = (
            standalone_region(grouped[0], width, height)
            if bubble_key is None
            else bubble_region(bubble_key, grouped)
        )
        if region is not None:
            regions.append(region)

    return partition_render_regions(suppress_composite_render_regions(regions))


def standalone_region(source: TextBlock, width: int, height: int) -> TextBlock | None:
    coords = clamp_xyxy([int(coord) for coord in source.xyxy], width, height)
    if coords[2] <= coords[0] or coords[3] <= coords[1]:
        return None
    region = TextBlock(
        coords,
        block_type=source.block_type,
        text=source.text,
        translated_text=source.translated_text,
    )
    region.confidence = source.confidence
    region.render_from_bubble = False
    if source.bubble_text_center is not None:
        region.bubble_text_center = list(source.bubble_text_center)
    return region


def bubble_region(bubble_key: Box, blocks: list[TextBlock]) -> TextBlock:
    """Merge the blocks sharing a bubble into one region inset from its edge."""
    deduplicated: dict[tuple[str, str], TextBlock] = {}
    for block in blocks:
        key = (
            normalize_render_source(block.text),
            normalize_render_source(block.translated_text),
        )
        existing = deduplicated.get(key)
        if existing is None or block.confidence > existing.confidence:
            deduplicated[key] = block
    grouped = list(deduplicated.values())

    vertical_source = sum(
        block.height > block.width * 1.25 for block in grouped
    ) > len(grouped) / 2
    if vertical_source:
        grouped.sort(key=lambda block: (-block.x2, block.y1))
    else:
        grouped.sort(key=lambda block: (block.y1, block.x1))

    composite_groups = {
        block.composite_render_group
        for block in grouped
        if block.composite_render_group is not None
    }
    composite_group = next(iter(composite_groups)) if len(composite_groups) == 1 else None

    bx1, by1, bx2, by2 = bubble_key
    inset = 0
    if composite_group is None:
        shorter_side = min(bx2 - bx1, by2 - by1)
        inset = min(8, max(2, int(shorter_side * 0.05)), max(0, (shorter_side - 1) // 2))
    coords = [bx1 + inset, by1 + inset, bx2 - inset, by2 - inset]
    if coords[2] <= coords[0] or coords[3] <= coords[1]:
        coords = list(bubble_key)

    def joined(values: list[str]) -> str:
        stripped = (value.strip() for value in values)
        return " ".join(value for value in stripped if value)

    region = TextBlock(
        coords,
        block_type="text",
        text=joined([block.text for block in grouped]),
        translated_text=joined([block.translated_text for block in grouped]),
    )
    region.confidence = max(
        (block.confidence for block in grouped),
        default=1.0,
    )
    region.render_from_bubble = True
    body_centers = [
        block.bubble_text_center
        for block in grouped
        if block.bubble_text_center is not None
    ]
    if body_centers:
        region.bubble_text_center = [
            float(np.median([center[0] for center in body_centers])),
            float(np.median([center[1] for center in body_centers])),
        ]
    if composite_group is not None:
        region.composite_render_group = composite_group
    return region


def expand_short_standalone_regions(
    regions: list[TextBlock], image_shape: tuple[int, ...]
) -> int:
    """Give tiny short labels enough room to remain readable."""
    height, width = image_shape[:2]
    expanded = 0

    def grown_start(
        start: int,
        end: int,
        center: float,
        neighbor_center: float | None,
        size: int,
        original_size: int,
    ) -> int:
        # Grow away from the nearest bubble, or evenly when there is none.
        if size == original_size or (neighbor_center is not None and center > neighbor_center):
            return start
        if neighbor_center is not None and center < neighbor_center:
            return end - size
        return int(round(center - size / 2))

    for region in regions:
        if region.render_from_bubble:
            continue
        text = str(region.translated_text or "").strip()
        compact = re.sub(r"\s+", "", text)
        if not compact or len(compact) > 12 or has_cjk(compact):
            continue

        original_width = region.width
        original_height = region.height
        target_width = min(
            width,
            original_width + 16,
            max(original_width, min(48, max(30, len(compact) * 6))),
        )
        target_height = min(height, original_height + 12, max(original_height, 24))
        if target_width == original_width and target_height == original_height:
            continue

        center_x, center_y = region.center
        others = [other for other in regions if other is not region]
        nearest_bubble = min(
            (other for other in others if other.render_from_bubble),
            key=lambda other: (center_x - other.center[0]) ** 2
            + (center_y - other.center[1]) ** 2,
            default=None,
        )
        neighbor_x: float | None
        neighbor_y: float | None
        neighbor_x, neighbor_y = (
            nearest_bubble.center if nearest_bubble is not None else (None, None)
        )

        def candidate_box(box_width: int, box_height: int) -> list[int]:
            x1 = grown_start(region.x1, region.x2, center_x, neighbor_x, box_width, original_width)
            y1 = grown_start(region.y1, region.y2, center_y, neighbor_y, box_height, original_height)
            x1 = max(0, min(width - box_width, x1))
            y1 = max(0, min(height - box_height, y1))
            return [x1, y1, x1 + box_width, y1 + box_height]

        def overlaps_other(box: list[int]) -> bool:
            return any(
                box[0] < other.x2 and box[2] > other.x1 and box[1] < other.y2 and box[3] > other.y1
                for other in others
            )

        best = next(
            (
                box
                for box_width in range(target_width, original_width - 1, -1)
                for box_height in range(target_height, original_height - 1, -1)
                for box in [candidate_box(box_width, box_height)]
                if not overlaps_other(box)
            ),
            None,
        )

        if best is not None and best != region.xyxy.tolist():
            region.xyxy = best
            expanded += 1

    return expanded


def partition_render_regions(regions: list[TextBlock]) -> list[TextBlock]:
    """Shrink overlapping render rectangles into pairwise-disjoint regions."""
    ordered = sorted(
        regions,
        key=lambda region: (
            region.y1,
            region.x1,
            region.y2,
            region.x2,
            normalize_render_source(region.text),
        ),
    )
    discarded: set[int] = set()
    minimum_side = 12

    def long_enough(box: list[int], original: list[int]) -> bool:
        return (
            box[2] - box[0] >= min(minimum_side, original[2] - original[0])
            and box[3] - box[1] >= min(minimum_side, original[3] - original[1])
        )

    for first_index, first in enumerate(ordered):
        if first_index in discarded:
            continue
        for second_index in range(first_index + 1, len(ordered)):
            if second_index in discarded:
                continue
            second = ordered[second_index]
            overlap_x1 = max(first.x1, second.x1)
            overlap_y1 = max(first.y1, second.y1)
            overlap_x2 = min(first.x2, second.x2)
            overlap_y2 = min(first.y2, second.y2)
            if overlap_x2 <= overlap_x1 or overlap_y2 <= overlap_y1:
                continue

            best: tuple[tuple[float, float, int, bool, int], list[int], list[int]] | None = None
            first_box: list[int] = first.xyxy.tolist()
            second_box: list[int] = second.xyxy.tolist()
            first_area = max(first.area, 1)
            second_area = max(second.area, 1)

            for axis, start, end in (
                (0, overlap_x1, overlap_x2),
                (1, overlap_y1, overlap_y2),
            ):
                first_is_before = (first.center[axis], first_box[axis], first_index) <= (
                    second.center[axis],
                    second_box[axis],
                    second_index,
                )
                for split in range(start, end + 1):
                    candidate_first = list(first_box)
                    candidate_second = list(second_box)
                    before, after = (
                        (candidate_first, candidate_second)
                        if first_is_before
                        else (candidate_second, candidate_first)
                    )
                    before[axis + 2] = min(before[axis + 2], split)
                    after[axis] = max(after[axis], split)

                    if not (long_enough(candidate_first, first_box) and long_enough(candidate_second, second_box)):
                        continue
                    retained_first = (candidate_first[2] - candidate_first[0]) * (candidate_first[3] - candidate_first[1])
                    retained_second = (candidate_second[2] - candidate_second[0]) * (candidate_second[3] - candidate_second[1])
                    score = (
                        min(retained_first / first_area, retained_second / second_area),
                        retained_first / first_area + retained_second / second_area,
                        retained_first + retained_second,
                        axis == 0,
                        -split,
                    )
                    if best is None or score > best[0]:
                        best = (score, candidate_first, candidate_second)

            if best is None:
                first_rank = (first.confidence, first.area, -first_index)
                second_rank = (second.confidence, second.area, -second_index)
                discarded.add(second_index if first_rank >= second_rank else first_index)
                if first_index in discarded:
                    break
                continue

            first.xyxy = best[1]
            second.xyxy = best[2]

    return [
        region for index, region in enumerate(ordered) if index not in discarded
    ]


def find_overflow_region(
    block: TextBlock,
    regions: list[TextBlock],
    image_shape: tuple[int, ...],
    fit_box: FitBox,
    minimum_font_size: int,
) -> tuple[list[int], FitLayout] | None:
    """Find the smallest, least overlapping growth that reaches a readable font."""
    x1, y1, x2, y2 = [int(value) for value in block.xyxy]
    width, height = x2 - x1, y2 - y1
    image_height, image_width = image_shape[:2]
    group = block.composite_render_group
    width_growth = 16 if group is not None else max(48, min(72, int(round(width * 0.75))))
    height_growth = 12 if group is not None else max(16, min(48, int(round(height * 0.3))))

    def dimensions(start: int, end: int) -> list[int]:
        values = list(range(start, end + 1, 2))
        if not values or values[-1] != end:
            values.append(end)
        return values

    neighbors = [
        other
        for other in regions
        if other is not block
        and (group is None or other.composite_render_group != group)
    ]
    candidate_sizes = sorted(
        (
            (candidate_width, candidate_height)
            for candidate_width in dimensions(width, min(image_width, width + width_growth))
            for candidate_height in dimensions(height, min(image_height, height + height_growth))
        ),
        key=lambda size: (size[0] - width + size[1] - height, size[0] * size[1]),
    )

    best = None
    best_growth = None
    for candidate_width, candidate_height in candidate_sizes:
        growth = candidate_width - width + candidate_height - height
        if best_growth is not None and growth > best_growth:
            break
        layout = fit_box(candidate_width, candidate_height)
        if layout[3] < minimum_font_size:
            continue

        left = max(0, min(image_width - candidate_width, int(round((x1 + x2) / 2 - candidate_width / 2))))
        top = max(0, min(image_height - candidate_height, int(round((y1 + y2) / 2 - candidate_height / 2))))
        right, bottom = left + candidate_width, top + candidate_height

        overlap_area = sum(
            max(0, min(right, other.x2) - max(left, other.x1))
            * max(0, min(bottom, other.y2) - max(top, other.y1))
            for other in neighbors
        )
        score = (overlap_area, candidate_width * candidate_height)
        if best is None or score < best[0]:
            best_growth = growth
            best = (score, [left, top, right, bottom], layout)

    return None if best is None else (best[1], best[2])