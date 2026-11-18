"""Verified fill tiers: flat, smooth and periodic-lattice fills for one block.

The router replaces the old white-or-model choice. Instead of trusting hand
thresholds about what the background looks like, each cheap tier must prove
itself on a held-out band: a 3 px strip of the context ring next to the mask
is filled from the rest of the ring, and only a tier whose band error stays
below its bound is accepted for the mask itself. A block where no cheap tier
passes is queued for the model (tier 4: crop LaMa, with FLUX above it).

Tier design (method plan, research section 2.5):
- Tier 0 "flat": per-channel ring median; accepts uniform backgrounds of any
  colour, not just white. Idea from koharu's flat fill (MIT OR Apache-2.0,
  stages/inpaint/flat.rs), re-implemented with held-out verification.
- Tier 1 "smooth": a least-squares plane per channel; covers gradients and
  airbrushed panels. Quadratic and harmonic variants stay future work.
- Tier 2 "lattice": masked autocorrelation finds the period of screentone or
  hatching, and each masked pixel is copied from a known pixel at a lattice
  translate. Idea from near-regular texture analysis (Liu, Lin and Hays,
  SIGGRAPH 2004); the implementation is original.

No code is ported verbatim from koharu; only the flat-median idea informed
the tolerance levels below.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

logger = logging.getLogger(__name__)

TIER_MODEL = 4
TIER_SKIPPED = -1
TIER_NAMES = {0: "flat", 1: "smooth", 2: "lattice", TIER_MODEL: "model", TIER_SKIPPED: "skipped"}

# Ambiguous antialiasing strip hugging the mask: used neither as evidence nor
# as a holdout, so glyph fringes cannot fail an otherwise clean background.
FRINGE_RADIUS = 2
# Held-out verification band next to the fringe, and its outer edge.
BAND_WIDTH = 3
# Outer radius of the context ring the tiers may sample from.
RING_RADIUS = 14
# Cheapest-first acceptance bounds on the mean band error, in gray levels.
FLAT_MAX_ERROR = 6.0
SMOOTH_MAX_ERROR = 8.0
LATTICE_MAX_ERROR = 8.0
# Verification needs this many band and ring pixels to mean anything.
MIN_BAND_PX = 40
MIN_RING_PX = 120
# Lattice search bounds.
MAX_LATTICE_PERIOD = 24
MIN_LATTICE_PEAK_R = 0.45
MIN_LATTICE_SECONDARY_R = 0.35
MIN_INDEPENDENCE = 0.35
MAX_LATTICE_SHELL = 9
MAX_LATTICE_OFFSETS = 400
MAX_LATTICE_ROUNDS = 8
# Band pixels a lattice must reach at all before its error is even measured.
MIN_BAND_COVERAGE = 0.9
# A lattice only makes sense on a textured ring. Near-flat rings reach tier 2
# only because stray ink spoiled their band; letting a lattice "continue"
# the flatness there would erase structure the band never saw (an outline
# crossing the mask, say) instead of escalating to the model.
MIN_TEXTURED_RING_FRACTION = 0.08
TEXTURED_DEVIATION = 24
# Mask pixels a lattice may leave behind for the nearest-known fallback.
MAX_MASK_STRAGGLER_FRACTION = 0.02
# Band pixels dropped from the lattice error statistic, worst first. The
# glyph mask can miss real ink (faint lettering, edge-clipped strokes), and a
# missed letter can occupy a sizable share of the thin held-out band. The
# lattice's own reproduction is a pure copy, so its only legitimate band
# outliers are that foreign ink; flat and smooth stay strict (a sparse
# periodic texture must keep failing them, H2).
LATTICE_TRIM_FRACTION = 0.30


@dataclass(frozen=True)
class FillDecision:
    """How one block was filled, for logs and eval statistics."""

    tier: int
    band_error: float | None
    mask_px: int
    band_px: int
    ring_px: int
    reason: str

    def as_dict(self, strategy: str) -> dict[str, object]:
        return {
            "strategy": strategy,
            "tier": int(self.tier),
            "tier_name": TIER_NAMES.get(self.tier, "unknown"),
            "band_error": None if self.band_error is None else round(float(self.band_error), 4),
            "mask_px": int(self.mask_px),
            "band_px": int(self.band_px),
            "ring_px": int(self.ring_px),
            "reason": self.reason,
        }


def _binary(values: np.ndarray) -> np.ndarray:
    return np.where(values > 0, np.uint8(255), np.uint8(0))


def _grow(values: np.ndarray, radius: int) -> np.ndarray:
    """Dilate a boolean array by a square element of the given radius."""
    if radius <= 0:
        return values.astype(bool)
    kernel = np.ones((2 * radius + 1, 2 * radius + 1), np.uint8)
    return cv2.dilate(_binary(values), kernel).astype(bool)


def _shrink(values: np.ndarray, radius: int) -> np.ndarray:
    """Erode a boolean array by a square element of the given radius."""
    if radius <= 0:
        return values.astype(bool)
    kernel = np.ones((2 * radius + 1, 2 * radius + 1), np.uint8)
    return cv2.erode(_binary(values), kernel).astype(bool)


def sample_sets(
    mask: np.ndarray, excluded: np.ndarray, interior_mode: bool
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (band, ring, fringe) boolean sets for one block crop.

    Normal mode holds out a band of the context ring next to the mask and
    samples the ring beyond it. Interior mode (the mask covers everything
    worth sampling, e.g. a whole bubble interior) holds out a strip a few
    pixels inside the mask edge — past the bubble outline's residue — and
    samples the rest of the mask.
    """
    m = mask.astype(bool)
    off = excluded.astype(bool)
    if interior_mode:
        # The mask covers everything worth sampling (a whole bubble
        # interior), so hold out a strip deeper inside: the interior edge
        # itself carries the bubble outline's residue, which no tier should
        # have to reproduce.
        inner = _shrink(m, BAND_WIDTH)
        core = _shrink(m, 2 * BAND_WIDTH)
        band = (inner & ~core) & ~off
        return band, core & ~off, np.zeros_like(m)

    near = _grow(m, FRINGE_RADIUS)
    band_edge = _grow(m, FRINGE_RADIUS + BAND_WIDTH)
    band = (band_edge & ~near) & ~off
    ring = (_grow(m, RING_RADIUS) & ~band_edge) & ~off
    return band, ring, near


