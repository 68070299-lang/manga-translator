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

    def test_faint_fringe_on_flat_paper_leaves_no_ghost(self):
        # Resampled lettering fades out over a few pixels; a ring only a few
        # levels below white is invisible next to the glyph but traces it
        # once the core is painted over, so on flat paper it is covered too.
        glyph = np.zeros((60, 60), bool)
        glyph[20:40, 20:40] = True
        distance = cv2.distanceTransform((~glyph).astype(np.uint8), cv2.DIST_L2, 3)
        # 6-16 levels below white: well under the 10% alpha the near ring
        # needs, yet a visible outline of the glyph once the glyph is gone.
        gray = np.full(glyph.shape, 255.0, np.float32)
        gray[glyph] = 0.0
        gray[(distance > 0) & (distance <= 2.5)] = 239.0
        gray[(distance > 2.5) & (distance <= 4.0)] = 249.0
        roi = np.repeat(np.rint(gray).astype(np.uint8)[:, :, None], 3, axis=2)
        paper = masks._Paper(100.0, 0.0, 0.0, np.full(3, 255.0, np.float32))

        covered = masks._cover_halo(roi, glyph, paper, [np.zeros(3, np.float32)])

        ghost = (gray < 255) & ~covered
        self.assertEqual(int(np.count_nonzero(ghost)), 0, "visible fringe left outside the mask")
        self.assertFalse(covered[5, 5], "the paper far from the glyph must stay unmasked")

    def test_faint_fringe_is_not_taken_from_textured_surroundings(self):
        # On tone the same few levels are texture, not antialiasing.
        glyph = np.zeros((60, 60), bool)
        glyph[20:40, 20:40] = True
        yy, xx = np.mgrid[0:60, 0:60]
        tone = np.where((yy % 4 < 2) ^ (xx % 4 < 2), 200.0, 255.0).astype(np.float32)
        tone[glyph] = 0.0
        roi = np.repeat(np.rint(tone).astype(np.uint8)[:, :, None], 3, axis=2)
        paper = masks._Paper(90.0, 0.0, 0.0, np.full(3, 228.0, np.float32))

        covered = masks._cover_halo(roi, glyph, paper, [np.zeros(3, np.float32)])

        # Only the flat-paper rule reaches past the 3 px near ring.
        near = cv2.dilate(glyph.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
        self.assertEqual(int(np.count_nonzero(covered & ~near)), 0)

    def test_outline_clipped_by_the_text_box_is_not_masked(self):
        # A bubble outline cutting through the corners of a text box is not
        # lettering: it continues far outside the crop. Erasing its clipped
        # ends notched the outline wherever no interior clipped the mask.
        image = np.full((200, 220, 3), 255, np.uint8)
        cv2.circle(image, (110, 100), 85, (0, 0, 0), 5)
        image[60:140, 95:105] = 0
        image[90:100, 80:120] = 0
        block = TextBlock([40, 40, 140, 160])
        local, bounds = masks.glyph_mask_for_block(image, block)
        mask = _place(local, bounds, image.shape)

        ys, xs = np.mgrid[0:200, 0:220]
        radius = np.hypot(xs - 110, ys - 100)
        outline = (np.abs(radius - 85) <= 3) & np.all(image == 0, axis=2)
        self.assertTrue(mask[100, 100], "the glyph must stay in the mask")
        self.assertEqual(int(np.count_nonzero(mask & outline)), 0, "outline pixels were masked")

    def test_hollow_lettering_is_masked_whole(self):
        # Outlined lettering is extracted as its outline; the enclosed letter
        # body joins the mask so erasing it leaves no letter-shaped island.
        image = np.full((120, 160, 3), 255, np.uint8)
        for left in (40, 70, 100):  # three hollow "letters" in one SFX
            image[45:75, left : left + 22] = 0
            image[50:70, left + 5 : left + 17] = 255
        block = TextBlock([34, 38, 128, 82])
        local, bounds = masks.glyph_mask_for_block(image, block)
        mask = _place(local, bounds, image.shape)

        self.assertTrue(mask[47, 51], "the outline must be masked")
        for left in (40, 70, 100):
            self.assertTrue(mask[60, left + 11], "the hollow body must be masked")
        self.assertFalse(mask[10, 10], "the paper far from the letters must stay unmasked")

    def test_large_enclosed_regions_are_not_hole_filled(self):
        ring = np.zeros((100, 100), bool)
        ring[10:90, 10:90] = True
        ring[14:86, 14:86] = False
        small = np.zeros((100, 100), bool)
        small[40:60, 40:60] = True
        small[44:56, 44:56] = False

        self.assertFalse(masks._fill_glyph_holes(ring)[50, 50])
        self.assertTrue(masks._fill_glyph_holes(small)[50, 50])

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

    def test_flood_retry_seals_a_speed_line_flash_balloon(self):
        # A flash balloon is walled only by radial speed lines whose inner
        # tips leave ~4 px gaps; the single 3x3 close leaks through them, so
        # the retry with one more pass must find the interior.
        image = np.full((220, 280, 3), 255, np.uint8)
        centre_x, centre_y, radius_x, radius_y = 140, 110, 90, 60
        for angle in np.linspace(0.0, 2.0 * np.pi, 100, endpoint=False):
            inner = (
                int(round(centre_x + radius_x * np.cos(angle))),
                int(round(centre_y + radius_y * np.sin(angle))),
            )
            outer = (
                int(round(centre_x + 400 * np.cos(angle))),
                int(round(centre_y + 400 * np.sin(angle))),
            )
            cv2.line(image, inner, outer, (0, 0, 0), 1)
        image[100:120, 126:154] = 0
        block = TextBlock([124, 98, 156, 122])
        block.bubble_xyxy = [40, 40, 240, 180]
        result = masks.bubble_interior_details(image, block)

        self.assertTrue(result.found, f"flash balloon interior not found ({result.reason})")
        interior = _place(result.interior, result.bounds, image.shape)
        self.assertTrue(interior[80, 140], "the balloon centre must be interior")
        self.assertFalse(interior[45, 45], "the speed lines outside must stay outside")

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

    def test_outside_in_flood_protects_pinched_bubble_walls_when_haze_touches_wall(self):
        # Reproduce the [d01] topology: narrow diamond bubble whose left/right
        # walls sit inside the text box while gray motion-blur haze and thick
        # bottom lettering touch the inner wall surface.
        image = np.full((260, 180, 3), 255, np.uint8)
        # Vertical speed lines outside the diamond bubble.
        for x in range(10, 170, 8):
            image[:, x : x + 2] = 60
        diamond = np.array([[90, 20], [150, 90], [128, 150], [150, 210], [90, 240], [30, 210], [52, 150], [30, 90]], np.int32)
        cv2.fillPoly(image, [diamond], (255, 255, 255))
        cv2.polylines(image, [diamond], True, (0, 0, 0), 4)
        # Dark strokes + motion-blur gray bridging toward the walls & touching bottom waist.
        image[55:215, 78:102] = 0
        image[120:180, 55:125] = 175
        image[125:175, 80:100] = 0

        block = TextBlock([48, 50, 132, 222], block_type="text")
        block.bubble_xyxy = [28, 18, 152, 242]
        result = masks.bubble_interior_details(image, block)

        self.assertTrue(result.found, f"expected interior, got {result.reason}")
        self.assertEqual(result.method, "flood")
        _local, _bounds, strategy = masks.contour_safe_mask_for_block(image, block)
        self.assertEqual(strategy, "bubble-clipped-glyph")

        cleaned, model_mask, filled_count, model_count, decisions = masks.prepare_flux_inpainting(
            image, [block]
        )
        self.assertEqual(filled_count, 1)
        self.assertEqual(model_count, 0)
        self.assertEqual(decisions[0]["tier_name"], "flat")
        # Both left/right pinched waist walls at y=150 (x~52, x~128) must stay black.
        self.assertLess(int(cleaned[150, 52].mean()), 30)
        self.assertLess(int(cleaned[150, 128].mean()), 30)
        self.assertFalse(np.any(model_mask))

    def test_neural_seg_map_guides_glyph_mask_and_falls_back_when_sparse(self):
        image = np.full((120, 120, 3), 255, np.uint8)
        # Draw dark ink in block plus an unrelated grey bar outside neural seg.
        image[30:90, 45:75] = 0
        image[22:26, 20:100] = 0
        block = TextBlock([18, 18, 102, 95], block_type="text")

        seg_map = np.zeros((120, 120), dtype=np.float32)
        seg_map[30:90, 45:75] = 0.85

        with masks.image_analysis_cache(image, text_seg_map=seg_map):
            local, bounds = masks.glyph_mask_for_block(image, block)
        mask = _place(local, bounds, image.shape)
        self.assertTrue(mask[60, 60])
        self.assertFalse(mask[24, 30], "detached non-seg bar must remain unmasked")

        # Sparse neural seg on thick dark ink must fall back to classical extractor.
        sparse_seg = np.zeros((120, 120), dtype=np.float32)
        sparse_seg[82:88, 55:65] = 0.90
        with masks.image_analysis_cache(image, text_seg_map=sparse_seg):
            fb_local, fb_bounds = masks.glyph_mask_for_block(image, block)
        fb_mask = _place(fb_local, fb_bounds, image.shape)
        self.assertTrue(fb_mask[40, 60], "upper dark ink must still be masked via fallback")

    def test_bubbled_block_preserves_classical_ink_when_neural_seg_misses_stroke(self):
        image = np.full((180, 180, 3), 255, np.uint8)
        cv2.rectangle(image, (20, 20), (160, 160), (0, 0, 0), 3)
        # Two solid black strokes inside the bubble: upper covered by seg, lower missed.
        image[50:80, 70:110] = 0
        image[100:130, 70:110] = 0
        block = TextBlock([45, 45, 135, 140], block_type="text")
        block.bubble_xyxy = [20, 20, 160, 160]

        seg_map = np.zeros((180, 180), dtype=np.float32)
        seg_map[50:80, 70:110] = 0.90  # misses [100:130, 70:110]

        with masks.image_analysis_cache(image, text_seg_map=seg_map):
            local, bounds, strategy = masks.contour_safe_mask_for_block(image, block)
        mask = _place(local, bounds, image.shape)
        self.assertEqual(strategy, "bubble-clipped-glyph")
        self.assertTrue(mask[65, 90])
        self.assertTrue(mask[115, 90], "missed dark stroke inside bubble must stay masked")

    def test_unboxed_sfx_is_not_clipped_by_accidental_background_pocket(self):
        image = np.full((160, 160, 3), 255, np.uint8)
        # Closed border pocket covering only the bottom half of an unboxed SFX.
        cv2.rectangle(image, (40, 80), (120, 145), (0, 0, 0), 3)
        image[30:70, 65:95] = 0    # top half outside pocket
        image[92:130, 65:95] = 0   # bottom half inside pocket
        block = TextBlock([55, 25, 105, 135], block_type="sfx")

        local, bounds, strategy = masks.contour_safe_mask_for_block(image, block)
        mask = _place(local, bounds, image.shape)
        self.assertEqual(strategy, "glyph")
        self.assertTrue(mask[50, 80], "unboxed SFX outside accidental pocket must stay masked")
        self.assertTrue(mask[110, 80])
