"""Inpainting mask construction: glyph extraction, bubble interiors and FLUX masks."""

from __future__ import annotations

import logging

import cv2
import numpy as np

from manga_backend.pipeline.regions import int_box, suppress_composite_render_regions
from manga_backend.utils.textblock import Box, TextBlock

logger = logging.getLogger(__name__)

Slices = tuple[slice, slice]


def uniform_white_color(pixels: np.ndarray, min_bright_ratio: float) -> np.ndarray | None:
    """Return the median white when most pixels are bright, flat and neutral."""
    luminance = cv2.cvtColor(pixels.reshape(-1, 1, 3), cv2.COLOR_RGB2GRAY).reshape(-1)
    is_bright = luminance >= 235
    if np.count_nonzero(is_bright) / pixels.shape[0] < min_bright_ratio:
        return None

    color = np.median(pixels[is_bright], axis=0)
    if (
        float(np.min(color)) < 242
        or float(np.max(color) - np.min(color)) > 8
        or float(np.std(luminance[is_bright])) > 7
    ):
        return None
    return np.rint(color).astype(np.uint8)


def overlap_slices(first_bounds: Box, second_bounds: Box) -> tuple[Slices, Slices] | None:
    """Return array slices for where two xyxy windows overlap, or None."""
    fx1, fy1, fx2, fy2 = first_bounds
    sx1, sy1, sx2, sy2 = second_bounds
    x1, y1, x2, y2 = max(fx1, sx1), max(fy1, sy1), min(fx2, sx2), min(fy2, sy2)
    if x2 <= x1 or y2 <= y1:
        return None
    return (
        (slice(y1 - fy1, y2 - fy1), slice(x1 - fx1, x2 - fx1)),
        (slice(y1 - sy1, y2 - sy1), slice(x1 - sx1, x2 - sx1)),
    )


def create_mask(
    image: np.ndarray,
    blocks: list[TextBlock],
    rectangular: bool = False,
) -> np.ndarray:
    """Create a rectangular YOLO mask or the default contour-safe mask."""
    h, w = image.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)

    for block in suppress_composite_render_regions(blocks):
        mask_builder = (
            rectangular_mask_for_block
            if rectangular
            else contour_safe_mask_for_block
        )
        local_mask, (x1, y1, x2, y2), strategy = mask_builder(image, block)
        if local_mask.size == 0 or not np.any(local_mask):
            logger.warning(
                "Skipping unsafe inpaint fallback for block %s (%s)",
                block.xyxy.tolist(),
                strategy,
            )
            continue
        target = mask[y1:y2, x1:x2]
        np.maximum(target, local_mask, out=target)

    return mask


def rectangular_mask_for_block(
    image: np.ndarray, block: TextBlock
) -> tuple[np.ndarray, Box, str]:
    """Mask an AnimeText YOLO box while retaining an outer sampling ring."""
    height, width = image.shape[:2]
    tx1, ty1, tx2, ty2 = [int(value) for value in block.xyxy]
    rx1, ry1 = max(0, tx1), max(0, ty1)
    rx2, ry2 = min(width, tx2), min(height, ty2)
    if rx2 <= rx1 or ry2 <= ry1:
        return (
            np.zeros((0, 0), dtype=np.uint8),
            (rx1, ry1, rx2, ry2),
            "rectangle",
        )

    context = max(4, int(round(min(rx2 - rx1, ry2 - ry1) * 0.15)))
    x1, y1 = max(0, rx1 - context), max(0, ry1 - context)
    x2, y2 = min(width, rx2 + context), min(height, ry2 + context)
    local_mask = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
    local_mask[ry1 - y1 : ry2 - y1, rx1 - x1 : rx2 - x1] = 255
    return local_mask, (x1, y1, x2, y2), "rectangle"


