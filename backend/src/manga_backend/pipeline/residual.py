"""Pre-render residual check with one bounded escalation (research section 2.7).

Step 6 used to re-read the *rendered* page with manga-ocr: about 7 s a page,
more than OCR itself, and it ran after drawing, so it could never drive a
repair. It also read short rendered English as kana, which is the noise floor
the WP4 investigation found. This module replaces that check on the
production path with a pixel check that runs before rendering:

* After the fill router repaints a block, the WP5 ink extractor
  (``masks.glyph_mask_for_block``) runs again on the inpainted crop. The
  post-fill ink components are matched, shape against shape, with the
  components of the block's original glyph mask (core, outline ring and
  unmixed halo). A component that reproduces the *shape* of original ink is
  residue: the fill left something where lettering used to be.
* A flagged block escalates exactly once: its mask grows by the outline/alpha
  structures the mask left out (the original ink inside the alpha band next
  to the mask, ``ESCALATION_GROWTH_RADIUS``) plus the flagged residue
  components themselves, and the block is re-routed at the next tier (flat ->
  smooth -> lattice -> model). The escalated fill is measured once more; a
  second flag is recorded and reported, never retried.

Why shape and not pixel overlap. The obvious reading -- count post-fill ink
that lands on the original mask -- was measured on the corpus and rejected:
the fill paints the mask from the context ring, so a *perfect* lattice fill
of a textured block is re-detected as ink (hatch fragments cover 100% of the
masked pixels). Escalating those two blocks turned a 1.2% SFX residual into
21.5% and tone holes from 0 to 2,610 px. The pixels are "new ink" but their
shapes are texture, not lettering: matching components by IoU with the
original lettering shapes (``MIN_RESIDUE_IOU``) separates the two, while a
reference component that is background-sized (an extractor overshoot that
covers the whole block) is not a letter and does not count.

What the check can and cannot see is a property of its reference: it compares
the inpainted crop with the extractor's mask on the original crop, using the
detector block box as the exposure window. Ink the detector box never
contained, or that the extractor classifies as line art or tone, is outside
both sides and cannot be flagged. A fill that paints the wrong *background*
inside the mask is invisible here too: the fill's own held-out band is that
evidence, and it already gates the tier. Both limits are measured on the
corpus and reported in the work-package notes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from manga_backend.pipeline.fill_tiers import (
    FLAT_MAX_ERROR,
    LATTICE_MAX_ERROR,
    SMOOTH_MAX_ERROR,
    TIER_MODEL,
    FillDecision,
    route_block_fill,
)
from manga_backend.pipeline.regions import int_box
from manga_backend.utils.textblock import Box, TextBlock

logger = logging.getLogger(__name__)

# A post-fill component is residue when its IoU with an original ink
# component reaches this share. A letter that survived the fill keeps its
# shape, while a fill artefact (a lattice copy of tone or hatching, a repainted
# fringe) is a different shape. Measured on the corpus: a perfect lattice fill
# of hatching peaks at 0.47, one under-covered faint glyph reaches 1.0.
MIN_RESIDUE_IOU = 0.5
# Components smaller than this are antialiasing fringe, not a missed stroke.
MIN_RESIDUE_COMPONENT_PX = 6
# Total residue below this is not worth a repair: it is at most a couple of
# glyph fringes, and every escalation moves a block up a tier.
MIN_RESIDUE_PX = 12
# A reference component covering more than this share of the block box is not
# lettering: the extractor occasionally returns (nearly) the whole text box
# for a tone or dark block. Matching it would flag the restored background.
MAX_REFERENCE_SHARE = 0.4
# A reference component that touches two edges of the block box, or spans more
# than this aspect ratio, is panel or frame structure the extractor kept in
# the mask; the fill reproducing it is correct, not residue. Letters in a
# tight detector box may touch one edge, but the block corners are empty for
# vertical and horizontal text alike.
MAX_REFERENCE_ASPECT = 4.0
# The mask grows by at most the extractor's halo radius (masks.HALO_RADIUS).
# The reference mask already contains WP5's outline rings, so this is "grow
# by the outline ring or alpha band" without reaching structure the mask
# deliberately left alone.
ESCALATION_GROWTH_RADIUS = 3
# The band error each accepted cheap tier promised to stay under.
TIER_BOUNDS = {0: FLAT_MAX_ERROR, 1: SMOOTH_MAX_ERROR, 2: LATTICE_MAX_ERROR}
# flat -> smooth -> lattice -> model; the model has no next tier.
NEXT_TIER = {0: 1, 1: 2, 2: TIER_MODEL}


@dataclass(frozen=True)
class ResidueObservation:
    """What one re-run of the ink extractor saw on an inpainted crop."""

    residual_px: int
    reference_px: int
    best_iou: float
    components: int
    area: int
    flagged: bool
    residue: np.ndarray

    def as_dict(self) -> dict[str, Any]:
        return {
            "residual_px": int(self.residual_px),
            "reference_px": int(self.reference_px),
            "best_iou": round(float(self.best_iou), 4),
            "components": int(self.components),
            "roi_px": int(self.area),
            "flagged": bool(self.flagged),
        }


def _shifted_block(block: TextBlock, origin: tuple[int, int]) -> TextBlock:
    x1, y1, x2, y2 = int_box(block.xyxy)
    ox, oy = origin
    return TextBlock(
        [x1 - ox, y1 - oy, x2 - ox, y2 - oy],
        block_type=block.block_type,
    )


def _ink_mask_in_crop(
    crop: np.ndarray, block: TextBlock, origin: tuple[int, int]
) -> np.ndarray:
    """The extractor's glyph mask for the block, placed in crop coordinates."""
    # Imported here because masks.py imports this module (the router entry
    # point lives there); a module-level import would be circular.
    from manga_backend.pipeline.masks import glyph_mask_for_block

    shifted = _shifted_block(block, origin)
    height, width = crop.shape[:2]
    if shifted.x2 <= 0 or shifted.y2 <= 0 or shifted.x1 >= width or shifted.y1 >= height:
        return np.zeros((height, width), dtype=bool)
    local, bounds = glyph_mask_for_block(crop, shifted)
    mask = np.zeros((height, width), dtype=bool)
    if local.size == 0:
        return mask
    x1, y1, x2, y2 = bounds
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(width, x2), min(height, y2)
    if x2 > x1 and y2 > y1:
        mask[y1:y2, x1:x2] = (
            local[y1 - bounds[1] : y2 - bounds[1], x1 - bounds[0] : x2 - bounds[0]] > 0
        )
    return mask


