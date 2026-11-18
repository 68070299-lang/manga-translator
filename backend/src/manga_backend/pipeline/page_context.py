"""Page context for the translator: numbered units, length budgets, fit test.

Two of the plan's translation levers live here (research 2.4, candidates 2-3):

- The page image with a small number drawn at each translation unit, in
  reading order, for a vision-capable provider. Lippmann et al. (COLING 2025)
  found "page plus image, bubbles replaced by reading-order numbers" the best
  Japanese-to-English setup; the text prompt lists the OCR text per number.
- A per-unit length budget in characters, read from the unit's WP7 room at the
  page's anchored lettering size (``fit.room_capacity``), and the fit test that
  decides the single re-ask: a unit that cannot be set even at the minimum
  readable size gets one shorter version.

The room plan is built before translation, from the original page: the mask
stage's own bubble interiors need only boxes and pixels, and the same rooms are
built again at render time from the filled page. On the private pages the two
plans agree (same room kind, bounds and capacity on all 60 regions, see the
WP6b notes), because the interiors are cut by the bubble outline and paper, not
by the lettering.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from manga_backend.pipeline.fit import (
    MIN_READABLE_FONT_SIZE,
    fit_text_in_room,
    room_capacity,
)
from manga_backend.pipeline.fonts import get_cached_font, get_font_object
from manga_backend.pipeline.layout import bubble_region, standalone_region
from manga_backend.pipeline.regions import clamp_xyxy, int_box
from manga_backend.pipeline.rendering import anchor_targets, build_rooms
from manga_backend.pipeline.rooms import Room
from manga_backend.pipeline.text import has_cjk
from manga_backend.utils.textblock import Box, TextBlock

logger = logging.getLogger(__name__)

# A budget below this many characters is not an instruction a translator can
# act on ("at most 3 characters"), so such a room is quoted at the capacity it
# has at the minimum readable size instead: the fitter may set a unit that
# small, not smaller.
MIN_ANCHORED_BUDGET = 12
# Never quote less than this, however small the room: below it no English line
# is possible, and the re-ask could not help either.
MIN_PROMPT_BUDGET = 6
# Longest side of the page image sent to a vision provider. The page only has
# to carry the reading-order marks and the art; full resolution costs image
# tokens without adding context.
PAGE_IMAGE_MAX_SIDE = 1024


def unit_box(unit: Sequence[TextBlock]) -> Box:
    """Where a unit sits on the page: its bubble box, else its blocks' union."""
    for block in unit:
        if block.block_type == "text" and block.bubble_xyxy is not None:
            return int_box(block.bubble_xyxy)
    return (
        min(int(block.x1) for block in unit),
        min(int(block.y1) for block in unit),
        max(int(block.x2) for block in unit),
        max(int(block.y2) for block in unit),
    )


@dataclass(frozen=True)
class UnitBudget:
    """One unit's room, its page lettering size and its character budget."""

    chars: int
    target: int
    kind: str
    box: Box

    def as_dict(self) -> dict[str, object]:
        return {
            "chars": self.chars,
            "target": self.target,
            "kind": self.kind,
            "box": list(self.box),
        }


@dataclass
class PageContext:
    """Per-unit budgets plus the fit test the single re-ask is decided on."""

    budgets: list[UnitBudget | None]
    rooms: list[Room | None]
    regions: list[TextBlock | None]
    target: int
    font_path: str | None
    line_spacing: float = 1.2
    stroke_width: int = 0
    auto_scale: bool = True
    text_direction: str = "auto"

    @property
    def character_budgets(self) -> list[int | None]:
        return [None if budget is None else budget.chars for budget in self.budgets]

    def fits(self, index: int, text: str) -> bool:
        """True when ``text`` can be set at the minimum readable size or larger.

        This mirrors what the renderer will do: fit the text into the unit's
        room starting at the page's anchored size and see whether the result
        stays readable. A unit with no room (or no budget) cannot be improved
        by a shorter translation, so it counts as fitting and is not re-asked.
        """
        budget = self.budgets[index] if index < len(self.budgets) else None
        room = self.rooms[index] if index < len(self.rooms) else None
        if budget is None or room is None or not str(text).strip():
            return True
        result = fit_text_in_room(
            room,
            text,
            self.font_path,
            target=budget.target,
            line_spacing=self.line_spacing,
            base_stroke_width=self.stroke_width,
            vertical=self._vertical(text, room, budget),
            fixed_size=not self.auto_scale,
            kind=budget.kind,
        )
        return result.size >= MIN_READABLE_FONT_SIZE and not result.overflow

    def _vertical(self, text: str, room: Room, budget: UnitBudget) -> bool:
        """The renderer's vertical rule: explicit, or CJK in a tall room."""
        if self.text_direction == "vertical":
            return True
        if self.text_direction != "auto":
            return False
        return has_cjk(text) and room.height > room.width * 1.5


def _unit_region(unit: Sequence[TextBlock], shape: tuple[int, ...]) -> TextBlock | None:
    """The render region a unit will become, before it has any translation.

    Dialogue uses ``layout.bubble_region``'s geometry (the detector's bubble
    box, inset from its edge); a unit without a bubble box stays a standalone
    box region. The translation-dependent parts (dropping empty items, the
    short-label growth, composite suppression) are not replicated: none of
    them moves a room's outline.
    """
    if not unit:
        return None
    first = unit[0]
    height, width = shape[:2]
    if first.block_type == "text" and first.bubble_xyxy is not None:
        key = int_box(clamp_xyxy(int_box(first.bubble_xyxy), width, height))
        if key[2] > key[0] and key[3] > key[1]:
            members = [
                block
                for block in unit
                if block.block_type == "text"
                and block.bubble_xyxy is not None
                and int_box(clamp_xyxy(int_box(block.bubble_xyxy), width, height)) == key
            ]
            return bubble_region(key, members or [first])
    return standalone_region(first, width, height)