def glyph_mask_for_block(
    image: np.ndarray,
    block: TextBlock,
) -> tuple[np.ndarray, Box]:
    """Extract dark glyphs while rejecting line art crossing a text crop."""
    height, width = image.shape[:2]
    x1, y1, x2, y2 = [int(value) for value in block.xyxy]
    padding = 2
    x1, y1 = max(0, x1 - padding), max(0, y1 - padding)
    x2, y2 = min(width, x2 + padding), min(height, y2 + padding)
    if x2 <= x1 or y2 <= y1:
        return np.zeros((0, 0), dtype=np.uint8), (x1, y1, x2, y2)

    roi = image[y1:y2, x1:x2]
    gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
    background_level = float(np.percentile(gray, 80))
    # Manga glyphs and outlines are normally the darkest cluster. A low cap
    # avoids treating gray screentone as text on textured regions.
    threshold = int(max(24, min(110, background_level - 32)))
    roi_height, roi_width = gray.shape

    def extract_components(candidate: np.ndarray) -> tuple[np.ndarray, int]:
        count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
            (candidate > 0).astype(np.uint8), connectivity=8
        )
        selected = np.zeros_like(candidate)
        largest_component = 0
        for label in range(1, count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            if area < 2:
                continue
            left = int(stats[label, cv2.CC_STAT_LEFT])
            top = int(stats[label, cv2.CC_STAT_TOP])
            component_width = int(stats[label, cv2.CC_STAT_WIDTH])
            component_height = int(stats[label, cv2.CC_STAT_HEIGHT])
            touches_edge = (
                left == 0
                or top == 0
                or left + component_width >= roi_width
                or top + component_height >= roi_height
            )
            touches_image_edge = (
                (x1 == 0 and left == 0)
                or (y1 == 0 and top == 0)
                or (x2 == width and left + component_width >= roi_width)
                or (y2 == height and top + component_height >= roi_height)
            )
            long_rule = (
                component_width >= roi_width * 0.65
                and component_height <= 3
            ) or (
                component_height >= roi_height * 0.65
                and component_width <= 3
            )
            edge_structure_rule = (
                touches_edge
                and not touches_image_edge
                and (
                    component_width >= roi_width * 0.2
                    or component_height >= roi_height * 0.2
                )
            )
            if long_rule or edge_structure_rule:
                continue
            selected[labels == label] = 255
            largest_component = max(largest_component, area)
        return selected, largest_component

    dark = np.where(gray <= threshold, 255, 0).astype(np.uint8)
    glyphs, dark_largest = extract_components(dark)

    median_level = float(np.percentile(gray, 50))
    bright_level = float(np.percentile(gray, 95))
    if median_level < 210 and bright_level - median_level >= 28:
        bright_threshold = int(max(180, min(245, median_level + 30)))
        bright = np.where(gray >= bright_threshold, 255, 0).astype(np.uint8)
        bright_glyphs, bright_largest = extract_components(bright)
        # Light-on-dark UI text tends to form a strong connected outline,
        # while dark candidates in the same crop are isolated screen tone.
        if bright_largest >= max(12, int(dark_largest * 1.5)):
            glyphs = bright_glyphs

    if np.any(glyphs):
        glyphs = cv2.dilate(glyphs, np.ones((3, 3), np.uint8), iterations=1)
    return glyphs, (x1, y1, x2, y2)


def bubble_interior_mask_for_block(
    image: np.ndarray, block: TextBlock
) -> tuple[np.ndarray, Box]:
    """Find the smallest closed bubble contour surrounding a text block."""
    height, width = image.shape[:2]
    tx1, ty1, tx2, ty2 = [int(value) for value in block.xyxy]
    tx1, ty1 = max(0, tx1), max(0, ty1)
    tx2, ty2 = min(width, tx2), min(height, ty2)
    if tx2 <= tx1 or ty2 <= ty1:
        return np.zeros((0, 0), dtype=np.uint8), (tx1, ty1, tx2, ty2)

    bubble = block.bubble_xyxy
    detector_bounds: Box | None = None
    if bubble is not None:
        try:
            candidate = int_box(bubble)
        except (TypeError, ValueError):
            candidate = None
        if candidate is not None and candidate[2] > candidate[0] and candidate[3] > candidate[1]:
            detector_bounds = candidate

    block_width = tx2 - tx1
    block_height = ty2 - ty1
    if detector_bounds is not None:
        bx1, by1, bx2, by2 = detector_bounds
        bubble_width = bx2 - bx1
        bubble_height = by2 - by1
        padding = max(6, int(min(bubble_width, bubble_height) * 0.08))
        x1, y1 = max(0, bx1 - padding), max(0, by1 - padding)
        x2, y2 = min(width, bx2 + padding), min(height, by2 + padding)
    else:
        padding_x = max(
            32, int(block_width * 1.25), int(block_height * 0.5)
        )
        padding_y = max(
            32, int(block_height * 0.75), int(block_width * 1.25)
        )
        x1, y1 = max(0, tx1 - padding_x), max(0, ty1 - padding_y)
        x2, y2 = min(width, tx2 + padding_x), min(height, ty2 + padding_y)

    roi = image[y1:y2, x1:x2]
    if roi.size == 0 or min(roi.shape[:2]) < 5:
        return np.zeros(roi.shape[:2], dtype=np.uint8), (x1, y1, x2, y2)

    gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
    local_text = (tx1 - x1, ty1 - y1, tx2 - x1, ty2 - y1)
    structure_gray = gray.copy()
    sx1, sy1 = max(0, local_text[0]), max(0, local_text[1])
    sx2, sy2 = min(gray.shape[1], local_text[2]), min(
        gray.shape[0], local_text[3]
    )
    if sx2 > sx1 and sy2 > sy1:
        # OCR ink close to a bubble edge can otherwise join the outline
        # and make contour extraction carve character-shaped holes into
        # the inferred interior. Remove the known text rectangle only for
        # structural contour discovery; the source image is untouched.
        structure_gray[sy1:sy2, sx1:sx2] = int(np.percentile(gray, 90))
    blurred = cv2.GaussianBlur(structure_gray, (3, 3), 0)
    otsu_threshold, _binary = cv2.threshold(
        blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    dark_threshold = int(np.clip(otsu_threshold, 70, 210))
    dark = np.where(blurred <= dark_threshold, 255, 0).astype(np.uint8)
    edges = cv2.Canny(blurred, 50, 150)
    outline = cv2.bitwise_or(dark, edges)
    outline = cv2.morphologyEx(
        outline,
        cv2.MORPH_CLOSE,
        np.ones((3, 3), np.uint8),
        iterations=1,
    )
    contours, _hierarchy = cv2.findContours(
        outline, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE
    )

    text_width = max(1, local_text[2] - local_text[0])
    text_height = max(1, local_text[3] - local_text[1])
    text_area = text_width * text_height
    center = (
        (local_text[0] + local_text[2]) / 2.0,
        (local_text[1] + local_text[3]) / 2.0,
    )
    roi_height, roi_width = gray.shape
    candidates = []
    for contour in contours:
        area = float(abs(cv2.contourArea(contour)))
        if area < max(48.0, text_area * 1.15) or area >= roi.size / 3 * 0.9:
            continue
        if cv2.pointPolygonTest(contour, center, False) < 0:
            continue
        cx, cy, cw, ch = cv2.boundingRect(contour)
        if cx <= 1 or cy <= 1 or cx + cw >= roi_width - 1 or cy + ch >= roi_height - 1:
            continue
        overlap_width = max(
            0, min(cx + cw, local_text[2]) - max(cx, local_text[0])
        )
        overlap_height = max(
            0, min(cy + ch, local_text[3]) - max(cy, local_text[1])
        )
        if overlap_width * overlap_height < text_area * 0.75:
            continue
        candidates.append((area, contour))

    if not candidates:
        return np.zeros(gray.shape, dtype=np.uint8), (x1, y1, x2, y2)

    _area, contour = min(candidates, key=lambda candidate: candidate[0])
    interior = np.zeros(gray.shape, dtype=np.uint8)
    cv2.drawContours(interior, [contour], -1, 255, thickness=cv2.FILLED)
    interior = cv2.erode(
        interior, np.ones((3, 3), np.uint8), iterations=2
    )
    text_roi = interior[
        max(0, local_text[1]) : min(roi_height, local_text[3]),
        max(0, local_text[0]) : min(roi_width, local_text[2]),
    ]
    if text_roi.size == 0 or np.count_nonzero(text_roi) < text_area * 0.35:
        interior.fill(0)
    return interior, (x1, y1, x2, y2)


def contour_safe_mask_for_block(
    image: np.ndarray, block: TextBlock
) -> tuple[np.ndarray, Box, str]:
    """Extract ink, using a detected bubble contour only as a safety boundary."""
    glyphs, glyph_bounds = glyph_mask_for_block(image, block)
    has_glyphs = glyphs.size and np.any(glyphs)
    interior = np.zeros((0, 0), dtype=np.uint8)
    interior_bounds = glyph_bounds
    if block.block_type == "text":
        interior, interior_bounds = bubble_interior_mask_for_block(
            image, block
        )
        if has_glyphs and interior.size and np.any(interior):
            # Shape-aware edge filtering removes broad crossing lines but
            # keeps compact clipped characters. The bubble interior is the
            # final safety boundary around those candidates.
            clipped = np.zeros_like(glyphs)
            overlap = overlap_slices(glyph_bounds, interior_bounds)
            if overlap:
                clipped[overlap[0]] = cv2.bitwise_and(glyphs[overlap[0]], interior[overlap[1]])
            if np.any(clipped):
                return clipped, glyph_bounds, "bubble-clipped-glyph"

    if has_glyphs:
        return glyphs, glyph_bounds, "glyph"

    if block.block_type != "text":
        interior, interior_bounds = bubble_interior_mask_for_block(
            image, block
        )
    bounds = interior_bounds
    if interior.size == 0 or not np.any(interior):
        return interior, bounds, "no-closed-bubble"

    x1, y1, x2, y2 = bounds
    roi = image[y1:y2, x1:x2]
    gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
    window = np.zeros_like(interior)
    bx1, by1, bx2, by2 = [int(value) for value in block.xyxy]
    bx1, by1 = max(x1, bx1 - 2), max(y1, by1 - 2)
    bx2, by2 = min(x2, bx2 + 2), min(y2, by2 + 2)
    window[by1 - y1 : by2 - y1, bx1 - x1 : bx2 - x1] = 255

    adaptive_block_size = min(15, min(gray.shape))
    if adaptive_block_size % 2 == 0:
        adaptive_block_size -= 1
    adaptive_block_size = max(3, adaptive_block_size)
    adaptive = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        adaptive_block_size,
        7,
    )
    adaptive = cv2.bitwise_and(adaptive, interior)
    adaptive = cv2.bitwise_and(adaptive, window)
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        (adaptive > 0).astype(np.uint8), connectivity=8
    )
    ink = np.zeros_like(adaptive)
    for label in range(1, count):
        if int(stats[label, cv2.CC_STAT_AREA]) >= 2:
            ink[labels == label] = 255
    if np.any(ink):
        ink = cv2.dilate(ink, np.ones((3, 3), np.uint8), iterations=1)
        ink = cv2.bitwise_and(ink, interior)
        return ink, bounds, "contour-clipped-glyph"

    if block.block_type == "text":
        return interior, bounds, "bubble-interior"
    return np.zeros_like(interior), bounds, "unsafe-sfx"


