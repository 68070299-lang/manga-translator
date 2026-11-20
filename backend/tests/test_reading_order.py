"""Reading order and translation units (WP6a).

Covers the two required cases plus the helpers around them:
- the reading order of a synthetic two-panel page (manga RTL and webtoon LTR);
- the blocks of one bubble merged into one translation unit before translation.
"""

from __future__ import annotations

import unittest

import numpy as np

from manga_backend.pipeline import TranslationPipeline
from manga_backend.pipeline.layout import (
    bubble_region,
    build_render_regions,
    group_translation_units,
    reading_order_agreement,
    resolve_reading_direction,
    sort_reading_order,
    unit_source_text,
)
from manga_backend.utils.textblock import TextBlock


def two_panel_page(width: int = 400, height: int = 300) -> np.ndarray:
    """A white page with two framed panels and a paper gutter between them."""
    image = np.full((height, width, 3), 255, dtype=np.uint8)
    for box in ((4, 4, 196, height - 4), (204, 4, 396, height - 4)):
        x1, y1, x2, y2 = box
        image[y1 : y2, x1 : x1 + 4] = 0
        image[y1 : y2, x2 - 4 : x2] = 0
        image[y1 : y1 + 4, x1:x2] = 0
        image[y2 - 4 : y2, x1:x2] = 0
    return image


def block(box: list[int], text: str = "", bubble: list[int] | None = None) -> TextBlock:
    made = TextBlock(box, text=text)
    made.bubble_xyxy = bubble
    return made


class ReadingDirectionTests(unittest.TestCase):
    def test_auto_reads_tall_pages_left_to_right_and_other_pages_right_to_left(self):
        self.assertEqual(resolve_reading_direction("auto", (1000, 720)), "rtl")
        self.assertEqual(resolve_reading_direction("auto", (2400, 640)), "ltr")
        self.assertEqual(resolve_reading_direction("ltr", (100, 100)), "ltr")
        self.assertEqual(resolve_reading_direction("RTL", (100, 100)), "rtl")
        with self.assertRaises(ValueError):
            resolve_reading_direction("diagonal", (100, 100))


class TwoPanelOrderTests(unittest.TestCase):
    def test_manga_page_reads_right_panel_first(self):
        image = two_panel_page()
        right_top = block([300, 20, 380, 100], "A")
        right_bottom = block([300, 200, 380, 280], "B")
        left = block([50, 120, 150, 180], "C")

        # Detector order (confidence) is deliberately scrambled.
        ordered = sort_reading_order(image, [left, right_bottom, right_top])

        self.assertEqual([b.text for b in ordered], ["A", "B", "C"])
        # And the same page read left to right.
        self.assertEqual(
            [b.text for b in sort_reading_order(image, [left, right_bottom, right_top], "ltr")],
            ["C", "A", "B"],
        )

    def test_tall_page_reads_left_to_right_by_default(self):
        image = two_panel_page(width=300, height=600)
        left_top = block([40, 20, 120, 100], "L")
        right_bottom = block([180, 400, 260, 480], "R")

        self.assertEqual(resolve_reading_direction("auto", image.shape), "ltr")
        self.assertEqual(
            [b.text for b in sort_reading_order(image, [right_bottom, left_top])],
            ["L", "R"],
        )

    def test_rows_read_top_to_bottom_within_a_panel(self):
        image = two_panel_page()
        low = block([300, 200, 380, 280], "low")
        high = block([300, 20, 380, 100], "high")

        self.assertEqual(
            [b.text for b in sort_reading_order(image, [low, high])], ["high", "low"]
        )

    def test_duplicate_geometry_keeps_detector_order(self):
        image = two_panel_page()
        first = block([300, 20, 380, 100], "first")
        second = block([300, 20, 380, 100], "second")

        self.assertEqual(
            [b.text for b in sort_reading_order(image, [first, second])],
            ["first", "second"],
        )


