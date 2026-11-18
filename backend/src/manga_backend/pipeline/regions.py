"""Geometry and de-duplication helpers shared by masking, bubbles and layout."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from manga_backend.pipeline.text import normalize_render_source
from manga_backend.utils.textblock import Box, TextBlock


def int_box(values: Iterable[Any]) -> Box:
    """Coerce four coordinates to an int xyxy tuple."""
    x1, y1, x2, y2 = (int(value) for value in values)
    return x1, y1, x2, y2


def clamp_xyxy(box: Iterable[int], width: int, height: int) -> list[int]:
    x1, y1, x2, y2 = box
    return [
        max(0, min(width, x1)),
        max(0, min(height, y1)),
        max(0, min(width, x2)),
        max(0, min(height, y2)),
    ]


def longest_word_length(block: TextBlock) -> int:
    words = re.findall(r"[\w']+", str(block.translated_text or ""), flags=re.UNICODE)
    return max((len(word) for word in words), default=1)


def is_inside(child: TextBlock, parent: TextBlock) -> bool:
    return (
        child.x1 >= parent.x1
        and child.y1 >= parent.y1
        and child.x2 <= parent.x2
        and child.y2 <= parent.y2
    )


def reading_order_source(parent: TextBlock, children: list[TextBlock]) -> str:
    """Sort children (in place) into the parent's reading order and join their text."""
    if parent.height > parent.width * 1.25:
        children.sort(key=lambda child: (-child.x2, child.y1))
    else:
        children.sort(key=lambda child: (child.y1, child.x1))
    return "".join(normalize_render_source(child.text) for child in children)


def suppress_composite_render_regions(regions: list[TextBlock]) -> list[TextBlock]:
    """Drop exact OCR composites while retaining legitimate nested text."""

    def duplicate_rank(region: TextBlock) -> tuple[bool, float]:
        return (
            region.render_from_bubble
            or (region.block_type == "text" and region.bubble_xyxy is not None),
            region.confidence,
        )

    unique: dict[tuple[Box, str], TextBlock] = {}
    for region in regions:
        key = (int_box(region.xyxy), normalize_render_source(region.text))
        existing = unique.get(key)
        if existing is None or duplicate_rank(region) > duplicate_rank(existing):
            unique[key] = region

    candidates = list(unique.values())
    suppressed: set[int] = set()
    for first_index, first in enumerate(candidates):
        if first_index in suppressed:
            continue
        first_key = (
            normalize_render_source(first.text),
            normalize_render_source(first.translated_text),
        )
        if not any(first_key):
            continue
        for second_index in range(first_index + 1, len(candidates)):
            if second_index in suppressed:
                continue
            second = candidates[second_index]
            second_key = (
                normalize_render_source(second.text),
                normalize_render_source(second.translated_text),
            )
            if first_key != second_key:
                continue
            overlap_width = max(0, min(first.x2, second.x2) - max(first.x1, second.x1))
            overlap_height = max(0, min(first.y2, second.y2) - max(first.y1, second.y1))
            if overlap_width * overlap_height < min(first.area, second.area) * 0.8:
                continue
            first_rank = (first.render_from_bubble, first.area, first.confidence)
            second_rank = (second.render_from_bubble, second.area, second.confidence)
            suppressed.add(second_index if first_rank >= second_rank else first_index)
            if first_index in suppressed:
                break

    for parent_index, parent in enumerate(candidates):
        if parent_index in suppressed or parent.render_from_bubble:
            continue
        parent_source = normalize_render_source(parent.text)
        if not parent_source:
            continue

        children = [
            child
            for child_index, child in enumerate(candidates)
            if child_index != parent_index
            and child_index not in suppressed
            and child.area < parent.area
            and is_inside(child, parent)
        ]
        if children and reading_order_source(parent, children) == parent_source:
            suppressed.add(parent_index)

    return [region for index, region in enumerate(candidates) if index not in suppressed]