def plan_page_context(
    image: np.ndarray,
    units: Sequence[Sequence[TextBlock]],
    blocks: Sequence[TextBlock],
    *,
    preferred_font_size: int = 16,
    line_spacing: float = 1.2,
    stroke_width: float = 0,
    auto_scale: bool = True,
    text_direction: str = "auto",
) -> PageContext:
    """Build one budget and one fit test per translation unit.

    The rooms come from the same WP7 machinery the renderer uses
    (``rendering.build_rooms`` over ``layout``'s region geometry), so a budget
    is the unit's real capacity rather than a page-area guess. Blocks whose
    source em was already measured (the mask stage writes it) anchor the page
    size; before masking the caller measures it, which is why this runs on the
    original page.
    """
    regions: list[TextBlock | None] = []
    for unit in units:
        regions.append(_unit_region(unit, image.shape))
    planned = [region for region in regions if region is not None]
    target = max(1, int(preferred_font_size))
    if not planned:
        return PageContext(
            budgets=[None] * len(units),
            rooms=[None] * len(units),
            regions=regions,
            target=target,
            font_path=None,
            line_spacing=line_spacing,
            stroke_width=int(round(stroke_width)),
            auto_scale=auto_scale,
            text_direction=text_direction,
        )

    rooms = build_rooms(image, planned, list(blocks))
    # anchor_targets harmonises every region to one page size; the first entry
    # is that size.
    targets = anchor_targets(planned, preferred_font_size, list(blocks))
    target = max(MIN_READABLE_FONT_SIZE, targets[0] if targets else preferred_font_size)
    font_path = get_cached_font("english") or get_cached_font("cjk")
    stroke = int(round(stroke_width))

    budgets: list[UnitBudget | None] = []
    room_by_unit: list[Room | None] = []
    planned_index = 0
    for region, unit in zip(regions, units):
        if region is None:
            budgets.append(None)
            room_by_unit.append(None)
            continue
        room = rooms[planned_index]
        planned_index += 1
        kind = "dialogue" if region.render_from_bubble else "free"
        anchored = room_capacity(room, font_path, target, line_spacing=line_spacing, base_stroke_width=stroke)
        if anchored < MIN_ANCHORED_BUDGET:
            # The anchored size cannot hold a phrase here; quote the readable
            # floor's capacity, which is what keeps the unit out of
            # below_min_readable.
            anchored = room_capacity(
                room,
                font_path,
                MIN_READABLE_FONT_SIZE,
                line_spacing=line_spacing,
                base_stroke_width=stroke,
            )
        chars = max(MIN_PROMPT_BUDGET, anchored)
        budgets.append(UnitBudget(chars=chars, target=target, kind=kind, box=unit_box(unit)))
        room_by_unit.append(room)
    return PageContext(
        budgets=budgets,
        rooms=room_by_unit,
        regions=regions,
        target=target,
        font_path=font_path,
        line_spacing=line_spacing,
        stroke_width=stroke,
        auto_scale=auto_scale,
        text_direction=text_direction,
    )


def build_numbered_page(
    image: np.ndarray,
    boxes: Sequence[Box],
    *,
    max_side: int = PAGE_IMAGE_MAX_SIDE,
) -> tuple[bytes, list[tuple[int, int, int]]]:
    """Downscale the page and draw each unit's reading-order number on it.

    ``boxes`` are the units' page boxes in reading order; unit ``i`` is marked
    with the number ``i + 1`` at the centre of that box. Returns the PNG bytes
    and the drawn marks as ``(number, x, y)`` in the downscaled image, so a
    caller (or a test) can check where each number landed.
    """
    height, width = image.shape[:2]
    scale = 1.0
    longest = max(height, width)
    if max_side > 0 and longest > max_side:
        scale = max_side / float(longest)
        size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
        page = Image.fromarray(image).resize(size, Image.Resampling.LANCZOS)
    else:
        page = Image.fromarray(image)
    draw = ImageDraw.Draw(page)
    page_width, page_height = page.size
    radius = max(8, int(round(min(page_width, page_height) * 0.012)))
    font = get_font_object(get_cached_font("english") or get_cached_font("cjk"), max(11, int(round(radius * 1.5))))
    marks: list[tuple[int, int, int]] = []
    for index, box in enumerate(boxes):
        x1, y1, x2, y2 = (float(value) * scale for value in box)
        cx = int(round((x1 + x2) / 2.0))
        cy = int(round((y1 + y2) / 2.0))
        cx = max(radius + 1, min(page_width - radius - 2, cx))
        cy = max(radius + 1, min(page_height - radius - 2, cy))
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=(255, 255, 255),
            outline=(0, 0, 0),
            width=max(2, radius // 4),
        )
        label = str(index + 1)
        draw.text((cx, cy), label, font=font, fill=(0, 0, 0), anchor="mm")
        marks.append((index + 1, cx, cy))
    buffer = io.BytesIO()
    page.save(buffer, format="PNG")
    return buffer.getvalue(), marks
