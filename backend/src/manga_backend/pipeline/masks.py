"""Inpainting mask construction: ink extraction, bubble interiors and the fill router.

Ink extraction follows research section 2.1 (candidate 1). ROI colours are
clustered in Lab (k = 2-3) against a paper estimate taken from the ring
outside the block box, so dark cores and light cores can coexist instead of
the old either/or bright test. Components are then classified by stroke
width from a distance transform: thin line art crossing the box is dropped,
tone dots that sit on a periodic lattice are dropped, and solid art blobs
are dropped. Outline rings grow outward by distance-transform bands while a
band stays uniform and differs from the local background - the idea is
koharu's outline measurement (``crates/koharu-*``/``measure.rs``, MIT OR
Apache-2.0); the implementation here is original, written against OpenCV and
numpy, not ported. Halos are covered by alpha unmixing against a local
background instead of the old fixed 3x3 dilation.

Bubble interiors follow research section 2.2 (candidate 1): a seeded flood
fill over paper from inside the text box, with structure (the outline) as
the barrier, a 1-2 px gap close, hole filling and leak rejection. The old
smallest-closed-contour search stays as the fallback for open or borderless
bubbles, and it is still where its own failure codes come from.

The mask union, the router and the composite are unchanged; masks only get
tighter and more complete.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from manga_backend.pipeline.fill_tiers import (
    FRINGE_RADIUS,
    RING_RADIUS,
    TIER_MODEL,
    TIER_SKIPPED,
    FillDecision,
)
from manga_backend.pipeline.residual import route_block_fill_checked
from manga_backend.pipeline.regions import int_box, suppress_composite_render_regions
from manga_backend.utils.textblock import Box, TextBlock

logger = logging.getLogger(__name__)

Slices = tuple[slice, slice]

# How far the router's context ring reaches beyond the block mask, and how
# far inside a detector bubble box the sampling region stays.
ROUTER_CONTEXT = RING_RADIUS + FRINGE_RADIUS
BUBBLE_VALID_INSET = 4
# Mask strategies that were clipped to a verified bubble interior; their
# sampling stays inside the bubble so the outline cannot fail every tier.
INTERIOR_STRATEGIES = frozenset({"bubble-clipped-glyph", "contour-clipped-glyph"})

# --- Ink extraction ------------------------------------------------------
# The paper estimate comes from a ring this wide outside the block box.
PAPER_RING_RADIUS = 10
# Half-width of the dominant ring mode that counts as paper.
PAPER_MODE_TOLERANCE = 12.0
# A block can sit on its own bright island (a caption box, a white panel) so
# the block box's own dominant level overrides the ring when it is clearly
# brighter and covers most of the box.
PAPER_ISLAND_SHARE = 0.5
# Per-pixel paper seed tolerance when looking for bubble-interior seeds.
PAPER_SEED_TOLERANCE = 8.0
SEED_CHROMA_TOLERANCE = 10.0
# Lab k-means: sample budget, cluster count and merge distance. Two clusters
# near each other are one population split by JPEG noise; anything closer to
# the paper cluster than INK_MIN_DISTANCE is background, not ink.
KMEANS_SAMPLES = 6000
KMEANS_MAX_K = 3
KMEANS_ITERATIONS = 8
CLUSTER_MERGE_DISTANCE = 12.0
INK_MIN_DISTANCE = 14.0
# A component is "thin" when twice its maximum distance-transform radius (a
# stroke-width estimate in the spirit of SWT, Epshtein et al. 2010) stays
# below this many pixels. Cut glyph strokes and lettering blobs are thicker;
# bubble outlines, rules and hatching are thinner.
THIN_STROKE_RADIUS = 2.2
THIN_STROKE_LIMIT = 2.0 * THIN_STROKE_RADIUS
# A component can also be structure rather than text: a compact solid blob
# whose level is a mid-tone, in a crop that already carries a much stronger
# ink population. Glyphs are strokes (never blocky) and faded lettering is
# the crop's only ink, so both survive; a grey filled shape inside a dark
# text box does not.
BLOCKY_THICKNESS_SHARE = 0.7
BLOCKY_ELONGATION = 2.5
BLOCKY_MIN_AREA = 24.0
# The "strong" ink population of a crop is measured on components at least
# this large (or this share of the crop).
STRONG_MIN_AREA = 40.0
STRONG_MIN_AREA_RATIO = 0.004
MID_TONE_CONTRAST_SHARE = 0.5
# Line art: thin components that also cross or touch the crop border, and
# that look like a line rather than like a cut glyph: either sparse (a
# diagonal hatch or a long arc at a shallow angle fills almost none of its
# bounding box) or a solid bar with a strong length-to-width ratio.
CROSS_SPAN_FRACTION = 0.6
CROSS_MAX_THICKNESS = 5.0
CROSS_THICKNESS_FRACTION = 0.12
LINE_MAX_FILL = 0.2
LINE_MAX_LINEARITY = 2.3
LINE_SPARSE_FILL = 0.35
# A sparse structure that fills a real share of the crop and touches its
# border is a frame or a rule crossing the block, whatever its corners.
LINE_SPARSE_MIN_AREA_RATIO = 0.1
# Tone dots: small, round and thin, and they sit on a periodic lattice whose
# period is shared by a whole population. They are only rejected when their
# colour is a separate population from the text ink, so small grey lettering
# is never mistaken for tone.
DOT_MAX_AREA = 60
DOT_MAX_EXTENT = 9
DOT_MIN_FILL = 0.4
DOT_MIN_POPULATION = 12
DOT_MIN_SPACING = 3.0
DOT_LATTICE_TOLERANCE = 1.6
DOT_LATTICE_SHARE = 0.7
DOT_COLOR_GAP = 10.0
# Outline-ring growth: one distance-transform band at a time, accepted while
# the band stays uniform and differs from the local background (or while the
# background is textured and the band is flat, which is how a white SFX
# outline over screentone is found).
RING_MAX_RADIUS = 6
RING_MIN_DIFF = 8.0
# The band's own level and texture, measured against the annulus beyond it.
RING_LEVEL_TOLERANCE = 12.0
RING_TEXTURE_DEVIATION = 24.0
RING_ACCEPT_SHARE = 0.15
# A colour-distinct band needs no texture argument; a band that matches the
# paper level (a white outline on white-backed screentone, say) must instead
# be a gap in a textured background that carries none of that texture.
RING_TEXTURE_SHARE = 0.12
# A ring that has to be told apart from texture by the *absence* of it must
# carry almost none of that texture on its own bands.
RING_FLAT_SHARE = 0.05
RING_BACKGROUND_INNER = 9
RING_BACKGROUND_OUTER = 21
# Halo coverage: alpha unmixing against a local background, so a glyph's
# antialiased fringe is measured from the data instead of a fixed dilation.
HALO_RADIUS = 3
HALO_ALPHA_MIN = 0.1
HALO_MIN_CONTRAST = 8.0
# Tone never belongs to the ink: screentone dots sit at their own level, and
# a dot touching a glyph would otherwise be kept as glyph ink (it is not,
# and filling it would flatten the tone). The halo also skips that level, so
# the removed pixels are not readmitted as blends.
TONE_REMOVAL_TOLERANCE = 24.0

# --- Bubble interiors ----------------------------------------------------
# Structure threshold bounds for the flood barrier (same Otsu clamp as the
# contour fallback). Gaps in the barrier are closed by 1-2 px only.
INTERIOR_DARK_MIN = 70
INTERIOR_DARK_MAX = 210
INTERIOR_GAP_CLOSE = 1
INTERIOR_ERODE = 1
# Leak rejection: the fill must not exceed the detector bubble box by more
# than this factor, and it must stay off the page edge.
INTERIOR_LEAK_FACTOR = 1.3
INTERIOR_MIN_COVERAGE = 0.5
INTERIOR_MIN_AREA_FRACTION = 0.5
# An enclosed island this small, or one mostly inside the text box, is text
# ink rather than a hole in the bubble (glyphs become interior after filling).
INTERIOR_HOLE_MAX_PX = 4000.0
INTERIOR_HOLE_TEXT_SHARE = 0.5

# Interior failure reasons, reported by the eval harness as mask reason codes.
INTERIOR_OK = "interior"
INTERIOR_NO_CONTEXT = "no_contour"
INTERIOR_NO_SEED = "no_paper_seed"
INTERIOR_LEAK = "flood_leak"
INTERIOR_SMALL = "flood_small"
INTERIOR_UNCOVERED = "flood_coverage"
INTERIOR_CONTOUR_EDGE = "contour_touches_roi_edge"
INTERIOR_CONTOUR_AREA = "area_test_failed"
INTERIOR_CONTOUR_COVERAGE = "coverage_below_35"


@dataclass(frozen=True)
class _Paper:
    """Local paper estimate in Lab and RGB, taken from the ring outside the box."""

    light: float
    channel_a: float
    channel_b: float
    rgb: np.ndarray


@dataclass(frozen=True)
class InteriorResult:
    """One bubble-interior attempt: the mask, where it came from and why it failed."""

    interior: np.ndarray
    bounds: Box
    method: str
    reason: str

    @property
    def found(self) -> bool:
        return bool(self.interior.size) and bool(np.any(self.interior))


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


def _lab_channels(pixels: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Lab L (0-255 scale), a and b (signed) as float32 planes."""
    converted = cv2.cvtColor(pixels, cv2.COLOR_RGB2LAB)
    return (
        converted[..., 0].astype(np.float32),
        converted[..., 1].astype(np.float32) - 128.0,
        converted[..., 2].astype(np.float32) - 128.0,
    )