def _block_roi(
    crop: np.ndarray, block: TextBlock, origin: tuple[int, int], padding: int = 2
) -> np.ndarray:
    """The extractor's ROI (the block box, padded) in crop coordinates."""
    height, width = crop.shape[:2]
    x1, y1, x2, y2 = int_box(block.xyxy)
    ox, oy = origin
    x1, y1 = max(0, x1 - ox - padding), max(0, y1 - oy - padding)
    x2, y2 = min(width, x2 - ox + padding), min(height, y2 - oy + padding)
    roi = np.zeros((height, width), dtype=bool)
    if x2 > x1 and y2 > y1:
        roi[y1:y2, x1:x2] = True
    return roi


def _is_structure(component_area: int, width: int, height: int, roi_edges: int) -> bool:
    """Whether a reference component looks like frame/panel structure."""
    if roi_edges >= 2:
        return True
    return max(width, height) >= MAX_REFERENCE_ASPECT * max(1, min(width, height))


def _component(mask: np.ndarray, stats: np.ndarray, label: int) -> tuple[np.ndarray, int, int, int, int]:
    x = int(stats[label, cv2.CC_STAT_LEFT])
    y = int(stats[label, cv2.CC_STAT_TOP])
    width = int(stats[label, cv2.CC_STAT_WIDTH])
    height = int(stats[label, cv2.CC_STAT_HEIGHT])
    return mask[y : y + height, x : x + width] == label, x, y, width, height


