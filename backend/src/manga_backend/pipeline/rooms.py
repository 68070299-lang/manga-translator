"""Render rooms: the free area a translated line may occupy, and its bands.

The ideas are koharu's: a room built from the bubble interior and eroded by an
"air" inset, conjoined bubbles split per text group by a hull-defect chord
(seeded watershed when the chord does not separate the groups), and per-line
bands inside the room
(``crates/koharu-layout-highres``/``rooms.rs``,
``crates/koharu-renderer``/``fit.rs``, MIT OR Apache-2.0). The implementation
here is original, written against OpenCV and numpy, not ported.

A room is a binary mask in page coordinates plus its bounds. It is built once
per render region and then eroded by an inset that scales with the font size,
so the same base mask serves every size the fitter tries. A band is one line's
slot: a horizontal span (the narrowest row of the room inside that slot,
shifted towards the room's body axis) and the vertical slot itself.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from manga_backend.pipeline.bubbles import bubble_body_center
from manga_backend.pipeline.regions import int_box
from manga_backend.utils.textblock import Box, TextBlock

# The air inset keeps lettering off the outline; it scales with the font size
# so a big line keeps proportionally clear of the wall.
AIR_FRACTION = 0.18
AIR_MINIMUM = 2
# Free-space rooms: a pixel is blank when the local gradient is this low (a
# flat or smooth fill has no structure left; lattice fill and art do).
BLANK_GRADIENT = 10
# A free-space room must be worth the change: at least this much bigger than
# the source box (area ratio), otherwise the box is used.
FREE_ROOM_GAIN = 1.15
# How far a free-space room may reach from the source box, in source-box
# widths/heights. Text has to stay recognisably where the original was.
FREE_GROWTH_X = 1.6
FREE_GROWTH_Y = 0.9
FREE_GROWTH_MIN_X = 24
FREE_GROWTH_MIN_Y = 16
# Conjoined outlines are cut at the waist between lobes. Convexity defects
# shallower than this (px) are outline jitter, not the edge of a lobe; only
# the deepest few are tried as the ends of the cut.
WAIST_MIN_DEFECT_DEPTH = 3.0
WAIST_DEFECTS = 6
# Each side of a waist cut keeps at least this share of the joined room. A
# small bump balloon beside a big one holds about an eighth of it; a smaller
# piece is a cap sliced off a lobe, not a lobe.
WAIST_MIN_SHARE = 0.08


@dataclass(frozen=True)
class Band:
    """One line's slot: usable horizontal span and vertical extent, in page px."""

    left: float
    right: float
    top: float
    bottom: float
    # The shared axis the line is centred on when it is narrower than the band.
    axis: float = 0.0

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2.0

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2.0