class TranslationUnitTests(unittest.TestCase):
    def test_two_block_bubble_becomes_one_unit(self):
        first = block([40, 20, 60, 50], "ちょっと", bubble=[30, 10, 90, 100])
        second = block([42, 55, 62, 80], "待って！", bubble=[30, 10, 90, 100])
        sfx = block([150, 150, 180, 180], "ドン")
        sfx.block_type = "sfx"
        # An SFX box can carry a bubble box without being bubble lettering.
        sfx.bubble_xyxy = [30, 10, 90, 100]
        narration = block([10, 150, 20, 170], "その日")

        units = group_translation_units([second, sfx, first, narration])

        self.assertEqual(len(units), 3)
        # Members keep the order they were given (reading order in the real
        # pipeline); the unit text itself is joined in the bubble's own
        # reading order.
        self.assertEqual(
            [[b.text for b in unit] for unit in units],
            [["待って！", "ちょっと"], ["ドン"], ["その日"]],
        )
        self.assertEqual(unit_source_text(units[0]), "ちょっと待って！")
        self.assertEqual(unit_source_text(units[1]), "ドン")
        # A single-block unit keeps its own text untouched.
        self.assertEqual(unit_source_text([narration]), "その日")

    def test_unit_text_joins_latin_parts_with_spaces(self):
        first = block([40, 20, 60, 50], "HEY", bubble=[30, 10, 90, 100])
        second = block([42, 55, 62, 80], "WAIT", bubble=[30, 10, 90, 100])

        self.assertEqual(
            unit_source_text(group_translation_units([first, second])[0]),
            "HEY WAIT",
        )

    def test_composite_whole_text_is_not_joined_with_its_parts(self):
        parent = block([30, 15, 80, 85], "ちょっと待って！", bubble=[25, 10, 90, 100])
        # Vertical columns: the right column is read first.
        part_one = block([55, 20, 75, 50], "ちょっと", bubble=[25, 10, 90, 100])
        part_two = block([30, 55, 50, 80], "待って！", bubble=[25, 10, 90, 100])

        unit = group_translation_units([part_one, part_two, parent])[0]

        self.assertEqual(unit_source_text(unit), "ちょっと待って！")

    def test_merged_unit_renders_once_per_bubble(self):
        first = block([40, 20, 60, 50], "ちょっと", bubble=[30, 10, 90, 100])
        second = block([42, 55, 62, 80], "待って！", bubble=[30, 10, 90, 100])
        # Every member of the unit carries the same translation.
        for member in (first, second):
            member.translated_text = "Hold on!"

        regions = build_render_regions([second, first], (120, 120, 3))

        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].translated_text, "Hold on!")

    def test_distinct_translations_still_join_for_rendering(self):
        first = block([40, 20, 60, 50], "A", bubble=[30, 10, 90, 100])
        second = block([42, 55, 62, 80], "B", bubble=[30, 10, 90, 100])
        first.translated_text = "FIRST"
        second.translated_text = "SECOND"

        region = bubble_region((30, 10, 90, 100), [second, first])

        self.assertEqual(region.translated_text, "FIRST SECOND")


class OrchestratedUnitsTests(unittest.IsolatedAsyncioTestCase):
    async def test_translate_merges_bubble_mates_and_keeps_block_mapping(self):
        pipeline = TranslationPipeline(device="cpu")

        class FakeDetector:
            async def detect(self, _image):
                return [
                    block([40, 20, 60, 50], "ちょっと", bubble=[30, 10, 90, 100]),
                    block([42, 55, 62, 80], "待って！", bubble=[30, 10, 90, 100]),
                    block([150, 150, 180, 180], "ドン"),
                ]

        class FakeOcr:
            async def process(self, _image, blocks):
                return [f"text-{index}" for index, _block in enumerate(blocks)]

        image = two_panel_page()
        pipeline._detectors.default = FakeDetector()
        pipeline._ocr = FakeOcr()

        result = await pipeline.translate(
            image, translator="none", inpainter="none", render=False
        )

        # One merged unit plus the standalone block: every bubble-mate carries
        # the unit's text, and the per-block result mapping is preserved.
        self.assertEqual(result["translated_texts"], ["text-0 text-1", "text-0 text-1", "text-2"])
        self.assertEqual(result["original_texts"], ["text-0", "text-1", "text-2"])
        self.assertEqual(len(result["blocks"]), 3)


class OrderAgreementTests(unittest.TestCase):
    def test_exact_orders_score_one(self):
        report = reading_order_agreement(["a", "b", "c"], ["a", "b", "c"])
        self.assertEqual(report["kendall_tau"], 1.0)
        self.assertTrue(report["exact"])
        self.assertEqual(report["discordant"], 0)

    def test_reversed_orders_score_minus_one(self):
        report = reading_order_agreement(["a", "b", "c"], ["c", "b", "a"])
        self.assertEqual(report["kendall_tau"], -1.0)
        self.assertFalse(report["exact"])

    def test_unmatched_labels_are_ignored(self):
        report = reading_order_agreement(["a", "b", "c"], ["b", "a", "d"])
        # Pair (a, b) is discordant; d is not labelled in expected.
        self.assertEqual(report["matched"], 2)
        self.assertEqual(report["kendall_tau"], -1.0)

    def test_fewer_than_two_matches_has_no_tau(self):
        report = reading_order_agreement(["a", "x"], ["a", "y"])
        self.assertEqual(report["matched"], 1)
        self.assertIsNone(report["kendall_tau"])
        self.assertFalse(report["exact"])


if __name__ == "__main__":
    unittest.main()