def measure_residue(
    reference: np.ndarray, inpainted: np.ndarray, roi: np.ndarray | None = None
) -> ResidueObservation:
    """Match the inpainted crop's ink shapes against the block's original ink.

    Both arguments are boolean glyph masks in the same crop coordinates (the
    reference from the original crop, the second from the filled crop). A
    post-fill component counts when its IoU with an original component --
    one that is lettering-sized, not an extractor overshoot the size of the
    whole block -- reaches ``MIN_RESIDUE_IOU``. The union of the surviving
    components is returned as ``residue`` so the escalation can add exactly
    what was flagged.
    """
    reference = reference.astype(bool)
    inpainted = inpainted.astype(bool)
    height, width = reference.shape[:2]
    if roi is not None:
        reference &= roi.astype(bool)
    roi_area = int(np.count_nonzero(roi)) if roi is not None else int(height * width)
    if roi is not None and roi.any():
        roi_y, roi_x = np.nonzero(roi)
        edges = (int(roi_x.min()), int(roi_y.min()), int(roi_x.max()), int(roi_y.max()))
    else:
        edges = (0, 0, width - 1, height - 1)
    residue = np.zeros((height, width), dtype=bool)
    total_components = 0
    best_iou = 0.0
    roi_px = roi_area
    if not reference.any() or not inpainted.any():
        return ResidueObservation(
            residual_px=0,
            reference_px=int(np.count_nonzero(reference)),
            best_iou=0.0,
            components=0,
            area=roi_px,
            flagged=False,
            residue=residue,
        )

    ref_count, ref_labels, ref_stats, _ = cv2.connectedComponentsWithStats(
        reference.astype(np.uint8), connectivity=8
    )
    keep: set[int] = set()
    for label in range(1, ref_count):
        area = int(ref_stats[label, cv2.CC_STAT_AREA])
        if area < MIN_RESIDUE_COMPONENT_PX or area > MAX_REFERENCE_SHARE * roi_px:
            continue
        _body, x, y, width_c, height_c = _component(ref_labels, ref_stats, label)
        touched = (
            int(x <= edges[0])
            + int(y <= edges[1])
            + int(x + width_c - 1 >= edges[2])
            + int(y + height_c - 1 >= edges[3])
        )
        if _is_structure(area, width_c, height_c, touched):
            continue
        keep.add(label)
    if not keep:
        return ResidueObservation(
            residual_px=0,
            reference_px=int(np.count_nonzero(reference)),
            best_iou=0.0,
            components=0,
            area=roi_px,
            flagged=False,
            residue=residue,
        )

    post_count, post_labels, post_stats, _ = cv2.connectedComponentsWithStats(
        inpainted.astype(np.uint8), connectivity=8
    )
    for label in range(1, post_count):
        area = int(post_stats[label, cv2.CC_STAT_AREA])
        if area < MIN_RESIDUE_COMPONENT_PX:
            continue
        component, x, y, width_c, height_c = _component(post_labels, post_stats, label)
        labels_here = np.unique(ref_labels[y : y + height_c, x : x + width_c][component])
        matched = False
        for ref_label in labels_here:
            ref_label = int(ref_label)
            if ref_label == 0 or ref_label not in keep:
                continue
            reference_slice = ref_labels[y : y + height_c, x : x + width_c] == ref_label
            intersection = int(np.count_nonzero(component & reference_slice))
            union = area + int(ref_stats[ref_label, cv2.CC_STAT_AREA]) - intersection
            iou = intersection / union if union else 0.0
            best_iou = max(best_iou, iou)
            if iou >= MIN_RESIDUE_IOU:
                matched = True
        if matched:
            total_components += 1
            residue[y : y + height_c, x : x + width_c] |= component
    residual_px = int(np.count_nonzero(residue))
    return ResidueObservation(
        residual_px=residual_px,
        reference_px=int(np.count_nonzero(reference)),
        best_iou=best_iou,
        components=total_components,
        area=roi_px,
        flagged=residual_px >= MIN_RESIDUE_PX,
        residue=residue,
    )


def grow_escalation_mask(
    original_crop: np.ndarray,
    filled_crop: np.ndarray,
    block: TextBlock,
    origin: tuple[int, int],
    mask: np.ndarray,
    residue: np.ndarray | None = None,
) -> tuple[np.ndarray, Box]:
    """Grow a block mask by the outline/alpha ink and the flagged residue.

    Two bounded additions: the extractor's original ink inside the alpha band
    next to the current mask (an under-covered stroke's fringe), and the
    residue components the check flagged (original ink the fill left behind).
    Nothing far from the mask and not itself flagged is added, so a bubble
    wall or panel line art the mask never claimed stays untouched. Returns the
    grown crop mask and its box in page coordinates (the harness records the
    real erase union from it).
    """
    mask_bool = mask.astype(bool)
    roi = _block_roi(original_crop, block, origin)
    reference = _ink_mask_in_crop(original_crop, block, origin) & roi
    radius = ESCALATION_GROWTH_RADIUS
    kernel = np.ones((2 * radius + 1, 2 * radius + 1), np.uint8)
    band = cv2.dilate(mask_bool.astype(np.uint8), kernel).astype(bool)
    if residue is None:
        post = _ink_mask_in_crop(filled_crop, block, origin)
        residue = measure_residue(reference, post, roi).residue
    grown = mask_bool | (reference & band) | residue.astype(bool)
    ys, xs = np.nonzero(grown)
    if ys.size == 0:
        return mask_bool, (origin[0], origin[1], origin[0], origin[1])
    return grown, (
        origin[0] + int(xs.min()),
        origin[1] + int(ys.min()),
        origin[0] + int(xs.max()) + 1,
        origin[1] + int(ys.max()) + 1,
    )


def _band_error_above_bound(decision: FillDecision) -> bool:
    bound = TIER_BOUNDS.get(decision.tier)
    if bound is None or decision.band_error is None:
        return False
    return decision.band_error > bound