class _PlaneFit:
    """Least-squares plane per channel over a sample set, with clamped output."""

    def __init__(self, crop: np.ndarray, sample: np.ndarray) -> None:
        ys, xs = np.nonzero(sample)
        self.count = int(ys.size)
        self.span_x = int(xs.max() - xs.min()) if ys.size else 0
        self.span_y = int(ys.max() - ys.min()) if ys.size else 0
        self.center_x = float(xs.mean())
        self.center_y = float(ys.mean())
        self.scale_x = max(1.0, float(self.span_x))
        self.scale_y = max(1.0, float(self.span_y))
        columns: list[np.ndarray] = [np.ones(ys.size, np.float64)]
        if self.span_x >= 4:
            columns.append((xs - self.center_x) / self.scale_x)
        if self.span_y >= 4:
            columns.append((ys - self.center_y) / self.scale_y)
        design = np.stack(columns, axis=1)
        self.samples = crop[ys, xs].astype(np.float64)
        self.coefficients, *_ = np.linalg.lstsq(design, self.samples, rcond=None)
        self.lo = np.maximum(0.0, self.samples.min(axis=0) - 6.0)
        self.hi = np.minimum(255.0, self.samples.max(axis=0) + 6.0)

    def usable(self) -> bool:
        return self.count >= 24 and (self.span_x >= 4 or self.span_y >= 4)

    def predict(self, ys: np.ndarray, xs: np.ndarray) -> np.ndarray:
        columns = [np.ones(ys.size, np.float64)]
        if self.span_x >= 4:
            columns.append((xs - self.center_x) / self.scale_x)
        if self.span_y >= 4:
            columns.append((ys - self.center_y) / self.scale_y)
        design = np.stack(columns, axis=1)
        return np.clip(design @ self.coefficients, self.lo, self.hi)


