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

from dataclasses import dataclass, replace

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

    @property
    def width(self) -> int:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> int:
        return self.bounds[3] - self.bounds[1]

    @property
    def area(self) -> int:
        return int(np.count_nonzero(self.mask))

    def eroded(self, air: int) -> Room:
        """Return this room with an air inset applied (no-op when air is 0).

        Erosion composes, so calling this on an already-eroded room adds the
        difference instead of eroding twice from the base mask.
        """
        delta = int(round(air)) - self.air
        if delta <= 0:
            return self
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (delta * 2 + 1, delta * 2 + 1)
        )
        eroded = cv2.erode(self.mask, kernel)
        if np.count_nonzero(eroded) < max(4, self.area // 10):
            return replace(self, air=int(round(air)))
        return replace(self, mask=eroded, air=int(round(air)))

    def row_spans(self) -> np.ndarray:
        """First and last set column per row as an (h, 2) array; -1 when empty."""
        mask = self.mask > 0
        height, width = mask.shape
        spans = np.full((height, 2), -1, dtype=np.int32)
        if not np.any(mask):
            return spans
        columns = np.arange(width, dtype=np.int32)
        left = np.where(mask, columns, width).min(axis=1)
        right = np.where(mask, columns, -1).max(axis=1)
        empty = right < 0
        spans[:, 0] = np.where(empty, -1, left)
        spans[:, 1] = np.where(empty, -1, right)
        return spans

    def bands(self, size: int, count: int, line_spacing: float) -> list[Band]:
        """Lay ``count`` line bands of ``size`` px on the room's body axis."""
        if count <= 0 or size <= 0:
            return []
        spans = self.row_spans()
        height = spans.shape[0]
        if height <= 0:
            return []
        pitch = max(1.0, size * line_spacing)
        block_height = (count - 1) * pitch + size
        top = self.center[1] - block_height / 2.0
        x1, y1 = self.bounds[0], self.bounds[1]

        raw: list[tuple[float, float, float, float]] = []
        for index in range(count):
            band_top = top + index * pitch
            band_bottom = band_top + size
            first = int(np.floor(band_top - y1))
            last = int(np.ceil(band_bottom - y1)) - 1
            first = max(0, first)
            last = min(height - 1, last)
            if last < first:
                return []
            rows = spans[first : last + 1]
            valid = rows[:, 0] >= 0
            if not np.all(valid):
                return []
            left = float(np.max(rows[:, 0]))
            right = float(np.min(rows[:, 1]))
            if right - left < 1:
                return []
            raw.append((left + x1, right + x1, band_top, band_bottom))

        # Lines share the room's body axis, so a tail or an off-centre box
        # does not drag the lettering off the bubble's visual centre.
        axis = float(self.center[0])
        bands: list[Band] = []
        for left, right, band_top, band_bottom in raw:
            width = right - left
            shifted = min(max(axis, left + width / 2.0), right - width / 2.0)
            bands.append(
                Band(
                    left=shifted - width / 2.0,
                    right=shifted + width / 2.0,
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
    region = np.isin(labels, list(seed_labels)).astype(np.uint8)

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
    bounds: Box = (wx1, wy1, wx2, wy2)
    center = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
    return room_from_mask(region, bounds, center, "free")


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

    A hull-defect chord is tried first (the line between the deepest convexity
    defect's start and end points, which is the waist of a figure-eight
    outline); when the chord does not put every seed in its own piece, a
    seeded watershed on the distance transform splits the room at its narrowest
    points instead.
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
    """Cut at the deepest convexity defect and check that seeds separate."""
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
    deepest = max(rows, key=lambda row: float(row[3]))
    start_index, end_index = int(deepest[0]), int(deepest[1])
    start_point = tuple(int(value) for value in contour[start_index][0])
    end_point = tuple(int(value) for value in contour[end_index][0])

    canvas = binary.copy()
    cv2.line(canvas, start_point, end_point, 0, thickness=1)
    count, labels = cv2.connectedComponents(canvas, connectivity=4)
    seed_labels = [int(labels[y, x]) for x, y in seeds]
    if 0 in seed_labels or len(set(seed_labels)) != len(seeds):
        return None
    pieces = [(labels == label).astype(np.uint8) for label in seed_labels]
    total = int(np.count_nonzero(binary))
    if any(np.count_nonzero(piece) < max(16, total * 0.2) for piece in pieces):
        # A chord that slices a cap off one lobe is not the waist; the
        # watershed below cuts at the narrowest point instead.
        return None
    return pieces


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
    kernel = np.ones((3, 3), dtype=np.uint8)
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
    while pending.any():
        claimed = False
        for index in range(1, len(seeds) + 1):
            grown = cv2.dilate((labels == index).astype(np.uint8), kernel) > 0
            claim = grown & pending
            if np.any(claim):
                labels[claim] = index
                pending[claim] = False
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
