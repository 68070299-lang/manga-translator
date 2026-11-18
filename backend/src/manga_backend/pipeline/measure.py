"""Per-block typography measurements: source glyph size and ink/paper colours.

The ideas are koharu's measurements (``crates/koharu-*``/``measure.rs``, MIT OR
Apache-2.0): a projection profile of the original lettering gives the glyph
size the translation should be anchored to, and Otsu plus colour clustering
gives the ink, paper and outline colours of a block. The implementation here
is original, written against OpenCV and numpy, not ported.

The anchored target is what replaces the old 16 px ceiling: dialogue is fitted
downwards from ``LATIN_TARGET_FACTOR`` times the source glyph advance, then
snapped to the page median so a page of balloons shares one lettering size.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import cv2
import numpy as np

from manga_backend.pipeline.regions import int_box
from manga_backend.utils.textblock import TextBlock

if TYPE_CHECKING:
    from manga_backend.pipeline.rooms import Room

RGB = tuple[int, int, int]

# Japanese lettering advance is about one em; the translated Latin face is
# drawn at this fraction of it. Calibrated once on the private pages: an em
# measured at 14-16 px lettered at 14-16 px fills the same balloon as the
# source after the usual 2.7x text expansion.
LATIN_TARGET_FACTOR = 1.0
# A source glyph estimate outside this range is a measurement failure.
MIN_SOURCE_GLYPH = 5.0
MAX_SOURCE_GLYPH = 160.0
# A Japanese glyph's ink fills about this share of its em cell (kana sit
# smaller than kanji, hence the generous share), so the median glyph span over
# it estimates the em size.
GLYPH_EM_SHARE = 0.75
# A pitch this much larger than the glyph-box estimate means merged runs.
advance_factor = 1.6
# Two estimates further apart than this factor disagree because one of them
# measured merged runs (pitch too big) or outside ink (cross too big).
MERGED_RUN_FACTOR = 1.6
# Below this paper luminance a block is light-on-dark lettering.
DARK_PAPER_LUMINANCE = 96.0
# Ring width used to measure the outline colour around a room.
OUTLINE_RING_PX = 4


def luminance(colour: RGB) -> float:
    """Rec. 601 luma, the scale the luminance thresholds above are stated in."""
    red, green, blue = colour
    return 0.299 * red + 0.587 * green + 0.114 * blue


def _median_run(profile: np.ndarray, minimum: int = 2) -> float:
    """Median length of the runs of a projection profile."""
    values = profile > 0
    if not np.any(values):
        return 0.0
    spans: list[int] = []
    start: int | None = None
    for index, value in enumerate(values):
        if value and start is None:
            start = index
        elif not value and start is not None:
            spans.append(index - start)
            start = None
    if start is not None:
        spans.append(len(values) - start)
    usable = [span for span in spans if span >= minimum]
    return float(np.median(usable)) if usable else 0.0


def _median_pitch(profile: np.ndarray, minimum: int = 2) -> float:
    """Median distance between the centres of a projection profile's runs."""
    values = profile > 0
    if not np.any(values):
        return 0.0
    centres: list[float] = []
    start: int | None = None
    for index, value in enumerate(values):
        if value and start is None:
            start = index
        elif not value and start is not None:
            centres.append((start + index - 1) / 2.0)
            start = None
    if start is not None:
        centres.append((start + len(values) - 1) / 2.0)
    pitches = [second - first for first, second in zip(centres, centres[1:])]
    usable = [pitch for pitch in pitches if pitch >= minimum]
    return float(np.median(usable)) if usable else 0.0


