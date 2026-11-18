"""Size fitting: the anchored target, the bands and the line-breaking search.

The idea is koharu's fitter (``crates/koharu-renderer``/``fit.rs``, MIT OR
Apache-2.0): score each candidate line count by the unfilled space its bands
leave plus an overflow penalty, and search downwards from the size the source
lettering anchors, coarse first and then bisecting. The implementation here is
original, written against Pillow and numpy, not ported.

There is no fixed ceiling: the search starts at the anchored target, which for
a big balloon is larger than the old 16 px preference, and only ever shrinks
when the room cannot hold the text at that size.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from manga_backend.pipeline.fonts import FontObject, get_font_object
from manga_backend.pipeline.linebreak import break_lines
from manga_backend.pipeline.rooms import Band, Room, air_inset
from manga_backend.pipeline.text import has_cjk

# Sizes are searched in steps of about this fraction of the target before the
# exact maximum is bisected.
COARSE_STEP_FRACTION = 0.12
COARSE_STEP_MINIMUM = 2
# A band narrower than this fraction of the em is not usable for a line.
MIN_BAND_FRACTION = 0.35
# More lines than this are never lettered, however tall the room is.
MAX_LINES = 12
# Below this size the harness counts a region as unreadable; the fitter tries
# tighter leading before it lets a region fall under it.
MIN_READABLE_FONT_SIZE = 10
# Leading multipliers tried, in order, when the room cannot hold the text at
# the requested line spacing: cramped rooms get tighter leading, not smaller
# type.
LEADING_FACTORS = (1.0, 0.9, 0.82)
# Vertical text is set one character per line; this is the smallest pitch the
# fitter will use before it reports an overflow.
MIN_VERTICAL_SIZE = 1


@dataclass(frozen=True)
class FitResult:
    """One region's finished fit: size, lines, where each line goes."""

    size: int
    lines: list[str]
    bands: list[Band]
    line_widths: list[float]
    target: int
    stroke_width: int
    overflow: bool
    hyphenated: bool
    mode: str
    kind: str = "text"


def text_units(text: str) -> tuple[list[str], Callable[[Sequence[str]], str]]:
    """Split text into breaking units and return the joiner for them.

    Latin text breaks at spaces; a token is also broken after an em or en dash,
    and those joins carry no space. CJK without spaces breaks per character.
    """
    cleaned = str(text)
    if has_cjk(cleaned) and not any(char.isspace() for char in cleaned):
        return list(cleaned), (lambda parts: "".join(parts))

    units: list[str] = []
    for token in cleaned.split():
        units.extend(re.findall(r".+?[—–](?=.)|.+", token))
    if not units:
        return [], (lambda parts: " ".join(parts))

    def join(parts: Sequence[str]) -> str:
        pieces: list[str] = []
        for part in parts:
            if pieces and not pieces[-1].endswith(("—", "–")):
                pieces.append(" ")
            pieces.append(part)
        return "".join(pieces)

    return units, join


def _size_candidates(target: int) -> list[int]:
    """Descending coarse sizes from the target down to the smallest readable."""
    step = max(COARSE_STEP_MINIMUM, int(round(target * COARSE_STEP_FRACTION)))
    sizes: list[int] = []
    size = target
    while size > 0:
        sizes.append(size)
        size -= step
    if sizes[-1] != 1:
        sizes.append(1)
    return sizes


class _Measurer:
    """Width measurement for one font size, cached per string."""

    def __init__(self, font: FontObject, stroke_width: int) -> None:
        self._font = font
        self._stroke = stroke_width
        self._probe = Image.new("L", (1, 1), 0)
        self._draw = ImageDraw.Draw(self._probe)
        self._cache: dict[str, float] = {}

    def __call__(self, value: str) -> float:
        cached = self._cache.get(value)
        if cached is not None:
            return cached
        if not value:
            return 0.0
        box = self._font.getbbox(value)
        width = float(box[2] - box[0]) + self._stroke * 2.0 if box else 0.0
        self._cache[value] = width
        return width


def _bands_for(
    room: Room,
    size: int,
    count: int,
    line_spacing: float,
    spans: np.ndarray | None = None,
) -> list[Band]:
    return room.eroded(air_inset(size)).bands(size, count, line_spacing, spans=spans)


