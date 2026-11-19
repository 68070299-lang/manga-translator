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
# A mid-word line is a last resort at a given size: it costs more than any
# plausible slack, so whole words always win when they fit.
HYPHEN_PENALTY = 1.5


@dataclass(frozen=True)
class BrokenLines:
    """One feasible breaking: the lines, their widths and the DP cost."""

    lines: list[str]
    widths: list[float]
    hyphenated: list[bool]
    cost: float


VOWELS = frozenset("aeiouyAEIOUY")
# A break between two consonants ("Hur-ry") is the conventional one; a break
# between a consonant and a vowel ("Hurr-y") reads as a typo.
BREAK_SCORES = {(False, False): 3, (False, True): 2, (True, False): 1, (True, True): 0}
MIN_PREFIX = 2
MIN_SUFFIX = 2


def _break_score(word: str, index: int) -> int:
    if index < MIN_PREFIX or len(word) - index < MIN_SUFFIX:
        return -1
    before = word[index - 1] in VOWELS
    after = word[index] in VOWELS
    return BREAK_SCORES[(before, after)]


def split_oversized_unit(
    unit: str, max_width: float, measure: Callable[[str], float]
) -> list[str]:
    """Split a unit wider than every band into chunks that fit, char by char.

    The chunk budget leaves room for the hyphen the broken line carries, so a
    chunk never overflows the band it was sized for.
    """
    if measure(unit) <= max_width:
        return [unit]
    budget = max(1.0, max_width - measure("-"))
    remaining = unit
    chunks: list[str] = []
    while remaining:
        length = len(remaining)
        cut = length
        for index in range(1, length + 1):
            if measure(remaining[:index]) > budget:
                cut = max(1, index - 1)
                break
        if cut >= length:
            chunks.append(remaining)
            break
        # Walk back a little to a conventional syllable boundary.
        best_cut, best_score = cut, _break_score(remaining, cut)
        for candidate in range(cut, max(MIN_PREFIX, cut - 3), -1):
            score = _break_score(remaining, candidate)
            if score > best_score:
                best_cut, best_score = candidate, score
        chunks.append(remaining[:best_cut])
        remaining = remaining[best_cut:]
    return chunks or [unit]


# Proper hyphenation: only words with this many letters are split at all, and
# each side of a break keeps at least MIN_PREFIX / MIN_SUFFIX letters
# (punctuation does not count), so "Ah!", "Whoa!" or "Home..." are never cut.
MIN_HYPHEN_WORD_LETTERS = 5


def _letters(value: str) -> int:
    return sum(1 for char in value if char.isalpha())


def _has_syllable(part: str, *, word_end: bool) -> bool:
    """Whether ``part`` holds a vowel sound of its own.

    A trailing consonant + "e" is silent, so "me" in "Co-me" or "se" in
    "Plea-se" is no syllable; "sh" in "sh-ould" has no vowel at all.
    """
    letters = [char for char in part if char.isalpha()]
    if word_end and len(letters) > 1 and letters[-1] in "eE" and letters[-2] not in VOWELS:
        letters = letters[:-1]
    return any(char in VOWELS for char in letters)


def _proper_break(word: str, index: int) -> bool:
    """Whether ``word`` may be hyphenated before ``index`` by the usual rules."""
    if not (word[index - 1].isalpha() and word[index].isalpha()):
        return False
    if _letters(word[:index]) < MIN_PREFIX or _letters(word[index:]) < MIN_SUFFIX:
        return False
    if not (
        _has_syllable(word[:index], word_end=False) and _has_syllable(word[index:], word_end=True)
    ):
        return False
    # Never inside a vowel pair ("Who-a"); consonant boundaries score higher.
    return BREAK_SCORES[(word[index - 1] in VOWELS, word[index] in VOWELS)] > 0


def split_at_proper_breaks(
    unit: str, max_width: float, measure: Callable[[str], float]
) -> list[str] | None:
    """Split an oversized unit only at proper hyphenation points.

    Returns None when the unit is too short to hyphenate or no proper break
    leaves chunks that fit; the caller then shrinks the type instead.
    """
    if measure(unit) <= max_width:
        return [unit]
    if _letters(unit) < MIN_HYPHEN_WORD_LETTERS:
        return None
    budget = max(1.0, max_width - measure("-"))
    remaining = unit
    chunks: list[str] = []
    while measure(remaining) > max_width:
        fitting = [
            index
            for index in range(1, len(remaining))
            if _proper_break(remaining, index) and measure(remaining[:index]) <= budget
        ]
        if not fitting:
            return None
        longest = fitting[-1]
        # Within a few letters of the longest fit, prefer the conventional break.
        nearby = [index for index in fitting if index >= longest - 3]
        cut = max(nearby, key=lambda index: (_break_score(remaining, index), index))
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    chunks.append(remaining)
    return chunks