def plain_white_fill_color(
    roi: np.ndarray, glyph_mask: np.ndarray
) -> np.ndarray | None:
    """Return the local white background color only for a uniform white ROI."""
    if roi.size == 0 or glyph_mask.size == 0 or not np.any(glyph_mask):
        return None
    background = roi[glyph_mask == 0]
    if background.shape[0] < max(16, roi.shape[0] * roi.shape[1] // 4):
        return None

    # Tight detector boxes around small chat bubbles include some gray UI
    # outside the bubble. A strong two-thirds white majority is sufficient
    # because the color and low-variance checks still reject tone.
    return uniform_white_color(background, 0.68)


def plain_white_interior_color(
    roi: np.ndarray, interior_mask: np.ndarray
) -> np.ndarray | None:
    """Sample a uniform white bubble interior without consulting its exterior."""
    if roi.size == 0 or interior_mask.size == 0 or not np.any(interior_mask):
        return None
    interior = roi[interior_mask > 0]
    if interior.shape[0] < 16:
        return None
    return uniform_white_color(interior, 0.72)


def white_fill_mask(
    image: np.ndarray,
    block: TextBlock,
    glyphs: np.ndarray,
    bounds: Box,
    strategy: str,
) -> np.ndarray:
    """Grow glyphs to cover the gray antialiasing around tiny UI text.

    Only uniform white regions use this, and structural components were
    removed from the glyphs already.
    """
    x1, y1, x2, y2 = bounds
    fill_mask = cv2.dilate(glyphs, np.ones((3, 3), np.uint8), iterations=1)
    text_window = np.zeros_like(fill_mask)
    tx1, ty1, tx2, ty2 = [int(value) for value in block.xyxy]
    wx1, wy1 = max(0, tx1 - x1), max(0, ty1 - y1)
    wx2, wy2 = min(x2 - x1, tx2 - x1), min(y2 - y1, ty2 - y1)
    if wx2 > wx1 and wy2 > wy1:
        text_window[wy1:wy2, wx1:wx2] = 255
    roi_gray = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_RGB2GRAY)
    antialias = np.where(roi_gray <= 245, 255, 0).astype(np.uint8)
    antialias = cv2.bitwise_and(antialias, text_window)
    near_glyphs = cv2.dilate(glyphs, np.ones((7, 7), np.uint8), iterations=1)
    fill_mask = cv2.bitwise_or(fill_mask, cv2.bitwise_and(antialias, near_glyphs))

    if strategy == "bubble-clipped-glyph":
        interior, interior_bounds = bubble_interior_mask_for_block(image, block)
        safety = np.zeros_like(fill_mask)
        overlap = overlap_slices(bounds, interior_bounds)
        if overlap:
            safety[overlap[0]] = interior[overlap[1]]
        fill_mask = cv2.bitwise_and(fill_mask, safety)
    return fill_mask


