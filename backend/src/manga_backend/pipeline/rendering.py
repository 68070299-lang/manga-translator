"""Translated-text rendering: rooms, anchored sizes, line breaking and drawing.

Text is no longer fitted to a rectangle: each render region gets a *room* (the
bubble interior eroded by an air inset, or for free text the blank area left
after the fill), the size is anchored to the source lettering and harmonised
per page, and lines are broken by dynamic programming over per-line band
widths. The ideas are koharu's rooms, bands, line breaker and fitter
(``crates/koharu-*``: ``rooms.rs``, ``fit.rs``, ``lines.rs``, ``measure.rs``,
MIT OR Apache-2.0); the implementation here is original, written against
OpenCV, numpy and Pillow, not ported.

The old overflow rescue is gone: free text gets a free-space room instead of a
grown rectangle, so a region is never lettered outside the blank area its fill
left behind.
"""

from __future__ import annotations

import logging
from dataclasses import replace

import numpy as np
from PIL import Image, ImageDraw

from manga_backend.pipeline.bubbles import (
    infer_bubble_render_bounds,
    infer_bubble_text_centers,
)
from manga_backend.pipeline.fit import (
    MIN_READABLE_FONT_SIZE,
    FitResult,
    fit_text_in_room,
)
from manga_backend.pipeline.fonts import FontObject, get_cached_font, get_font_object
from manga_backend.pipeline.layout import (
    build_render_regions,
    expand_short_standalone_regions,
)
from manga_backend.pipeline.masks import bubble_interior_details, image_analysis_cache
from manga_backend.pipeline.measure import (
    anchor_target,
    measure_block_colours,
    page_lettering_size,
)
from manga_backend.pipeline.regions import clamp_xyxy, int_box
from manga_backend.pipeline.rooms import (
    Room,
    box_room,
    bubble_room,
    free_space_room,
    split_conjoined_room,
)
from manga_backend.pipeline.text import has_cjk
from manga_backend.utils.textblock import Box, TextBlock

logger = logging.getLogger(__name__)

# Dialogue regions whose bubbles are this close share one room candidate: a
# conjoined outline is one shape with two lobes.
CONJOINED_GAP_PX = 12
# A split room must keep at least this share of the joined room for each lobe.
CONJOINED_MIN_SHARE = 0.15

RGB = tuple[int, int, int]


def hex_to_rgb(hex_color: str) -> RGB:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return (0, 0, 0)
    return (
        int(hex_color[0:2], 16),
        int(hex_color[2:4], 16),
        int(hex_color[4:6], 16),
    )


def _dialogue_interiors(
    image: np.ndarray, blocks: list[TextBlock]
) -> dict[Box, tuple[np.ndarray, Box]]:
    """Flood-fill interior per detected bubble, keyed by the bubble box."""
    interiors: dict[Box, tuple[np.ndarray, Box]] = {}
    for block in blocks:
        if block.block_type != "text" or block.bubble_xyxy is None:
            continue
        key = int_box(block.bubble_xyxy)
        if key in interiors:
            continue
        result = bubble_interior_details(image, block)
        if result.found:
            interiors[key] = (result.interior, result.bounds)
    return interiors


def _nearest_interior(
    key: Box, interiors: dict[Box, tuple[np.ndarray, Box]]
) -> tuple[np.ndarray, Box] | None:
    if key in interiors:
        return interiors[key]
    best: tuple[float, tuple[np.ndarray, Box]] | None = None
    for candidate, value in interiors.items():
        overlap_x = max(0, min(key[2], candidate[2]) - max(key[0], candidate[0]))
        overlap_y = max(0, min(key[3], candidate[3]) - max(key[1], candidate[1]))
        if overlap_x <= 0 or overlap_y <= 0:
            continue
        overlap = overlap_x * overlap_y
        area = (key[2] - key[0]) * (key[3] - key[1])
        score = overlap / max(1, area)
        if score >= 0.5 and (best is None or score > best[0]):
            best = (score, value)
    return None if best is None else best[1]


def _boxes_touch(first: Box, second: Box, gap: int) -> bool:
    return not (
        first[2] + gap < second[0]
        or second[2] + gap < first[0]
        or first[3] + gap < second[1]
        or second[3] + gap < first[1]
    )


