"""Unit tests for the verified fill-tier router (pipeline/fill_tiers.py).

Each test builds a small synthetic crop whose clean background is known,
masks a region, and checks that the router picks the expected tier and
reproduces the background — or refuses and escalates to the model.
"""

from __future__ import annotations

import unittest

import numpy as np

from manga_backend.pipeline.fill_tiers import (
    TIER_MODEL,
    TIER_SKIPPED,
    FillDecision,
    route_block_fill,
    sample_sets,
)
from manga_backend.pipeline.masks import prepare_flux_inpainting
from manga_backend.utils.textblock import TextBlock


def _flat(color: tuple[int, int, int], height: int = 60, width: int = 80) -> np.ndarray:
    return np.full((height, width, 3), color, dtype=np.uint8)


def _checkerboard(height: int, width: int, dark: int = 110, light: int = 240) -> np.ndarray:
    yy, xx = np.mgrid[0:height, 0:width]
    square = np.where((xx + yy) % 2 == 0, dark, light).astype(np.uint8)
    return np.repeat(square[:, :, None], 3, axis=2)


def _dot_tone(
    height: int, width: int, spacing: int = 7, level: int = 110, radius: float = 1.6
) -> np.ndarray:
    yy, xx = np.mgrid[0:height, 0:width]
    dots = ((yy % spacing) - spacing / 2.0) ** 2 + ((xx % spacing) - spacing / 2.0) ** 2
    tone = np.full((height, width, 3), 255, dtype=np.uint8)
    tone[dots <= radius * radius] = level
    return tone


def _rotated_tone(
    height: int, width: int, spacing: float, level: int, angle: float, radius: float
) -> np.ndarray:
    yy, xx = np.mgrid[0:height, 0:width]
    theta = np.deg2rad(angle)
    u = xx * np.cos(theta) + yy * np.sin(theta)
    v = -xx * np.sin(theta) + yy * np.cos(theta)
    du = np.mod(u, spacing) - spacing / 2.0
    dv = np.mod(v, spacing) - spacing / 2.0
    tone = np.full((height, width, 3), 255, dtype=np.uint8)
    tone[du * du + dv * dv <= radius * radius] = level
    return tone