def _ring_is_textured(crop: np.ndarray, ring: np.ndarray) -> bool:
    """Whether enough of the ring deviates visibly from its own median.

    Screentone, hatching and noise all clear this easily; a ring that is
    flat plus a few stray ink marks does not, and a lattice must not be
    used to continue flatness over whatever those marks belonged to.
    """
    if not ring.any():
        return False
    samples = crop[ring].astype(np.int16)
    median = np.median(samples, axis=0)
    deviating = np.count_nonzero(np.abs(samples - median).max(axis=1) > TEXTURED_DEVIATION)
    return deviating >= MIN_TEXTURED_RING_FRACTION * samples.shape[0]


def _selected_error(
    values: np.ndarray, crop: np.ndarray, selector: np.ndarray, trim: float = 0.0
) -> float:
    """Mean absolute error of the predicted values, optionally trimmed.

    With ``trim`` > 0 the worst fraction of pixels is dropped before
    averaging: the lattice reproduction is judged on the texture it actually
    claims to reproduce, and ink the glyph pass missed next to the mask
    cannot veto it. The flat and smooth tiers stay strict — a sparse
    periodic texture must keep failing them (H2), and their own errors do
    not concentrate in a few outliers the way missed ink does.
    """
    actual = crop[selector].astype(np.float64)
    per_pixel = np.abs(np.broadcast_to(values, actual.shape) - actual).mean(axis=1)
    if trim <= 0.0:
        return float(per_pixel.mean())
    count = per_pixel.size
    if count == 0:
        return 0.0
    ordered = np.sort(per_pixel, axis=None)
    keep = max(1, int(np.ceil((1.0 - trim) * count)))
    return float(ordered[:keep].mean())


def _flat_tier(
    crop: np.ndarray, ring: np.ndarray, band: np.ndarray
) -> tuple[float, np.ndarray] | None:
    """Ring median as a constant fill; returns its band error and colour."""
    if not ring.any():
        return None
    color = np.median(crop[ring], axis=0)
    error = _selected_error(np.broadcast_to(color.astype(np.float64), (1, 3)), crop, band)
    return error, color


