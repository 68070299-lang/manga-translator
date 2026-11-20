"""Tests for the pre-render residual check and its one-round escalation."""

import unittest
from unittest.mock import patch

import cv2
import numpy as np

from manga_backend.pipeline import residual
from manga_backend.pipeline.fill_tiers import TIER_MODEL, FillDecision
from manga_backend.utils.textblock import TextBlock


def _letter(shape: tuple[int, int] = (60, 60)) -> np.ndarray:
    """A letter-like ring: a thick stroke with a hole, like a glyph body."""
    mask = np.zeros(shape, dtype=bool)
    mask[10:24, 10:24] = True
    mask[13:21, 13:21] = False
    return mask


def _observation(residual_px: int, flagged: bool, shape: tuple[int, int] = (80, 100)):
    return residual.ResidueObservation(
        residual_px=residual_px,
        reference_px=100,
        best_iou=0.9 if residual_px else 0.0,
        components=1 if residual_px else 0,
        area=shape[0] * shape[1],
        flagged=flagged,
        residue=np.zeros(shape, dtype=bool),
    )


class MeasureResidueTests(unittest.TestCase):
    def test_a_surviving_glyph_shape_is_residue(self):
        reference = _letter()
        observation = residual.measure_residue(reference, reference.copy())

        self.assertTrue(observation.flagged)
        self.assertEqual(observation.residual_px, int(np.count_nonzero(reference)))
        self.assertEqual(observation.components, 1)
        self.assertAlmostEqual(observation.best_iou, 1.0)

    def test_restored_texture_fragments_are_not_residue(self):
        """A perfect lattice fill of hatch is re-detected as ink; its shapes differ."""
        reference = _letter()
        hatched = np.zeros_like(reference)
        for row in range(0, reference.shape[0], 6):
            hatched[row, 10:45] = True

        observation = residual.measure_residue(reference, hatched)

        self.assertFalse(observation.flagged)
        self.assertLess(observation.best_iou, residual.MIN_RESIDUE_IOU)

    def test_background_sized_reference_component_is_not_lettering(self):
        """An extractor overshoot covering the block must not flag the fill."""
        reference = np.zeros((60, 60), dtype=bool)
        reference[5:55, 5:55] = True

        observation = residual.measure_residue(reference, reference.copy())

        self.assertFalse(observation.flagged)
        self.assertEqual(observation.residual_px, 0)

    def test_no_ink_after_fill_is_clean(self):
        observation = residual.measure_residue(_letter(), np.zeros((60, 60), dtype=bool))

        self.assertFalse(observation.flagged)
        self.assertEqual(observation.residual_px, 0)

    def test_iou_threshold_is_inclusive_and_documented(self):
        reference = _letter()
        partial = np.zeros_like(reference)
        partial[10:24, 10:14] = True  # a vertical bar: part of the ring's left side
        observation = residual.measure_residue(reference, partial)
        area = int(np.count_nonzero(partial))
        iou = area / int(np.count_nonzero(reference))
        self.assertLess(iou, residual.MIN_RESIDUE_IOU)
        self.assertFalse(observation.flagged)