def prepare_flux_inpainting(
    image: np.ndarray,
    blocks: list[TextBlock],
    rectangular: bool = False,
) -> tuple[np.ndarray, np.ndarray, int, int]:
    """Build a FLUX mask, preserving RT-DETR's white-region shortcut."""
    cleaned = image.copy()
    flux_mask = np.zeros(image.shape[:2], dtype=np.uint8)
    white_count = 0
    textured_count = 0

    for block in suppress_composite_render_regions(blocks):
        glyphs, bounds, strategy = contour_safe_mask_for_block(image, block)
        if glyphs.size == 0 or not np.any(glyphs):
            logger.warning(
                "Skipping FLUX block with no safe mask: %s (%s)",
                block.xyxy.tolist(),
                strategy,
            )
            continue

        x1, y1, x2, y2 = bounds
        roi = image[y1:y2, x1:x2]
        if strategy == "bubble-interior" and not rectangular:
            fill_color = plain_white_interior_color(roi, glyphs)
        else:
            fill_color = plain_white_fill_color(roi, glyphs)

        if fill_color is None:
            local_mask = flux_mask[y1:y2, x1:x2]
            np.maximum(local_mask, glyphs, out=local_mask)
            textured_count += 1
            continue

        fill_mask = (
            white_fill_mask(image, block, glyphs, bounds, strategy)
            if rectangular
            else glyphs
        )
        cleaned[y1:y2, x1:x2][fill_mask > 0] = fill_color
        white_count += 1

    logger.info(
        "FLUX mask: direct-filled %s white regions; queued %s regions",
        white_count,
        textured_count,
    )
    return cleaned, flux_mask, white_count, textured_count