def _paper_estimate(image: np.ndarray, box: Box) -> _Paper:
    """The dominant ring colour outside a block box: this block's paper.

    The ring is read from the page, not the crop, so a text-free margin still
    gives an estimate when the block box itself is full of ink.
    """
    height, width = image.shape[:2]
    x1, y1, x2, y2 = (int(value) for value in box)
    ox1, oy1 = max(0, x1 - PAPER_RING_RADIUS), max(0, y1 - PAPER_RING_RADIUS)
    ox2, oy2 = min(width, x2 + PAPER_RING_RADIUS), min(height, y2 + PAPER_RING_RADIUS)
    if ox2 <= ox1 or oy2 <= oy1:
        return _Paper(128.0, 0.0, 0.0, np.full(3, 128.0, np.float32))
    ring = np.zeros((height, width), np.uint8)
    ring[oy1:oy2, ox1:ox2] = 1
    ring[max(0, y1 - 1) : min(height, y2 + 1), max(0, x1 - 1) : min(width, x2 + 1)] = 0
    pixels = image[ring > 0]
    if pixels.size == 0:
        pixels = image[oy1:oy2, ox1:ox2].reshape(-1, image.shape[2])
    if pixels.size == 0:
        return _Paper(128.0, 0.0, 0.0, np.full(3, 128.0, np.float32))
    light, channel_a, channel_b = _lab_channels(pixels.reshape(-1, 1, 3))
    values = light.ravel()
    histogram, edges = np.histogram(values, bins=64, range=(0.0, 256.0))
    mode = float(edges[int(np.argmax(histogram))] + 2.0)
    near = np.abs(values - mode) <= PAPER_MODE_TOLERANCE
    if int(np.count_nonzero(near)) < max(8, int(0.25 * values.size)):
        near = np.ones(values.shape, bool)
    flat_pixels = pixels.reshape(-1, image.shape[2])
    paper = _Paper(
        float(np.median(values[near])),
        float(np.median(channel_a.ravel()[near])),
        float(np.median(channel_b.ravel()[near])),
        np.median(flat_pixels[near], axis=0).astype(np.float32),
    )
    return _block_island_paper(image, box, paper)


def _block_island_paper(image: np.ndarray, box: Box, paper: _Paper) -> _Paper:
    """Override the ring paper when the block box sits on its own bright island.

    A caption box or a white panel inside dark art has its paper *inside* the
    block box, not around it; using the ring there would make the box itself
    the ink. Only a clearly brighter, dominant level qualifies.
    """
    height, width = image.shape[:2]
    x1, y1, x2, y2 = (int(value) for value in box)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(width, x2), min(height, y2)
    if x2 - x1 < 4 or y2 - y1 < 4:
        return paper
    inside = image[y1:y2, x1:x2].reshape(-1, image.shape[2])
    light, channel_a, channel_b = _lab_channels(inside.reshape(-1, 1, 3))
    values = light.ravel()
    histogram, edges = np.histogram(values, bins=64, range=(0.0, 256.0))
    mode = float(edges[int(np.argmax(histogram))] + 2.0)
    share = float(np.count_nonzero(np.abs(values - mode) <= PAPER_MODE_TOLERANCE)) / max(
        1, values.size
    )
    if share < PAPER_ISLAND_SHARE or mode < paper.light + INK_MIN_DISTANCE:
        return paper
    selected = np.abs(values - mode) <= PAPER_MODE_TOLERANCE
    return _Paper(
        float(np.median(values[selected])),
        float(np.median(channel_a.ravel()[selected])),
        float(np.median(channel_b.ravel()[selected])),
        np.median(inside[selected], axis=0).astype(np.float32),
    )


def _cluster_lab_centres(sample: np.ndarray, cluster_count: int) -> np.ndarray:
    """Deterministic Lab k-means: initial centres from lightness quantiles.

    OpenCV's own initialisation draws from a global RNG, which would make the
    mask (and therefore every quality row) non-deterministic across runs, so
    the starting centres come from the sample's own lightness quantiles.
    """
    levels = np.percentile(sample[:, 0], np.linspace(10.0, 90.0, cluster_count))
    seeds = [sample[int(np.argmin(np.abs(sample[:, 0] - level)))].copy() for level in levels]
    unique: list[np.ndarray] = []
    for seed in seeds:
        if all(float(np.linalg.norm(seed - other)) > 0.5 for other in unique):
            unique.append(np.asarray(seed, np.float32))
    if len(unique) < 2:
        return np.stack(unique) if unique else sample[:1].copy()
    centres = np.stack(unique)
    labels = np.zeros((sample.shape[0], 1), np.int32)
    criteria = (
        cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
        KMEANS_ITERATIONS,
        0.5,
    )
    _compactness, _labels, centres = cv2.kmeans(
        sample,
        centres.shape[0],
        labels,
        criteria,
        1,
        cv2.KMEANS_USE_INITIAL_LABELS,
        centres,
    )
    return np.asarray(centres, np.float32)