class TierSelectionTests(unittest.TestCase):
    def setUp(self):
        self.mask = np.zeros((60, 80), np.uint8)
        self.mask[20:40, 30:50] = 255
        self.excluded = np.zeros((60, 80), np.uint8)

    def route(self, crop: np.ndarray, mask: np.ndarray | None = None):
        mask = self.mask if mask is None else mask
        excluded = np.zeros(mask.shape, np.uint8)
        return route_block_fill(crop, mask, excluded, False)

    def test_flat_colored_background_accepts_tier0_any_color(self):
        clean = _flat((196, 168, 120))
        crop = clean.copy()
        crop[self.mask > 0] = 0  # ink under the mask
        decision, filled = self.route(crop)

        self.assertEqual(decision.tier, 0)
        self.assertLessEqual(decision.band_error, 6.0)
        self.assertTrue(np.array_equal(filled, clean))

    def test_steep_gradient_accepts_smooth_tier(self):
        yy = np.mgrid[0:60, 0:80][0]
        ramp = np.clip(40 + yy * 2.0, 0, 255).astype(np.uint8)
        clean = np.repeat(ramp[:, :, None], 3, axis=2)
        crop = clean.copy()
        crop[self.mask > 0] = 0
        decision, filled = self.route(crop)

        self.assertEqual(decision.tier, 1)
        self.assertLessEqual(decision.band_error, 8.0)
        # A flat median is ~50 levels off halfway up the block; the plane
        # must be visibly closer than that.
        error = np.abs(filled[self.mask > 0].astype(int) - clean[self.mask > 0].astype(int))
        self.assertLessEqual(float(error.max()), 4)

    def test_integer_lattice_tone_accepts_lattice_tier_and_reproduces_it(self):
        clean = _dot_tone(80, 100)
        crop = clean.copy()
        mask = np.zeros((80, 100), np.uint8)
        mask[25:55, 35:65] = 255
        crop[mask > 0] = 0
        decision, filled = route_block_fill(crop, mask, np.zeros_like(mask), False)

        self.assertEqual(decision.tier, 2)
        self.assertLessEqual(decision.band_error, 8.0)
        self.assertTrue(np.array_equal(filled, clean))

    def test_rotated_non_integer_tone_accepts_lattice_tier(self):
        # The adversarial case: real printed tone is rotated with non-integer
        # periods, so an exact-lattice copy is impossible. The router must
        # still recover a close copy from the best integer translates.
        clean = _rotated_tone(80, 100, spacing=7.3, level=90, angle=15.0, radius=1.7)
        crop = clean.copy()
        mask = np.zeros((80, 100), np.uint8)
        mask[25:55, 35:65] = 255
        crop[mask > 0] = 0
        decision, filled = route_block_fill(crop, mask, np.zeros_like(mask), False)

        self.assertEqual(decision.tier, 2)
        error = np.abs(filled[mask > 0].astype(int) - clean[mask > 0].astype(int))
        # Near-exact on average, with only dot-edge phase slips.
        self.assertLessEqual(float(error.mean()), 6.0)

    def test_checkerboard_hatching_is_lattice_not_model(self):
        clean = _checkerboard(60, 80)
        crop = clean.copy()
        crop[self.mask > 0] = 0
        decision, filled = self.route(crop)

        self.assertEqual(decision.tier, 2)
        self.assertTrue(np.array_equal(filled, clean))

    def test_tone_on_a_minority_of_the_ring_is_not_lattice_filled(self):
        # Hollow lettering over a tone patch: most of the ring is flat paper,
        # so the trimmed band error is dominated by the paper and cannot
        # vouch for the copied tone (on real pages that left stripes). The
        # block goes to the model instead.
        clean = np.full((80, 100, 3), 255, np.uint8)
        clean[:, :45] = _dot_tone(80, 100)[:, :45]
        crop = clean.copy()
        mask = np.zeros((80, 100), np.uint8)
        mask[25:55, 35:65] = 255
        crop[mask > 0] = 0
        decision, _ = route_block_fill(crop, mask, np.zeros_like(mask), False)

        self.assertEqual(decision.tier, TIER_MODEL)

    def test_speed_lines_that_change_inside_the_mask_are_not_extruded(self):
        # Vertical speed lines look periodic along a column, so the band test
        # passes (a few pixels of extrapolation reproduce them exactly). But
        # the lines above the mask are not the lines below it: a lattice copy
        # would extrude each set to a seam mid-mask (a scream balloon on a
        # real page lost its lettering's background that way). The copies
        # from above and below disagree, so the block goes to the model.
        rng = np.random.default_rng(3)
        lines = np.full((120, 100), 255, np.uint8)
        lines[:60, rng.random(100) < 0.3] = 40
        lines[60:, rng.random(100) < 0.3] = 40
        clean = np.repeat(lines[:, :, None], 3, axis=2)
        crop = clean.copy()
        mask = np.zeros((120, 100), np.uint8)
        mask[35:85, 30:70] = 255
        crop[mask > 0] = 0
        decision, filled = route_block_fill(crop, mask, np.zeros_like(mask), False)

        self.assertEqual(decision.tier, TIER_MODEL)
        self.assertIsNone(filled)

    def test_one_direction_hatching_across_the_mask_stays_lattice(self):
        # Genuine hatching is periodic along its only period direction, so
        # copies from both sides of the mask agree and it keeps the cheap tier.
        yy, xx = np.mgrid[0:80, 0:120]
        hatching = np.where((xx + yy) % 10 < 3, 60, 235).astype(np.uint8)
        clean = np.repeat(hatching[:, :, None], 3, axis=2)
        crop = clean.copy()
        mask = np.zeros((80, 120), np.uint8)
        mask[25:55, 35:85] = 255
        crop[mask > 0] = 0
        decision, filled = route_block_fill(crop, mask, np.zeros_like(mask), False)

        self.assertEqual(decision.tier, 2)
        assert filled is not None
        self.assertTrue(np.array_equal(filled, clean))

    def test_random_texture_escalates_to_model(self):
        rng = np.random.default_rng(11)
        noise = rng.integers(0, 255, (60, 80, 3)).astype(np.uint8)
        decision, filled = self.route(noise)

        self.assertEqual(decision.tier, TIER_MODEL)
        self.assertIsNone(filled)

    def test_contaminated_band_escalates_to_model(self):
        # A sizable aperiodic ink blob inside the held-out band is something
        # no cheap tier may reproduce from the ring; the block must go to the
        # model. (A *periodic* intruder, like a long straight line, is fair
        # game for the lattice tier: it genuinely reproduces it. And a little
        # missed ink is tolerated by the band's trimmed error statistic.)
        crop = _flat((255, 255, 255))
        crop[15:18, 25:55] = 0  # blob covering most of the top band strip
        decision, filled = self.route(crop)

        self.assertEqual(decision.tier, TIER_MODEL)
        self.assertIsNone(filled)

    def test_tiny_mask_without_context_escalates_to_model(self):
        mask = np.zeros((20, 20), np.uint8)
        mask[0:2, 0:2] = 255
        decision, filled = self.route(_flat((255, 255, 255)), mask)

        self.assertEqual(decision.tier, TIER_MODEL)
        self.assertEqual(decision.reason, "insufficient-context")

    def test_empty_mask_is_skipped(self):
        decision, filled = self.route(_flat((255, 255, 255)), np.zeros((60, 80), np.uint8))

        self.assertEqual(decision.tier, TIER_SKIPPED)
        self.assertIsNone(filled)