@dataclass(frozen=True)
class Room:
    """A binary area in page coordinates that text may occupy."""

    mask: np.ndarray
    bounds: Box
    center: tuple[float, float]
    kind: str
    air: int = 0
    _eroded_cache: dict[int, Room] = field(
        default_factory=dict, init=False, repr=False, compare=False, hash=False
    )
    _spans_cache: list[tuple[np.ndarray, list[int], list[int]]] = field(
        default_factory=list, init=False, repr=False, compare=False, hash=False
    )
    _area_cache: list[int] = field(
        default_factory=list, init=False, repr=False, compare=False, hash=False
    )

    @property
    def width(self) -> int:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> int:
        return self.bounds[3] - self.bounds[1]

    @property
    def area(self) -> int:
        if not self._area_cache:
            self._area_cache.append(int(np.count_nonzero(self.mask)))
        return self._area_cache[0]

    def eroded(self, air: int) -> Room:
        """Return this room with an air inset applied (no-op when air is 0).

        Erosion composes, so calling this on an already-eroded room adds the
        difference instead of eroding twice from the base mask.
        """
        target_air = int(round(air))
        delta = target_air - self.air
        if delta <= 0:
            return self
        cached = self._eroded_cache.get(target_air)
        if cached is not None:
            return cached
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (delta * 2 + 1, delta * 2 + 1)
        )
        eroded = cv2.erode(self.mask, kernel)
        if np.count_nonzero(eroded) < max(4, self.area // 10):
            result = Room(self.mask, self.bounds, self.center, self.kind, target_air)
        else:
            result = Room(eroded, self.bounds, self.center, self.kind, target_air)
        self._eroded_cache[target_air] = result
        return result

    def _row_spans_with_lists(self) -> tuple[np.ndarray, list[int], list[int]]:
        if self._spans_cache:
            return self._spans_cache[0]
        mask = self.mask > 0
        height, width = mask.shape
        spans = np.full((height, 2), -1, dtype=np.int32)
        if mask.any():
            nonempty = mask.any(axis=1)
            if nonempty.all():
                spans[:, 0] = mask.argmax(axis=1)
                spans[:, 1] = width - 1 - mask[:, ::-1].argmax(axis=1)
            else:
                rows = mask[nonempty]
                spans[nonempty, 0] = rows.argmax(axis=1)
                spans[nonempty, 1] = width - 1 - rows[:, ::-1].argmax(axis=1)
        entry = (spans, spans[:, 0].tolist(), spans[:, 1].tolist())
        self._spans_cache.append(entry)
        return entry

    def row_spans(self) -> np.ndarray:
        """First and last set column per row as an (h, 2) array; -1 when empty."""
        return self._row_spans_with_lists()[0]

    def bands(
        self,
        size: int,
        count: int,
        line_spacing: float,
        spans: np.ndarray | None = None,
    ) -> list[Band]:
        """Lay ``count`` line bands of ``size`` px on the room's body axis."""
        if count <= 0 or size <= 0:
            return []
        if spans is None or (self._spans_cache and spans is self._spans_cache[0][0]):
            spans_arr, lefts, rights = self._row_spans_with_lists()
        else:
            spans_arr = spans
            lefts = spans[:, 0].tolist()
            rights = spans[:, 1].tolist()
        height = spans_arr.shape[0]
        if height <= 0:
            return []
        pitch = max(1.0, size * line_spacing)
        block_height = (count - 1) * pitch + size
        top = self.center[1] - block_height / 2.0
        x1, y1 = self.bounds[0], self.bounds[1]
        floor = math.floor
        ceil = math.ceil

        raw: list[tuple[float, float, float, float]] = []
        for index in range(count):
            band_top = top + index * pitch
            band_bottom = band_top + size
            first = floor(band_top - y1)
            last = ceil(band_bottom - y1) - 1
            if first < 0:
                first = 0
            if last >= height:
                last = height - 1
            if last < first:
                return []
            row_lefts = lefts[first : last + 1]
            if min(row_lefts) < 0:
                return []
            left = float(max(row_lefts))
            right = float(min(rights[first : last + 1]))
            if right - left < 1.0:
                return []
            raw.append((left + x1, right + x1, band_top, band_bottom))

        # Lines share the room's body axis, so a tail or an off-centre box
        # does not drag the lettering off the bubble's visual centre.
        axis = float(self.center[0])
        bands: list[Band] = []
        for left, right, band_top, band_bottom in raw:
            width = right - left
            half = width * 0.5
            shifted = min(max(axis, left + half), right - half)
            bands.append(
                Band(
                    left=shifted - half,
                    right=shifted + half,
                    top=band_top,
                    bottom=band_bottom,
                    axis=axis,
                )
            )
        return bands


def air_inset(size: int) -> int:
    """The inset that keeps a line of ``size`` px clear of the room's edge."""
    return max(AIR_MINIMUM, int(round(size * AIR_FRACTION)))


def box_room(block: TextBlock, inset: int = 1) -> Room:
    """A plain rectangular room: the render box, the fallback when nothing else fits."""
    x1, y1, x2, y2 = int_box(block.xyxy)
    width = max(1, x2 - x1)
    height = max(1, y2 - y1)
    mask = np.full((height, width), 255, dtype=np.uint8)
    if inset > 0 and width > inset * 2 + 2 and height > inset * 2 + 2:
        mask[:inset, :] = 0
        mask[-inset:, :] = 0
        mask[:, :inset] = 0
        mask[:, -inset:] = 0
    return Room(mask, (x1, y1, x2, y2), (x1 + width / 2.0, y1 + height / 2.0), "box")


def room_from_mask(
    mask: np.ndarray,
    bounds: Box,
    center: tuple[float, float] | None,
    kind: str,
) -> Room | None:
    """Wrap a page-local mask as a Room, or None when it is empty."""
    if mask.size == 0 or not np.any(mask):
        return None
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    x1, y1, x2, y2 = bounds
    rows = np.flatnonzero(np.any(binary, axis=1))
    columns = np.flatnonzero(np.any(binary, axis=0))
    if rows.size and columns.size:
        top, bottom = int(rows[0]), int(rows[-1]) + 1
        left, right = int(columns[0]), int(columns[-1]) + 1
        if (left, top, right, bottom) != (0, 0, binary.shape[1], binary.shape[0]):
            binary = binary[top:bottom, left:right]
            x1, y1, x2, y2 = x1 + left, y1 + top, x1 + right, y1 + bottom
    if center is None:
        body = bubble_body_center(binary, (x1, y1))
        center = (body[0], body[1]) if body is not None else (
            (x1 + x2) / 2.0,
            (y1 + y2) / 2.0,
        )
    return Room(binary, (x1, y1, x2, y2), (float(center[0]), float(center[1])), kind)


def free_space_room(
    image: np.ndarray,
    block: TextBlock,
    *,
    obstacles: list[Box],
    claimed: np.ndarray | None = None,
    obstacle_margin: int = 4,
) -> Room | None:
    """The blank area left around a block after its fill, clipped to its panel.

    "Blank" is measured on the inpainted page as a low local gradient, which is
    what a flat or smooth fill leaves behind; lattice fill and untouched art
    keep edges and stop the region. ``obstacles`` are the other render boxes,
    so two rooms never fight over the same paper.
    """
    image_height, image_width = image.shape[:2]
    x1, y1, x2, y2 = int_box(block.xyxy)
    block_width = max(1, x2 - x1)
    block_height = max(1, y2 - y1)
    grow_x = int(min(96, max(FREE_GROWTH_MIN_X, round(block_width * FREE_GROWTH_X))))
    grow_y = int(min(64, max(FREE_GROWTH_MIN_Y, round(block_height * FREE_GROWTH_Y))))
    wx1 = max(0, x1 - grow_x)
    wy1 = max(0, y1 - grow_y)
    wx2 = min(image_width, x2 + grow_x)
    wy2 = min(image_height, y2 + grow_y)
    if wx2 - wx1 < 4 or wy2 - wy1 < 4:
        return None

    window = image[wy1:wy2, wx1:wx2]
    gray = cv2.cvtColor(window, cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    gradient_x = cv2.Sobel(blurred, cv2.CV_16S, 1, 0, ksize=3)
    gradient_y = cv2.Sobel(blurred, cv2.CV_16S, 0, 1, ksize=3)
    magnitude = cv2.convertScaleAbs(gradient_x) + cv2.convertScaleAbs(gradient_y)
    blank = (magnitude < BLANK_GRADIENT).astype(np.uint8)

    # Keep only the blank area that reaches the block's own box.
    seed_box = np.zeros_like(blank)
    sx1, sy1 = max(0, x1 - wx1), max(0, y1 - wy1)
    sx2, sy2 = min(blank.shape[1], x2 - wx1), min(blank.shape[0], y2 - wy1)
    if sx2 <= sx1 or sy2 <= sy1:
        return None
    seed_box[sy1:sy2, sx1:sx2] = 1
    seeds = blank & seed_box
    if not np.any(seeds):
        return None
    count, labels = cv2.connectedComponents(blank, connectivity=4)
    seed_labels = set(int(value) for value in np.unique(labels[seeds > 0])) - {0}
    if not seed_labels:
        return None
    keep_labels = np.zeros(count, dtype=np.uint8)
    for label in seed_labels:
        keep_labels[label] = 1
    region = keep_labels[np.asarray(labels, dtype=np.int32)]

    for ox1, oy1, ox2, oy2 in obstacles:
        left = max(0, ox1 - obstacle_margin - wx1)
        top = max(0, oy1 - obstacle_margin - wy1)
        right = min(region.shape[1], ox2 + obstacle_margin - wx1)
        bottom = min(region.shape[0], oy2 + obstacle_margin - wy1)
        if right > left and bottom > top:
            region[top:bottom, left:right] = 0
    if claimed is not None:
        # Rooms built earlier already own part of this blank area; two rooms
        # must never letter into the same paper.
        page = claimed[wy1:wy2, wx1:wx2]
        if page.shape == region.shape:
            region[page] = 0

    if not np.any(region):
        return None
    source_area = block_width * block_height
    if np.count_nonzero(region) < source_area * FREE_ROOM_GAIN:
        return None
    # A blank component can be a thin strip between the art and a panel edge,
    # which no line fits into. Letter in the largest blank rectangle around
    # the block's own centre instead; it is still blank paper, and it keeps
    # the text where the original lettering was.
    centre_local = (int(round((x1 + x2) / 2.0 - wx1)), int(round((y1 + y2) / 2.0 - wy1)))
    rectangle = blank_rectangle(region, centre_local, block_width)
    if rectangle is None:
        return None
    left, top, right, bottom = rectangle
    if (right - left + 1) * (bottom - top + 1) < source_area * FREE_ROOM_GAIN:
        return None
    mask = np.full((bottom - top + 1, right - left + 1), 255, dtype=np.uint8)
    bounds = (wx1 + left, wy1 + top, wx1 + right + 1, wy1 + bottom + 1)
    center = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
    return Room(mask, bounds, center, "free")


def blank_rectangle(
    mask: np.ndarray, centre: tuple[int, int], minimum_width: int
) -> tuple[int, int, int, int] | None:
    """Largest blank rectangle around ``centre``, as (left, top, right, bottom).

    Grows up and down from the centre row while the row's span still covers the
    centre, keeping the narrowest span seen; that is the widest rectangle the
    blank area allows at each height, and the widest one wins.
    """
    height, width = mask.shape[:2]
    cx, cy = centre
    cx = min(max(0, cx), width - 1)
    cy = min(max(0, cy), height - 1)
    if mask[cy, cx] == 0:
        return None

    def half_width(row: int) -> int:
        line = mask[row]
        if line[cx] == 0:
            return -1
        left = cx
        while left > 0 and line[left - 1] > 0:
            left -= 1
        right = cx
        while right < width - 1 and line[right + 1] > 0:
            right += 1
        return min(cx - left, right - cx)

    start = half_width(cy)
    if start < 0:
        return None
    best = (start, cy, cy)
    top = bottom = cy
    current = start
    while True:
        up = half_width(top - 1) if top > 0 else -1
        down = half_width(bottom + 1) if bottom < height - 1 else -1
        if up < 0 and down < 0:
            break
        if down >= up:
            bottom += 1
            current = min(current, down)
        else:
            top -= 1
            current = min(current, up)
        if current * 2 + 1 >= minimum_width:
            if current * (bottom - top + 1) > best[0] * (best[2] - best[1] + 1):
                best = (current, top, bottom)
    half, top, bottom = best
    return (cx - half, top, cx + half, bottom)


def bubble_room(
    interior: np.ndarray,
    bounds: Box,
    *,
    center: tuple[float, float] | None = None,
    kind: str = "bubble",
) -> Room | None:
    """The room a bubble interior gives: its filled outline, tail included."""
    if interior.size == 0 or not np.any(interior):
        return None
    filled = fill_small_holes(interior)
    return room_from_mask(filled, bounds, center, kind)


def fill_small_holes(mask: np.ndarray) -> np.ndarray:
    """Fill enclosed holes in a room mask so text never falls into a pinhole."""
    binary = (mask > 0).astype(np.uint8)
    contours, _hierarchy = cv2.findContours(
        binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return binary * 255
    filled = np.zeros_like(binary)
    cv2.drawContours(filled, contours, -1, 1, thickness=cv2.FILLED)
    return filled * 255


def split_conjoined_room(
    room: Room, seeds: list[tuple[float, float]]
) -> list[Room]:
    """Split a room that holds several text groups into one room per group.

    Two groups are cut at the waist first: the shortest chord between two
    convexity defects (the notches on either side of the neck) that puts each
    seed in its own piece. Otherwise, or for more groups, a seeded geodesic
    watershed splits the room midway between the seeds instead.
    """
    if len(seeds) < 2 or room.mask.size == 0:
        return [room]
    binary = (room.mask > 0).astype(np.uint8)
    x1, y1 = room.bounds[0], room.bounds[1]
    local_seeds = [
        (int(round(seed[0] - x1)), int(round(seed[1] - y1))) for seed in seeds
    ]
    local_seeds = [
        (min(max(0, x), binary.shape[1] - 1), min(max(0, y), binary.shape[0] - 1))
        for x, y in local_seeds
    ]

    chord_pieces = _chord_split(binary, local_seeds)
    pieces = chord_pieces or _watershed_split(binary, local_seeds)
    if pieces is None or len(pieces) < 2:
        return [room]

    rooms: list[Room] = []
    for piece, seed in zip(pieces, seeds):
        split = room_from_mask(piece * 255, room.bounds, seed, "bubble-split")
        if split is not None:
            rooms.append(split)
    return rooms if len(rooms) == len(seeds) else [room]


def _chord_split(
    binary: np.ndarray, seeds: list[tuple[int, int]]
) -> list[np.ndarray] | None:
    """Cut two lobes apart at their waist, or None when there is none.

    The waist runs between the deepest points of two notches in the outline
    (convexity defects), one on each side of the neck. Of the chords between
    the deepest defects, the shortest one that puts each seed in its own,
    not too small, piece is the cut. A defect's start and end points lie on
    the convex hull, outside the shape, so a chord between those never cuts.
    """
    if len(seeds) != 2:
        return None
    contours, _hierarchy = cv2.findContours(
        binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
    )
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    if len(contour) < 8:
        return None
    hull_indices = cv2.convexHull(contour, returnPoints=False)
    if hull_indices is None or len(hull_indices) < 4:
        return None
    try:
        defects = cv2.convexityDefects(contour, hull_indices)
    except cv2.error:  # pragma: no cover - malformed hull
        return None
    if defects is None or not len(defects):
        return None
    rows = np.asarray(defects).reshape(-1, 4)
    # The depth is fixed-point, in 1/256 px.
    rows = rows[rows[:, 3] >= WAIST_MIN_DEFECT_DEPTH * 256]
    rows = rows[np.argsort(-rows[:, 3], kind="stable")][:WAIST_DEFECTS]
    notches = [tuple(int(value) for value in contour[int(row[2])][0]) for row in rows]

    total = int(np.count_nonzero(binary))
    minimum = max(16, total * WAIST_MIN_SHARE)
    best: tuple[float, list[np.ndarray]] | None = None
    for index, first in enumerate(notches):
        for second in notches[index + 1 :]:
            length = math.hypot(first[0] - second[0], first[1] - second[1])
            if best is not None and length >= best[0]:
                continue
            canvas = binary.copy()
            cv2.line(canvas, first, second, 0, thickness=1)
            _count, labels = cv2.connectedComponents(canvas, connectivity=4)
            seed_labels = [int(labels[y, x]) for x, y in seeds]
            if 0 in seed_labels or seed_labels[0] == seed_labels[1]:
                continue
            pieces = [(labels == label).astype(np.uint8) for label in seed_labels]
            if any(np.count_nonzero(piece) < minimum for piece in pieces):
                continue
            best = (length, pieces)
    return None if best is None else best[1]


def _watershed_split(
    binary: np.ndarray, seeds: list[tuple[int, int]]
) -> list[np.ndarray] | None:
    """Split at the narrowest waist: a seeded watershed by geodesic growth.

    Each seed's label grows one pixel at a time inside the room, so the basins
    meet along the shortest path between the groups - the waist of a joined
    outline. That is the discrete form of a seeded watershed on the distance
    transform, and it never leaks around the outside of the room.
    """
    labels = np.zeros(binary.shape, dtype=np.int32)
    mask = binary > 0
    height, width = binary.shape
    for index, (x, y) in enumerate(seeds, start=1):
        if not mask[y, x]:
            candidates = np.argwhere(mask)
            if not len(candidates):
                return None
            distances = (candidates[:, 0] - y) ** 2 + (candidates[:, 1] - x) ** 2
            row, column = candidates[int(np.argmin(distances))]
            x, y = int(column), int(row)
        labels[y, x] = index

    pending = mask & (labels == 0)
    # 8-connected frontier BFS matching 3x3 dilation waves in seed order.
    dy = np.array([-1, -1, -1, 0, 0, 1, 1, 1], dtype=np.int32)
    dx = np.array([-1, 0, 1, -1, 1, -1, 0, 1], dtype=np.int32)
    frontiers: list[tuple[np.ndarray, np.ndarray]] = []
    for index in range(1, len(seeds) + 1):
        fy, fx = np.nonzero(labels == index)
        frontiers.append((fy.astype(np.int32), fx.astype(np.int32)))

    empty = np.empty(0, dtype=np.int32)
    remaining_pending = int(np.count_nonzero(pending))
    while remaining_pending > 0:
        claimed = False
        for pos, index in enumerate(range(1, len(seeds) + 1)):
            fy, fx = frontiers[pos]
            if fy.size == 0:
                continue
            ny = (fy[:, None] + dy).ravel()
            nx = (fx[:, None] + dx).ravel()
            in_bounds = (ny >= 0) & (ny < height) & (nx >= 0) & (nx < width)
            if not in_bounds.any():
                frontiers[pos] = (empty, empty)
                continue
            ny = ny[in_bounds]
            nx = nx[in_bounds]
            is_pending = pending[ny, nx]
            if not is_pending.any():
                frontiers[pos] = (empty, empty)
                continue
            cy = ny[is_pending]
            cx = nx[is_pending]
            uniq = np.unique(cy * width + cx)
            uy = (uniq // width).astype(np.int32)
            ux = (uniq % width).astype(np.int32)
            labels[uy, ux] = index
            pending[uy, ux] = False
            remaining_pending -= int(uniq.size)
            frontiers[pos] = (uy, ux)
            claimed = True
        if not claimed:
            break

    pieces: list[np.ndarray] = []
    for index in range(1, len(seeds) + 1):
        piece = ((labels == index) & mask).astype(np.uint8)
        if np.count_nonzero(piece) < 16:
            return None
        pieces.append(piece)
    return pieces