def _conjoined_rooms(
    image: np.ndarray, regions: list[TextBlock], unresolved: list[int]
) -> dict[int, Room]:
    """Split a joined outline into one room per text group.

    Only regions whose own bubble had no usable interior are considered: their
    window cuts the shared outline, so the flood leaks. The union window of the
    group contains the whole outline, and the resulting interior is split per
    text group by a hull-defect chord (seeded watershed as the fallback).
    """
    rooms: dict[int, Room] = {}
    remaining = list(unresolved)
    while remaining:
        first = remaining.pop(0)
        first_box = int_box(regions[first].bubble_xyxy or regions[first].xyxy)
        group = [first]
        for other in list(remaining):
            other_box = int_box(regions[other].bubble_xyxy or regions[other].xyxy)
            if _boxes_touch(first_box, other_box, CONJOINED_GAP_PX):
                group.append(other)
                remaining.remove(other)
        if len(group) < 2:
            continue

        boxes = [int_box(regions[index].xyxy) for index in group]
        bubbles = [int_box(regions[index].bubble_xyxy or regions[index].xyxy) for index in group]
        union_text = (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )
        union_bubble = (
            min(box[0] for box in bubbles),
            min(box[1] for box in bubbles),
            max(box[2] for box in bubbles),
            max(box[3] for box in bubbles),
        )
        probe = TextBlock(union_text, block_type="text")
        probe.bubble_xyxy = list(union_bubble)
        result = bubble_interior_details(image, probe)
        if not result.found:
            continue
        joined = bubble_room(result.interior, result.bounds)
        if joined is None:
            continue
        seeds: list[tuple[float, float]] = []
        for index in group:
            centre = regions[index].bubble_text_center
            if centre is None:
                seeds.append((float(regions[index].center[0]), float(regions[index].center[1])))
            else:
                seeds.append((float(centre[0]), float(centre[1])))
        lobes = split_conjoined_room(joined, seeds)
        if len(lobes) != len(group):
            continue
        if any(
            lobe.area < joined.area * CONJOINED_MIN_SHARE for lobe in lobes
        ):
            continue
        for index, lobe in zip(group, lobes):
            rooms[index] = lobe
    return rooms


def build_rooms(
    image: np.ndarray,
    regions: list[TextBlock],
    blocks: list[TextBlock] | None = None,
) -> list[Room]:
    """One room per render region: bubble interior, free space, or the box."""
    height, width = image.shape[:2]
    interiors = _dialogue_interiors(image, blocks if blocks is not None else regions)
    obstacles: list[Box] = [
        (int(region.x1), int(region.y1), int(region.x2), int(region.y2))
        for region in regions
    ]

    rooms: list[Room | None] = []
    unresolved: list[int] = []
    claimed = np.zeros((height, width), dtype=bool)
    for index, region in enumerate(regions):
        room: Room | None = None
        if region.render_from_bubble and region.bubble_xyxy is not None:
            key = int_box(region.bubble_xyxy)
            found = _nearest_interior(key, interiors)
            if found is not None:
                room = bubble_room(
                    found[0],
                    found[1],
                    center=(
                        tuple(region.bubble_text_center)  # type: ignore[arg-type]
                        if region.bubble_text_center is not None
                        else None
                    ),
                )
            if room is None:
                unresolved.append(index)
        else:
            room = free_space_room(
                image,
                region,
                obstacles=[box for other, box in enumerate(obstacles) if other != index],
                claimed=claimed,
            )
            if room is not None:
                cx1, cy1, cx2, cy2 = room.bounds
                claimed[cy1:cy2, cx1:cx2] |= room.mask > 0
        rooms.append(room)

    conjoined = _conjoined_rooms(image, regions, unresolved)
    resolved: list[Room] = []
    for index, region in enumerate(regions):
        room = rooms[index] or conjoined.get(index)
        if room is None:
            room = box_room(region)
            if region.bubble_text_center is not None:
                centre = region.bubble_text_center
                room = replace(room, center=(float(centre[0]), float(centre[1])))
        clamped = clamp_xyxy(room.bounds, width, height)
        if clamped != list(room.bounds):
            room = _clamp_room(room, clamped)
        resolved.append(room)
    return resolved


