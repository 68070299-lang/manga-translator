"""Unit tests for the ink-structure masks and the seeded bubble interiors (WP5).

Each test builds a small page whose ink is known: a black SFX core inside a
white outline on a hatched page (H1), a dot lattice behind a glyph (H2), a
thin rule crossing a crop, a closed bubble whose interior the flood fill has
to find, and a bubble with a hairline gap the gap close has to seal.
"""

from __future__ import annotations

import unittest

import cv2
import numpy as np

from manga_backend.pipeline import masks
from manga_backend.utils.textblock import TextBlock


def _place(
    local: np.ndarray, bounds: masks.Box, shape: tuple[int, ...]
) -> np.ndarray:
    """Put a block-local mask on the page for assertions."""
    canvas = np.zeros(shape[:2], bool)
    x1, y1, x2, y2 = bounds
    if local.size:
        canvas[y1:y2, x1:x2] = local > 0
    return canvas


def _glyph_page() -> np.ndarray:
    """White page with a black cross whose strokes are glyph-thick."""
    image = np.full((140, 180, 3), 255, np.uint8)
    image[50:90, 80:96] = 0
    image[62:78, 60:116] = 0
    return image


def _sfx_outline_page() -> np.ndarray:
    """Grey hatched page with a black SFX core inside a white outline (H1)."""
    image = np.full((140, 200, 3), 225, np.uint8)
    ys, xs = np.mgrid[0:140, 0:200]
    image[(xs + ys) % 10 < 2] = 120
    core = np.zeros(image.shape[:2], bool)
    core[56:88, 84:96] = True
    core[64:76, 64:116] = True
    ring = (
        cv2.dilate(core.astype(np.uint8), np.ones((11, 11), np.uint8)).astype(bool)
        & ~core
    )
    image[ring] = 255
    image[core] = 0
    return image


def _tone_page() -> np.ndarray:
    """White page with a mid-grey dot lattice behind a black glyph."""
    image = np.full((140, 180, 3), 255, np.uint8)
    ys, xs = np.mgrid[0:140, 0:180]
    dy = (ys % 7) - 3.5
    dx = (xs % 7) - 3.5
    image[dx * dx + dy * dy <= 1.6 * 1.6] = 110
    image[40:70, 40:60] = 0
    image[48:62, 30:70] = 0
    return image


