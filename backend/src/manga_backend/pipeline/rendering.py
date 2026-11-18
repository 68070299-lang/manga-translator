"""Translated-text rendering: wrapping, auto-fit and drawing onto the page."""

from __future__ import annotations

import logging
import re

import numpy as np
from PIL import Image, ImageDraw

from manga_backend.pipeline.bubbles import (
    infer_bubble_render_bounds,
    infer_bubble_text_centers,
)
from manga_backend.pipeline.fonts import FontObject, get_cached_font, get_font_object
from manga_backend.pipeline.layout import (
    build_render_regions,
    expand_short_standalone_regions,
    find_overflow_region,
)
from manga_backend.pipeline.text import has_cjk
from manga_backend.utils.textblock import TextBlock

logger = logging.getLogger(__name__)

MIN_READABLE_FONT_SIZE = 10

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
    """Render translated text onto the image with wrapping and styling."""
    img = Image.fromarray(image).convert("RGBA")
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

    for block in render_regions:
        text = block.translated_text
        if not text:
            continue

        if uppercase:
            text = text.upper()

        x1, y1, x2, y2 = (int(v) for v in block.xyxy)
        original_box = (x1, y1, x2, y2)
        width = x2 - x1
        height = y2 - y1

        if width <= 0 or height <= 0:
            continue

        font_path = (
            cjk_font_path if has_cjk(text) else english_font_path or cjk_font_path
        ) or fallback_font_path

        base_stroke_width = max(0, int(border_size))
        is_vertical = text_direction == "vertical" or (
            text_direction == "auto" and has_cjk(text) and height > width * 1.5
        )
        group = block.composite_render_group

        def inner_area(box_width: int, box_height: int) -> tuple[int, int, int]:
            padding_limit = 1 if group is not None else max(2, base_stroke_width + 1)
            box_padding = min(padding_limit, max(0, (min(box_width, box_height) - 1) // 4))
            return (
                box_padding,
                max(1, box_width - box_padding * 2),
                max(1, box_height - box_padding * 2),
            )

        def fit_box(box_width: int, box_height: int) -> tuple[int, int, int, int]:
            box_padding, inner_width, inner_height = inner_area(box_width, box_height)
            fitted_size = auto_fit_font_size(
                font_path,
                text,
                inner_width,
                inner_height,
                line_spacing,
                pil_align,
                stroke_width=base_stroke_width,
                preferred_font_size=preferred_font_size,
                vertical=is_vertical,
            )
            return box_padding, inner_width, inner_height, fitted_size

        if auto_scale:
            padding, available_width, available_height, font_size = fit_box(width, height)
            minimum_font_size = min(
                8 if group is not None else MIN_READABLE_FONT_SIZE,
                max(1, int(preferred_font_size)),
            )
            compact_text = re.sub(r"\s+", "", str(text))
            can_overflow = not has_cjk(compact_text) and (
                block.render_from_bubble or len(compact_text) <= 12
            )
            if can_overflow and font_size < minimum_font_size:
                overflow = find_overflow_region(
                    block, render_regions, image.shape, fit_box, minimum_font_size
                )
                if overflow is not None:
                    box, (padding, available_width, available_height, font_size) = overflow
                    x1, y1, x2, y2 = box
                    width, height = x2 - x1, y2 - y1
                    block.xyxy = box

            if font_size <= 0:
                logger.warning(
                    "Skipping translated text that cannot fit render region %s",
                    [x1, y1, x2, y2],
                )
                continue
        else:
            font_size = preferred_font_size
            padding, available_width, available_height = inner_area(width, height)

        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer, "RGBA")
        if bg_opacity > 0:
            background_box = [
                max(0, original_box[0] - x1),
                max(0, original_box[1] - y1),
                min(width - 1, original_box[2] - x1 - 1),
                min(height - 1, original_box[3] - y1 - 1),
            ]
            if (
                background_box[2] >= background_box[0]
                and background_box[3] >= background_box[1]
            ):
                draw.rectangle(background_box, fill=(*user_bg_color, bg_alpha))

        stroke_width = min(base_stroke_width, max(0, int(round(font_size * 0.15))))
        font = get_font_object(font_path, font_size)
        text_spacing = max(0, int((line_spacing - 1.0) * font_size))
        wrapped_text = "\n".join(
            wrap_text_to_lines(text, font, available_width, stroke_width=stroke_width)
        )

        body_center = block.bubble_text_center
        body_center_x = (
            float(body_center[0]) - x1
            if body_center is not None
            else padding + available_width / 2
        )
        body_center_y = (
            float(body_center[1]) - y1
            if body_center is not None
            else padding + available_height / 2
        )

        if is_vertical:
            text_x = (
                body_center_x
                if text_alignment == "center"
                else (padding if text_alignment == "left" else width - padding)
            )
            draw_vertical_text(
                draw,
                text,
                (text_x, padding),
                font,
                user_text_color,
                stroke_width=stroke_width,
                stroke_fill=user_border_color,
                spacing=text_spacing,
            )
        else:
            bbox = draw.multiline_textbbox(
                (0, 0),
                wrapped_text,
                font=font,
                align=pil_align,
                stroke_width=stroke_width,
                spacing=text_spacing,
            )
            rendered_width = bbox[2] - bbox[0]
            rendered_height = bbox[3] - bbox[1]
            if text_alignment == "left":
                painted_x = padding
            elif text_alignment == "right":
                painted_x = width - padding - rendered_width
            else:
                painted_x = max(
                    padding,
                    min(width - padding - rendered_width, body_center_x - rendered_width / 2),
                )
            painted_y = max(
                padding,
                min(height - padding - rendered_height, body_center_y - rendered_height / 2),
            )
            draw.multiline_text(
                (painted_x - bbox[0], painted_y - bbox[1]),
                wrapped_text,
                font=font,
                fill=user_text_color,
                align=pil_align,
                stroke_width=stroke_width,
                stroke_fill=user_border_color,
                spacing=text_spacing,
            )

        img.alpha_composite(layer, dest=(x1, y1))

    return np.array(img.convert("RGB"))


def wrap_text_to_lines(
    text: str, font: FontObject, width: int, stroke_width: int = 0
) -> list[str]:
    """Wrap using the same pixel measurements used by font fitting."""
    max_width = max(1, int(width))

    def measured_width(value: str) -> float:
        bbox = font.getbbox(value)
        if not bbox:
            return 0
        return bbox[2] - bbox[0] + stroke_width * 2

    def split_oversized_unit(unit: str) -> list[str]:
        if measured_width(unit) <= max_width:
            return [unit]

        chunks: list[str] = []
        current = ""
        for char in unit:
            candidate = current + char
            if current and measured_width(candidate) > max_width:
                chunks.append(current)
                current = char
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks or [unit]

    lines: list[str] = []
    for paragraph in str(text).split("\n"):
        if paragraph == "":
            lines.append("")
            continue

        character_wrap = has_cjk(paragraph) and not any(
            char.isspace() for char in paragraph
        )
        if character_wrap:
            units = list(paragraph)
        else:
            units = []
            for token in paragraph.split():
                units.extend(re.findall(r".+?[—–](?=.)|.+", token))
        if not units:
            lines.append("")
            continue

        current = ""
        for unit in units:
            chunks = split_oversized_unit(unit)
            if len(chunks) > 1:
                if current:
                    lines.append(current)
                lines.extend(chunks[:-1])
                current = chunks[-1]
                continue

            unit = chunks[0]
            if not current:
                current = unit
                continue
            separator = "" if character_wrap or current.endswith(("—", "–")) else " "
            candidate = current + separator + unit
            if measured_width(candidate) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = unit
        lines.append(current)

    return lines or [""]


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


def auto_fit_font_size(
    font_path: str | None,
    text: str,
    width: int,
    height: int,
    line_spacing: float,
    align: str,
    stroke_width: int = 0,
    preferred_font_size: int = 16,
    vertical: bool = False,
) -> int:
    """Binary-search the largest font size whose wrapped text fits the box."""
    if not text:
        return 12

    low = 1
    high = max(low, min(96, int(preferred_font_size)))
    best = 0
    probe = Image.new("L", (1, 1), 0)
    draw = ImageDraw.Draw(probe)

    for _ in range(12):  # ~12 iterations gives sub-pixel precision
        if low > high:
            break
        mid = (low + high) // 2

        font = get_font_object(font_path, mid)
        spacing = max(0, int((line_spacing - 1.0) * mid))
        candidate_stroke_width = min(stroke_width, max(0, int(round(mid * 0.15))))
        words_fit = True
        if vertical:
            characters = list(str(text).replace("\n", ""))
            glyph_boxes = [
                draw.textbbox((0, 0), char, font=font, stroke_width=candidate_stroke_width)
                for char in characters
            ]
            rendered_width = max((box[2] - box[0] for box in glyph_boxes), default=0)
            rendered_height = len(characters) * max(mid + spacing, 1)
        else:
            if not has_cjk(text):
                word_boxes = (
                    font.getbbox(word) for word in str(text).replace("\n", " ").split()
                )
                words_fit = all(
                    (box[2] - box[0]) + candidate_stroke_width * 2 <= width
                    for box in word_boxes
                )
            wrapped = "\n".join(
                wrap_text_to_lines(text, font, width, stroke_width=candidate_stroke_width)
            )
            bbox = draw.multiline_textbbox(
                (0, 0),
                wrapped,
                font=font,
                spacing=spacing,
                align=align,
                stroke_width=candidate_stroke_width,
            )
            rendered_width = bbox[2] - bbox[0]
            rendered_height = bbox[3] - bbox[1]

        if rendered_height <= height and rendered_width <= width and words_fit:
            best = mid
            low = mid + 1
        else:
            high = mid - 1

    return best
