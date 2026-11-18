"""Knuth-Plass-style line breaking over per-line widths (bands).

The idea is koharu's dynamic-programming line breaker
(``crates/koharu-renderer``/``lines.rs``, MIT OR Apache-2.0); the
implementation here is original, written against plain Python, not ported.

The breaker takes text units (words for Latin, characters for CJK) and one
usable width per line, and minimises the sum of squared slack over the lines,
with an overflow penalty (a line that does not fit is infeasible) and a cap on
consecutive mid-word breaks. There is no hyphenation dictionary: D7 forbids
adding packages, so a unit that is wider than every band is split by
characters and the continuation lines are flagged as hyphenated, which is what
the consecutive-hyphen cap then bounds.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

# A line whose width is within this fraction of its band costs nothing extra:
# lettering does not have to be pixel-perfect to look even.
SLACK_TOLERANCE = 0.04
# The last line of a paragraph is allowed to be short in "ragged" mode.
LAST_LINE_WEIGHT = 0.15
# One extra line is a real cost, so a balanced layout that needs a line more
# only wins when it fills the room noticeably better.
LINE_PENALTY = 0.08


@dataclass(frozen=True)
class BrokenLines:
    """One feasible breaking: the lines, their widths and the DP cost."""

    lines: list[str]
    widths: list[float]
    hyphenated: list[bool]
    cost: float


def split_oversized_unit(
    unit: str, max_width: float, measure: Callable[[str], float]
) -> list[str]:
    """Split a unit wider than every band into chunks that fit, char by char."""
    if measure(unit) <= max_width:
        return [unit]
    chunks: list[str] = []
    current = ""
    for char in unit:
        candidate = current + char
        if current and measure(candidate) > max_width:
            chunks.append(current)
            current = char
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [unit]


def break_lines(
    units: Sequence[str],
    band_widths: Sequence[float],
    measure: Callable[[str], float],
    *,
    joiner: Callable[[Sequence[str]], str] | None = None,
    mode: str = "even",
    max_hyphen_run: int = 2,
) -> BrokenLines | None:
    """Return the cheapest feasible breaking of ``units`` over the bands.

    ``mode`` is "even" (balanced, for dialogue: every line pays for its slack)
    or "ragged" (the last line is nearly free). Returns None when no layout
    fits, which is the caller's signal to try a smaller size.
    """
    if not units:
        return None
    join = joiner if joiner is not None else (lambda parts: " ".join(parts))
    widths = [max(0.0, float(width)) for width in band_widths]
    if not widths or min(widths) <= 0:
        return None
    line_count = len(widths)

    chunks: list[str] = []
    continues: list[bool] = []
    widest = max(widths)
    for unit in units:
        parts = split_oversized_unit(unit, widest, measure)
        for index, part in enumerate(parts):
            chunks.append(part)
            continues.append(index > 0)
    if len(chunks) < line_count:
        # Empty lines are not a layout; fewer lines will be tried instead.
        return None

    width_cache: dict[tuple[int, int], float] = {}

    def span_width(start: int, end: int) -> float:
        key = (start, end)
        cached = width_cache.get(key)
        if cached is None:
            cached = measure(join(chunks[start:end]))
            width_cache[key] = cached
        return cached

    def segment_cost(width: float, band: float, last: bool) -> float | None:
        if width > band:
            return None
        slack = max(0.0, band - width)
        scale = max(band, 1.0)
        normalised = max(0.0, (slack / scale) - SLACK_TOLERANCE) / (1.0 - SLACK_TOLERANCE)
        cost = normalised * normalised
        if last and mode == "ragged":
            cost *= LAST_LINE_WEIGHT
        return cost

    unit_count = len(chunks)
    # dp[line][unit][hyphen_run] = cost; parents reconstruct the breaks.
    infinity = float("inf")
    dp: list[list[list[float]]] = [
        [[infinity] * (max_hyphen_run + 1) for _ in range(unit_count + 1)]
        for _ in range(line_count + 1)
    ]
    parents: list[list[list[tuple[int, int, int] | None]]] = [
        [[None] * (max_hyphen_run + 1) for _ in range(unit_count + 1)]
        for _ in range(line_count + 1)
    ]
    dp[0][0][0] = 0.0

    for line in range(1, line_count + 1):
        band = widths[line - 1]
        last = line == line_count
        for end in range(1, unit_count + 1):
            for start in range(end - 1, -1, -1):
                width = span_width(start, end)
                if width > band and start < end - 1:
                    # Adding more units only makes the segment wider.
                    break
                if width > band:
                    continue
                cost = segment_cost(width, band, last)
                if cost is None:
                    continue
                cost += LINE_PENALTY
                hyphenated = continues[start] if start > 0 else False
                for run in range(max_hyphen_run + 1):
                    previous = dp[line - 1][start][run]
                    if previous == infinity:
                        continue
                    next_run = run + 1 if hyphenated else 0
                    if next_run > max_hyphen_run:
                        continue
                    total = previous + cost
                    if total < dp[line][end][next_run]:
                        dp[line][end][next_run] = total
                        parents[line][end][next_run] = (start, run, end - start)

    best_run = min(range(max_hyphen_run + 1), key=lambda run: dp[line_count][unit_count][run])
    best_cost = dp[line_count][unit_count][best_run]
    if best_cost == infinity:
        return None

    lines: list[str] = []
    widths_used: list[float] = []
    hyphenated_lines: list[bool] = []
    line, unit, run = line_count, unit_count, best_run
    while line > 0:
        parent = parents[line][unit][run]
        if parent is None:  # pragma: no cover - reconstruction invariant
            return None
        start, previous_run, count = parent
        lines.append(join(chunks[start:unit]))
        widths_used.append(span_width(start, unit))
        hyphenated_lines.append(continues[start] if start > 0 else False)
        line, unit, run = line - 1, start, previous_run
    lines.reverse()
    widths_used.reverse()
    hyphenated_lines.reverse()
    return BrokenLines(lines, widths_used, hyphenated_lines, best_cost)