def _ink_populations(
    roi: np.ndarray, paper: _Paper
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Cluster ROI colours in Lab and return one (mask, colour) per ink population.

    The paper estimate anchors the cluster set: the cluster nearest to it is
    this crop's background and every cluster far enough from it is ink, so a
    dark core and a light core can be ink at the same time (H1). Populations
    stay separate on purpose: a shape rule must judge the lettering on its
    own, not a blob that line art and lettering formed together. Clusters are
    capped at three, so a fourth population (faint grey lettering beside
    screentone, say) can be absorbed by the paper cluster; every pixel far
    enough from the paper estimate is therefore a population as well.
    """
    light, channel_a, channel_b = _lab_channels(roi)
    features = np.stack(
        [light.ravel(), channel_a.ravel(), channel_b.ravel()], axis=1
    ).astype(np.float32)
    if features.shape[0] < 24:
        return []
    stride = max(1, features.shape[0] // KMEANS_SAMPLES)
    sample = features[::stride]
    cluster_count = int(min(KMEANS_MAX_K, max(2, sample.shape[0] // 12)))
    centres = _cluster_lab_centres(sample, cluster_count)
    if centres.ndim == 1:
        centres = centres.reshape(1, -1)
    merged: list[np.ndarray] = []
    for centre in centres:
        centre = np.asarray(centre, np.float32)
        if all(
            float(np.linalg.norm(centre - other)) > CLUSTER_MERGE_DISTANCE
            for other in merged
        ):
            merged.append(centre)
    if not merged:
        return []
    stacked = np.stack(merged)
    paper_point = np.array([paper.light, paper.channel_a, paper.channel_b], np.float32)
    paper_distance = np.linalg.norm(features - paper_point, axis=1)
    # Only darker-than-paper pixels join through this fallback. A bright
    # population far from the paper is a ring or halo candidate: adding it
    # here would bridge glyphs to surrounding texture before the shape rules
    # run, so bright rings are left to the band-growth pass instead.
    faint = (paper_distance >= INK_MIN_DISTANCE) & (features[:, 0] < paper.light)
    paper_index = int(np.argmin(np.linalg.norm(stacked - paper_point, axis=1)))
    ink_indices = [
        index
        for index in range(len(merged))
        if index != paper_index
        and float(np.linalg.norm(merged[index] - merged[paper_index]))
        >= INK_MIN_DISTANCE
    ]
    nearest = np.argmin(
        np.linalg.norm(features[:, None, :] - stacked[None, :, :], axis=2), axis=1
    )
    flat_roi = roi.reshape(-1, roi.shape[2]).astype(np.float32)
    populations: list[tuple[np.ndarray, np.ndarray]] = []
    claimed = np.zeros(features.shape[0], bool)
    for index in ink_indices:
        selected = nearest == index
        if not np.any(selected):
            continue
        claimed |= selected
        populations.append(
            (
                selected.reshape(light.shape),
                np.median(flat_roi[selected], axis=0).astype(np.float32),
            )
        )
    leftover = faint & ~claimed
    if np.any(leftover):
        populations.append(
            (
                leftover.reshape(light.shape),
                np.median(flat_roi[leftover], axis=0).astype(np.float32),
            )
        )
    return populations


def _label_max(
    values: np.ndarray, labels: np.ndarray, count: int
) -> np.ndarray:
    """Per-label maximum of ``values`` (labels run from 0 to count-1)."""
    flat_labels = labels.ravel()
    order = np.argsort(flat_labels, kind="stable")
    sorted_labels = flat_labels[order]
    sorted_values = values.ravel()[order]
    starts = np.searchsorted(sorted_labels, np.arange(count))
    ends = np.append(starts[1:], sorted_labels.size)
    maxima = np.zeros(count, np.float64)
    filled = ends > starts
    if np.any(filled):
        maxima[filled] = np.maximum.reduceat(sorted_values, starts[filled])
    return maxima


def _label_mean(
    values: np.ndarray, labels: np.ndarray, count: int
) -> np.ndarray:
    """Per-label mean of ``values``."""
    flat_labels = labels.ravel()
    sums = np.bincount(flat_labels, weights=values.ravel().astype(np.float64), minlength=count)
    counts = np.bincount(flat_labels, minlength=count)
    return sums / np.maximum(counts, 1)


def _reject_tone_dots(
    stats: np.ndarray,
    centroids: np.ndarray,
    thickness: np.ndarray,
    mean_light: np.ndarray,
    dot_like: np.ndarray,
) -> np.ndarray:
    """Dot components that sit on a periodic lattice, as a boolean label set.

    Tone is a population of isomorphic small blobs on a regular lattice; real
    lettering that small is a separate colour population and does not form
    one. Both conditions are required, so ambiguous cases stay in the mask.
    """
    rejected = np.zeros(dot_like.shape, bool)
    indices = np.nonzero(dot_like)[0]
    if indices.size < DOT_MIN_POPULATION:
        return rejected
    centres = centroids[indices].astype(np.float64)
    delta = centres[:, None, :] - centres[None, :, :]
    distance = np.hypot(delta[..., 0], delta[..., 1])
    np.fill_diagonal(distance, np.inf)
    nearest = distance.min(axis=1)
    spacing = float(np.median(nearest))
    if spacing < DOT_MIN_SPACING:
        return rejected
    share = float(np.mean(np.abs(nearest - spacing) <= DOT_LATTICE_TOLERANCE))
    thick = (~dot_like) & (thickness > THIN_STROKE_LIMIT)
    if not np.any(thick):
        return rejected
    dots_light = float(np.median(mean_light[indices]))
    ink_light = float(np.median(mean_light[thick]))
    if abs(dots_light - ink_light) < DOT_COLOR_GAP:
        return rejected
    if share < DOT_LATTICE_SHARE:
        return rejected
    rejected[indices] = True
    return rejected


def _classify_ink_components(
    candidate: np.ndarray, light: np.ndarray, paper_level: float
) -> np.ndarray:
    """Drop line art, periodic tone dots and mid-tone structure from a candidate mask."""
    if not np.any(candidate):
        return np.zeros_like(candidate, bool)
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        candidate.astype(np.uint8), connectivity=8
    )
    labels = np.asarray(labels, dtype=np.int32)
    if count <= 1:
        return np.zeros_like(candidate, bool)
    # Distance transform with a background border: outside the crop counts as
    # background, so a component that touches the crop border still reports its
    # real stroke width instead of the distance to the crop edge.
    padded = np.zeros((candidate.shape[0] + 2, candidate.shape[1] + 2), np.uint8)
    padded[1:-1, 1:-1] = candidate.astype(np.uint8)
    distance = cv2.distanceTransform(padded, cv2.DIST_L2, 5)[1:-1, 1:-1]
    thickness = 2.0 * _label_max(distance, labels, count)
    areas = stats[:, cv2.CC_STAT_AREA].astype(np.float64)
    widths = stats[:, cv2.CC_STAT_WIDTH].astype(np.float64)
    heights = stats[:, cv2.CC_STAT_HEIGHT].astype(np.float64)
    lefts = stats[:, cv2.CC_STAT_LEFT].astype(np.float64)
    tops = stats[:, cv2.CC_STAT_TOP].astype(np.float64)
    roi_height, roi_width = candidate.shape
    mean_light = _label_mean(light, labels, count)
    touches_edge = (
        (lefts <= 0)
        | (tops <= 0)
        | (lefts + widths >= roi_width)
        | (tops + heights >= roi_height)
    )
    thin = thickness <= THIN_STROKE_LIMIT
    fill = areas / np.maximum(1.0, widths * heights)
    long_rule = ((widths >= roi_width * 0.65) & (heights <= 3.0)) | (
        (heights >= roi_height * 0.65) & (widths <= 3.0)
    )
    cross_limit = max(CROSS_MAX_THICKNESS, CROSS_THICKNESS_FRACTION * max(roi_height, roi_width))
    crosses = (
        (widths >= roi_width * CROSS_SPAN_FRACTION) & (heights <= cross_limit)
    ) | ((heights >= roi_height * CROSS_SPAN_FRACTION) & (widths <= cross_limit))
    radius = np.maximum(thickness / 2.0, 0.5)
    linearity = areas / (radius * np.maximum(1.0, np.hypot(widths, heights)))
    sparse_line = fill <= LINE_MAX_FILL
    line_like = (linearity <= LINE_MAX_LINEARITY) | (fill <= LINE_SPARSE_FILL)
    line_art = (thin & (long_rule | crosses | (touches_edge & line_like))) | (
        sparse_line
        & touches_edge
        & (areas >= LINE_SPARSE_MIN_AREA_RATIO * roi_height * roi_width)
    )
    contrast = np.abs(mean_light - paper_level)
    strong_limit = max(STRONG_MIN_AREA, STRONG_MIN_AREA_RATIO * roi_height * roi_width)
    strong = (areas >= strong_limit) & (~thin)
    strong_gap = float(contrast[strong].max()) if np.any(strong) else 0.0
    blocky = (
        (thickness >= BLOCKY_THICKNESS_SHARE * np.maximum(1.0, np.minimum(widths, heights)))
        & (np.maximum(widths, heights) <= BLOCKY_ELONGATION * np.maximum(1.0, np.minimum(widths, heights)))
        & (areas >= BLOCKY_MIN_AREA)
    )
    structure = (
        blocky
        & (strong_gap > 0.0)
        & (contrast <= MID_TONE_CONTRAST_SHARE * strong_gap)
    )
    rejected = line_art | structure
    rejected[0] = True
    keep = ~rejected
    keep[0] = False
    return keep[labels]


def _tone_dot_mask(candidate: np.ndarray, light: np.ndarray) -> np.ndarray:
    """Pixels belonging to a periodic tone-dot population inside a candidate mask.

    Dot rejection is a population decision, so it runs once on every ink
    population together: the dots are only recognisable as tone by contrast
    with the lettering's own colour.
    """
    empty = np.zeros_like(candidate, bool)
    if not np.any(candidate):
        return empty
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        candidate.astype(np.uint8), connectivity=8
    )
    labels = np.asarray(labels, dtype=np.int32)
    if count <= 1:
        return empty
    padded = np.zeros((candidate.shape[0] + 2, candidate.shape[1] + 2), np.uint8)
    padded[1:-1, 1:-1] = candidate.astype(np.uint8)
    distance = cv2.distanceTransform(padded, cv2.DIST_L2, 5)[1:-1, 1:-1]
    thickness = 2.0 * _label_max(distance, labels, count)
    areas = stats[:, cv2.CC_STAT_AREA].astype(np.float64)
    widths = stats[:, cv2.CC_STAT_WIDTH].astype(np.float64)
    heights = stats[:, cv2.CC_STAT_HEIGHT].astype(np.float64)
    fill = areas / np.maximum(1.0, widths * heights)
    mean_light = _label_mean(light, labels, count)
    dot_like = (
        (thickness <= THIN_STROKE_LIMIT)
        & (areas <= DOT_MAX_AREA)
        & (widths <= DOT_MAX_EXTENT)
        & (heights <= DOT_MAX_EXTENT)
        & (fill >= DOT_MIN_FILL)
    )
    rejected: np.ndarray = _reject_tone_dots(
        stats, centroids, thickness, mean_light, dot_like
    )
    if not np.any(rejected):
        return empty
    return rejected[labels]


def _dominant_level(light_values: np.ndarray) -> float:
    """Mode of a lightness distribution, quantized to 8-level bins."""
    histogram, edges = np.histogram(
        light_values, bins=32, range=(0.0, 256.0)
    )
    return float(edges[int(np.argmax(histogram))] + 4.0)


def _ring_level_verdict(
    background_lightness: np.ndarray, level: float
) -> tuple[bool, bool, float | None]:
    """Whether a band level can be an outline ring, and how to prove it.

    Two cases are possible. A ring whose level differs from the paper estimate
    is distinctive by colour and needs no further argument. A ring level equal
    to the paper (a white outline on white-backed screentone) can only be told
    apart from the background by carrying none of the background texture, so
    the bands must stay free of it. Returns (plausible, requires_flat_bands,
    texture_level).
    """
    outside_level = _dominant_level(background_lightness)
    if abs(level - outside_level) >= RING_MIN_DIFF:
        return True, False, None
    deviating = background_lightness[
        np.abs(background_lightness - outside_level) > RING_TEXTURE_DEVIATION
    ]
    if deviating.size < 8:
        return False, False, None
    if float(deviating.size) / max(1, background_lightness.size) < RING_TEXTURE_SHARE:
        return False, False, None
    texture_level = float(np.median(deviating))
    # Screentone and hatching are drawn darker than the paper they sit on.
    if texture_level > level - RING_TEXTURE_DEVIATION:
        return False, False, None
    return True, True, texture_level


def _grow_outline_bands(
    roi: np.ndarray, mask: np.ndarray, max_radius: int = RING_MAX_RADIUS
) -> np.ndarray:
    """Grow outline rings outward by distance-transform bands.

    The ring level is locked from the first band (the band that touches the
    core) and every following band contributes only its pixels on that
    level, so a ring that is partly interrupted by surrounding texture still
    grows along the rest of its outline. Growth stops when a band no longer
    carries the ring level in any quantity, or when the level could only be
    the background.
    """
    grown = mask.astype(bool).copy()
    if not grown.any():
        return grown
    lightness = roi.astype(np.float32).mean(axis=2)
    inner_kernel = np.ones((RING_BACKGROUND_INNER, RING_BACKGROUND_INNER), np.uint8)
    outer_kernel = np.ones((RING_BACKGROUND_OUTER, RING_BACKGROUND_OUTER), np.uint8)
    level: float | None = None
    texture_level: float | None = None
    for _step in range(max(0, max_radius)):
        binary = grown.astype(np.uint8)
        shell = cv2.dilate(binary, np.ones((3, 3), np.uint8)).astype(bool) & ~grown
        if not shell.any():
            break
        if level is None:
            # Lock the ring level on a slightly wider band: the first shell
            # alone is mostly glyph antialiasing, which would lock growth onto
            # a mid grey instead of the ring itself.
            level_band = (
                cv2.dilate(binary, np.ones((7, 7), np.uint8)).astype(bool) & ~grown
            )
            level = _dominant_level(lightness[level_band if level_band.any() else shell])
            inner = cv2.dilate(binary, inner_kernel).astype(bool)
            outer = cv2.dilate(binary, outer_kernel).astype(bool)
            background = outer & ~inner
            if not background.any():
                break
            plausible, requires_flat, texture_level = _ring_level_verdict(
                lightness[background], level
            )
            if not plausible:
                break
            if requires_flat and texture_level is None:
                break
        shell_pixels = float(np.count_nonzero(shell))
        accepted = shell & (np.abs(lightness - level) <= RING_LEVEL_TOLERANCE)
        if float(np.count_nonzero(accepted)) < RING_ACCEPT_SHARE * shell_pixels:
            break
        if texture_level is not None:
            textured = shell & (
                np.abs(lightness - texture_level) <= RING_TEXTURE_DEVIATION
            )
            if float(np.count_nonzero(textured)) > RING_FLAT_SHARE * shell_pixels:
                break
        grown |= accepted
    return grown


def _texture_level(lightness: np.ndarray, paper_level: float) -> float | None:
    """The level of the background texture (screentone dots, hatching), if any."""
    inside = lightness[np.abs(lightness - paper_level) <= PAPER_MODE_TOLERANCE]
    if inside.size == 0:
        return None
    dominant = _dominant_level(inside)
    deviating = lightness[np.abs(lightness - dominant) > RING_TEXTURE_DEVIATION]
    if deviating.size < 8:
        return None
    if float(deviating.size) / max(1, lightness.size) < RING_TEXTURE_SHARE:
        return None
    return float(np.median(deviating))


def _tone_dot_bodies(light: np.ndarray, tone_dots: np.ndarray) -> np.ndarray:
    """The body of every tone dot, including dots merged into a glyph.

    A dot that touches a glyph joins the glyph's component and escapes the
    population test, yet it is still paper: filling it would flatten the
    tone. A dot's interior is a little plateau at the tone level, while a
    glyph's antialiased edge is a one-pixel ramp, so plateau pixels (their
    whole 3x3 neighbourhood stays on the level) plus one pixel of margin
    pick the dots and only the dots.
    """
    empty = np.zeros(light.shape, bool)
    if int(np.count_nonzero(tone_dots)) < DOT_MIN_POPULATION:
        return empty
    tone_level = float(np.median(light[tone_dots]))
    plateau = (np.abs(light - tone_level) <= TONE_REMOVAL_TOLERANCE).astype(np.uint8)
    cross = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    interior = cv2.erode(plateau, cross) > 0
    if not np.any(interior):
        return empty
    margin = np.ones((3, 3), np.uint8)
    return (cv2.dilate(interior.astype(np.uint8), margin) > 0) & (plateau > 0)


def _cover_halo(
    roi: np.ndarray,
    mask: np.ndarray,
    paper: _Paper,
    ink_colours: list[np.ndarray],
    excluded: np.ndarray | None = None,
) -> np.ndarray:
    """Add antialiased fringe pixels by alpha-unmixing against a local background.

    alpha = ((I - B) . (F - B)) / |F - B|^2 with B the local background taken
    from a blurred copy of the crop whose mask is repainted with paper. This
    replaces the fixed 3x3 dilation with a halo measured from the data.
    """
    grown = mask.astype(bool)
    if not grown.any() or not ink_colours:
        return grown
    kernel = np.ones((2 * HALO_RADIUS + 1, 2 * HALO_RADIUS + 1), np.uint8)
    halo = cv2.dilate(grown.astype(np.uint8), kernel).astype(bool) & ~grown
    if excluded is not None:
        halo &= ~excluded
    if not halo.any():
        return grown
    values = roi.astype(np.float32)
    filled = roi.copy()
    filled[grown] = np.clip(np.rint(paper.rgb), 0, 255).astype(np.uint8)
    background = cv2.blur(filled.astype(np.float32), (7, 7))
    accepted = np.zeros(grown.shape, bool)
    for colour in ink_colours:
        difference = colour.astype(np.float32) - background
        norm = (difference * difference).sum(axis=2)
        covered = norm > HALO_MIN_CONTRAST * HALO_MIN_CONTRAST
        if not np.any(covered):
            continue
        alpha = ((values - background) * difference).sum(axis=2) / np.maximum(norm, 1.0)
        accepted |= covered & (alpha >= HALO_ALPHA_MIN)
    return grown | (halo & accepted)


def _core_colours(
    roi: np.ndarray, light: np.ndarray, cores: np.ndarray, paper: _Paper
) -> list[np.ndarray]:
    """Dominant ink colours of the accepted cores, split around the paper level.

    Alpha unmixing needs one foreground colour per polarity; mistaking tone
    or hatch (which may dominate the raw candidate) for the glyph colour
    would un-mix against the wrong ink.
    """
    values = roi.astype(np.float32)
    colours: list[np.ndarray] = []
    for selector in (
        cores & (light < paper.light),
        cores & (light > paper.light),
    ):
        if np.any(selector):
            colours.append(np.median(values[selector], axis=0).astype(np.float32))
    return colours


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
    """Extract lettering ink: clustered cores, grown outline rings, unmixed halos."""
    height, width = image.shape[:2]
    x1, y1, x2, y2 = [int(value) for value in block.xyxy]
    padding = 2
    x1, y1 = max(0, x1 - padding), max(0, y1 - padding)
    x2, y2 = min(width, x2 + padding), min(height, y2 + padding)
    if x2 <= x1 or y2 <= y1:
        return np.zeros((0, 0), dtype=np.uint8), (x1, y1, x2, y2)

    roi = image[y1:y2, x1:x2]
    paper = _paper_estimate(image, int_box(block.xyxy))
    populations = _ink_populations(roi, paper)
    if not populations:
        return np.zeros(roi.shape[:2], dtype=np.uint8), (x1, y1, x2, y2)

    light = _lab_channels(roi)[0]
    cores = np.zeros(roi.shape[:2], bool)
    union = np.zeros(roi.shape[:2], bool)
    for candidate, _colour in populations:
        union |= candidate
        cores |= _classify_ink_components(candidate, light, paper.rgb.mean())
    tone_dots = _tone_dot_mask(union, light)
    cores &= ~tone_dots
    tone = _tone_dot_bodies(light, tone_dots)
    cores &= ~tone
    if not np.any(cores):
        return np.zeros(roi.shape[:2], dtype=np.uint8), (x1, y1, x2, y2)
    grown = _grow_outline_bands(roi, cores)
    glyphs = _cover_halo(
        roi, grown, paper, _core_colours(roi, light, cores, paper), tone
    )
    return glyphs.astype(np.uint8) * 255, (x1, y1, x2, y2)


def _interior_window(
    image: np.ndarray, block: TextBlock
) -> tuple[Box, tuple[int, int, int, int], Box | None] | None:
    """Bubble search window, the text box in its coordinates, and the detector box."""
    height, width = image.shape[:2]
    tx1, ty1, tx2, ty2 = [int(value) for value in block.xyxy]
    tx1, ty1 = max(0, tx1), max(0, ty1)
    tx2, ty2 = min(width, tx2), min(height, ty2)
    if tx2 <= tx1 or ty2 <= ty1:
        return None

    detector_bounds: Box | None = None
    bubble = block.bubble_xyxy
    if bubble is not None:
        try:
            candidate = int_box(bubble)
        except (TypeError, ValueError):
            candidate = None
        if (
            candidate is not None
            and candidate[2] > candidate[0]
            and candidate[3] > candidate[1]
        ):
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
        padding_x = max(32, int(block_width * 1.25), int(block_height * 0.5))
        padding_y = max(32, int(block_height * 0.75), int(block_width * 1.25))
        x1, y1 = max(0, tx1 - padding_x), max(0, ty1 - padding_y)
        x2, y2 = min(width, tx2 + padding_x), min(height, ty2 + padding_y)
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2), (tx1 - x1, ty1 - y1, tx2 - x1, ty2 - y1), detector_bounds


def _drop_dot_barriers(barrier: np.ndarray) -> np.ndarray:
    """Remove dot-like components from a flood barrier.

    Tone dots are small round blobs. Left in place they can enclose paper
    pockets between them, and a pocket would be mistaken for a bubble
    interior; a wall is thin line art, not a dot.
    """
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        barrier.astype(np.uint8), connectivity=8
    )
    labels = np.asarray(labels, dtype=np.int32)
    if count <= 1:
        return barrier
    areas = stats[:, cv2.CC_STAT_AREA].astype(np.float64)
    widths = stats[:, cv2.CC_STAT_WIDTH].astype(np.float64)
    heights = stats[:, cv2.CC_STAT_HEIGHT].astype(np.float64)
    fill = areas / np.maximum(1.0, widths * heights)
    drop = (
        (areas <= DOT_MAX_AREA)
        & (widths <= DOT_MAX_EXTENT)
        & (heights <= DOT_MAX_EXTENT)
        & (fill >= DOT_MIN_FILL)
    )
    drop[0] = False
    if not np.any(drop):
        return barrier
    kept: np.ndarray = drop
    return barrier & ~kept[labels]


def _flood_interior(
    roi: np.ndarray,
    local_text: tuple[int, int, int, int],
    paper: _Paper,
    detector_bounds: Box | None,
    ink_pixels: np.ndarray | None = None,
) -> tuple[np.ndarray | None, str]:
    """Seeded flood fill over paper, bounded by structure, with leak rejection.

    ``ink_pixels`` marks this block's own lettering inside the crop. Lettering
    is passable: it is not part of the outline, so text hugging or touching a
    bubble wall must not split the interior (and the old search's text
    blanking, which could open the outline, is gone). Where lettering truly
    covers the outline the flood leaks and the contour fallback takes over.
    """
    roi_height, roi_width = roi.shape[:2]
    light, channel_a, channel_b = _lab_channels(roi)
    gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    otsu_threshold, _binary = cv2.threshold(
        blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    dark_threshold = int(np.clip(otsu_threshold, INTERIOR_DARK_MIN, INTERIOR_DARK_MAX))
    dark_barrier = blurred <= dark_threshold
    edge_barrier = cv2.Canny(blurred, 50, 150) > 0
    # Neither background texture nor paper itself is a wall. Tone dots that
    # survive the close can knit into a lattice and enclose paper pockets,
    # and Canny marks a paper-coloured pixel next to every tone dot; either
    # way a pocket would be mistaken for a bubble interior. The paper test
    # applies to edges only: a dark outline pixel is never paper, and
    # removing light antialiased stroke pixels from the dark term would open
    # the very wall the flood has to stop at.
    texture_level = _texture_level(light, float(paper.light))
    if texture_level is not None:
        texture_pixels = np.abs(light - texture_level) <= RING_TEXTURE_DEVIATION
        dark_barrier &= ~texture_pixels
        edge_barrier &= ~texture_pixels
    edge_barrier &= ~(np.abs(light - paper.light) <= PAPER_SEED_TOLERANCE)
    barrier = dark_barrier | edge_barrier
    barrier = cv2.morphologyEx(
        barrier.astype(np.uint8) * 255,
        cv2.MORPH_CLOSE,
        np.ones((3, 3), np.uint8),
        iterations=INTERIOR_GAP_CLOSE,
    ).astype(bool)
    if ink_pixels is not None:
        barrier &= ~ink_pixels
    barrier = _drop_dot_barriers(barrier)

    paper_colour = (np.abs(light - paper.light) <= PAPER_SEED_TOLERANCE) & (
        np.abs(channel_a - paper.channel_a) + np.abs(channel_b - paper.channel_b)
        <= SEED_CHROMA_TOLERANCE
    )
    text_x1, text_y1, text_x2, text_y2 = local_text
    text_x1, text_y1 = max(0, text_x1), max(0, text_y1)
    text_x2, text_y2 = min(roi_width, text_x2), min(roi_height, text_y2)
    if text_x2 <= text_x1 or text_y2 <= text_y1:
        return None, INTERIOR_NO_CONTEXT
    text_box = np.zeros((roi_height, roi_width), bool)
    text_box[text_y1:text_y2, text_x1:text_x2] = True
    text_area = float(np.count_nonzero(text_box))

    seeds = paper_colour & ~barrier & text_box
    if not np.any(seeds):
        seeds = paper_colour & ~barrier
    if not np.any(seeds):
        return None, INTERIOR_NO_SEED

    passable = (~barrier).astype(np.uint8)
    count, labels = cv2.connectedComponents(passable, connectivity=4)
    labels = np.asarray(labels, dtype=np.int32)
    seed_labels = np.unique(labels[seeds > 0])
    seed_labels = seed_labels[seed_labels > 0]
    if seed_labels.size == 0:
        return None, INTERIOR_NO_SEED
    # A seed that belongs to the border-touching component is on the wrong
    # side of the outline (a one-pixel nick where the box corner meets the
    # wall is enough); only enclosed seeds define the interior.
    border_labels = np.unique(
        np.concatenate([labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]])
    )
    seed_labels = seed_labels[~np.isin(seed_labels, border_labels)]
    if seed_labels.size == 0:
        return None, INTERIOR_LEAK
    interior = np.isin(labels, seed_labels)
    area = float(np.count_nonzero(interior))
    if area < max(64.0, INTERIOR_MIN_AREA_FRACTION * text_area):
        return None, INTERIOR_SMALL

    # Leaks: a bubble is enclosed, so the fill must not reach the window
    # border, and it must not outgrow the detector box it is supposed to be.
    reaches_border = bool(
        interior[0, :].any()
        or interior[-1, :].any()
        or interior[:, 0].any()
        or interior[:, -1].any()
    )
    if reaches_border:
        return None, INTERIOR_LEAK
    if detector_bounds is not None:
        box_area = float(
            max(1, detector_bounds[2] - detector_bounds[0])
            * max(1, detector_bounds[3] - detector_bounds[1])
        )
        if area > INTERIOR_LEAK_FACTOR * box_area:
            return None, INTERIOR_LEAK

    # Fill enclosed islands: glyphs inside the bubble are interior, the
    # outline ring is not (it is not enclosed by this flood, or it reaches
    # beyond the text box).
    hole_count, hole_labels = cv2.connectedComponents(
        (~interior).astype(np.uint8), connectivity=4
    )
    hole_labels = np.asarray(hole_labels, dtype=np.int32)
    if hole_count > 1:
        hole_flat = hole_labels.ravel().astype(np.int64)
        hole_areas = np.bincount(hole_flat, minlength=hole_count).astype(np.float64)
        hole_in_text = np.bincount(
            hole_flat,
            weights=text_box.ravel().astype(np.float64),
            minlength=hole_count,
        )
        border_labels = np.unique(
            np.concatenate(
                [hole_labels[0, :], hole_labels[-1, :], hole_labels[:, 0], hole_labels[:, -1]]
            )
        ).astype(np.int64)
        hole_border = np.zeros(hole_count, bool)
        hole_border[border_labels] = True
        accept = (
            (np.arange(hole_count) > 0)
            & ~hole_border
            & (
                (hole_areas <= INTERIOR_HOLE_MAX_PX)
                | (hole_in_text >= INTERIOR_HOLE_TEXT_SHARE * np.maximum(hole_areas, 1.0))
            )
        )
        if np.any(accept):
            interior |= accept[hole_labels]
            area = float(np.count_nonzero(interior))

    covered = float(np.count_nonzero(interior & text_box)) / max(1.0, text_area)
    if covered < INTERIOR_MIN_COVERAGE:
        return None, INTERIOR_UNCOVERED
    if INTERIOR_ERODE > 0:
        interior = cv2.erode(
            interior.astype(np.uint8),
            np.ones((3, 3), np.uint8),
            iterations=INTERIOR_ERODE,
        ).astype(bool)
    if not np.any(interior):
        return None, INTERIOR_SMALL
    return interior.astype(np.uint8) * 255, INTERIOR_OK


def _contour_interior(
    roi: np.ndarray, local_text: tuple[int, int, int, int]
) -> tuple[np.ndarray | None, str]:
    """The previous smallest-closed-contour interior search, with its reasons.

    This is the fallback for open and borderless bubbles; it blanks the text
    rectangle (which can open the outline where lettering touches it) and
    rejects contours that reach the crop border.
    """
    gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
    roi_height, roi_width = gray.shape
    structure_gray = gray.copy()
    sx1, sy1 = max(0, local_text[0]), max(0, local_text[1])
    sx2, sy2 = min(roi_width, local_text[2]), min(roi_height, local_text[3])
    if sx2 > sx1 and sy2 > sy1:
        structure_gray[sy1:sy2, sx1:sx2] = int(np.percentile(gray, 90))
    blurred = cv2.GaussianBlur(structure_gray, (3, 3), 0)
    otsu_threshold, _binary = cv2.threshold(
        blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    dark_threshold = int(np.clip(otsu_threshold, INTERIOR_DARK_MIN, INTERIOR_DARK_MAX))
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
    if not contours:
        return None, INTERIOR_NO_CONTEXT

    text_width = max(1, local_text[2] - local_text[0])
    text_height = max(1, local_text[3] - local_text[1])
    text_area = text_width * text_height
    center = (
        (local_text[0] + local_text[2]) / 2.0,
        (local_text[1] + local_text[3]) / 2.0,
    )
    candidates: list[tuple[float, Any]] = []
    single_failures = {"area": 0, "edge": 0}
    for contour in contours:
        area = float(abs(cv2.contourArea(contour)))
        failed: list[str] = []
        if area < max(48.0, text_area * 1.15) or area >= roi.size / 3 * 0.9:
            failed.append("area")
        if cv2.pointPolygonTest(contour, center, False) < 0:
            failed.append("center")
        cx, cy, cw, ch = cv2.boundingRect(contour)
        if cx <= 1 or cy <= 1 or cx + cw >= roi_width - 1 or cy + ch >= roi_height - 1:
            failed.append("edge")
        overlap_width = max(0, min(cx + cw, local_text[2]) - max(cx, local_text[0]))
        overlap_height = max(0, min(cy + ch, local_text[3]) - max(cy, local_text[1]))
        if overlap_width * overlap_height < text_area * 0.75:
            failed.append("overlap")
        if not failed:
            candidates.append((area, contour))
        elif len(failed) == 1 and failed[0] in single_failures:
            single_failures[failed[0]] += 1

    if not candidates:
        if single_failures["edge"]:
            return None, INTERIOR_CONTOUR_EDGE
        if single_failures["area"]:
            return None, INTERIOR_CONTOUR_AREA
        return None, INTERIOR_NO_CONTEXT

    _area, contour = min(candidates, key=lambda candidate: candidate[0])
    interior = np.zeros(gray.shape, dtype=np.uint8)
    cv2.drawContours(interior, [contour], -1, 255, thickness=cv2.FILLED)
    interior = cv2.erode(interior, np.ones((3, 3), np.uint8), iterations=2)
    text_roi = interior[
        max(0, local_text[1]) : min(roi_height, local_text[3]),
        max(0, local_text[0]) : min(roi_width, local_text[2]),
    ]
    if text_roi.size == 0 or np.count_nonzero(text_roi) < text_area * 0.35:
        return None, INTERIOR_CONTOUR_COVERAGE
    return interior, INTERIOR_OK


def bubble_interior_details(image: np.ndarray, block: TextBlock) -> InteriorResult:
    """Find the bubble interior around a text block: flood fill first, contour second."""
    window = _interior_window(image, block)
    if window is None:
        return InteriorResult(
            np.zeros((0, 0), dtype=np.uint8), (0, 0, 0, 0), "none", INTERIOR_NO_CONTEXT
        )
    bounds, local_text, detector_bounds = window
    x1, y1, x2, y2 = bounds
    roi = image[y1:y2, x1:x2]
    if roi.size == 0 or min(roi.shape[:2]) < 5:
        return InteriorResult(
            np.zeros(roi.shape[:2], dtype=np.uint8), bounds, "none", INTERIOR_NO_CONTEXT
        )

    paper = _paper_estimate(image, int_box(block.xyxy))
    glyphs, glyph_bounds = glyph_mask_for_block(image, block)
    ink_pixels = np.zeros(roi.shape[:2], bool)
    overlap = overlap_slices(bounds, glyph_bounds)
    if overlap is not None and glyphs.size:
        ink_pixels[overlap[0]] = glyphs[overlap[1]] > 0
    flooded, flood_reason = _flood_interior(
        roi, local_text, paper, detector_bounds, ink_pixels if np.any(ink_pixels) else None
    )
    if flooded is not None:
        return InteriorResult(flooded, bounds, "flood", INTERIOR_OK)
    contour, contour_reason = _contour_interior(roi, local_text)
    if contour is not None:
        return InteriorResult(contour, bounds, "contour", INTERIOR_OK)
    # The flood is the primary method, so its diagnosis is the one reported;
    # the contour fallback's own failure is kept for debugging.
    logger.debug(
        "Bubble interior failed for block %s: flood=%s contour=%s",
        block.xyxy.tolist(),
        flood_reason,
        contour_reason,
    )
    return InteriorResult(
        np.zeros(roi.shape[:2], dtype=np.uint8),
        bounds,
        "none",
        flood_reason if flood_reason != INTERIOR_NO_CONTEXT else contour_reason,
    )


def bubble_interior_mask_for_block(
    image: np.ndarray, block: TextBlock
) -> tuple[np.ndarray, Box]:
    """Find the bubble interior surrounding a text block."""
    result = bubble_interior_details(image, block)
    return result.interior, result.bounds


def contour_safe_mask_for_block(
    image: np.ndarray, block: TextBlock
) -> tuple[np.ndarray, Box, str]:
    """Extract ink, using a found bubble interior only as a safety boundary."""
    glyphs, glyph_bounds = glyph_mask_for_block(image, block)
    has_glyphs = glyphs.size and np.any(glyphs)
    interior, interior_bounds = bubble_interior_mask_for_block(image, block)
    has_interior = interior.size and np.any(interior)

    if has_glyphs and has_interior:
        # The interior is the final safety boundary: lettering inside the
        # bubble is masked, the outline the flood had to cross is not.
        clipped = np.zeros_like(glyphs)
        overlap = overlap_slices(glyph_bounds, interior_bounds)
        if overlap:
            clipped[overlap[0]] = cv2.bitwise_and(
                glyphs[overlap[0]], interior[overlap[1]]
            )
        if np.any(clipped):
            return clipped, glyph_bounds, "bubble-clipped-glyph"

    if has_glyphs:
        return glyphs, glyph_bounds, "glyph"

    bounds = interior_bounds
    if not has_interior:
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


def _bubble_valid_region(
    block: TextBlock, crop_bounds: Box, shape: tuple[int, ...]
) -> np.ndarray | None:
    """The detector bubble box shrunk past its outline, in crop coordinates.

    Blocks whose mask was clipped to a verified bubble interior only sample
    evidence inside the bubble, so the outline and the panel beyond it
    cannot fail an otherwise clean fill. Returns None when no bubble box is
    known or the shrunk region degenerates.
    """
    bubble = block.bubble_xyxy
    if bubble is None:
        return None
    try:
        bx1, by1, bx2, by2 = int_box(bubble)
    except (TypeError, ValueError):
        return None
    vx1, vy1 = bx1 + BUBBLE_VALID_INSET, by1 + BUBBLE_VALID_INSET
    vx2, vy2 = bx2 - BUBBLE_VALID_INSET, by2 - BUBBLE_VALID_INSET
    if vx2 <= vx1 or vy2 <= vy1:
        return None
    cx1, cy1 = crop_bounds[0], crop_bounds[1]
    width, height = shape[1], shape[0]
    lx1, ly1 = max(0, vx1 - cx1), max(0, vy1 - cy1)
    lx2, ly2 = min(width, vx2 - cx1), min(height, vy2 - cy1)
    if lx2 <= lx1 or ly2 <= ly1:
        return None
    valid = np.zeros(shape[:2], dtype=np.uint8)
    valid[ly1:ly2, lx1:lx2] = 255
    return valid


def _router_crop(bounds: Box, width: int, height: int) -> Box:
    """Block bounds grown by the context ring and clipped to the page."""
    x1, y1, x2, y2 = bounds
    return (
        max(0, x1 - ROUTER_CONTEXT),
        max(0, y1 - ROUTER_CONTEXT),
        min(width, x2 + ROUTER_CONTEXT),
        min(height, y2 + ROUTER_CONTEXT),
    )


def prepare_flux_inpainting(
    image: np.ndarray,
    blocks: list[TextBlock],
    rectangular: bool = False,
) -> tuple[np.ndarray, np.ndarray, int, int, list[dict[str, Any]]]:
    """Route each block through verified fill tiers; queue the rest for a model.

    ``rectangular`` selects the mask builder exactly like ``create_mask``:
    rectangular block masks for the AnimeText YOLO route, contour-safe
    glyph masks otherwise. Every block is then filled by the cheapest tier
    (flat, smooth, lattice) that reproduces a held-out band of its context
    ring; blocks no tier can verify go into the model mask instead.

    Returns ``(cleaned, model_mask, filled_count, model_count, decisions)``
    where ``cleaned`` already carries every router fill, so the model only
    ever sees the blocks queued in ``model_mask``.
    """
    height, width = image.shape[:2]
    cleaned = image.copy()
    model_mask = np.zeros((height, width), dtype=np.uint8)
    decisions: list[dict[str, Any]] = []

    routed = suppress_composite_render_regions(blocks)
    entries: list[tuple[TextBlock, np.ndarray, Box, str]] = []
    ink_union = np.zeros((height, width), dtype=np.uint8)
    for block in routed:
        mask_builder = (
            rectangular_mask_for_block if rectangular else contour_safe_mask_for_block
        )
        local, bounds, strategy = mask_builder(image, block)
        if local.size == 0 or not np.any(local):
            logger.warning(
                "Skipping fill block with no safe mask: %s (%s)",
                block.xyxy.tolist(),
                strategy,
            )
            decisions.append(
                FillDecision(TIER_SKIPPED, None, 0, 0, 0, "no-safe-mask").as_dict(strategy)
            )
            continue
        x1, y1, x2, y2 = bounds
        target = ink_union[y1:y2, x1:x2]
        np.maximum(target, local, out=target)
        entries.append((block, local, bounds, strategy))

    filled_count = 0
    model_count = 0
    for block, local, bounds, strategy in entries:
        x1, y1, x2, y2 = bounds
        cx1, cy1, cx2, cy2 = _router_crop(bounds, width, height)
        crop = image[cy1:cy2, cx1:cx2]

        local_mask = np.zeros(crop.shape[:2], dtype=np.uint8)
        local_mask[y1 - cy1 : y2 - cy1, x1 - cx1 : x2 - cx1] = local
        # Other blocks' ink is neither evidence nor a copy source: it is
        # somebody else's job to erase it.
        excluded = np.where(
            local_mask > 0, 0, ink_union[cy1:cy2, cx1:cx2]
        ).astype(np.uint8)

        interior_mode = strategy == "bubble-interior"
        valid = None
        if not interior_mode and strategy in INTERIOR_STRATEGIES:
            valid = _bubble_valid_region(block, (cx1, cy1, cx2, cy2), crop.shape)

        # Route, then re-run the ink extractor on the inpainted crop and
        # escalate a flagged block once (research section 2.7).
        decision, filled, model_local = route_block_fill_checked(
            crop,
            local_mask,
            excluded,
            interior_mode,
            valid,
            block=block,
            origin=(cx1, cy1),
            strategy=strategy,
        )
        if filled is not None and decision["tier"] != TIER_MODEL:
            cleaned[cy1:cy2, cx1:cx2] = filled
            filled_count += 1
        else:
            target_crop = model_mask[cy1:cy2, cx1:cx2]
            np.maximum(target_crop, model_local, out=target_crop)
            model_count += 1
        decisions.append(decision)

    logger.info(
        "Fill router: %s blocks filled by verified tiers; %s queued for the model",
        filled_count,
        model_count,
    )
    return cleaned, model_mask, filled_count, model_count, decisions