def _clamp_room(room: Room, bounds: list[int]) -> Room:
    x1, y1, x2, y2 = bounds
    ox, oy = room.bounds[0], room.bounds[1]
    left = max(0, x1 - ox)
    top = max(0, y1 - oy)
    right = min(room.mask.shape[1], x2 - ox)
    bottom = min(room.mask.shape[0], y2 - oy)
    if right <= left or bottom <= top:
        return room
    mask = np.zeros((bottom - top, right - left), dtype=np.uint8)
    mask[:, :] = room.mask[top:bottom, left:right]
    center = (
        min(max(room.center[0], x1), x2),
        min(max(room.center[1], y1), y2),
    )
    return Room(mask, (x1, y1, x2, y2), center, room.kind, room.air)


def _source_estimates(blocks: list[TextBlock]) -> dict[Box, float]:
    """Source glyph size per bubble, measured on the original OCR text box.

    The renderer only ever sees the filled page, so the measurement itself
    happens while masking (``prepare_flux_inpainting`` has the original
    pixels); this maps each bubble to the size its blocks recorded.
    """
    estimates: dict[Box, float] = {}
    for block in blocks:
        if block.block_type != "text" or block.bubble_xyxy is None:
            continue
        key = int_box(block.bubble_xyxy)
        if key in estimates:
            continue
        size = block.source_font_size
        if size is not None:
            estimates[key] = size
    return estimates


def _nearest_estimate(key: Box, estimates: dict[Box, float]) -> float | None:
    best: tuple[float, float] | None = None
    for candidate, value in estimates.items():
        overlap_x = max(0, min(key[2], candidate[2]) - max(key[0], candidate[0]))
        overlap_y = max(0, min(key[3], candidate[3]) - max(key[1], candidate[1]))
        if overlap_x <= 0 or overlap_y <= 0:
            continue
        overlap = overlap_x * overlap_y
        area = (key[2] - key[0]) * (key[3] - key[1])
        score = overlap / max(1, area)
        if score >= 0.5 and (best is None or score > best[0]):
            best = (score, value)
    return None if best is None else best[1]


def anchor_targets(
    regions: list[TextBlock],
    preferred_font_size: int,
    blocks: list[TextBlock] | None = None,
) -> list[int]:
    """Anchored size per region, harmonised to one page lettering size.

    Every region is anchored to its source lettering's em (measured while
    masking); the page's size is the median of those anchors, so a page is
    lettered in one type scale. A region with no measurement (or free text on
    a page with none) keeps the user's preferred size, and the result is
    floored at the minimum readable size.
    """
    estimates = _source_estimates(blocks if blocks is not None else regions)
    anchors: list[int] = []
    for region in regions:
        glyph: float | None = None
        if region.render_from_bubble:
            key = int_box(region.bubble_xyxy or region.xyxy)
            glyph = estimates.get(key)
            if glyph is None:
                glyph = _nearest_estimate(key, estimates)
        elif region.source_font_size is not None:
            glyph = region.source_font_size
        anchors.append(anchor_target(glyph, preferred_font_size))
    page_size = page_lettering_size(anchors, preferred_font_size)
    return [max(MIN_READABLE_FONT_SIZE, page_size) for _ in regions]


