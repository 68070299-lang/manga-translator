"""Tests for the page context: length budgets, fit test and numbered page."""

import io
import unittest

import cv2
import numpy as np
from PIL import Image

from manga_backend.pipeline.fit import MIN_READABLE_FONT_SIZE, room_capacity
from manga_backend.pipeline.page_context import (
    MIN_PROMPT_BUDGET,
    build_numbered_page,
    plan_page_context,
    unit_box,
)
from manga_backend.pipeline.rooms import box_room
from manga_backend.utils.textblock import TextBlock


class PageContextBudgetTests(unittest.TestCase):
    """A unit's budget is its WP7 room's capacity, in characters."""

    def test_free_unit_budget_is_its_room_capacity_at_the_page_size(self):
        image = np.full((200, 260, 3), 255, dtype=np.uint8)
        block = TextBlock([20, 20, 140, 100], text="テスト")
        unit = [block]

        context = plan_page_context(image, [unit], [block])

        budget = context.budgets[0]
        self.assertIsNotNone(budget)
        assert budget is not None
        self.assertEqual(budget.kind, "free")
        self.assertEqual(budget.target, context.target)
        self.assertEqual(budget.box, (20, 20, 140, 100))
        # The room plan is the same one the renderer builds for a free box.
        self.assertIsNotNone(context.rooms[0])
        anchored = room_capacity(
            context.rooms[0], context.font_path, budget.target, base_stroke_width=0
        )
        floor = room_capacity(
            context.rooms[0], context.font_path, MIN_READABLE_FONT_SIZE, base_stroke_width=0
        )
        expected = anchored if anchored >= 12 else floor
        self.assertEqual(budget.chars, max(MIN_PROMPT_BUDGET, expected))

    def test_a_bigger_box_gets_a_bigger_budget(self):
        image = np.full((240, 320, 3), 255, dtype=np.uint8)
        small = TextBlock([20, 20, 80, 60], text="テスト")
        large = TextBlock([120, 20, 300, 140], text="テスト")

        small_context = plan_page_context(image, [[small]], [small])
        large_context = plan_page_context(image, [[large]], [large])

        small_budget = small_context.budgets[0]
        large_budget = large_context.budgets[0]
        assert small_budget is not None and large_budget is not None
        self.assertGreater(large_budget.chars, small_budget.chars)

    def test_a_tiny_room_keeps_the_honest_minimum_budget(self):
        # Textured page: no blank area to grow into, so the room is the box.
        rng = np.random.default_rng(0)
        image = rng.integers(0, 255, (60, 60, 3), dtype=np.uint8)
        block = TextBlock([20, 20, 28, 28], text="テスト")

        context = plan_page_context(image, [[block]], [block])

        budget = context.budgets[0]
        assert budget is not None
        self.assertEqual(budget.chars, MIN_PROMPT_BUDGET)

    def test_dialogue_unit_uses_its_bubble_room(self):
        image = np.full((160, 160, 3), 255, dtype=np.uint8)
        cv2.ellipse(image, (80, 80), (55, 60), 0, 0, 360, (0, 0, 0), 2)
        block = TextBlock([65, 45, 95, 115], text="テスト")
        block.bubble_xyxy = [30, 25, 130, 135]

        context = plan_page_context(image, [[block]], [block])

        budget = context.budgets[0]
        assert budget is not None
        self.assertEqual(budget.kind, "dialogue")
        self.assertIsNotNone(context.rooms[0])
        self.assertGreaterEqual(budget.chars, MIN_PROMPT_BUDGET)

    def test_no_units_gives_no_budgets(self):
        image = np.full((40, 40, 3), 255, dtype=np.uint8)

        context = plan_page_context(image, [], [])

        self.assertEqual(context.budgets, [])
        self.assertEqual(context.character_budgets, [])

    def test_budgeted_unit_without_a_room_is_still_reported(self):
        image = np.full((60, 60, 3), 255, dtype=np.uint8)
        # A zero-height render box cannot produce a standalone region.
        block = TextBlock([10, 10, 40, 40], text="テスト")

        context = plan_page_context(image, [[block]], [block])

        self.assertEqual(len(context.budgets), 1)
        self.assertIsNotNone(context.budgets[0])