def break_lines(
    units: Sequence[str],
    band_widths: Sequence[float],
    measure: Callable[[str], float],
    *,
    joiner: Callable[[Sequence[str]], str] | None = None,
    mode: str = "even",
    max_hyphen_run: int = 2,
    strict_hyphenation: bool = False,
) -> BrokenLines | None:
    """Return the cheapest feasible breaking of ``units`` over the bands.

    ``mode`` is "even" (balanced, for dialogue: every line pays for its slack)
    or "ragged" (the last line is nearly free). Returns None when no layout
    fits, which is the caller's signal to try a smaller size.

    With ``strict_hyphenation`` an oversized word is only split at proper
    hyphenation points (``split_at_proper_breaks``); a word that has none
    makes the layout infeasible instead of being cut anywhere.
    """
    if not units:
        return None
    join = joiner if joiner is not None else (lambda parts: " ".join(parts))
    widths = [max(0.0, float(width)) for width in band_widths]
    if not widths or min(widths) <= 0:
        return None
    line_count = len(widths)

    chunks: list[str] = []
    # Which word each chunk belongs to and where it sits inside that word, so
    # a line that ends mid-word can carry a hyphen and the next line can glue
    # its continuation back on without a space.
    chunk_word: list[int] = []
    chunk_part: list[int] = []
    word_parts: list[int] = []
    widest = max(widths)
    for word, unit in enumerate(units):
        if measure(unit) <= widest:
            word_parts.append(1)
            chunks.append(unit)
            chunk_word.append(word)
            chunk_part.append(0)
            continue
        if strict_hyphenation:
            proper = split_at_proper_breaks(unit, widest, measure)
            if proper is None:
                return None
            parts = proper
        else:
            parts = split_oversized_unit(unit, widest, measure)
        word_parts.append(len(parts))
        for index, part in enumerate(parts):
            chunks.append(part)
            chunk_word.append(word)
            chunk_part.append(index)
    continues = [chunk_part[index] > 0 for index in range(len(chunks))]
    if len(chunks) < line_count:
        # Empty lines are not a layout; fewer lines will be tried instead.
        return None

    width_cache: dict[tuple[int, int], float] = {}

    def ends_mid_word(end: int) -> bool:
        return end < len(chunks) and continues[end]

    def span_text(start: int, end: int) -> str:
        pieces: list[str] = []
        buffer = ""
        current = -1
        for index in range(start, end):
            word = chunk_word[index]
            if word != current:
                if buffer:
                    pieces.append(buffer)
                buffer = chunks[index]
                current = word
            else:
                buffer += chunks[index]
        if buffer:
            pieces.append(buffer)
        text = join(pieces)
        last = end - 1
        if chunk_part[last] < word_parts[chunk_word[last]] - 1 and not text.endswith(
            ("-", "—", "–")
        ):
            text += "-"
        return text

    def span_width(start: int, end: int) -> float:
        key = (start, end)
        cached = width_cache.get(key)
        if cached is None:
            cached = measure(span_text(start, end))
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

    reachable: list[int] = [0]
    for line in range(1, line_count + 1):
        band = widths[line - 1]
        last = line == line_count
        next_reachable: set[int] = set()
        max_end = unit_count - (line_count - line)
        for start in reachable:
            prev_row = dp[line - 1][start]
            if last:
                end_range = (unit_count,)
            else:
                end_range = range(start + 1, max_end + 1)
            for end in end_range:
                width = span_width(start, end)
                if width > band:
                    if not last and end > start + 1:
                        # Adding more units only makes the segment wider.
                        break
                    continue
                cost = segment_cost(width, band, last)
                if cost is None:
                    continue
                cost += LINE_PENALTY
                hyphenated = continues[start] if start > 0 else False
                if ends_mid_word(end):
                    cost += HYPHEN_PENALTY
                improved = False
                for run in range(max_hyphen_run + 1):
                    previous = prev_row[run]
                    if previous == infinity:
                        continue
                    next_run = run + 1 if hyphenated else 0
                    if next_run > max_hyphen_run:
                        continue
                    total = previous + cost
                    if total < dp[line][end][next_run]:
                        dp[line][end][next_run] = total
                        parents[line][end][next_run] = (start, run, end - start)
                        improved = True
                if improved:
                    next_reachable.add(end)
        if not next_reachable:
            return None
        reachable = sorted(next_reachable)

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
        lines.append(span_text(start, unit))
        widths_used.append(span_width(start, unit))
        hyphenated_lines.append(continues[start] if start > 0 else False)
        line, unit, run = line - 1, start, previous_run
    lines.reverse()
    widths_used.reverse()
    hyphenated_lines.reverse()
    return BrokenLines(lines, widths_used, hyphenated_lines, best_cost)