class VerificationDesignTests(unittest.TestCase):
    """The held-out band and the exclusion/validity sets behave as documented."""

    def test_sample_sets_normal_mode_skips_the_fringe(self):
        mask = np.zeros((60, 80), np.uint8)
        mask[20:40, 30:50] = 255
        band, ring, fringe = sample_sets(mask, np.zeros_like(mask), False)

        # Band pixels sit 2-5 px outside the mask; the fringe in between is
        # used by neither side.
        self.assertTrue(band.any() and ring.any() and fringe.any())
        self.assertFalse((band & (mask > 0)).any())
        self.assertFalse((band & fringe).any())
        self.assertFalse((ring & fringe).any())
        self.assertFalse((band & ring).any())

    def test_sample_sets_interior_mode_holds_out_the_outer_strip(self):
        mask = np.zeros((60, 80), np.uint8)
        mask[10:50, 10:70] = 255
        band, ring, fringe = sample_sets(mask, np.zeros_like(mask), True)

        self.assertTrue((band & (mask > 0)).any())
        self.assertTrue((ring & (mask > 0)).any())
        self.assertFalse((band & ring).any())
        self.assertFalse(fringe.any())

    def test_interior_mode_fills_flat_interior_with_faint_ink(self):
        # A whole bubble interior is erased (glyph extraction found nothing);
        # faint lettering lives inside it. The flat tier must still recover
        # the interior colour from the majority background.
        clean = _flat((255, 255, 255))
        crop = clean.copy()
        mask = np.zeros((60, 80), np.uint8)
        mask[8:52, 8:72] = 255
        crop[20:40, 30:50] = 185  # faint ink the glyph pass missed
        decision, filled = route_block_fill(crop, mask, np.zeros_like(mask), True)

        self.assertEqual(decision.tier, 0)
        self.assertTrue(np.array_equal(filled, clean))

    def test_other_blocks_ink_is_excluded_from_evidence(self):
        # A neighbour's lettering inside the crop must neither fail the flat
        # tier (excluded from the band) nor leak into the fill.
        crop = _flat((255, 255, 255))
        crop[20:40, 55:75] = 0  # adjacent block's glyphs
        mask = np.zeros((60, 80), np.uint8)
        mask[20:40, 30:50] = 255
        excluded = np.zeros((60, 80), np.uint8)
        excluded[20:40, 55:75] = 255
        decision, filled = route_block_fill(crop, mask, excluded, False)

        self.assertEqual(decision.tier, 0)
        self.assertTrue((filled[20:40, 55:75] == 0).all())  # untouched, not erased
        self.assertTrue(np.all(filled[20:40, 30:50] == 255))

    def test_valid_region_keeps_a_crossing_outline_out_of_the_band(self):
        # The bubble outline runs just outside the text; clipping sampling to
        # the shrunk bubble box keeps the flat tier usable.
        crop = _flat((255, 255, 255))
        crop[14:16, 20:60] = 0  # outline just above the block's context
        crop[20:40, 30:50] = 0  # ink under the mask
        mask = np.zeros((60, 80), np.uint8)
        mask[20:40, 30:50] = 255
        valid = np.zeros((60, 80), np.uint8)
        valid[17:53, 12:68] = 255
        decision, filled = route_block_fill(crop, mask, np.zeros_like(mask), False, valid)

        self.assertEqual(decision.tier, 0)
        changed = np.count_nonzero(np.any(filled != crop, axis=2))
        self.assertEqual(changed, np.count_nonzero(mask))
        self.assertTrue((filled[14:16, 20:60] == 0).all())  # outline untouched