def _bubble_page(
    gap: int = 0, block_type: str = "text"
) -> tuple[np.ndarray, TextBlock]:
    """Grey page with a white bubble whose outline is 3 px wide."""
    image = np.full((220, 280, 3), 150, np.uint8)
    cv2.ellipse(image, (140, 110), (100, 70), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(image, (140, 110), (100, 70), 0, 0, 360, (0, 0, 0), 3)
    if gap:
        image[38 : 38 + gap, 138:142] = 255
    image[100:120, 126:154] = 0
    block = TextBlock([124, 98, 156, 122], block_type=block_type)
    block.bubble_xyxy = [38, 38, 242, 182]
    return image, block


class GlyphMaskTests(unittest.TestCase):
    def test_dark_core_and_bright_outline_are_both_masked(self):
        image = _sfx_outline_page()
        block = TextBlock([58, 50, 122, 94], block_type="sfx")
        local, bounds = masks.glyph_mask_for_block(image, block)
        mask = _place(local, bounds, image.shape)

        self.assertTrue(mask[70, 90], "the black SFX core must be masked")
        self.assertTrue(mask[52, 90], "the white outline above the core must be masked (H1)")
        self.assertTrue(mask[92, 90], "the white outline below the core must be masked (H1)")
        self.assertFalse(mask[10, 10], "the hatched page far from the SFX must stay unmasked")

    def test_tone_dots_on_the_period_lattice_are_rejected(self):
        image = _tone_page()
        block = TextBlock([20, 20, 100, 90])
        local, bounds = masks.glyph_mask_for_block(image, block)
        mask = _place(local, bounds, image.shape)

        glyph_pixels = np.all(image == 0, axis=2)
        dot_pixels = np.all(image == 110, axis=2)
        glyph_px = int(np.count_nonzero(glyph_pixels))
        self.assertTrue(mask[55, 40], "the glyph must stay in the mask")
        # The old threshold swallowed the whole lattice. Now only the dots
        # that touch the glyph (merged ink) or sit inside its halo may stay;
        # the rest of the tone must leave the mask (H2).
        masked_dots = int(np.count_nonzero(mask & dot_pixels))
        self.assertLessEqual(masked_dots, 0.15 * int(np.count_nonzero(dot_pixels)))
        self.assertFalse(
            mask[25, 85], "tone far from the glyph must not be masked"
        )
        self.assertLess(int(np.count_nonzero(mask)), glyph_px * 3.0)

    def test_line_art_crossing_the_crop_is_not_masked(self):
        image = _glyph_page()
        image[20:23, :] = 0
        block = TextBlock([70, 45, 110, 95])
        local, bounds = masks.glyph_mask_for_block(image, block)
        mask = _place(local, bounds, image.shape)

        self.assertTrue(mask[70, 85], "the glyph must stay in the mask")
        self.assertFalse(mask[21, 85], "the thin rule crossing the crop must not be masked")

    def test_antialiased_halo_is_covered_without_a_fixed_dilation(self):
        image = np.full((100, 120, 3), 255, np.uint8)
        core = np.zeros(image.shape[:2], bool)
        core[40:70, 55:63] = True
        core[50:58, 40:78] = True
        fringe = cv2.dilate(core.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & ~core
        image[fringe] = 128  # a 50 % coverage antialiased edge around the core
        image[core] = 0
        block = TextBlock([38, 38, 80, 72])
        local, bounds = masks.glyph_mask_for_block(image, block)
        mask = _place(local, bounds, image.shape)

        self.assertTrue(mask[55, 59], "the core must be masked")
        self.assertTrue(mask[55, 39], "the half-covered fringe must be masked")
        self.assertFalse(mask[20, 59], "the paper beyond the halo must stay unmasked")

    def test_glyph_mask_is_deterministic(self):
        image = _tone_page()
        block = TextBlock([20, 20, 100, 90])
        first_local, first_bounds = masks.glyph_mask_for_block(image, block)
        second_local, second_bounds = masks.glyph_mask_for_block(image, block)
        np.testing.assert_array_equal(
            _place(first_local, first_bounds, image.shape),
            _place(second_local, second_bounds, image.shape),
        )


class BubbleInteriorTests(unittest.TestCase):
    def test_flood_finds_a_closed_bubble_interior(self):
        image, block = _bubble_page()
        result = masks.bubble_interior_details(image, block)

        self.assertEqual(result.method, "flood")
        self.assertTrue(result.found)
        interior = _place(result.interior, result.bounds, image.shape)
        self.assertTrue(interior[110, 140], "the bubble centre must be interior")
        self.assertFalse(interior[110, 40], "the bubble outline must stay outside")
        self.assertFalse(interior[5, 5], "the panel outside the bubble must stay outside")

    def test_flood_seals_a_hairline_gap_in_the_outline(self):
        image, block = _bubble_page(gap=1)
        result = masks.bubble_interior_details(image, block)

        self.assertTrue(result.found, "a one-pixel gap must be closed before the flood")
        interior = _place(result.interior, result.bounds, image.shape)
        self.assertFalse(interior[5, 5], "the fill must not leak out of the bubble")

    def test_flood_rejects_a_leak_and_reports_it(self):
        image = np.full((160, 200, 3), 255, np.uint8)
        image[60:80, 60:140] = 0
        block = TextBlock([58, 58, 142, 82])
        result = masks.bubble_interior_details(image, block)

        self.assertFalse(result.found, "an open background must not become an interior")
        self.assertEqual(result.reason, masks.INTERIOR_LEAK)

    def test_text_free_blocks_try_the_bubble_interior_too(self):
        image, block = _bubble_page(block_type="sfx")
        local, bounds, strategy = masks.contour_safe_mask_for_block(image, block)
        mask = _place(local, bounds, image.shape)

        self.assertEqual(strategy, "bubble-clipped-glyph")
        self.assertTrue(mask[110, 140], "the lettering must stay masked")
        self.assertFalse(mask[110, 40], "the outline must not enter a clipped mask")

    def test_reason_codes_name_the_contour_rules_of_the_fallback(self):
        # A flat white crop has no contour at all.
        blank = np.full((120, 120, 3), 255, np.uint8)
        self.assertEqual(
            masks._contour_interior(blank, (40, 40, 80, 80))[1],
            masks.INTERIOR_NO_CONTEXT,
        )
        # A ring hugging the text box is under the area floor.
        hug = np.full((260, 260, 3), 255, np.uint8)
        hug[56:66, 56:204] = 0
        hug[194:204, 56:204] = 0
        hug[56:204, 56:66] = 0
        hug[56:204, 194:204] = 0
        self.assertEqual(
            masks._contour_interior(hug, (60, 60, 200, 200))[1],
            masks.INTERIOR_CONTOUR_AREA,
        )

    def test_image_analysis_cache_deduplicates_and_snapshots_interiors(self):
        from unittest.mock import patch

        image, block = _bubble_page()
        with patch.object(
            masks,
            "_compute_bubble_interior_details",
            wraps=masks._compute_bubble_interior_details,
        ) as spy_interior, patch.object(
            masks,
            "_compute_glyph_mask_for_block",
            wraps=masks._compute_glyph_mask_for_block,
        ) as spy_glyph:
            with masks.image_analysis_cache(image):
                first = masks.bubble_interior_details(image, block)
                second = masks.bubble_interior_details(image, block)
                masks.glyph_mask_for_block(image, block)
                cached = masks.snapshot_cached_interiors(image)

            self.assertEqual(spy_interior.call_count, 1)
            self.assertEqual(spy_glyph.call_count, 1)
            self.assertIs(first, second)

            rendered = image.copy()
            with masks.image_analysis_cache(rendered, precomputed_interiors=cached):
                reused = masks.bubble_interior_details(rendered, block)
            self.assertEqual(spy_interior.call_count, 1)
            self.assertIs(reused, first)