def _fit_at_size(
    room: Room,
    units: Sequence[str],
    joiner: Callable[[Sequence[str]], str],
    font_path: str | None,
    size: int,
    line_spacing: float,
    base_stroke: int,
    mode: str,
) -> FitResult | None:
    """Best line count for one size, or None when the size cannot be set."""
    stroke = min(base_stroke, max(0, int(round(size * 0.15))))
    font = get_font_object(font_path, size)
    measure = _Measurer(font, stroke)
    air = air_inset(size)
    room_height = room.height - air * 2
    if room_height < size:
        return None
    eroded = room.eroded(air)
    spans = eroded.row_spans()
    pitch = max(1.0, size * line_spacing)
    # Not bounded by the unit count: one long word can be hyphenated across
    # several lines, and bounding by units made narrow regions shrink instead.
    max_lines = min(MAX_LINES, int(room_height // pitch) + 1)
    minimum_band = max(3.0, size * MIN_BAND_FRACTION)

    best: tuple[float, int, list[Band], list[str], bool, list[float]] | None = None
    for count in range(1, max_lines + 1):
        bands = eroded.bands(size, count, line_spacing, spans=spans)
        if len(bands) < count:
            continue
        widths = [band.width for band in bands]
        if min(widths) < minimum_band:
            continue
        broken = break_lines(units, widths, measure, joiner=joiner, mode=mode)
        if broken is None:
            continue
        if broken.widths and any(
            width > band.width + 0.5 for width, band in zip(broken.widths, bands)
        ):
            continue
        score = broken.cost
        if best is None or score < best[0] - 1e-9:
            best = (
                score,
                count,
                bands,
                broken.lines,
                any(broken.hyphenated),
                list(broken.widths),
            )
    if best is None:
        return None
    _score, _count, bands, lines, hyphenated, line_widths = best
    return FitResult(
        size=size,
        lines=lines,
        bands=bands,
        line_widths=line_widths,
        target=size,
        stroke_width=stroke,
        overflow=False,
        hyphenated=hyphenated,
        mode=mode,
    )


def fit_vertical_text(
    room: Room, text: str, font_path: str | None, target: int, line_spacing: float
) -> FitResult:
    """Vertical text: one character per line down the room's body axis."""
    characters = [char for char in str(text).replace("\n", "") if not char.isspace()]
    available = max(1, room.height - air_inset(target) * 2)
    if not characters:
        size = max(1, target)
    else:
        pitch = max(1.0, line_spacing)
        size = max(MIN_VERTICAL_SIZE, min(target, int(available / (len(characters) * pitch))))
    font = get_font_object(font_path, size)
    measure = _Measurer(font, 0)
    widest = max((measure(char) for char in characters), default=0.0)
    band = Band(
        left=room.center[0] - widest / 2.0,
        right=room.center[0] + widest / 2.0,
        top=room.center[1] - available / 2.0,
        bottom=room.center[1] + available / 2.0,
        axis=room.center[0],
    )
    return FitResult(
        size=size,
        lines=characters,
        bands=[band],
        line_widths=[widest],
        target=target,
        stroke_width=0,
        overflow=bool(characters) and size < 1,
        hyphenated=False,
        mode="vertical",
    )


def fit_text_in_room(
    room: Room,
    text: str,
    font_path: str | None,
    *,
    target: int,
    line_spacing: float = 1.2,
    base_stroke_width: int = 0,
    mode: str = "even",
    vertical: bool = False,
    fixed_size: bool = False,
    kind: str = "text",
) -> FitResult:
    """Fit ``text`` into ``room``, starting at ``target`` and shrinking if needed.

    The largest size that can be set wins; the line count is chosen by the band
    score. When even the smallest size cannot be set, the best-effort result is
    returned with ``overflow`` set, so a region is never silently skipped.
    """
    target = max(1, int(target))
    if vertical:
        result = fit_vertical_text(room, text, font_path, target, line_spacing)
        return FitResult(
            size=result.size,
            lines=result.lines,
            bands=result.bands,
            line_widths=result.line_widths,
            target=target,
            stroke_width=result.stroke_width,
            overflow=result.overflow,
            hyphenated=False,
            mode="vertical",
            kind=kind,
        )

    units, joiner = text_units(text)
    if not units:
        return FitResult(target, [], [], [], target, 0, False, False, mode, kind)

    result: FitResult | None = None
    for factor in LEADING_FACTORS:
        spacing = max(1.0, line_spacing * factor)
        result = _search_size(
            room,
            str(text),
            units,
            joiner,
            font_path,
            target,
            spacing,
            base_stroke_width,
            mode,
            fixed_size,
            kind,
        )
        if result.size >= MIN_READABLE_FONT_SIZE:
            return result
    assert result is not None
    return result


def _search_size(
    room: Room,
    text: str,
    units: Sequence[str],
    joiner: Callable[[Sequence[str]], str],
    font_path: str | None,
    target: int,
    line_spacing: float,
    base_stroke_width: int,
    mode: str,
    fixed_size: bool,
    kind: str,
) -> FitResult:
    """Coarse-then-bisect downward search for the largest size that fits."""
    feasible: FitResult | None = None
    previous_failed: int | None = None
    candidates = [target] if fixed_size else _size_candidates(target)
    for size in candidates:
        result = _fit_at_size(
            room,
            units,
            joiner,
            font_path,
            size,
            line_spacing,
            base_stroke_width,
            mode,
        )
        if result is not None:
            feasible = result
            break
        previous_failed = size

    if feasible is None:
        # Nothing fits: set the text at the smallest size anyway, so the
        # region is lettered rather than dropped, and say so.
        smallest = 1
        result = _fit_at_size(
            room,
            units,
            joiner,
            font_path,
            smallest,
            line_spacing,
            base_stroke_width,
            mode,
        )
        if result is None:
            band = Band(
                left=room.center[0],
                right=room.center[0],
                top=room.center[1],
                bottom=room.center[1],
            )
            return FitResult(
                smallest, [str(text)], [band], [0.0], target, 0, True, False, mode, kind
            )
        return FitResult(
            size=result.size,
            lines=result.lines,
            bands=result.bands,
            line_widths=result.line_widths,
            target=target,
            stroke_width=result.stroke_width,
            overflow=True,
            hyphenated=result.hyphenated,
            mode=mode,
            kind=kind,
        )

    # Bisect between the largest failing size above and the feasible one.
    low, high = feasible.size + 1, previous_failed if previous_failed is not None else feasible.size + 1
    while low < high:
        middle = (low + high) // 2
        candidate = _fit_at_size(
            room,
            units,
            joiner,
            font_path,
            middle,
            line_spacing,
            base_stroke_width,
            mode,
        )
        if candidate is not None:
            feasible = candidate
            low = middle + 1
        else:
            high = middle
    return FitResult(
        size=feasible.size,
        lines=feasible.lines,
        bands=feasible.bands,
        line_widths=feasible.line_widths,
        target=target,
        stroke_width=feasible.stroke_width,
        overflow=False,
        hyphenated=feasible.hyphenated,
        mode=mode,
        kind=kind,
    )