def source_glyph_size(image: np.ndarray, block: TextBlock) -> float | None:
    """Estimate the original lettering's em size from its glyph boxes.

    Otsu splits the block's lettering from its paper; the median span of the
    glyph components is then one em (Japanese lettering is drawn in square
    cells, and a Latin glyph's height is a similar share of its em). The
    projection profile of the advance direction is used as a cross-check and
    as the fallback for lettering that the threshold broke into strokes.
    """
    height, width = image.shape[:2]
    x1, y1, x2, y2 = int_box(block.xyxy)
    x1, y1 = max(0, min(width - 1, x1)), max(0, min(height - 1, y1))
    x2, y2 = max(x1 + 1, min(width, x2)), max(y1 + 1, min(height, y2))
    crop = image[y1:y2, x1:x2]
    if crop.size == 0 or min(crop.shape[:2]) < 4:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    _threshold, binary = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU
    )
    ink = binary > 0
    if float(np.mean(ink)) > 0.5:
        ink = ~ink
    if int(np.count_nonzero(ink)) < 12:
        return None

    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
        ink.astype(np.uint8), connectivity=8
    )
    spans: list[float] = []
    for label in range(1, count):
        area = float(stats[label, cv2.CC_STAT_AREA])
        component_width = float(stats[label, cv2.CC_STAT_WIDTH])
        component_height = float(stats[label, cv2.CC_STAT_HEIGHT])
        if area < 8:
            continue
        spans.append(max(component_width, component_height))
    component_estimate = float(np.median(spans)) / GLYPH_EM_SHARE if spans else 0.0

    vertical = block.height > block.width * 1.25
    advance_profile = ink.sum(axis=1) if vertical else ink.sum(axis=0)
    advance = _median_pitch(advance_profile)
    if advance > 0 and component_estimate > 0:
        # Touching glyphs merge runs and double the pitch; the glyph boxes
        # then give the honest answer.
        estimate = component_estimate if advance > advance_factor * component_estimate else advance
    elif advance > 0:
        estimate = advance
    else:
        estimate = component_estimate
    if estimate <= 0:
        return None
    if not MIN_SOURCE_GLYPH <= estimate <= MAX_SOURCE_GLYPH:
        return None
    return estimate


def anchor_target(source_glyph: float | None, preferred_font_size: int) -> int:
    """The size a dialogue region starts from: its source em, calibrated."""
    if source_glyph is None:
        return max(1, int(preferred_font_size))
    return max(1, int(round(source_glyph * LATIN_TARGET_FACTOR)))


def page_lettering_size(anchors: list[int], preferred_font_size: int) -> int:
    """The one size a page is lettered at: the median of its source anchors.

    A page mixes balloons lettered at slightly different sizes and free text
    with no anchor at all; taking the median keeps the page in one type scale
    (which is what professional lettering does) instead of giving every
    balloon its own. The fitter then only ever shrinks from it, so a room that
    cannot hold the page size still gets the largest size it can.
    """
    usable = [anchor for anchor in anchors if anchor > 0]
    if not usable:
        return max(1, int(preferred_font_size))
    return max(1, int(round(float(np.median(usable)))))


def _median_colour(pixels: np.ndarray) -> RGB:
    red, green, blue = np.median(pixels.reshape(-1, 3), axis=0)
    return (int(round(red)), int(round(green)), int(round(blue)))


def measure_block_colours(
    image: np.ndarray,
    room: "Room",
    user_text: RGB,
    user_border: RGB,
    user_background: RGB,
) -> tuple[RGB, RGB, RGB]:
    """Return (text, border, background) for one block.

    Paper is the median of the room's own pixels on the inpainted page; the
    outline is the darkest quartile of a thin ring just outside the room. Dark
    paper switches the block to light lettering with a dark stroke, which is
    what light-on-dark narration needs; anything else keeps the user's colours.
    """
    height, width = image.shape[:2]
    x1, y1, x2, y2 = room.bounds
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(width, x2), min(height, y2)
    if x2 - x1 < 2 or y2 - y1 < 2:
        return user_text, user_border, user_background
    crop = image[y1:y2, x1:x2]
    mask = room.mask[: crop.shape[0], : crop.shape[1]]
    inside = crop[mask > 0]
    if inside.size < 12:
        return user_text, user_border, user_background
    paper = _median_colour(inside)

    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (OUTLINE_RING_PX * 2 + 1, OUTLINE_RING_PX * 2 + 1)
    )
    grown = cv2.dilate((mask > 0).astype(np.uint8), kernel) > 0
    ring_pixels = crop[grown & (mask == 0)]
    outline = user_text
    if ring_pixels.size >= 12:
        ring_luma = ring_pixels.astype(np.float32) @ np.array(
            [0.299, 0.587, 0.114], dtype=np.float32
        )
        cutoff = float(np.percentile(ring_luma, 30))
        darkest = ring_pixels[ring_luma <= cutoff]
        if darkest.size >= 3:
            outline = _median_colour(darkest)

    if luminance(paper) < DARK_PAPER_LUMINANCE:
        text = user_border if luminance(user_border) > 128 else (255, 255, 255)
        border = outline if luminance(outline) < luminance(text) else (0, 0, 0)
        # The background box follows the measured paper here, otherwise a
        # light-on-dark block would letter light text onto the user's white box.
        return text, border, paper
    return user_text, user_border, user_background