class PageContextFitTests(unittest.TestCase):
    """The fit test is the single re-ask's trigger: readable at the floor."""

    def build_context(self):
        image = np.full((200, 240, 3), 255, dtype=np.uint8)
        block = TextBlock([20, 20, 120, 80], text="テスト")
        return plan_page_context(image, [[block]], [block])

    def test_a_long_translation_does_not_fit_and_a_short_one_does(self):
        context = self.build_context()
        budget = context.budgets[0]
        assert budget is not None

        self.assertTrue(context.fits(0, "Wait!"))
        self.assertFalse(context.fits(0, "word " * 200))

    def test_an_empty_translation_counts_as_fitting_so_it_is_not_reasked(self):
        context = self.build_context()

        self.assertTrue(context.fits(0, ""))
        self.assertTrue(context.fits(0, "   "))

    def test_unknown_unit_index_fits(self):
        context = self.build_context()

        self.assertTrue(context.fits(7, "anything"))

    def test_character_budgets_are_plain_ints(self):
        context = self.build_context()

        budgets = context.character_budgets

        self.assertEqual(len(budgets), 1)
        self.assertIsInstance(budgets[0], int)


class NumberedPageTests(unittest.TestCase):
    """The page image a vision provider receives: numbers at unit locations."""

    def test_page_is_downscaled_and_each_unit_is_marked_in_reading_order(self):
        page = np.zeros((1200, 800, 3), dtype=np.uint8)
        boxes = [(50, 100, 150, 200), (400, 300, 500, 400), (600, 900, 700, 1000)]

        png, marks = build_numbered_page(page, boxes, max_side=600)

        with Image.open(io.BytesIO(png)) as opened:
            self.assertEqual(opened.size, (400, 600))
            pixels = np.array(opened.convert("RGB"))
        scale = 600 / 1200
        self.assertEqual([mark[0] for mark in marks], [1, 2, 3])
        for number, (x1, y1, x2, y2) in zip((1, 2, 3), boxes):
            _number, cx, cy = marks[number - 1]
            self.assertAlmostEqual(cx, round((x1 + x2) / 2 * scale), delta=1)
            self.assertAlmostEqual(cy, round((y1 + y2) / 2 * scale), delta=1)
            # A white disc is drawn there over the black page.
            disc = pixels[cy - 5 : cy + 6, cx - 5 : cx + 6]
            self.assertTrue((disc > 200).any())
        # The page far from every mark is untouched.
        self.assertTrue((pixels[0:5, 0:5] == 0).all())

    def test_marks_stay_inside_the_image_for_edge_boxes(self):
        page = np.zeros((300, 400, 3), dtype=np.uint8)

        _png, marks = build_numbered_page(page, [(0, 0, 2, 2), (398, 298, 400, 300)])

        self.assertEqual(len(marks), 2)
        for _number, cx, cy in marks:
            self.assertGreaterEqual(cx, 8)
            self.assertLessEqual(cx, 400 - 9)
            self.assertGreaterEqual(cy, 8)
            self.assertLessEqual(cy, 300 - 9)

    def test_a_small_page_is_not_upscaled(self):
        page = np.zeros((120, 80, 3), dtype=np.uint8)

        png, marks = build_numbered_page(page, [(10, 10, 30, 30)])

        with Image.open(io.BytesIO(png)) as opened:
            self.assertEqual(opened.size, (80, 120))
        self.assertEqual(len(marks), 1)


class UnitBoxTests(unittest.TestCase):
    def test_unit_box_prefers_the_bubble_box(self):
        block = TextBlock([30, 30, 50, 50], text="テスト")
        block.bubble_xyxy = [10, 10, 70, 70]

        self.assertEqual(unit_box([block]), (10, 10, 70, 70))

    def test_unit_box_unions_members_without_a_bubble(self):
        first = TextBlock([10, 20, 30, 40], text="ア")
        second = TextBlock([40, 50, 60, 80], text="イ")

        self.assertEqual(unit_box([first, second]), (10, 20, 60, 80))


class BoxRoomCapacityTests(unittest.TestCase):
    """The additive capacity helper measures a plane box the fitter's way."""

    def test_capacity_grows_with_the_room_and_with_smaller_type(self):
        block = TextBlock([0, 0, 30, 30], translated_text="x")
        small = box_room(block)
        wide = box_room(TextBlock([0, 0, 120, 30], translated_text="x"))

        self.assertGreater(
            room_capacity(wide, None, 16), room_capacity(small, None, 16)
        )
        self.assertGreater(
            room_capacity(wide, None, MIN_READABLE_FONT_SIZE),
            room_capacity(wide, None, 40),
        )

    def test_capacity_is_zero_when_no_band_fits(self):
        block = TextBlock([0, 0, 4, 4], translated_text="x")

        self.assertEqual(room_capacity(box_room(block), None, 40), 0)

    def test_capacity_counts_whole_characters_per_band(self):
        block = TextBlock([0, 0, 200, 40], translated_text="x")
        room = box_room(block)

        capacity = room_capacity(room, None, 16, base_stroke_width=2)

        self.assertGreater(capacity, 0)
        self.assertIsInstance(capacity, int)


if __name__ == "__main__":
    unittest.main()
