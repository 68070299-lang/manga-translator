"""Unit tests for the shape-aware typesetter: rooms, bands, DP, anchored sizes."""

from __future__ import annotations

import unittest

import cv2
import numpy as np

from manga_backend.pipeline.fit import fit_text_in_room, text_units
from manga_backend.pipeline.linebreak import break_lines, split_at_proper_breaks
from manga_backend.pipeline.measure import (
    anchor_target,
    luminance,
    measure_block_colours,
    page_lettering_size,
    source_glyph_size,
)
from manga_backend.pipeline.masks import image_analysis_cache, snapshot_cached_interiors
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


def _lettering(image: np.ndarray, box: tuple[int, int, int, int]) -> None:
    """Columns of small crosses standing in for vertical lettering."""
    x1, y1, x2, y2 = box
    for x in range(x1 + 4, x2 - 8, 14):
        for y in range(y1 + 4, y2 - 8, 13):
            image[y + 3 : y + 6, x : x + 9] = 0
            image[y : y + 9, x + 3 : x + 6] = 0


def _lobed_page(
    shape: np.ndarray,
    texts: dict[str, tuple[int, int, int, int]],
    bubbles: dict[str, tuple[int, int, int, int]],
) -> tuple[np.ndarray, list[TextBlock], list[TextBlock]]:
    """A page with ``shape``'s outline and one block and region per text box.

    Blocks and regions follow the order of ``texts``; each region spans its
    bubble box (inset), as the renderer's bubble regions do.
    """
    image = np.full((*shape.shape, 3), 255, np.uint8)
    contours, _hierarchy = cv2.findContours(shape, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cv2.drawContours(image, contours, -1, (0, 0, 0), 2)
    blocks: list[TextBlock] = []
    regions: list[TextBlock] = []
    for name, text_box in texts.items():
        _lettering(image, text_box)
        block = TextBlock(list(text_box), block_type="text", translated_text=name)
        block.bubble_xyxy = list(bubbles[name])
        x1, y1, x2, y2 = text_box
        block.bubble_text_center = [(x1 + x2) / 2.0, (y1 + y2) / 2.0]
        blocks.append(block)
        bx1, by1, bx2, by2 = bubbles[name]
        region = TextBlock([bx1 + 4, by1 + 4, bx2 - 4, by2 - 4], translated_text=name)
        region.render_from_bubble = True
        region.bubble_xyxy = list(bubbles[name])
        region.bubble_text_center = list(block.bubble_text_center)
        regions.append(region)
    return image, blocks, regions


def _cloud_page() -> tuple[np.ndarray, np.ndarray, list[TextBlock], list[TextBlock]]:
    """A two-lobe cloud detected as one bubble box per lobe, plus an SFX.

    Each lobe's box cuts the shared interior open, so neither lobe finds an
    interior on its own; the SFX box touches the left lobe's box.
    """
    cloud = np.zeros((220, 320), np.uint8)
    cv2.ellipse(cloud, (115, 110), (80, 70), 0, 0, 360, 255, -1)
    cv2.ellipse(cloud, (225, 105), (65, 60), 0, 0, 360, 255, -1)
    image, blocks, regions = _lobed_page(
        cloud,
        # The SFX first, as page order may put it.
        {"sfx": (2, 120, 30, 200), "left": (80, 62, 150, 158), "right": (205, 66, 250, 146)},
        {"sfx": (0, 112, 34, 210), "left": (35, 40, 195, 180), "right": (160, 45, 290, 165)},
    )
    return image, cloud > 0, blocks, regions


def _bump_shapes() -> tuple[np.ndarray, np.ndarray]:
    """A big balloon with a small one (about a ninth of the whole) on its shoulder."""
    body = np.zeros((260, 240), np.uint8)
    cv2.ellipse(body, (100, 150), (70, 95), 0, 0, 360, 255, -1)
    bump = np.zeros_like(body)
    cv2.circle(bump, (160, 62), 30, 255, -1)
    return body > 0, bump > 0


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

    def test_a_bump_balloon_is_cut_at_its_neck(self):
        body, bump = _bump_shapes()
        shape = (body | bump).astype(np.uint8) * 255
        room = Room(shape, (0, 0, 240, 260), (110.0, 140.0), "bubble")

        small, big = split_conjoined_room(room, [(160.0, 62.0), (100.0, 155.0)])

        # Midway between the seeds lies inside the big body; the neck does not.
        x1, y1, x2, y2 = small.bounds
        self.assertFalse(((small.mask > 0) & ~bump[y1:y2, x1:x2]).any())
        x1, y1, x2, y2 = big.bounds
        self.assertFalse(((big.mask > 0) & ~body[y1:y2, x1:x2]).any())
        self.assertGreater(small.area, np.count_nonzero(bump & ~body) * 0.8)

    def test_a_bump_balloon_gets_its_own_lobe_room(self):
        body, bump = _bump_shapes()
        image, blocks, regions = _lobed_page(
            (body | bump).astype(np.uint8) * 255,
            {"bump": (148, 50, 172, 74), "body": (70, 110, 130, 200)},
            {"bump": (128, 30, 192, 94), "body": (28, 53, 172, 247)},
        )

        small, big = build_rooms(image, regions, blocks)

        # The bump is under the old 15% floor for a lobe, yet it is one.
        for room, lobe in ((small, bump), (big, body)):
            self.assertEqual(room.kind, "bubble-split")
            x1, y1, x2, y2 = room.bounds
            self.assertFalse(((room.mask > 0) & ~lobe[y1:y2, x1:x2]).any())

    def test_cloud_lobes_share_one_interior_split_per_lobe(self):
        image, cloud, blocks, regions = _cloud_page()

        rooms = build_rooms(image, regions, blocks)

        _sfx, left, right = rooms
        for room in (left, right):
            self.assertEqual(room.kind, "bubble-split")
            x1, y1, x2, y2 = room.bounds
            inside = cloud[y1:y2, x1:x2]
            # The text stays off the outline: no room pixel outside the cloud.
            self.assertFalse(((room.mask > 0) & ~inside).any())
        self.assertLess(left.center[0], right.center[0])
        self.assertNotEqual(rooms[0].kind, "bubble-split")

    def test_cloud_interior_is_cached_for_the_render_pass(self):
        # The renderer floods the erased page, where a thin outline can lose
        # its contrast; it must reuse the interior found on the original.
        image, _cloud, blocks, regions = _cloud_page()

        with image_analysis_cache(image):
            build_rooms(image, regions, blocks)
            cached = snapshot_cached_interiors(image)

        conjoined = [value for key, value in cached.items() if key[2].startswith("conjoined:")]
        self.assertTrue(any(result.found for result in conjoined))


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

    def test_an_oversized_unit_is_split_by_characters_with_a_hyphen(self):
        broken = break_lines(
            ["abcdefghijkl"], [5.0, 5.0, 5.0], self.measure, joiner=lambda parts: "".join(parts)
        )

        self.assertIsNotNone(broken)
        assert broken is not None
        self.assertEqual("".join(broken.lines).replace("-", ""), "abcdefghijkl")
        self.assertEqual(broken.hyphenated, [False, True, True])
        for line, width in zip(broken.lines, broken.widths):
            self.assertLessEqual(width, 5.0)
        self.assertEqual(broken.lines[-1], "hijkl")

    def test_consecutive_mid_word_lines_are_capped(self):
        unit = "abcdefghijkl"

        capped = break_lines(
            [unit],
            [5.0, 5.0, 5.0],
            self.measure,
            joiner=lambda parts: "".join(parts),
            max_hyphen_run=1,
        )
        allowed = break_lines(
            [unit],
            [5.0, 5.0, 5.0],
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

    def test_ascii_hyphen_breaks_carry_no_space_and_no_extra_hyphen(self):
        units, joiner = text_units("ALL-YOU-CAN-EAT buffet")

        self.assertEqual(units, ["ALL-", "YOU-", "CAN-", "EAT", "buffet"])
        self.assertEqual(joiner(units), "ALL-YOU-CAN-EAT buffet")
        broken = break_lines(units, [8.0, 8.0, 8.0], self.measure, joiner=joiner)
        self.assertIsNotNone(broken)
        assert broken is not None
        self.assertEqual("".join(broken.lines), "ALL-YOU-CAN-EATbuffet")
        for line in broken.lines:
            self.assertNotIn("--", line)
            self.assertFalse(line.startswith("-"))

    def test_strict_hyphenation_never_splits_short_words(self):
        for word in ("Ah!", "Whoa!", "Home...", "*Pant,", "pant*"):
            with self.subTest(word=word):
                self.assertIsNone(split_at_proper_breaks(word, 2.0, self.measure))
                broken = break_lines(
                    [word], [2.0, 2.0, 2.0], self.measure, strict_hyphenation=True
                )
                self.assertIsNone(broken)

    def test_short_words_can_stay_whole_without_strict_hyphenation(self):
        for word in ("Whoa!", "That.", "Come."):
            with self.subTest(word=word):
                cut = break_lines([word], [3.0, 3.0], self.measure)
                self.assertIsNotNone(cut)
                assert cut is not None
                self.assertEqual(cut.hyphenated, [False, True])
                kept = break_lines([word], [3.0, 3.0], self.measure, keep_short_words=True)
                self.assertIsNone(kept)
        # A word long enough to hyphenate is still cut wherever it must be.
        broken = break_lines(
            ["abcdefghijkl"],
            [5.0, 5.0, 5.0],
            self.measure,
            joiner=lambda parts: "".join(parts),
            keep_short_words=True,
        )
        self.assertIsNotNone(broken)

    def test_strict_hyphenation_uses_a_proper_break(self):
        self.assertEqual(split_at_proper_breaks("Hurry", 4.0, self.measure), ["Hur", "ry"])
        self.assertEqual(split_at_proper_breaks("listen", 4.0, self.measure), ["lis", "ten"])
        # One-syllable words have no break: no vowel ("sh-ould") or a silent
        # final e ("Co-me.", "plea-se!") on one side.
        for word in ("should!!", "Come.", "please!"):
            with self.subTest(word=word):
                self.assertIsNone(split_at_proper_breaks(word, 4.0, self.measure))
        # Never between a letter and punctuation, never inside a vowel pair.
        chunks = split_at_proper_breaks("Hooray!", 5.0, self.measure)
        self.assertIsNotNone(chunks)
        assert chunks is not None
        self.assertEqual("".join(chunks), "Hooray!")
        for left, right in zip(chunks, chunks[1:]):
            self.assertTrue(left[-1].isalpha() and right[0].isalpha())
            self.assertFalse(left[-1] in "aeiouyAEIOUY" and right[0] in "aeiouyAEIOUY")

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
    def test_page_lettering_size_is_the_median_anchor(self):
        self.assertEqual(page_lettering_size([26, 20, 26, 26], 16), 26)
        self.assertEqual(page_lettering_size([14, 15, 16, 30], 16), 16)

    def test_page_lettering_size_falls_back_to_the_preference(self):
        self.assertEqual(page_lettering_size([], 16), 16)
        self.assertEqual(page_lettering_size([0, 0], 18), 18)

    def test_page_lettering_size_is_never_zero(self):
        self.assertEqual(page_lettering_size([0], 0), 1)

    def test_anchor_uses_the_preferred_size_without_a_source_estimate(self):
        self.assertEqual(anchor_target(None, 16), 16)

    def test_anchor_scales_the_source_em(self):
        self.assertEqual(anchor_target(28.0, 16), 28)

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
        # The mask stage measures the source lettering on the original page.
        block.source_font_size = 30.0
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

    def test_render_text_defaults_to_transparent_background(self):
        image = np.full((120, 160, 3), 180, dtype=np.uint8)
        block = TextBlock([20, 20, 140, 100], translated_text="HI")

        rendered = render_text(image.copy(), [block])

        # Corner inside the region box stays at the original gray tone (no white sticker).
        self.assertEqual(tuple(int(v) for v in rendered[22, 22]), (180, 180, 180))

    def test_explicit_bg_opacity_clips_to_bubble_room_shape(self):
        image = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.ellipse(image, (100, 100), (70, 70), 0, 0, 360, (255, 255, 255), -1)
        cv2.ellipse(image, (100, 100), (70, 70), 0, 0, 360, (0, 0, 0), 3)
        block = TextBlock([55, 70, 145, 130], translated_text="HI")
        block.bubble_xyxy = [28, 28, 172, 172]

        rendered = render_text(
            image.copy(), [block], bg_color="#ff0000", bg_opacity=1.0
        )

        # Corner of bubble_xyxy sits outside the oval on black panel art: must stay black.
        self.assertEqual(tuple(int(v) for v in rendered[34, 34]), (0, 0, 0))
        # Pixel inside the oval interior receives the backdrop colour.
        r, g, b = (int(v) for v in rendered[60, 100])
        self.assertGreater(r, 200)
        self.assertLess(g, 60)
        self.assertLess(b, 60)


if __name__ == "__main__":
    unittest.main()