def render_text(
    image: np.ndarray,
    blocks: list[TextBlock],
    line_spacing: float = 1.2,
    border_size: float = 2.0,
    text_color: str = "#000000",
    border_color: str = "#ffffff",
    bg_color: str = "#ffffff",
    bg_opacity: float = 0.9,
    text_alignment: str = "center",
    text_direction: str = "auto",
    uppercase: bool = False,
    auto_scale: bool = True,
    preferred_font_size: int = 16,
) -> np.ndarray:
    """Render translated text onto the image with rooms and anchored sizes."""
    img = Image.fromarray(image).convert("RGBA")
    with image_analysis_cache(image):
        infer_bubble_render_bounds(image, blocks)
        infer_bubble_text_centers(image, blocks)
        render_regions = build_render_regions(blocks, image.shape)
        expand_short_standalone_regions(render_regions, image.shape)

        english_font_path = get_cached_font("english")
        cjk_font_path = get_cached_font("cjk")
        fallback_font_path = get_cached_font("fallback")

        user_text_color = hex_to_rgb(text_color)
        user_border_color = hex_to_rgb(border_color)
        user_bg_color = hex_to_rgb(bg_color)
        bg_alpha = int(bg_opacity * 255)
        pil_align = text_alignment if text_alignment in ("left", "right") else "center"
        base_stroke_width = max(0, int(border_size))

        rooms = build_rooms(image, render_regions, blocks)
    targets = anchor_targets(render_regions, preferred_font_size, blocks)
    page_height, page_width = image.shape[:2]

    for region, room, target in zip(render_regions, rooms, targets):
        text = region.translated_text
        if not text:
            continue
        if uppercase:
            text = text.upper()

        font_path = (
            cjk_font_path if has_cjk(text) else english_font_path or cjk_font_path
        ) or fallback_font_path
        width = region.width
        height = region.height
        is_vertical = text_direction == "vertical" or (
            text_direction == "auto" and has_cjk(text) and height > width * 1.5
        )

        fit = fit_text_in_room(
            room,
            text,
            font_path,
            target=target,
            line_spacing=line_spacing,
            base_stroke_width=base_stroke_width,
            vertical=is_vertical,
            fixed_size=not auto_scale,
            kind="dialogue" if region.render_from_bubble else "free",
        )
        if fit.size <= 0:
            logger.warning(
                "Skipping translated text that cannot fit room %s",
                list(room.bounds),
            )
            continue

        text_rgb, border_rgb, background_rgb = measure_block_colours(
            image, room, user_text_color, user_border_color, user_bg_color
        )
        _draw_region(
            img,
            region,
            room,
            fit,
            font_path,
            text_rgb,
            border_rgb,
            background_rgb,
            bg_alpha,
            pil_align,
            is_vertical,
            page_width,
            page_height,
        )

    return np.array(img.convert("RGB"))


def _draw_region(
    img: Image.Image,
    region: TextBlock,
    room: Room,
    fit: FitResult,
    font_path: str | None,
    text_rgb: RGB,
    border_rgb: RGB,
    background_rgb: RGB,
    bg_alpha: int,
    align: str,
    vertical: bool,
    page_width: int,
    page_height: int,
) -> None:
    """Draw one region's fitted lines inside its room's bounds."""
    pad = max(2, fit.stroke_width + 1)
    left = max(0, room.bounds[0] - pad)
    top = max(0, room.bounds[1] - pad)
    right = min(page_width, room.bounds[2] + pad)
    bottom = min(page_height, room.bounds[3] + pad)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return

    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    if bg_alpha > 0:
        background_box = [
            max(0, region.x1 - left),
            max(0, region.y1 - top),
            min(width - 1, region.x2 - left - 1),
            min(height - 1, region.y2 - top - 1),
        ]
        if background_box[2] >= background_box[0] and background_box[3] >= background_box[1]:
            draw.rectangle(background_box, fill=(*background_rgb, bg_alpha))

    font = get_font_object(font_path, fit.size)
    if vertical:
        draw_vertical_text(
            draw,
            "".join(fit.lines),
            (room.center[0] - left, fit.bands[0].top - top if fit.bands else 0),
            font,
            text_rgb,
            stroke_width=fit.stroke_width,
            stroke_fill=border_rgb,
            spacing=max(0, int(round(fit.size * 0.08))),
        )
        img.alpha_composite(layer, dest=(left, top))
        return

    anchors = {"center": "mm", "left": "lm", "right": "rm"}
    anchor = anchors.get(align, "mm")
    for index, (line, band) in enumerate(zip(fit.lines, fit.bands)):
        if not line:
            continue
        line_width = fit.line_widths[index] if index < len(fit.line_widths) else 0.0
        if align == "left":
            x = band.left
        elif align == "right":
            x = band.right
        else:
            # Centre on the room's body axis, but never past the band.
            x = min(
                max(band.axis, band.left + line_width / 2.0),
                band.right - line_width / 2.0,
            )
        draw.text(
            (x - left, band.center_y - top),
            line,
            font=font,
            fill=text_rgb,
            anchor=anchor,
            stroke_width=fit.stroke_width,
            stroke_fill=border_rgb,
        )
    img.alpha_composite(layer, dest=(left, top))


def draw_vertical_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    position: tuple[float, float],
    font: FontObject,
    fill: RGB,
    stroke_width: int = 0,
    stroke_fill: RGB | None = None,
    spacing: int = 0,
) -> None:
    """Draw one character per line for explicit vertical text direction."""
    x, y = position
    step = max(int(getattr(font, "size", 12) + spacing), 1)
    for index, char in enumerate(str(text).replace("\n", "")):
        draw.text(
            (x, y + index * step),
            char,
            font=font,
            fill=fill,
            anchor="ma",
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
