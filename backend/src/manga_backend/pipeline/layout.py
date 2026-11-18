"""Render-region layout: reading order, translation units and disjoint render regions."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

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


# ---------------------------------------------------------------------------
# Reading order
# ---------------------------------------------------------------------------
#
# Blocks reach the translator in detector output order, which is effectively
# confidence order. This section orders them the way a reader would: panels
# first (found as the regions between paper-colored gutters, split
# recursively by whole rows and columns), then the lettering inside each panel
# by recursive row/column splits. Manga reads right to left; tall webtoon
# strips read left to right (see ``resolve_reading_direction``). The method
# follows koharu's ``order.rs`` (MIT OR Apache-2.0) and Kovanen and Aizawa,
# "A layered method for determining manga text bubble reading order",
# ICIP 2015.

# A pixel within this many levels (per channel) of the page's paper color
# counts as paper when looking for gutters.
_PAPER_TOLERANCE = 12
# Share of a row/column that must be paper before it can be a gutter.
_PAPER_SPAN_FRACTION = 0.995
# Cuts must leave at least this much page on each side, so margins and thin
# slivers of art cannot shred a panel.
_MIN_PANEL_PX = 16
_MIN_PANEL_FRACTION = 0.06
_MAX_PANEL_DEPTH = 8
# Pages at least this tall relative to their width read left to right.
_WEBTOON_MIN_ASPECT = 1.6


def resolve_reading_direction(direction: str, image_shape: Sequence[int]) -> str:
    """Return "rtl" or "ltr" for one page.

    The accepted values are "rtl", "ltr" and "auto" (the default). "auto"
    reads the page as a manga (right to left) unless it is a tall strip —
    height >= 1.6 x width — which is read as a webtoon, left to right.
    """
    normalized = str(direction or "auto").strip().lower()
    if normalized in ("rtl", "ltr"):
        return normalized
    if normalized not in ("", "auto"):
        raise ValueError(f"Unsupported reading direction: {direction!r}")
    height, width = int(image_shape[0]), int(image_shape[1])
    return "ltr" if height >= _WEBTOON_MIN_ASPECT * max(1, width) else "rtl"


def _page_paper_mask(image: np.ndarray) -> np.ndarray:
    """Mask the pixels that carry the page's paper color.

    The paper color is the median of a thin ring around the page edge, where
    manga pages are blank even when the panels are full of art.
    """
    height, width = image.shape[:2]
    margin = max(4, int(round(min(height, width) * 0.02)))
    ring = np.concatenate(
        [
            image[:margin].reshape(-1, image.shape[2]),
            image[height - margin :].reshape(-1, image.shape[2]),
            image[:, :margin].reshape(-1, image.shape[2]),
            image[:, width - margin :].reshape(-1, image.shape[2]),
        ]
    )
    paper = np.median(ring, axis=0)
    difference = np.abs(image.astype(np.int16) - paper).max(axis=2)
    return difference <= _PAPER_TOLERANCE


def _gutter_bands(region_mask: np.ndarray, axis: int) -> list[tuple[int, int]]:
    """Maximal runs of rows (axis 0) or columns (axis 1) that are all paper."""
    spanning = region_mask.mean(axis=1 - axis) >= _PAPER_SPAN_FRACTION
    bands: list[tuple[int, int]] = []
    start: int | None = None
    for index, flag in enumerate(spanning):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            bands.append((start, index))
            start = None
    if start is not None:
        bands.append((start, len(spanning)))
    return bands


def _cut_pieces(
    bounds: list[int], paper: np.ndarray, axis: int, min_piece: int
) -> list[list[int]] | None:
    """Split a region along one axis at its valid gutters, or None if it has none.

    A gutter band is a valid cut when the page left between the previous cut
    and this one, and the page left after this cut, both still reach
    ``min_piece`` along the axis. Checking against the region end (not the
    next band) matters: the interior of a sparse panel is paper too, and must
    not stop a real gutter next to it from cutting.
    """
    x1, y1, x2, y2 = bounds
    region = paper[y1:y2, x1:x2]
    if region.size == 0:
        return None
    axis_length = region.shape[axis]
    bands = _gutter_bands(region, axis)

    cuts: list[tuple[int, int]] = []
    for start, end in bands:
        left = start - (cuts[-1][1] if cuts else 0)
        right = axis_length - end
        if left >= min_piece and right >= min_piece:
            cuts.append((start, end))
    if not cuts:
        return None

    # Piece boundaries along the axis: region start, then each accepted cut's
    # (start, end), then the region end. The bands themselves are dropped.
    boundaries = [0]
    boundaries.extend(value for cut in cuts for value in cut)
    boundaries.append(axis_length)

    pieces: list[list[int]] = []
    for inside_start, inside_end in zip(boundaries[0::2], boundaries[1::2]):
        if inside_end <= inside_start:
            continue
        if axis == 0:
            pieces.append([x1, y1 + inside_start, x2, y1 + inside_end])
        else:
            pieces.append([x1 + inside_start, y1, x1 + inside_end, y2])
    return pieces or None


def _split_panels(
    bounds: list[int],
    paper: np.ndarray,
    direction: str,
    axis: int,
    min_piece: int,
    depth: int,
    tried_other_axis: bool = False,
) -> list[list[int]]:
    """Recursively split a region into panels, returned in reading order."""
    if depth >= _MAX_PANEL_DEPTH:
        return [bounds]
    pieces = _cut_pieces(bounds, paper, axis, min_piece)
    if pieces is None:
        if tried_other_axis:
            return [bounds]
        return _split_panels(
            bounds, paper, direction, 1 - axis, min_piece, depth, True
        )
    if axis == 1 and direction == "rtl":
        pieces.reverse()
    panels: list[list[int]] = []
    for piece in pieces:
        panels += _split_panels(
            piece, paper, direction, 1 - axis, min_piece, depth + 1
        )
    return panels


def detect_panels(image: np.ndarray, direction: str = "auto") -> list[list[int]]:
    """Find the page's panels (xyxy boxes) in reading order.

    Panels are the regions that remain after cutting the page along its
    paper-colored gutters, rows first and then columns, recursively. A page
    with no detectable gutters (a webtoon strip, a full-bleed spread) stays
    one panel.
    """
    height, width = image.shape[:2]
    resolved = resolve_reading_direction(direction, image.shape)
    paper = _page_paper_mask(image)
    min_piece = max(
        _MIN_PANEL_PX, int(round(_MIN_PANEL_FRACTION * max(height, width)))
    )
    return _split_panels([0, 0, int(width), int(height)], paper, resolved, 0, min_piece, 0)


def _interval_groups(
    blocks: Sequence[TextBlock], axis: int
) -> list[list[TextBlock]]:
    """Group blocks whose spans on one axis overlap into connected chains.

    Axis 0 means rows, so the blocks are chained by their y spans (xyxy[1],
    xyxy[3]); axis 1 means columns, chained by x spans.
    """
    span_start, span_end = 1 - axis, 3 - axis
    ordered = sorted(
        blocks,
        key=lambda block: (
            int(block.xyxy[span_start]),
            int(block.xyxy[span_end]),
            int(block.xyxy[axis]),
            int(block.xyxy[axis + 2]),
        ),
    )
    groups: list[list[TextBlock]] = []
    current: list[TextBlock] = []
    group_end: int | None = None
    for block in ordered:
        start, end = int(block.xyxy[span_start]), int(block.xyxy[span_end])
        if current and (group_end is None or start >= group_end):
            groups.append(current)
            current = []
        current.append(block)
        group_end = end if group_end is None else max(group_end, end)
    if current:
        groups.append(current)
    return groups


def _leaf_key(block: TextBlock, direction: str) -> tuple[float, ...]:
    center_x = (int(block.x1) + int(block.x2)) / 2.0
    center_y = (int(block.y1) + int(block.y2)) / 2.0
    if direction == "rtl":
        return (-center_x, center_y, -int(block.x2), int(block.y1), int(block.x1))
    return (center_x, center_y, int(block.x1), int(block.y1), int(block.x2))


def _order_within_region(
    blocks: Sequence[TextBlock], direction: str, axis: int
) -> list[TextBlock]:
    """Order blocks by recursive row/column splits; ties keep input order."""
    if len(blocks) <= 1:
        return list(blocks)
    groups = _interval_groups(blocks, axis)
    if len(groups) == 1:
        if axis == 0:
            return _order_within_region(blocks, direction, 1)
        return sorted(blocks, key=lambda block: _leaf_key(block, direction))
    if axis == 1 and direction == "rtl":
        groups.reverse()
    ordered: list[TextBlock] = []
    for group in groups:
        ordered += _order_within_region(group, direction, 1 - axis)
    return ordered


def sort_reading_order(
    image: np.ndarray,
    blocks: Sequence[TextBlock],
    direction: str = "auto",
) -> list[TextBlock]:
    """Return the blocks in reading order: panels first, lettering inside each.

    The returned list reuses the caller's block objects without mutating them;
    ties (identical geometry, e.g. duplicate detections) keep detector order.
    """
    ordered = list(blocks)
    if len(ordered) <= 1:
        return ordered
    resolved = resolve_reading_direction(direction, image.shape)
    panels = detect_panels(image, resolved)

    def panel_of(block: TextBlock) -> int:
        center_x, center_y = (int(block.x1) + int(block.x2)) / 2.0, (
            int(block.y1) + int(block.y2)
        ) / 2.0
        for index, (x1, y1, x2, y2) in enumerate(panels):
            if x1 <= center_x < x2 and y1 <= center_y < y2:
                return index
        return min(
            range(len(panels)),
            key=lambda index: (
                ((panels[index][0] + panels[index][2]) / 2.0 - center_x) ** 2
                + ((panels[index][1] + panels[index][3]) / 2.0 - center_y) ** 2
            ),
        )

    result: list[TextBlock] = []
    assignments = [panel_of(block) for block in ordered]
    for panel_index in range(len(panels)):
        members = [
            block
            for block, assigned in zip(ordered, assignments)
            if assigned == panel_index
        ]
        if members:
            result += _order_within_region(members, resolved, 0)
    return result


# ---------------------------------------------------------------------------
# Translation units
# ---------------------------------------------------------------------------


def group_translation_units(blocks: Sequence[TextBlock]) -> list[list[TextBlock]]:
    """Group bubble-mates into one translation unit each, in reading order.

    A unit is one block, except that every "text" block whose detected bubble
    box is the same becomes one unit, so a sentence split across two detected
    blocks is translated as one item. Blocks without a bubble (SFX, captions
    without a box) stay single. The input is expected in reading order; each
    unit takes the position of its first member.
    """
    units: list[list[TextBlock]] = []
    by_bubble: dict[tuple[int, int, int, int], list[TextBlock]] = {}
    for block in blocks:
        bubble = block.bubble_xyxy if block.block_type == "text" else None
        if bubble is None:
            units.append([block])
            continue
        key = int_box(bubble)
        members = by_bubble.get(key)
        if members is None:
            by_bubble[key] = [block]
            units.append(by_bubble[key])
        else:
            members.append(block)
    return units


def bubble_reading_order(blocks: Sequence[TextBlock]) -> list[TextBlock]:
    """Sort the blocks of one bubble the way their text is read."""
    vertical = sum(block.height > block.width * 1.25 for block in blocks) > len(
        blocks
    ) / 2
    if vertical:
        return sorted(blocks, key=lambda block: (-block.x2, block.y1))
    return sorted(blocks, key=lambda block: (block.y1, block.x1))


def _composite_whole_text(texts: Sequence[str]) -> str | None:
    """Return the member text that is exactly the join of the others', if any.

    A detector sometimes emits a whole-bubble block whose OCR text is the
    reading-order join of its parts; translating that unit should not repeat
    the parts' text.
    """
    normalized = [normalize_render_source(text) for text in texts]
    for index, text in enumerate(normalized):
        if not text:
            continue
        others = [value for other, value in enumerate(normalized) if other != index]
        if others and text == "".join(others):
            return str(texts[index])
    return None


def unit_source_text(members: Sequence[TextBlock]) -> str:
    """One source string for a translation unit.

    Bubble-mates are joined in their reading order. CJK parts are glued
    without a separator; a unit that holds Latin text joins with spaces. A
    single-block unit returns that block's text unchanged.
    """
    if len(members) == 1:
        return str(members[0].text or "")
    grouped = bubble_reading_order(members)
    texts = [str(block.text or "") for block in grouped]
    whole = _composite_whole_text(texts)
    if whole is not None:
        return whole
    parts = [text.strip() for text in texts if text.strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    separator = " " if any(re.search(r"[A-Za-z0-9]", part) for part in parts) else ""
    return separator.join(parts)


# ---------------------------------------------------------------------------
# Order agreement
# ---------------------------------------------------------------------------


def reading_order_agreement(
    expected: Sequence[str], actual: Sequence[str]
) -> dict[str, Any]:
    """Kendall's tau between two reading orders over their shared labels.

    ``expected`` and ``actual`` are sequences of labels (one per block) in
    their reading order. Labels missing from either side are ignored, so a
    detector miss does not penalize the order of the blocks it did find. The
    result reports the matched label count, concordant and discordant pairs,
    the tau in [-1, 1] (None when fewer than two labels matched) and whether
    both orders agree exactly over the same label set.
    """
    expected_ranks = {label: index for index, label in enumerate(expected) if label}
    actual_ranks = {label: index for index, label in enumerate(actual) if label}
    shared = [label for label in expected_ranks if label in actual_ranks]

    concordant = discordant = 0
    for first in range(len(shared)):
        for second in range(first + 1, len(shared)):
            first_label, second_label = shared[first], shared[second]
            agrees = (
                expected_ranks[first_label] < expected_ranks[second_label]
            ) == (actual_ranks[first_label] < actual_ranks[second_label])
            if agrees:
                concordant += 1
            else:
                discordant += 1
    pairs = concordant + discordant
    tau = round((concordant - discordant) / pairs, 4) if pairs else None
    exact = (
        pairs > 0
        and discordant == 0
        and len(shared) == len(expected_ranks) == len(actual_ranks)
    )
    return {
        "matched": len(shared),
        "concordant": concordant,
        "discordant": discordant,
        "kendall_tau": tau,
        "exact": exact,
    }


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
    # Pre-translation unit merging gives every bubble-mate the same translated
    # text, so collapse members by translation and keep the most confident
    # member of each group as the source representative; the unit then renders
    # once instead of once per detected block.
    deduplicated: dict[str, TextBlock] = {}
    for block in blocks:
        key = normalize_render_source(block.translated_text)
        existing = deduplicated.get(key)
        if existing is None or block.confidence > existing.confidence:
            deduplicated[key] = block
    grouped = bubble_reading_order(list(deduplicated.values()))

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