class RouterIntegrationTests(unittest.TestCase):
    """prepare_flux_inpainting composites router fills and queues the rest."""

    def test_white_block_is_router_filled_and_nothing_outside_changes(self):
        image = _flat((255, 255, 255), 100, 140)
        image[30:40, 40:70] = 0
        block = TextBlock([38, 28, 72, 42], translated_text="TEXT")

        cleaned, model_mask, filled_count, model_count, decisions = (
            prepare_flux_inpainting(image, [block])
        )

        self.assertEqual((filled_count, model_count), (1, 0))
        self.assertFalse(np.any(model_mask))
        self.assertTrue(np.all(cleaned[30:40, 40:70] == 255))
        # Composite guarantee: nothing outside the erased region changed.
        outside = np.ones(image.shape[:2], dtype=bool)
        outside[28:44, 36:74] = False
        self.assertTrue(np.array_equal(cleaned[outside], image[outside]))
        self.assertEqual(decisions[0]["tier"], 0)
        self.assertEqual(decisions[0]["tier_name"], "flat")

    def test_textured_block_is_lattice_filled_not_queued(self):
        # Screentone passes the old white test (H2); the router must fill it
        # with the lattice tier instead of painting it flat or calling a model.
        clean = _dot_tone(80, 120)
        image = clean.copy()
        image[30:50, 45:75] = 0
        block = TextBlock([43, 28, 77, 52], translated_text="TEXT")

        cleaned, model_mask, filled_count, model_count, decisions = (
            prepare_flux_inpainting(image, [block])
        )

        self.assertEqual((filled_count, model_count), (1, 0))
        self.assertTrue(np.array_equal(cleaned, clean))
        self.assertEqual(decisions[0]["tier"], 2)

    def test_unverifiable_block_is_queued_for_the_model(self):
        # Irregular art crossing the whole block region: no tier can
        # reproduce it, so the block is queued and the page is untouched.
        rng = np.random.default_rng(5)
        image = rng.integers(0, 255, (80, 120, 3)).astype(np.uint8)
        block = TextBlock([43, 28, 77, 52], translated_text="TEXT")

        cleaned, model_mask, filled_count, model_count, decisions = (
            prepare_flux_inpainting(image, [block])
        )

        self.assertEqual((filled_count, model_count), (0, 1))
        self.assertTrue(np.any(model_mask))
        self.assertTrue(np.array_equal(cleaned, image))
        self.assertEqual(decisions[0]["tier"], TIER_MODEL)

    def test_rectangular_mode_queues_the_full_detection_rect(self):
        image = _flat((255, 255, 255), 100, 140)
        image[30:40, 40:70] = 0
        rng = np.random.default_rng(7)
        # Texture must extend past the noise block's rectangle: the router
        # verifies against what surrounds the mask, not what hides under it.
        image[55:100, 0:140] = rng.integers(0, 255, (45, 140, 3)).astype(np.uint8)
        image[70:80, 40:70] = 0
        white_block = TextBlock([38, 28, 72, 42], translated_text="TEXT")
        noise_block = TextBlock([38, 68, 72, 82], translated_text="ART")

        cleaned, model_mask, filled_count, model_count, decisions = (
            prepare_flux_inpainting(image, [white_block, noise_block], rectangular=True)
        )

        self.assertEqual((filled_count, model_count), (1, 1))
        tiers = [decision["tier"] for decision in decisions]
        self.assertIn(0, tiers)
        self.assertIn(TIER_MODEL, tiers)
        # The white rectangle is flat-filled; the noisy one is queued whole.
        self.assertTrue(np.all(cleaned[30:40, 40:70] == 255))
        self.assertTrue(np.any(model_mask[55:100, 20:110]))
        # Nothing changes outside the two blocks' rectangles.
        outside = np.ones(image.shape[:2], dtype=bool)
        outside[22:48, 30:80] = False  # white block's rectangle
        outside[47:83, 16:116] = False  # noise block's rectangle
        self.assertTrue(np.array_equal(cleaned[outside], image[outside]))

    def test_adjacent_blocks_do_not_fail_each_other(self):
        # Two lettering clusters side by side: each is erased using the other
        # as excluded ink, so both still verify against the shared background.
        image = _flat((255, 255, 255), 80, 140)
        image[25:45, 20:40] = 0
        image[25:45, 60:80] = 0
        blocks = [
            TextBlock([18, 23, 42, 47], translated_text="AA"),
            TextBlock([58, 23, 82, 47], translated_text="BB"),
        ]

        cleaned, model_mask, filled_count, model_count, decisions = (
            prepare_flux_inpainting(image, blocks)
        )

        self.assertEqual((filled_count, model_count), (2, 0))
        self.assertTrue(np.all(cleaned[25:45, 20:40] == 255))
        self.assertTrue(np.all(cleaned[25:45, 60:80] == 255))


if __name__ == "__main__":
    unittest.main()