class EscalationTests(unittest.TestCase):
    @staticmethod
    def _flat_case():
        crop = np.full((80, 100, 3), 250, np.uint8)
        mask = np.zeros((80, 100), np.uint8)
        mask[30:50, 35:65] = 255
        return crop, mask, TextBlock([10, 10, 60, 60])

    def _run(self, observations, grown_delta_px=25):
        crop, mask, block = self._flat_case()
        grown = mask.copy()
        if grown_delta_px:
            rows, columns = np.nonzero(mask)
            grown[rows.min() - 5 : rows.min(), columns.min() - 5 : columns.min()] = 255
        calls = []
        real = residual.route_block_fill

        def recording(crop, mask, excluded, interior_mode, valid=None, start_tier=0):
            calls.append(start_tier)
            return real(crop, mask, excluded, interior_mode, valid, start_tier=start_tier)

        with (
            patch.object(residual, "route_block_fill", recording),
            patch.object(residual, "measure_residue", side_effect=observations),
            patch.object(
                residual,
                "grow_escalation_mask",
                return_value=(grown, (0, 0, 100, 80)),
            ),
        ):
            decision, filled, model_mask = residual.route_block_fill_checked(
                crop,
                mask,
                np.zeros_like(mask),
                False,
                None,
                block=block,
                origin=(0, 0),
                strategy="glyph",
            )
        return decision, filled, model_mask, calls

    def test_clean_fill_is_not_escalated(self):
        decision, filled, _model_mask, calls = self._run([_observation(0, False)])

        self.assertEqual(calls, [0])
        self.assertFalse(decision["residual_flag"])
        self.assertFalse(decision["escalated"])
        self.assertIsNotNone(filled)

    def test_flag_grows_the_mask_and_reroutes_once_at_the_next_tier(self):
        decision, filled, _model_mask, calls = self._run(
            [_observation(40, True), _observation(0, False)]
        )

        self.assertEqual(calls, [0, 1], "one escalation, re-routed from the next tier")
        self.assertTrue(decision["residual_flag"])
        self.assertTrue(decision["escalated"])
        self.assertEqual(decision["initial_tier"], 0)
        self.assertEqual(decision["escalation_tier"], 1)
        self.assertEqual(decision["tier"], 1)
        self.assertEqual(decision["escalation_grown_px"], 25)
        self.assertFalse(decision["second_flag"])
        self.assertIsNotNone(filled)
        self.assertEqual(decision["flag_reason"], "residue")

    def test_second_flag_is_recorded_but_never_retried(self):
        decision, filled, _model_mask, calls = self._run(
            [_observation(40, True), _observation(40, True)]
        )

        self.assertEqual(calls, [0, 1], "a second flag must not start a second escalation")
        self.assertTrue(decision["escalated"])
        self.assertTrue(decision["second_flag"])
        self.assertEqual(decision["second_residual_px"], 40)
        self.assertIsNotNone(filled)

    def test_lattice_block_escalates_to_the_model(self):
        crop = np.full((80, 100, 3), 250, np.uint8)
        mask = np.zeros((80, 100), np.uint8)
        mask[30:50, 35:65] = 255
        block = TextBlock([10, 10, 60, 60])
        flagged = _observation(40, True)
        grown = mask.copy()
        with (
            patch.object(residual, "route_block_fill") as routed,
            patch.object(residual, "measure_residue", return_value=flagged),
            patch.object(
                residual, "grow_escalation_mask", return_value=(grown, (0, 0, 100, 80))
            ),
        ):
            routed.side_effect = [
                (FillDecision(2, 0.0, 600, 400, 800, "lattice"), crop.copy()),
                (FillDecision(TIER_MODEL, None, 600, 400, 800, "band-error"), None),
            ]
            decision, filled, model_mask = residual.route_block_fill_checked(
                crop,
                mask,
                np.zeros_like(mask),
                False,
                None,
                block=block,
                origin=(0, 0),
                strategy="glyph",
            )

        self.assertEqual(decision["tier"], TIER_MODEL)
        self.assertEqual(decision["escalation_tier"], TIER_MODEL)
        self.assertIsNone(filled)
        self.assertTrue(np.array_equal(model_mask, grown))

    def test_band_error_above_the_bound_is_a_flag_condition(self):
        self.assertTrue(
            residual._band_error_above_bound(
                FillDecision(0, residual.FLAT_MAX_ERROR + 0.5, 10, 10, 10, "flat")
            )
        )
        self.assertFalse(
            residual._band_error_above_bound(
                FillDecision(0, residual.FLAT_MAX_ERROR, 10, 10, 10, "flat")
            )
        )
        self.assertFalse(
            residual._band_error_above_bound(
                FillDecision(TIER_MODEL, None, 10, 10, 10, "band-error")
            )
        )