def route_block_fill_checked(
    crop: np.ndarray,
    mask: np.ndarray,
    excluded: np.ndarray,
    interior_mode: bool,
    valid: np.ndarray | None,
    *,
    block: TextBlock,
    origin: tuple[int, int],
    strategy: str,
) -> tuple[dict[str, Any], np.ndarray | None, np.ndarray]:
    """Route one block, then run the pre-render residual check and escalate once.

    Returns ``(decision, filled, model_mask)``: ``filled`` is the crop the
    router accepted (None when the block still needs the model) and
    ``model_mask`` is the crop-coordinate mask to queue for the model, grown
    when an escalation went that far. The decision dict carries the usual
    ``FillDecision`` fields plus the check's observations, so runs and logs
    can report what was flagged, what was escalated and what re-flagged.
    """
    decision, filled = route_block_fill(crop, mask, excluded, interior_mode, valid)
    extras: dict[str, Any] = {
        "block_bbox": [int(value) for value in block.xyxy],
        "initial_tier": int(decision.tier),
        "initial_tier_name": _tier_name(decision.tier),
        "residual_flag": False,
        "residual_px": 0,
        "residual_reference_px": 0,
        "residual_best_iou": 0.0,
        "residual_components": 0,
        "flag_reason": "",
        "escalated": False,
        "escalation_tier": None,
        "escalation_tier_name": None,
        "escalation_grown_px": 0,
        "escalation_band_error": None,
        "second_flag": False,
        "second_residual_px": 0,
        "second_residual_iou": 0.0,
    }
    model_mask = mask

    if filled is None or decision.tier not in NEXT_TIER:
        # Nothing was painted: a model-tier block is filled later by the
        # model, and a skipped block has no mask at all.
        return _decision_dict(decision, strategy, extras), filled, model_mask

    roi = _block_roi(crop, block, origin)
    reference = _ink_mask_in_crop(crop, block, origin) & roi
    observation = measure_residue(
        reference, _ink_mask_in_crop(filled, block, origin), roi
    )
    band_flag = _band_error_above_bound(decision)
    extras.update(
        {
            "residual_px": observation.residual_px,
            "residual_reference_px": observation.reference_px,
            "residual_best_iou": round(float(observation.best_iou), 4),
            "residual_components": observation.components,
        }
    )
    flagged = observation.flagged or band_flag
    if not flagged:
        return _decision_dict(decision, strategy, extras), filled, model_mask

    reason = "band-error" if band_flag and not observation.flagged else (
        "residue+band-error" if band_flag else "residue"
    )
    extras["residual_flag"] = True
    extras["flag_reason"] = reason
    logger.info(
        "Residual check: block %s flagged (%s, residue %s px in %s components, "
        "best IoU %.2f)",
        extras["block_bbox"],
        reason,
        observation.residual_px,
        observation.components,
        observation.best_iou,
    )

    grown, grown_bounds = grow_escalation_mask(
        crop, filled, block, origin, mask, observation.residue
    )
    grown_px = int(np.count_nonzero(grown)) - int(np.count_nonzero(mask.astype(bool)))
    next_tier = NEXT_TIER[decision.tier]
    escalated, escalated_filled = route_block_fill(
        crop, grown, excluded, interior_mode, valid, start_tier=next_tier
    )
    extras.update(
        {
            "escalated": True,
            "escalation_tier": int(escalated.tier),
            "escalation_tier_name": _tier_name(escalated.tier),
            "escalation_grown_px": max(0, grown_px),
            "escalation_band_error": None
            if escalated.band_error is None
            else round(float(escalated.band_error), 4),
            "escalation_bounds": [int(value) for value in grown_bounds],
        }
    )
    chosen = escalated
    if escalated_filled is not None and escalated.tier != TIER_MODEL:
        second = measure_residue(
            reference, _ink_mask_in_crop(escalated_filled, block, origin), roi
        )
        extras.update(
            {
                "second_flag": bool(second.flagged),
                "second_residual_px": second.residual_px,
                "second_residual_iou": round(float(second.best_iou), 4),
            }
        )
        if second.flagged:
            logger.warning(
                "Residual check: block %s re-flagged after escalation to %s "
                "(residue %s px); reported, not retried",
                extras["block_bbox"],
                extras["escalation_tier_name"],
                second.residual_px,
            )
        filled = escalated_filled
        model_mask = grown
    else:
        filled = None
        model_mask = grown
    return _decision_dict(chosen, strategy, extras), filled, model_mask


def _tier_name(tier: int) -> str:
    from manga_backend.pipeline.fill_tiers import TIER_NAMES

    return TIER_NAMES.get(tier, "unknown")


def _decision_dict(
    decision: FillDecision, strategy: str, extras: dict[str, Any]
) -> dict[str, Any]:
    payload = decision.as_dict(strategy)
    payload.update(extras)
    return payload
