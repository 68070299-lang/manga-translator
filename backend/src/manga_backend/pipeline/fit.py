"""Size fitting: the anchored target, the bands and the line-breaking search.

The idea is koharu's fitter (``crates/koharu-renderer``/``fit.rs``, MIT OR
Apache-2.0): score each candidate line count by the unfilled space its bands
leave plus an overflow penalty, and search downwards from the size the source
lettering anchors, coarse first and then bisecting. The implementation here is
original, written against Pillow and numpy, not ported.

There is no fixed ceiling: the search starts at the anchored target, which for
a big balloon is larger than the old 16 px preference, and only ever shrinks
when the room cannot hold the text at that size.

``room_capacity`` is the same geometry read the other way: how many characters
fit in a room at a size. The translation step uses it as each unit's length
budget.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

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
# A word too short to hyphenate properly ("That.", "Whoa!") is set whole down
# to this size before the fitter's last pass may cut it anywhere ("T-h-a-t.").
MIN_WHOLE_WORD_FONT_SIZE = 8
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
        units.extend(re.findall(r".+?[-—–](?=.)|.+", token))
    if not units:
        return [], (lambda parts: " ".join(parts))

    def join(parts: Sequence[str]) -> str:
        pieces: list[str] = []
        for part in parts:
            if pieces and not pieces[-1].endswith(("-", "—", "–")):
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


_FONT_WIDTH_CACHES: dict[tuple[str | None, int], tuple[FontObject, dict[str, float]]] = {}
_MAX_CACHED_FONTS = 128
_MAX_CACHED_STRINGS_PER_FONT = 4096


def _shared_bbox_cache(font_path: str | None, size: int, font: FontObject) -> dict[str, float]:
    key = (font_path, size)
    entry = _FONT_WIDTH_CACHES.get(key)
    if entry is not None and entry[0] is font:
        cache = entry[1]
        if len(cache) >= _MAX_CACHED_STRINGS_PER_FONT:
            cache.clear()
        return cache
    if len(_FONT_WIDTH_CACHES) >= _MAX_CACHED_FONTS:
        _FONT_WIDTH_CACHES.clear()
    cache: dict[str, float] = {}
    _FONT_WIDTH_CACHES[key] = (font, cache)
    return cache


class _Measurer:
    """Width measurement for one font size, cached per string."""

    def __init__(
        self,
        font: FontObject,
        stroke_width: int,
        bbox_cache: dict[str, float] | None = None,
    ) -> None:
        self._font = font
        self._stroke_pad = stroke_width * 2.0
        self._bbox_cache: dict[str, float] = {} if bbox_cache is None else bbox_cache

    def __call__(self, value: str) -> float:
        if not value:
            return 0.0
        cached = self._bbox_cache.get(value)
        if cached is not None:
            return cached + self._stroke_pad
        box = self._font.getbbox(value)
        base = float(box[2] - box[0]) if box else 0.0
        self._bbox_cache[value] = base
        return base + self._stroke_pad


def _measurer_for(font_path: str | None, size: int, stroke_width: int) -> _Measurer:
    font = get_font_object(font_path, size)
    return _Measurer(font, stroke_width, _shared_bbox_cache(font_path, size, font))


def _fit_at_size(
    room: Room,
    units: Sequence[str],
    joiner: Callable[[Sequence[str]], str],
    font_path: str | None,
    size: int,
    line_spacing: float,
    base_stroke: int,
    mode: str,
    *,
    strict_hyphenation: bool = False,
    keep_short_words: bool = False,
) -> FitResult | None:
    """Best line count for one size, or None when the size cannot be set."""
    stroke = min(base_stroke, max(0, int(round(size * 0.15))))
    measure = _measurer_for(font_path, size, stroke)
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
        broken = break_lines(
            units,
            widths,
            measure,
            joiner=joiner,
            mode=mode,
            strict_hyphenation=strict_hyphenation,
            keep_short_words=keep_short_words,
        )
        if broken is None:
            # The consecutive-hyphen cap is a typographic preference; when it
            # is the only thing standing between this size and a layout, a
            # readable size wins over a ladder-free one.
            broken = break_lines(
                units,
                widths,
                measure,
                joiner=joiner,
                mode=mode,
                max_hyphen_run=MAX_LINES,
                strict_hyphenation=strict_hyphenation,
                keep_short_words=keep_short_words,
            )
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


# Mean Latin character width for a room's capacity: the lowercase alphabet is
# what translated dialogue is mostly made of, and it is wider than the spaces,
# so counting it alone keeps the character budget slightly conservative.
CAPACITY_ALPHABET = "abcdefghijklmnopqrstuvwxyz"


def room_capacity(
    room: Room,
    font_path: str | None,
    size: int,
    *,
    line_spacing: float = 1.2,
    base_stroke_width: int = 0,
) -> int:
    """How many characters of average Latin width fit in ``room`` at ``size``.

    This is the same band machinery the fitter uses (air inset, per-line band
    widths, the minimum usable band), with the line breaker's job replaced by
    division: every band holds ``width / mean advance`` characters. The line
    count that holds the most characters wins. The result is the per-unit
    length budget the translator is told about, so it is expressed in
    characters at one font size rather than in pixels.
    """
    size = max(1, int(size))
    stroke = min(base_stroke_width, max(0, int(round(size * 0.15))))
    measure = _measurer_for(font_path, size, stroke)
    advance = sum(measure(char) for char in CAPACITY_ALPHABET) / len(CAPACITY_ALPHABET)
    if advance <= 0:
        return 0
    air = air_inset(size)
    room_height = room.height - air * 2
    if room_height < size:
        return 0
    eroded = room.eroded(air)
    spans = eroded.row_spans()
    pitch = max(1.0, size * line_spacing)
    max_lines = min(MAX_LINES, int(room_height // pitch) + 1)
    minimum_band = max(3.0, size * MIN_BAND_FRACTION)
    best = 0
    for count in range(1, max_lines + 1):
        bands = eroded.bands(size, count, line_spacing, spans=spans)
        if len(bands) < count:
            continue
        widths = [band.width for band in bands]
        if min(widths) < minimum_band:
            continue
        best = max(best, sum(int(width // advance) for width in widths))
    return best


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
    whole: FitResult | None = None
    # First pass: words are only hyphenated at proper points ("Hur-ry").
    # Only when no readable size can be set that way does the second pass
    # cut longer words wherever they have to be cut. Words too short to
    # hyphenate ("Ah!", "Whoa!", "That.") stay whole in both and shrink, down
    # to MIN_WHOLE_WORD_FONT_SIZE, since "Th-at." reads as a typo; only a room
    # too narrow even for that lets the last pass cut them too.
    for strict_hyphenation, keep_short_words in ((True, True), (False, True), (False, False)):
        if not keep_short_words and whole is not None and whole.size >= MIN_WHOLE_WORD_FONT_SIZE:
            return whole
        for factor in LEADING_FACTORS:
            spacing = max(1.0, line_spacing * factor)
            candidate = _search_size(
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
                strict_hyphenation=strict_hyphenation,
                keep_short_words=keep_short_words,
            )
            if not candidate.overflow:
                if candidate.size >= MIN_READABLE_FONT_SIZE:
                    return candidate
                if keep_short_words and (whole is None or candidate.size > whole.size):
                    whole = candidate
            result = candidate
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
    *,
    strict_hyphenation: bool = False,
    keep_short_words: bool = False,
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
            strict_hyphenation=strict_hyphenation,
            keep_short_words=keep_short_words,
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
            strict_hyphenation=strict_hyphenation,
            keep_short_words=keep_short_words,
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
            strict_hyphenation=strict_hyphenation,
            keep_short_words=keep_short_words,
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