def _lattice_vectors(gray: np.ndarray, known: np.ndarray) -> list[tuple[int, int, float]]:
    """Period candidates (dy, dx, score) from masked normalized autocorrelation."""
    height, width = gray.shape[:2]
    max_period = min(MAX_LATTICE_PERIOD, max(2, min(height, width) // 2 - 1))
    if max_period < 2:
        return []
    window = known.astype(np.float32)
    values = gray.astype(np.float32) - float(np.mean(gray[known]))
    padded = np.zeros((height * 2, width * 2), np.float32)
    weight = np.zeros((height * 2, width * 2), np.float32)
    padded[:height, :width] = values * window
    weight[:height, :width] = window

    values_fft = np.fft.rfft2(padded)
    weight_fft = np.fft.rfft2(weight)
    numerator = np.fft.irfft2(values_fft * np.conj(values_fft), s=(height * 2, width * 2))
    denominator = np.fft.irfft2(weight_fft * np.conj(weight_fft), s=(height * 2, width * 2))
    variance = float(numerator[0, 0]) / max(float(denominator[0, 0]), 1.0)
    if variance <= 1.0:
        return []
    known_count = int(np.count_nonzero(known))
    min_overlap = max(24.0, 0.2 * known_count)

    shifts: list[tuple[int, int, float]] = []
    for dy in range(-max_period, max_period + 1):
        for dx in range(-max_period, max_period + 1):
            if abs(dy) + abs(dx) < 2:
                continue
            overlap = float(denominator[dy, dx])
            if overlap < min_overlap:
                continue
            score = float(numerator[dy, dx]) / (variance * overlap)
            if score >= MIN_LATTICE_PEAK_R:
                shifts.append((dy, dx, score))
    # Strongest peaks first. A harmonic of the true period (2x the vector,
    # say) can outscore the fundamental, but the lattice it generates is a
    # sparse sublattice that fails the held-out band — so the router tries
    # the strongest candidates in order and lets verification choose.
    shifts.sort(key=lambda item: item[2], reverse=True)

    accepted: list[tuple[int, int, float]] = []
    for dy, dx, score in shifts[:96]:
        near_duplicate = any(
            (dy - other_y) ** 2 + (dx - other_x) ** 2 <= 2 for other_y, other_x, _ in accepted
        )
        if near_duplicate:
            continue
        accepted.append((dy, dx, score))
        if len(accepted) >= 6:
            break
    return accepted


def _independent_second(
    vectors: list[tuple[int, int, float]], primary: tuple[int, int, float]
) -> tuple[int, int, float] | None:
    """The strongest peak not nearly parallel to the given period vector."""
    primary_y, primary_x = primary[0], primary[1]
    primary_norm = float(np.hypot(primary_y, primary_x))
    for candidate in vectors:
        if candidate is primary or candidate[2] < MIN_LATTICE_SECONDARY_R:
            continue
        cross = abs(primary_y * candidate[1] - primary_x * candidate[0])
        candidate_norm = float(np.hypot(candidate[0], candidate[1]))
        if cross >= MIN_INDEPENDENCE * primary_norm * candidate_norm:
            return candidate
    return None


def _lattice_attempts(
    vectors: list[tuple[int, int, float]]
) -> list[tuple[tuple[int, int, float], tuple[int, int, float] | None]]:
    """(primary, secondary) period pairs to try, strongest first.

    Each primary is tried with an independent second vector first, then on
    its own (hatching may expose only one true direction).
    """
    attempts: list[tuple[tuple[int, int, float], tuple[int, int, float] | None]] = []
    for primary in vectors[:3]:
        attempts.append((primary, _independent_second(vectors, primary)))
        attempts.append((primary, None))
    return attempts


def _lattice_offsets(
    pair: tuple[tuple[int, int, float], tuple[int, int, float] | None], reach: int
) -> list[tuple[int, int]]:
    """Lattice translates ordered by length, able to cross the whole mask."""
    primary, secondary = pair
    offsets: set[tuple[int, int]] = set()
    if secondary is None:
        # One period direction (hatching): walk along it in both directions.
        step_y, step_x = primary[0], primary[1]
        step = max(1, int(round(np.hypot(step_y, step_x))))
        steps = min(96, reach // step + 2)
        offsets.update(
            (index * step_y, index * step_x) for index in range(-steps, steps + 1) if index
        )
    else:
        # Two independent periods: enumerate shells of the integer lattice
        # they generate, nearest shells first.
        primary_y, primary_x = primary[0], primary[1]
        second_y, second_x = secondary[0], secondary[1]
        step_p = max(1, int(round(np.hypot(primary_y, primary_x))))
        step_s = max(1, int(round(np.hypot(second_y, second_x))))
        max_shell = min(MAX_LATTICE_SHELL, reach // min(step_p, step_s) + 2)
        for a in range(-max_shell, max_shell + 1):
            for b in range(-max_shell, max_shell + 1):
                if a == 0 and b == 0:
                    continue
                offsets.add((a * primary_y + b * second_y, a * primary_x + b * second_x))
    limit = float(reach) + 2.0 * max(
        float(np.hypot(primary[0], primary[1])),
        float(np.hypot(secondary[0], secondary[1])) if secondary else 0.0,
    )
    ordered = sorted(
        (offset for offset in offsets if np.hypot(offset[0], offset[1]) <= limit),
        key=lambda offset: (float(np.hypot(offset[0], offset[1])), offset[0], offset[1]),
    )
    return ordered[:MAX_LATTICE_OFFSETS]


def _lattice_paint(
    crop: np.ndarray,
    known: np.ndarray,
    targets: np.ndarray,
    offsets: list[tuple[int, int]],
) -> tuple[np.ndarray, np.ndarray]:
    """Copy each target pixel from a known pixel at a lattice translate.

    Returns the predicted values (uint8, zero where unfilled) and the boolean
    set of targets no lattice translate could reach in this pass.
    """
    height, width = crop.shape[:2]
    predicted = np.zeros(crop.shape, np.uint8)
    ys, xs = np.nonzero(targets)
    if ys.size == 0:
        return predicted, np.zeros_like(targets)
    pending = np.ones(ys.size, dtype=bool)
    for dy, dx in offsets:
        if not pending.any():
            break
        source_y = ys + dy
        source_x = xs + dx
        inside = (
            (source_y >= 0) & (source_y < height) & (source_x >= 0) & (source_x < width)
        )
        lookup_y = np.clip(source_y, 0, height - 1)
        lookup_x = np.clip(source_x, 0, width - 1)
        selected = pending & inside & known[lookup_y, lookup_x]
        if selected.any():
            target_y, target_x = ys[selected], xs[selected]
            predicted[target_y, target_x] = crop[lookup_y[selected], lookup_x[selected]]
            pending &= ~selected
    remaining = np.zeros_like(targets)
    remaining[ys[pending], xs[pending]] = True
    return predicted, remaining


def _nearest_known_fill(
    predicted: np.ndarray, crop: np.ndarray, known: np.ndarray, targets: np.ndarray
) -> None:
    """Fill leftover targets from their nearest known pixel in the crop.

    A distance transform gives each leftover pixel the radius that is
    guaranteed to contain its nearest known pixel, so one small window scan
    per (rare) straggler is enough.
    """
    if not targets.any() or not known.any():
        return
    height, width = known.shape
    distance = cv2.distanceTransform((~known).astype(np.uint8), cv2.DIST_L2, 5)
    for y, x in zip(*np.nonzero(targets)):
        radius = int(np.ceil(float(distance[y, x]))) + 1
        y0, y1 = max(0, y - radius), min(height, y + radius + 1)
        x0, x1 = max(0, x - radius), min(width, x + radius + 1)
        candidates_y, candidates_x = np.nonzero(known[y0:y1, x0:x1])
        if candidates_y.size == 0:
            continue
        deltas_y = candidates_y + y0 - y
        deltas_x = candidates_x + x0 - x
        best = min(
            range(deltas_y.size),
            key=lambda index: (deltas_y[index] ** 2 + deltas_x[index] ** 2, deltas_y[index]),
        )
        predicted[y, x] = crop[deltas_y[best] + y, deltas_x[best] + x]


def _verify_lattice(
    crop: np.ndarray, known: np.ndarray, band: np.ndarray, reach: int
) -> tuple[float, list[tuple[int, int]]] | None:
    """Find the period, then measure a lattice copy on the held-out band.

    Candidate period pairs are tried strongest first, and the first pair
    whose lattice actually reproduces the band below the bound wins — the
    held-out test, not the peak score, is the acceptance criterion.
    """
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    vectors = _lattice_vectors(gray, known)
    if not vectors:
        return None
    band_count = int(np.count_nonzero(band))
    if band_count == 0:
        return None
    for primary, secondary in _lattice_attempts(vectors):
        offsets = _lattice_offsets((primary, secondary), reach)
        if not offsets:
            continue
        predicted, unfilled = _lattice_paint(crop, known, band, offsets)
        if np.count_nonzero(unfilled) > (1.0 - MIN_BAND_COVERAGE) * band_count:
            continue
        measured = band & ~unfilled
        error = _selected_error(
            predicted[measured].astype(np.float64), crop, measured, trim=LATTICE_TRIM_FRACTION
        )
        if error <= LATTICE_MAX_ERROR:
            return error, offsets
    return None


def _apply_lattice(
    crop: np.ndarray,
    known: np.ndarray,
    mask: np.ndarray,
    offsets: list[tuple[int, int]],
    reach: int,
) -> tuple[np.ndarray, int]:
    """Fill the mask with lattice copies; returns values and straggler count.

    Lattice-filled pixels join the source set between rounds, so a lattice
    whose offsets only cross part of a wide mask still reaches every pixel
    (a composition of lattice translates stays on the lattice).
    """
    shortest = min(
        max(1.0, float(np.hypot(offset[0], offset[1]))) for offset in offsets[:1]
    ) if offsets else 1.0
    rounds = int(np.clip(np.ceil(reach / (MAX_LATTICE_SHELL * shortest)), 1, MAX_LATTICE_ROUNDS))
    predicted = np.zeros(crop.shape, np.uint8)
    targets = mask.astype(bool).copy()
    sources = known.copy()
    for _round in range(rounds):
        if not targets.any():
            break
        round_predicted, remaining = _lattice_paint(crop, sources, targets, offsets)
        filled_now = targets & ~remaining
        if not filled_now.any():
            break
        predicted[filled_now] = round_predicted[filled_now]
        sources |= filled_now
        targets = remaining
    stragglers = int(np.count_nonzero(targets))
    if stragglers:
        _nearest_known_fill(predicted, crop, sources, targets)
    return predicted, stragglers


def route_block_fill(
    crop: np.ndarray,
    mask: np.ndarray,
    excluded: np.ndarray,
    interior_mode: bool,
    valid: np.ndarray | None = None,
) -> tuple[FillDecision, np.ndarray | None]:
    """Choose the cheapest verified fill tier for one block crop.

    ``crop`` is a small RGB window containing ``mask`` (nonzero = erase),
    with ``excluded`` marking other blocks' ink and ``valid`` (when given)
    the region the fill may sample from (e.g. the bubble interior). Returns
    the decision and, when a tier was accepted, the filled crop with only
    the masked pixels repainted.
    """
    mask_bool = mask.astype(bool)
    mask_px = int(np.count_nonzero(mask_bool))
    if mask_px == 0:
        return FillDecision(TIER_SKIPPED, None, 0, 0, 0, "no-safe-mask"), None

    band, ring, fringe = sample_sets(mask, excluded, interior_mode)
    valid_bool: np.ndarray | None = None
    if valid is not None and not interior_mode:
        valid_bool = valid.astype(bool)
        band = band & valid_bool
        ring = ring & valid_bool
    band_px = int(np.count_nonzero(band))
    ring_px = int(np.count_nonzero(ring))
    if band_px < MIN_BAND_PX or ring_px < MIN_RING_PX:
        return (
            FillDecision(TIER_MODEL, None, mask_px, band_px, ring_px, "insufficient-context"),
            None,
        )

    # Tier 0, flat: any colour, accepted on a low band error.
    flat = _flat_tier(crop, ring, band)
    if flat is not None and flat[0] <= FLAT_MAX_ERROR:
        error, color = flat
        decision = FillDecision(0, error, mask_px, band_px, ring_px, "flat")
        filled = crop.copy()
        filled[mask_bool] = np.rint(color).astype(np.uint8)
        return decision, filled

    # Tier 1, smooth: a plane per channel over the ring, predicting the band.
    fit = _PlaneFit(crop, ring)
    if fit.usable():
        bys, bxs = np.nonzero(band)
        error = _selected_error(fit.predict(bys, bxs), crop, band)
        if error <= SMOOTH_MAX_ERROR:
            decision = FillDecision(1, error, mask_px, band_px, ring_px, "smooth")
            sample = ring | band if interior_mode else ring
            paint_fit = _PlaneFit(crop, sample)
            if paint_fit.usable():
                mys, mxs = np.nonzero(mask_bool)
                values = paint_fit.predict(mys, mxs)
                filled = crop.copy()
                filled[mys, mxs] = np.clip(np.rint(values), 0, 255).astype(np.uint8)
                return decision, filled

    # Tier 2, lattice: masked autocorrelation plus a verified lattice copy.
    # It only applies to genuinely textured rings (see MIN_TEXTURED_RING_FRACTION).
    if _ring_is_textured(crop, ring):
        if interior_mode:
            verify_sources = ring
        else:
            verify_sources = ~(mask_bool | fringe | band | excluded.astype(bool))
            if valid_bool is not None:
                verify_sources &= valid_bool
        reach = int(np.hypot(*crop.shape[:2]))
        lattice = _verify_lattice(crop, verify_sources, band, reach)
        if lattice is not None:
            error, offsets = lattice
            if interior_mode:
                fill_sources = ring | band
            else:
                fill_sources = ~(mask_bool | fringe | excluded.astype(bool))
                if valid_bool is not None:
                    fill_sources &= valid_bool
            painted, stragglers = _apply_lattice(crop, fill_sources, mask_bool, offsets, reach)
            if stragglers <= MAX_MASK_STRAGGLER_FRACTION * max(1, mask_px):
                decision = FillDecision(2, error, mask_px, band_px, ring_px, "lattice")
                filled = crop.copy()
                filled[mask_bool] = painted[mask_bool]
                return decision, filled

    return (
        FillDecision(TIER_MODEL, None, mask_px, band_px, ring_px, "band-error"),
        None,
    )