class GrowMaskTests(unittest.TestCase):
    def test_growth_adds_the_flagged_residue_and_reports_page_bounds(self):
        crop = np.full((120, 120, 3), 250, np.uint8)
        crop[30:60, 30:48] = 0  # a solid glyph the extractor can see
        mask = np.zeros((120, 120), bool)
        mask[30:60, 30:48] = True
        residue = np.zeros((120, 120), bool)
        residue[70:76, 70:76] = True
        block = TextBlock([20, 20, 80, 80])

        grown, bounds = residual.grow_escalation_mask(
            crop, crop, block, (10, 10), mask, residue
        )

        self.assertTrue(np.all(grown[residue]))
        self.assertTrue(np.all(grown[mask]))
        ys, xs = np.nonzero(grown)
        self.assertEqual(
            bounds,
            (10 + int(xs.min()), 10 + int(ys.min()), 10 + int(xs.max()) + 1, 10 + int(ys.max()) + 1),
        )


class ModelResidueRetryTests(unittest.TestCase):
    def test_flags_surviving_glyph_after_model_inpaint_and_skips_passthrough(self):
        original = np.full((120, 120, 3), 240, dtype=np.uint8)
        cv2.line(original, (0, 32), (120, 42), (120, 120, 120), 4)
        cv2.line(original, (0, 82), (120, 74), (105, 105, 105), 3)
        cv2.circle(original, (30, 62), 18, (130, 130, 130), 3)
        cv2.circle(original, (92, 58), 16, (115, 115, 115), 3)
        # Distinct dark stroke inside block [40, 40, 82, 76] that survives model pass.
        original[48:68, 52:58] = 0
        original[48:54, 52:72] = 0
        original[58:63, 52:70] = 0

        mask = np.zeros((120, 120), dtype=np.uint8)
        mask[48:68, 52:58] = 255
        mask[48:54, 52:72] = 255
        mask[58:63, 52:70] = 255

        block = TextBlock([40, 40, 82, 76], block_type="sfx")
        decisions = [{"tier": TIER_MODEL, "block_bbox": [40, 40, 82, 76]}]

        # No-op passthrough (identical inside mask) must not trigger retry.
        no_retry_mask, no_retry_count = residual.find_model_residue_retry(
            original, original.copy(), mask, [block], [dict(decisions[0])]
        )
        self.assertEqual(no_retry_count, 0)
        self.assertFalse(np.any(no_retry_mask))

        # Partially changed crop that still retains the dark stroke shape triggers retry.
        incomplete = original.copy()
        incomplete[48:50, 52:72] = 200
        retry_mask, retry_count = residual.find_model_residue_retry(
            original, incomplete, mask, [block], decisions
        )
        self.assertEqual(retry_count, 1)
        self.assertTrue(decisions[0]["model_retry"])
        self.assertGreater(decisions[0]["model_retry_residual_px"], 0)
        self.assertTrue(np.any(retry_mask > 0))

    def test_clean_model_inpaint_requires_no_retry(self):
        original = np.full((100, 100, 3), 240, dtype=np.uint8)
        original[30:55, 30:55] = 0
        mask = np.zeros((100, 100), dtype=np.uint8)
        mask[30:55, 30:55] = 255
        cleaned = np.full((100, 100, 3), 240, dtype=np.uint8)
        block = TextBlock([20, 20, 65, 65], block_type="sfx")
        decisions = [{"tier": TIER_MODEL, "block_bbox": [20, 20, 65, 65]}]

        retry_mask, retry_count = residual.find_model_residue_retry(
            original, cleaned, mask, [block], decisions
        )

        self.assertEqual(retry_count, 0)
        self.assertFalse(np.any(retry_mask))
        self.assertNotIn("model_retry", decisions[0])


if __name__ == "__main__":
    unittest.main()
