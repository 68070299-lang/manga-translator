"""Unit tests for the shape-aware typesetter: rooms, bands, DP, anchored sizes."""

from __future__ import annotations

import unittest

import cv2
import numpy as np

from manga_backend.pipeline.fit import fit_text_in_room, text_units
from manga_backend.pipeline.linebreak import break_lines
from manga_backend.pipeline.measure import (
    anchor_target,
    harmonize_targets,
    luminance,
    measure_block_colours,
    source_glyph_size,
)
from manga_backend.pipeline.rendering import build_rooms, render_text
from manga_backend.pipeline.rooms import (
    Band,
    Room,
    box_room,
    bubble_room,
    free_space_room,
    split_conjoined_room,
)
from manga_backend.utils.textblock import TextBlock


def oval_mask(width: int, height: int, *, inset: int = 0) -> np.ndarray:
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.ellipse(
        mask,
        (width // 2, height // 2),
        (width // 2 - inset, height // 2 - inset),
        0,
        0,
        360,
        255,
        -1,
    )
    return mask


def oval_room(width: int = 200, height: int = 120, **kwargs: object) -> Room:
    mask = oval_mask(width, height, **kwargs)  # type: ignore[arg-type]
    return Room(mask, (0, 0, width, height), (width / 2.0, height / 2.0), "bubble")


class RoomTests(unittest.TestCase):
    def test_oval_room_erodes_by_the_air_inset(self):
        room = oval_room()
        eroded = room.eroded(6)

        self.assertLess(eroded.area, room.area)
        self.assertEqual(eroded.air, 6)
        # Erosion composes: eroding again by the same inset changes nothing.
        self.assertEqual(eroded.eroded(6).area, eroded.area)
        self.assertLess(eroded.eroded(10).area, eroded.area)

    def test_bands_are_wider_in_the_middle_of_an_oval(self):
        room = oval_room()

        bands = room.bands(16, 3, 1.2)

        self.assertEqual(len(bands), 3)
        self.assertGreater(bands[1].width, bands[0].width)
        self.assertGreater(bands[1].width, bands[2].width)
        for band in bands:
            self.assertTrue(0 <= band.left < band.right <= 200)
            self.assertLess(band.top, band.bottom)

    def test_bands_shrink_with_the_line_count(self):
        room = oval_room()

        two = room.bands(16, 2, 1.2)
        four = room.bands(16, 4, 1.2)

        self.assertEqual(len(two), 2)
        self.assertEqual(len(four), 4)
        self.assertLess(min(band.width for band in four), min(band.width for band in two))

    def test_bands_return_nothing_when_the_lines_cannot_fit(self):
        room = oval_room(height=40)

        self.assertEqual(room.bands(30, 3, 1.2), [])

    def test_box_room_is_the_render_box(self):
        block = TextBlock([10, 20, 110, 70], translated_text="HELLO")

        room = box_room(block)

        self.assertEqual(room.bounds, (10, 20, 110, 70))
        self.assertEqual(room.kind, "box")
        self.assertEqual(room.center, (60.0, 45.0))
        self.assertEqual(room.area, 100 * 50 - 2 * (100 + 50) + 4)

    def test_bubble_room_fills_enclosed_holes(self):
        mask = oval_mask(80, 80)
        mask[30:40, 30:40] = 0  # a pinhole the lettering left behind

        room = bubble_room(mask, (0, 0, 80, 80))

        self.assertIsNotNone(room)
        assert room is not None
        self.assertEqual(room.area, int(np.count_nonzero(mask)) + 100)

    def test_free_space_room_grows_into_blank_paper_only(self):
        image = np.full((120, 200, 3), 255, dtype=np.uint8)
        # A black wall stops the room from running across the page.
        image[:, 160:] = 0
        block = TextBlock([70, 50, 110, 70], translated_text="HELLO")

        room = free_space_room(image, block, obstacles=[])

        self.assertIsNotNone(room)
        assert room is not None
        self.assertEqual(room.kind, "free")
        self.assertGreater(room.area, block.area)
        x1, y1, x2, y2 = room.bounds
        covered = np.zeros(image.shape[:2], dtype=bool)
        covered[y1:y2, x1:x2] = room.mask > 0
        self.assertFalse(covered[:, 162:].any())

    def test_free_space_room_stays_out_of_other_regions(self):
        image = np.full((120, 240, 3), 255, dtype=np.uint8)
        block = TextBlock([40, 50, 80, 70], translated_text="HELLO")
        neighbor = TextBlock([120, 50, 160, 70], translated_text="THERE")

        room = free_space_room(
            image, block, obstacles=[(neighbor.x1, neighbor.y1, neighbor.x2, neighbor.y2)]
        )

        self.assertIsNotNone(room)
        assert room is not None
        covered = np.zeros(image.shape[:2], dtype=bool)
        x1, y1, x2, y2 = room.bounds
        covered[y1:y2, x1:x2] = room.mask > 0
        self.assertFalse(covered[50:70, 120:160].any())

    def test_free_space_room_is_none_when_the_fill_left_no_blank_area(self):
        image = np.random.default_rng(3).integers(0, 255, (120, 120, 3), dtype=np.uint8)
        block = TextBlock([40, 40, 80, 80], translated_text="HELLO")

        self.assertIsNone(free_space_room(image, block, obstacles=[]))

    def test_conjoined_room_splits_into_one_lobe_per_seed(self):
        width, height = 260, 140
        mask = np.zeros((height, width), dtype=np.uint8)
        cv2.ellipse(mask, (80, 70), (65, 55), 0, 0, 360, 255, -1)
        cv2.ellipse(mask, (180, 70), (65, 55), 0, 0, 360, 255, -1)
        room = Room(mask, (0, 0, width, height), (130.0, 70.0), "bubble")

        lobes = split_conjoined_room(room, [(70.0, 70.0), (190.0, 70.0)])

        self.assertEqual(len(lobes), 2)
        left, right = lobes
        self.assertEqual(left.kind, "bubble-split")
        self.assertLess(left.center[0], right.center[0])
        self.assertLess(left.area, room.area)
        self.assertLess(right.area, room.area)
        self.assertGreater(left.area, room.area * 0.15)


class LineBreakTests(unittest.TestCase):
    @staticmethod
    def measure(value: str) -> float:
        return float(len(value))

    def test_even_mode_balances_lines(self):
        units = ["aaaa", "b", "cc"]

        broken = break_lines(
            units, [6.0, 6.0], self.measure, joiner=lambda parts: " ".join(parts), mode="even"
        )

        self.assertIsNotNone(broken)
        assert broken is not None
        self.assertEqual(broken.lines, ["aaaa", "b cc"])

    def test_ragged_mode_lets_the_last_line_run_short(self):
        units = ["aaaa", "b", "cc"]

        broken = break_lines(
            units, [6.0, 6.0], self.measure, joiner=lambda parts: " ".join(parts), mode="ragged"
        )

        self.assertIsNotNone(broken)
        assert broken is not None
        self.assertEqual(broken.lines, ["aaaa b", "cc"])

    def test_an_oversized_unit_is_split_by_characters(self):
        broken = break_lines(
            ["abcdefghijkl"], [4.0, 4.0, 4.0], self.measure, joiner=lambda parts: "".join(parts)
        )

        self.assertIsNotNone(broken)
        assert broken is not None
        self.assertEqual(broken.lines, ["abcd", "efgh", "ijkl"])
        for width in broken.widths:
            self.assertLessEqual(width, 4.0)

    def test_consecutive_mid_word_lines_are_capped(self):
        unit = "abcdefghijkl"

        capped = break_lines(
            [unit],
            [4.0, 4.0, 4.0],
            self.measure,
            joiner=lambda parts: "".join(parts),
            max_hyphen_run=1,
        )
        allowed = break_lines(
            [unit],
            [4.0, 4.0, 4.0],
            self.measure,
            joiner=lambda parts: "".join(parts),
            max_hyphen_run=2,
        )

        self.assertIsNone(capped)
        self.assertIsNotNone(allowed)

    def test_no_layout_when_the_text_needs_more_lines_than_the_bands(self):
        broken = break_lines(
            ["one", "two", "three", "four", "five"],
            [4.0],
            self.measure,
            joiner=lambda parts: " ".join(parts),
        )

        self.assertIsNone(broken)

    def test_em_dash_breaks_carry_no_space(self):
        units, joiner = text_units("NICE—LIVING")

        self.assertEqual(units, ["NICE—", "LIVING"])
        self.assertEqual(joiner(units), "NICE—LIVING")
        broken = break_lines(units, [7.0, 7.0], self.measure, joiner=joiner)
        self.assertIsNotNone(broken)
        assert broken is not None
        self.assertEqual(broken.lines, ["NICE—", "LIVING"])


class FitTests(unittest.TestCase):
    def test_fit_shrinks_to_its_room_and_reports_the_target(self):
        room = oval_room(160, 120)

        fit = fit_text_in_room(room, "REALLY WAIT HERE", None, target=28, line_spacing=1.2)

        self.assertLessEqual(fit.size, 28)
        self.assertEqual(fit.target, 28)
        self.assertGreater(fit.size, 0)
        self.assertFalse(fit.overflow)
        self.assertEqual(len(fit.lines), len(fit.bands))

    def test_fit_uses_the_whole_anchored_target_in_a_big_room(self):
        room = oval_room(400, 300)

        fit = fit_text_in_room(room, "HEY", None, target=40, line_spacing=1.2)

        self.assertEqual(fit.size, 40)

    def test_fit_never_returns_zero_for_non_empty_text(self):
        room = box_room(TextBlock([0, 0, 6, 6]))

        fit = fit_text_in_room(room, "UNREADABLE", None, target=16)

        self.assertGreater(fit.size, 0)

    def test_fixed_size_keeps_the_requested_size(self):
        room = oval_room(400, 300)

        fit = fit_text_in_room(room, "HEY", None, target=12, fixed_size=True)

        self.assertEqual(fit.size, 12)

    def test_vertical_fit_puts_one_character_per_line(self):
        room = oval_room(80, 300)

        fit = fit_text_in_room(room, "あいうえお", None, target=20, vertical=True)

        self.assertEqual(fit.lines, list("あいうえお"))
        self.assertGreater(fit.size, 0)


class AnchorTests(unittest.TestCase):
    def test_harmonize_snaps_close_targets_to_the_page_median(self):
        self.assertEqual(harmonize_targets([14, 15, 16, 30]), [16, 16, 16, 30])

    def test_harmonize_keeps_outliers(self):
        self.assertEqual(harmonize_targets([10, 10, 40]), [10, 10, 40])

    def test_harmonize_is_empty_safe(self):
        self.assertEqual(harmonize_targets([]), [])

    def test_anchor_uses_the_preferred_size_without_a_source_estimate(self):
        self.assertEqual(anchor_target(None, 16), 16)

    def test_source_glyph_size_reads_the_column_pitch(self):
        image = np.full((200, 80, 3), 255, dtype=np.uint8)
        for index in range(4):
            top = 20 + index * 24
            image[top : top + 20, 20:40] = 0
        block = TextBlock([20, 20, 40, 116], translated_text="")

        estimate = source_glyph_size(image, block)

        self.assertIsNotNone(estimate)
        assert estimate is not None
        self.assertGreaterEqual(estimate, 18.0)
        self.assertLessEqual(estimate, 28.0)

    def test_source_glyph_size_ignores_a_blank_box(self):
        image = np.full((100, 100, 3), 255, dtype=np.uint8)

        self.assertIsNone(source_glyph_size(image, TextBlock([10, 10, 60, 60])))


class ColourTests(unittest.TestCase):
    def test_light_on_dark_gets_light_lettering(self):
        image = np.full((120, 120, 3), 24, dtype=np.uint8)
        room = box_room(TextBlock([40, 40, 80, 80]))

        text, border, background = measure_block_colours(
            image, room, (0, 0, 0), (255, 255, 255), (255, 255, 255)
        )

        self.assertGreater(luminance(text), 128)
        self.assertLess(luminance(border), 60)
        self.assertLess(luminance(background), 60)

    def test_light_paper_keeps_the_user_colours(self):
        image = np.full((120, 120, 3), 255, dtype=np.uint8)
        room = box_room(TextBlock([40, 40, 80, 80]))

        text, border, background = measure_block_colours(
            image, room, (10, 20, 30), (200, 210, 220), (255, 255, 255)
        )

        self.assertEqual(text, (10, 20, 30))
        self.assertEqual(border, (200, 210, 220))
        self.assertEqual(background, (255, 255, 255))


class CeilingTests(unittest.TestCase):
    """The old 16 px preference was a hard ceiling; it is gone."""

    def _page(self) -> tuple[np.ndarray, TextBlock]:
        image = np.full((360, 360, 3), 255, dtype=np.uint8)
        cv2.ellipse(image, (180, 180), (150, 150), 0, 0, 360, (0, 0, 0), 3)
        # Source lettering at a 30 px em: four squares on a 30 px pitch.
        for index in range(4):
            top = 100 + index * 30
            image[top : top + 24, 168:192] = 0
        block = TextBlock([160, 90, 210, 230], text="ああああ", translated_text="HEY")
        block.bubble_xyxy = [30, 30, 330, 330]
        block.bubble_text_center = [180.0, 180.0]
        block.render_from_bubble = True
        return image, block

    def test_a_big_bubble_is_lettered_above_sixteen_pixels(self):
        image, block = self._page()

        rendered = render_text(image.copy(), [block], bg_opacity=0)

        changed = np.any(rendered != image, axis=2)
        _ys, xs = np.where(changed)
        self.assertGreater(len(xs), 0)
        height = int(_ys.max() - _ys.min() + 1)
        # A 16 px fit would leave a ~12 px tall cap height; 30 px is ~22 px.
        self.assertGreater(height, 18)

    def test_the_anchored_target_of_a_big_bubble_exceeds_sixteen(self):
        image, block = self._page()
        regions = [block]

        rooms = build_rooms(image, regions, regions)
        estimate = source_glyph_size(image, block)

        self.assertIsNotNone(estimate)
        assert estimate is not None
        self.assertGreater(anchor_target(estimate, 16), 16)
        self.assertEqual(rooms[0].kind, "bubble")


if __name__ == "__main__":
    unittest.main()
