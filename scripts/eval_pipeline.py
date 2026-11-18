"""Score the backend translation pipeline on a fixed corpus of manga pages.

The harness runs the real ``TranslationPipeline.translate`` (detect, OCR,
translate, inpaint, render, Step 6 re-OCR) on every page of a corpus manifest
and records what each stage did. It observes the pipeline by wrapping its stage
boundaries for the duration of a run; it never changes what the pipeline does.

Per page it records:

* CJK residue from the pipeline's own Step 6 re-OCR verification, plus a
  stricter kana/kanji-only reading of the same re-OCR text;
* pixels the inpainting step changed outside the masks it declared (expected 0);
* mask strategy counts (white fill, bubble interior, contour-safe glyph,
  rectangular, skipped);
* mask reason codes: for every block that ended without a bubble interior,
  why the interior was not used (text_free, no contour, contour touching the
  ROI edge, area test, coverage);
* inpainter fallbacks (for example FLUX to LaMa);
* render shrink, overflow and unfit events, plus the per-page font-size spread
  and rendered ink that crosses a bubble outline;
* per-stage timings;
* for synthetic pages, ground-truth scores against the clean plate: residual
  ink split into fill failure (inside the erase mask) and mask miss (outside
  it), ink-mask recall, a low-pass residual that ignores tone phase noise,
  tone flattened inside text boxes, and the OCR character error rate.

Two oracle modes isolate one half of the erase step at a time (synthetic
pages only): ``--mask-mode oracle`` masks the diluted truth ink, measuring
the fill alone, and ``--fill oracle`` pastes the clean plate inside the
pipeline's own mask, measuring the mask alone.

Usage (from the repository root)::

    uv run --project backend --locked python scripts/eval_pipeline.py \\
        --corpus eval/corpus.json --out eval/runs/my-change
    uv run --project backend --locked python scripts/eval_pipeline.py \\
        --compare eval/baseline/rtdetr-lama eval/runs/my-change

See eval/README.md for the corpus format and what each metric means.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import platform
import re
import statistics
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from manga_backend.bootstrap import bootstrap  # noqa: E402

bootstrap()

from manga_backend.pipeline import TranslationPipeline  # noqa: E402
from manga_backend.pipeline import masks as masks_module  # noqa: E402
from manga_backend.pipeline import orchestrator as orchestrator_module  # noqa: E402
from manga_backend.pipeline import rendering as rendering_module  # noqa: E402
from manga_backend.pipeline.fonts import get_cached_font  # noqa: E402
from manga_backend.pipeline.regions import int_box  # noqa: E402
from manga_backend.pipeline.inpainting import DEFAULT_INPAINTER  # noqa: E402
from manga_backend.pipeline.text import is_unchanged_punctuation  # noqa: E402
from manga_backend.utils.textblock import TextBlock  # noqa: E402

logger = logging.getLogger("eval_pipeline")

# Version 2 adds the WP1 diagnostics to every run (schema-additive: version 1
# keys are unchanged): the residual-ink split (ink_mask_recall,
# residual_in_mask_ratio, residual_out_of_mask_ratio), the low-pass residual,
# hole_in_tone_px, render_ink_outside_bubble_px, OCR character error rate,
# mask reason codes, font_size_spread and the oracle run modes.
SCHEMA_VERSION = 2
Box = tuple[int, int, int, int]

KNOWN_TAGS = frozenset(
    {
        "white-bubble",
        "textured-background",
        "sfx",
        "vertical-text",
        "horizontal-text",
        "conjoined-bubbles",
        "webtoon-strip",
        "bookwalker",
        "light-on-dark",
        "narration",
        "adversarial",
    }
)

# Render options the POST /translate route sends when the client leaves them
# unset (backend/src/manga_backend/api/deps.py). The test suite checks they
# stay in sync, so a run scores what the extension gets by default.
API_RENDER_DEFAULTS: dict[str, Any] = {
    "line_spacing": 1.2,
    "border_size": 2.0,
    "text_color": "#000000",
    "border_color": "#ffffff",
    "bg_color": "#ffffff",
    "bg_opacity": 0.0,
    "text_alignment": "center",
    "text_direction": "auto",
    "uppercase": False,
    "auto_scale": True,
    "preferred_font_size": 16,
}

STAGES = ("detect", "ocr", "translate", "mask", "inpaint_model", "render", "verify")
MASK_KINDS = ("contour_safe_glyph", "bubble_interior", "rectangular", "other", "skipped")
STUB_TRANSLATOR = "stub"
TRUTH_DETECTOR = "truth"


# ---------------------------------------------------------------------------
# Corpus manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CorpusPage:
    """One manifest entry: a page file or a named synthetic page."""

    id: str
    tags: tuple[str, ...]
    path: str | None = None
    synthetic: str | None = None
    notes: str = ""


def load_corpus(manifest: Path) -> list[CorpusPage]:
    """Parse and validate a corpus manifest."""
    data = json.loads(manifest.read_text(encoding="utf-8"))
    entries = data.get("pages")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{manifest}: 'pages' must be a non-empty list")
    pages: list[CorpusPage] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        where = f"{manifest} page {index}"
        page_id = str(entry.get("id") or "").strip()
        if not page_id:
            raise ValueError(f"{where}: missing id")
        if page_id in seen:
            raise ValueError(f"{where}: duplicate id {page_id!r}")
        seen.add(page_id)
        path = entry.get("path")
        synthetic = entry.get("synthetic")
        if (path is None) == (synthetic is None):
            raise ValueError(f"{where}: set exactly one of 'path' or 'synthetic'")
        if synthetic is not None and synthetic not in SYNTHETIC_PAGES:
            raise ValueError(
                f"{where}: unknown synthetic page {synthetic!r}; "
                f"choose from {sorted(SYNTHETIC_PAGES)}"
            )
        tags = tuple(str(tag) for tag in entry.get("tags") or ())
        unknown = sorted(set(tags) - KNOWN_TAGS)
        if unknown:
            raise ValueError(f"{where}: unknown tags {unknown}; known: {sorted(KNOWN_TAGS)}")
        pages.append(
            CorpusPage(
                id=page_id,
                tags=tags,
                path=None if path is None else str(path),
                synthetic=None if synthetic is None else str(synthetic),
                notes=str(entry.get("notes") or ""),
            )
        )
    return pages


# ---------------------------------------------------------------------------
# Synthetic pages: drawn in code so the harness runs without private pages.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TruthBox:
    xyxy: Box
    text: str
    kind: str  # bubble | caption | narration | sfx
    bubble: Box | None = None


@dataclass(frozen=True)
class SyntheticPage:
    name: str
    image: np.ndarray
    clean: np.ndarray
    ink: np.ndarray  # bool mask of lettering pixels (fill and outline)
    truth: tuple[TruthBox, ...]
    glyph_source: str


def _center(box: Box) -> tuple[float, float]:
    return (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0


class _Canvas:
    """Draws a clean plate, then lettering on separate alpha layers."""

    def __init__(self, width: int, height: int, background: tuple[int, int, int] = (255, 255, 255)):
        self.clean = Image.new("RGB", (width, height), background)
        self.draw = ImageDraw.Draw(self.clean)
        self.layers: list[tuple[np.ndarray, tuple[int, int, int]]] = []
        self.truth: list[TruthBox] = []
        self.font_path = get_cached_font("cjk")
        self.glyph_source = Path(self.font_path).name if self.font_path else "pseudo-glyphs"
        self._fonts: dict[int, ImageFont.FreeTypeFont] = {}

    # Clean-plate helpers -------------------------------------------------

    def _array(self) -> np.ndarray:
        return np.array(self.clean)

    def _commit(self, array: np.ndarray) -> None:
        self.clean = Image.fromarray(array)
        self.draw = ImageDraw.Draw(self.clean)

    def rect(self, box: Box, *, width: int = 4, fill: tuple[int, int, int] | None = None) -> None:
        self.draw.rectangle(box, outline=(0, 0, 0), width=width, fill=fill)

    def ellipse(self, box: Box, *, width: int = 3) -> None:
        self.draw.ellipse(box, outline=(0, 0, 0), width=width, fill=(255, 255, 255))

    def rounded(self, box: Box, radius: int, *, fill: tuple[int, int, int], outline: int = 0) -> None:
        self.draw.rounded_rectangle(
            box, radius, fill=fill, outline=(0, 0, 0) if outline else None, width=outline
        )

    def conjoined(self, first: Box, second: Box, *, width: int = 3) -> None:
        """Two bubbles sharing one outline, with no seam where they overlap."""
        for box in (first, second):
            grown = (box[0] - width, box[1] - width, box[2] + width, box[3] + width)
            self.draw.ellipse(grown, fill=(0, 0, 0))
        for box in (first, second):
            self.draw.ellipse(box, fill=(255, 255, 255))

    def tone(self, box: Box, *, spacing: int, radius: float, level: int) -> None:
        array = self._array()
        x1, y1, x2, y2 = box
        ys, xs = np.mgrid[y1:y2, x1:x2]
        dy = (ys % spacing) - spacing / 2.0
        dx = (xs % spacing) - spacing / 2.0
        dots = dx * dx + dy * dy <= radius * radius
        region = array[y1:y2, x1:x2]
        region[dots] = level
        self._commit(array)

    def tone_rotated(
        self, box: Box, *, spacing: float, radius: float, level: int, angle: float
    ) -> None:
        """Screentone on a rotated lattice with a non-integer period.

        The plain ``tone`` helper draws perfect axis-aligned integer lattices,
        which flatters any method that assumes a periodic background. Real
        printed tone is rarely that clean.
        """
        array = self._array()
        x1, y1, x2, y2 = box
        ys, xs = np.mgrid[y1:y2, x1:x2]
        theta = np.deg2rad(angle)
        u = xs * np.cos(theta) + ys * np.sin(theta)
        v = -xs * np.sin(theta) + ys * np.cos(theta)
        du = np.mod(u, spacing) - spacing / 2.0
        dv = np.mod(v, spacing) - spacing / 2.0
        dots = du * du + dv * dv <= radius * radius
        region = array[y1:y2, x1:x2]
        region[dots] = level
        self._commit(array)

    def gradient(
        self,
        box: Box,
        top: tuple[int, int, int],
        bottom: tuple[int, int, int],
        *,
        noise: float = 0.0,
        seed: int = 0,
    ) -> None:
        array = self._array().astype(np.float32)
        x1, y1, x2, y2 = box
        ramp = np.linspace(0.0, 1.0, y2 - y1, dtype=np.float32)[:, None, None]
        colors = np.array(top, np.float32) * (1 - ramp) + np.array(bottom, np.float32) * ramp
        region = np.broadcast_to(colors, (y2 - y1, x2 - x1, 3)).copy()
        if noise:
            rng = np.random.default_rng(seed)
            region += rng.normal(0.0, noise, region.shape[:2])[..., None].astype(np.float32)
        array[y1:y2, x1:x2] = region
        self._commit(np.clip(np.rint(array), 0, 255).astype(np.uint8))

    def hatch(self, box: Box, *, spacing: int, width: int, color: tuple[int, int, int]) -> None:
        array = self._array()
        x1, y1, x2, y2 = box
        ys, xs = np.mgrid[y1:y2, x1:x2]
        lines = (xs + ys) % spacing < width
        region = array[y1:y2, x1:x2]
        region[lines] = color
        self._commit(array)

    # Lettering -----------------------------------------------------------

    def _font(self, size: int) -> ImageFont.FreeTypeFont | None:
        if not self.font_path:
            return None
        if size not in self._fonts:
            self._fonts[size] = ImageFont.truetype(self.font_path, size)
        return self._fonts[size]

    def _glyph(self, draw: ImageDraw.ImageDraw, char: str, cell: tuple[float, float], size: int, stroke: int) -> None:
        x, y = cell
        font = self._font(size)
        if font is not None:
            draw.text(
                (x + size / 2.0, y),
                char,
                font=font,
                fill=255,
                anchor="mt",
                stroke_width=stroke,
                stroke_fill=255,
            )
            return
        # No CJK font on this machine: draw deterministic stroke clusters so
        # the page still has glyph-like ink at the same positions.
        rng = np.random.default_rng(ord(char))
        inset = size * 0.15
        line_width = max(2, size // 9) + stroke * 2
        for _ in range(3 + ord(char) % 3):
            ax, ay, bx, by = rng.uniform(inset, size - inset, 4).tolist()
            draw.line((x + ax, y + ay, x + bx, y + by), fill=255, width=line_width)

    def text(
        self,
        lines: Sequence[str],
        center: tuple[float, float],
        size: int,
        *,
        vertical: bool = True,
        kind: str = "bubble",
        bubble: Box | None = None,
        color: tuple[int, int, int] = (0, 0, 0),
        stroke: int = 0,
        stroke_color: tuple[int, int, int] = (255, 255, 255),
    ) -> None:
        """Letter one text block; vertical lines run right to left."""
        width, height = self.clean.size
        fill_layer = Image.new("L", (width, height), 0)
        fill_draw = ImageDraw.Draw(fill_layer)
        outline_layer = Image.new("L", (width, height), 0) if stroke else None
        outline_draw = ImageDraw.Draw(outline_layer) if outline_layer is not None else None

        cells: list[tuple[str, tuple[float, float]]] = []
        if vertical:
            step = round(size * 1.08)
            column_step = round(size * 1.3)
            longest = max(len(line) for line in lines)
            block_width = (len(lines) - 1) * column_step + size
            block_height = (longest - 1) * step + size
            left = center[0] - block_width / 2.0
            top = center[1] - block_height / 2.0
            for column, line in enumerate(lines):
                x = left + (len(lines) - 1 - column) * column_step
                cells.extend((char, (x, top + row * step)) for row, char in enumerate(line))
        else:
            line_step = round(size * 1.25)
            block_height = (len(lines) - 1) * line_step + size
            top = center[1] - block_height / 2.0
            for row, line in enumerate(lines):
                left = center[0] - len(line) * size / 2.0
                cells.extend(
                    (char, (left + column * size, top + row * line_step))
                    for column, char in enumerate(line)
                )

        for char, cell in cells:
            self._glyph(fill_draw, char, cell, size, 0)
            if outline_draw is not None:
                self._glyph(outline_draw, char, cell, size, stroke)

        if outline_layer is not None:
            self.layers.append((np.asarray(outline_layer), stroke_color))
        self.layers.append((np.asarray(fill_layer), color))
        bbox = (outline_layer or fill_layer).getbbox()
        if bbox:
            self.truth.append(
                TruthBox(
                    xyxy=(int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])),
                    text="".join(lines),
                    kind=kind,
                    bubble=bubble,
                )
            )

    def finish(self, name: str) -> SyntheticPage:
        clean = np.array(self.clean, dtype=np.uint8)
        page = clean.astype(np.float32)
        ink = np.zeros(clean.shape[:2], dtype=bool)
        for alpha_layer, color in self.layers:
            alpha = (alpha_layer.astype(np.float32) / 255.0)[..., None]
            page = page * (1.0 - alpha) + np.array(color, np.float32) * alpha
            ink |= alpha_layer > 127
        image = np.clip(np.rint(page), 0, 255).astype(np.uint8)
        return SyntheticPage(name, image, clean, ink, tuple(self.truth), self.glyph_source)


def _synthetic_white_bubbles() -> _Canvas:
    canvas = _Canvas(720, 1000)
    canvas.rect((16, 16, 704, 484))
    canvas.rect((16, 500, 704, 984))
    canvas.draw.arc((120, 330, 320, 530), 180, 360, fill=(40, 40, 40), width=3)
    canvas.draw.line((330, 470, 520, 360), fill=(40, 40, 40), width=3)
    canvas.draw.line((40, 960, 260, 860), fill=(40, 40, 40), width=3)
    bubbles: list[tuple[Box, list[str], int]] = [
        ((400, 40, 660, 330), ["ちょっと", "待って！"], 30),
        ((60, 70, 280, 300), ["本当に", "いいの？"], 28),
        ((360, 540, 670, 900), ["明日また", "ここで", "会おう"], 30),
        ((70, 620, 260, 820), ["うん"], 32),
    ]
    for box, lines, size in bubbles:
        canvas.ellipse(box)
        canvas.text(lines, _center(box), size, bubble=box)
    return canvas


def _synthetic_conjoined_bubbles() -> _Canvas:
    canvas = _Canvas(720, 720)
    canvas.rect((16, 16, 704, 704))
    left: Box = (70, 110, 380, 500)
    right: Box = (320, 200, 650, 610)
    canvas.conjoined(left, right)
    left_center = _center(left)
    right_center = _center(right)
    canvas.text(["えっ", "なんで"], (left_center[0] - 20, left_center[1]), 30, bubble=left)
    canvas.text(
        ["だって", "昨日", "言ったじゃん"], (right_center[0] + 20, right_center[1]), 28, bubble=right
    )
    return canvas


def _synthetic_textured_narration() -> _Canvas:
    canvas = _Canvas(720, 1000)
    canvas.rect((16, 16, 704, 484))
    canvas.tone((20, 20, 700, 480), spacing=7, radius=1.6, level=110)
    canvas.text(["その日", "町は", "眠っていた"], (560, 250), 30, kind="narration")
    canvas.rect((16, 500, 704, 984))
    canvas.gradient((20, 504, 700, 980), (30, 30, 30), (95, 95, 95), noise=6.0, seed=7)
    canvas.text(["静かな", "夜だった"], (520, 740), 30, kind="narration", color=(255, 255, 255))
    caption: Box = (60, 540, 250, 610)
    canvas.rect(caption, width=2, fill=(255, 255, 255))
    canvas.text(["三年後"], _center(caption), 30, vertical=False, kind="caption", bubble=caption)
    return canvas


def _synthetic_sfx() -> _Canvas:
    canvas = _Canvas(720, 720)
    canvas.rect((16, 16, 704, 704), fill=(225, 225, 225))
    canvas.hatch((20, 20, 700, 700), spacing=10, width=2, color=(120, 120, 120))
    canvas.text(["ドドド"], (560, 330), 80, kind="sfx", stroke=5)
    canvas.text(["バン！！"], (270, 560), 90, vertical=False, kind="sfx", stroke=5)
    return canvas


def _synthetic_webtoon_strip() -> _Canvas:
    canvas = _Canvas(640, 2400)
    canvas.gradient((0, 0, 640, 2400), (232, 240, 252), (252, 232, 222))
    canvas.rounded((40, 120, 600, 620), 24, fill=(190, 215, 235))
    canvas.draw.ellipse((200, 300, 440, 560), fill=(240, 200, 170), outline=(60, 60, 60), width=3)
    canvas.rounded((40, 1300, 600, 1900), 24, fill=(210, 230, 200))
    canvas.draw.ellipse((150, 1450, 490, 1800), fill=(250, 220, 190), outline=(60, 60, 60), width=3)
    bubbles: list[tuple[Box, list[str], int]] = [
        ((150, 680, 490, 800), ["おはよう！"], 32),
        ((100, 900, 540, 1080), ["今日は", "どこへ行くの？"], 30),
        ((180, 1960, 460, 2060), ["ひみつ"], 32),
        ((120, 2150, 520, 2330), ["えー", "教えてよ"], 30),
    ]
    for box, lines, size in bubbles:
        canvas.rounded(box, 36, fill=(255, 255, 255), outline=3)
        canvas.text(lines, _center(box), size, vertical=False, bubble=box)
    return canvas


def _synthetic_tone_rotated() -> _Canvas:
    """Adversarial: screentone on rotated lattices with non-integer periods.

    Every periodic-background trick tuned on the axis-aligned integer lattices
    of ``textured-narration`` should fail here first.
    """
    canvas = _Canvas(720, 1000)
    canvas.rect((16, 16, 704, 484))
    # Darker than the 110 gray of the classic page, like real printed tone.
    canvas.tone_rotated((20, 20, 700, 480), spacing=7.3, radius=1.7, level=90, angle=15.0)
    canvas.text(["その日", "雨は", "降り続いた"], (560, 250), 30, kind="narration")
    canvas.text(["ズドドン"], (240, 130), 72, vertical=False, kind="sfx", stroke=5)
    canvas.rect((16, 500, 704, 984))
    canvas.tone_rotated((20, 504, 700, 980), spacing=8.6, radius=1.5, level=95, angle=-24.0)
    canvas.text(["夜の", "海鳴り"], (540, 700), 30, vertical=False, kind="narration")
    caption: Box = (60, 540, 250, 610)
    canvas.rect(caption, width=2, fill=(255, 255, 255))
    canvas.text(["三日目"], _center(caption), 30, vertical=False, kind="caption", bubble=caption)
    return canvas


def _synthetic_faint_lettering() -> _Canvas:
    """Adversarial: light-gray (185) lettering where the dark-glyph tests pass it by."""
    canvas = _Canvas(720, 1000)
    canvas.rect((16, 16, 704, 484))
    bubbles: list[tuple[Box, list[str], int, tuple[int, int, int]]] = [
        ((400, 40, 660, 330), ["そうかも", "しれない"], 30, (185, 185, 185)),
        ((60, 70, 280, 300), ["ちょっと", "聞いて"], 28, (185, 185, 185)),
        ((360, 540, 670, 900), ["明日また", "ここで", "会おう"], 30, (0, 0, 0)),
    ]
    for box, lines, size, color in bubbles:
        canvas.ellipse(box)
        canvas.text(lines, _center(box), size, bubble=box, color=color)
    canvas.rect((16, 500, 704, 984))
    canvas.tone((20, 504, 700, 980), spacing=7, radius=1.6, level=110)
    canvas.text(["ゆっくり", "歩こう"], (520, 740), 30, vertical=False, kind="narration", color=(185, 185, 185))
    return canvas


def _synthetic_text_touching_outline() -> _Canvas:
    """Adversarial: lettering that crosses the bubble outline.

    Blanking the text rectangle opens the outline where text touches it
    (``masks.py:243``), and OCR ink joined to the outline breaks the
    smallest-closed-contour search. Two bubbles whose text touches by
    construction, one control bubble with room to spare.
    """
    canvas = _Canvas(720, 1000)
    canvas.rect((16, 16, 704, 984))
    canvas.draw.arc((120, 560, 320, 760), 180, 360, fill=(40, 40, 40), width=3)
    tall: Box = (100, 80, 360, 420)
    canvas.ellipse(tall)
    # 9 rows of 40 px cells: the block is 384 px tall inside a 340 px bubble,
    # so the first and last rows sit on the outline poles.
    canvas.text(
        ["はじめましてよね", "よろしくお願いし"],
        _center(tall),
        40,
        bubble=tall,
    )
    wide: Box = (400, 80, 680, 360)
    canvas.ellipse(wide)
    # 11 horizontal cells of 36 px: 360 px wide inside a 280 px bubble.
    canvas.text(
        ["さよならはもう言わない"],
        _center(wide),
        36,
        vertical=False,
        bubble=wide,
    )
    control: Box = (420, 500, 660, 700)
    canvas.ellipse(control)
    canvas.text(["うん"], _center(control), 30, bubble=control)
    return canvas


SYNTHETIC_PAGES: dict[str, Callable[[], _Canvas]] = {
    "white-bubbles": _synthetic_white_bubbles,
    "conjoined-bubbles": _synthetic_conjoined_bubbles,
    "textured-narration": _synthetic_textured_narration,
    "sfx": _synthetic_sfx,
    "webtoon-strip": _synthetic_webtoon_strip,
    "tone-rotated": _synthetic_tone_rotated,
    "faint-lettering": _synthetic_faint_lettering,
    "text-touching-outline": _synthetic_text_touching_outline,
}


@lru_cache(maxsize=None)
def build_synthetic_page(name: str) -> SyntheticPage:
    """Draw a named synthetic page. Callers must copy arrays before mutating."""
    return SYNTHETIC_PAGES[name]().finish(name)


@dataclass
class LoadedPage:
    page: CorpusPage
    image: np.ndarray
    source: str
    synthetic: SyntheticPage | None = None


def load_page(page: CorpusPage, manifest_dir: Path) -> LoadedPage | None:
    """Return the page pixels, or None when a private page file is absent."""
    if page.synthetic is not None:
        synthetic = build_synthetic_page(page.synthetic)
        return LoadedPage(page, synthetic.image.copy(), f"synthetic:{page.synthetic}", synthetic)
    assert page.path is not None
    path = (manifest_dir / page.path).resolve()
    if not path.is_file():
        return None
    with Image.open(path) as opened:
        image = np.array(opened.convert("RGB"), dtype=np.uint8)
    return LoadedPage(page, image, f"file:{page.path}")


def image_digest(image: np.ndarray) -> str:
    digest = hashlib.sha256()
    digest.update(repr(image.shape).encode("ascii"))
    digest.update(np.ascontiguousarray(image).tobytes())
    return digest.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Deterministic stand-in translator
# ---------------------------------------------------------------------------

_STUB_WORDS = (
    "hey", "wait", "what", "really", "that", "is", "not", "how", "we", "should",
    "go", "home", "now", "you", "never", "told", "me", "about", "this", "place",
    "again", "later", "maybe", "it", "was", "quiet", "night", "town", "still",
    "asleep", "come", "here", "please", "listen", "right", "okay", "sure", "the",
)  # fmt: skip


def stub_translate(text: str) -> str:
    """Deterministic English-like text about as long as a real translation.

    Typesetting metrics depend on text length, so the stub keeps the usual
    JA to EN expansion (about 2.7 Latin letters per Japanese character) while
    removing provider variance and network calls. Punctuation-only text is
    echoed, as real translators do.
    """
    source = unicodedata.normalize("NFKC", text).strip()
    letters = [char for char in source if char.isalnum()]
    if not letters:
        return text
    digest = hashlib.sha256(source.encode("utf-8")).digest()
    count = max(1, min(24, round(len(letters) / 2.2)))
    words = [_STUB_WORDS[digest[index] % len(_STUB_WORDS)] for index in range(count)]
    sentence = " ".join(words)
    trailing = re.search(r"[!?.]+$", source)
    return sentence[0].upper() + sentence[1:] + (trailing.group(0) if trailing else ".")


# ---------------------------------------------------------------------------
# Instrumentation: wrap stage boundaries for the duration of a run.
# ---------------------------------------------------------------------------


@contextmanager
def _swap(target: Any, name: str, replacement: Any) -> Iterator[None]:
    """Temporarily replace an attribute, restoring the original afterwards."""
    namespace = vars(target)
    had_own = name in namespace
    original = namespace.get(name)
    setattr(target, name, replacement)
    try:
        yield
    finally:
        if had_own:
            setattr(target, name, original)
        else:
            delattr(target, name)


@dataclass
class PageProbe:
    """Everything observed while one page went through the pipeline."""

    timings: dict[str, float] = field(default_factory=dict)
    in_mask_phase: bool = False
    mask_path: str | None = None
    mask_records: list[tuple[np.ndarray, Box, str]] = field(default_factory=list)
    # The block each mask record belongs to, index-aligned with mask_records.
    # Oracle-mask records (no real block) hold None.
    mask_blocks: list[TextBlock | None] = field(default_factory=list)
    # Per-block fill-router decisions (tier, band error, sizes, reason).
    fill_decisions: list[dict[str, Any]] = field(default_factory=list)
    flux_counts: tuple[int, int] | None = None
    white_filled_source: np.ndarray | None = None
    model_calls: list[tuple[np.ndarray, np.ndarray, np.ndarray, str]] = field(default_factory=list)
    inpaint_input: np.ndarray | None = None
    inpaint_output: np.ndarray | None = None
    in_render: bool = False
    in_overflow: bool = False
    preferred_font_size: int = int(API_RENDER_DEFAULTS["preferred_font_size"])
    render_regions: list[dict[str, Any]] = field(default_factory=list)
    verified_blocks: list[TextBlock] | None = None

    @contextmanager
    def timed(self, stage: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.timings[stage] = self.timings.get(stage, 0.0) + time.perf_counter() - started


class _TimedDetector:
    def __init__(self, inner: Any, instrumentation: Instrumentation) -> None:
        self._inner = inner
        self._instrumentation = instrumentation

    async def detect(self, image: np.ndarray) -> list[TextBlock]:
        with self._instrumentation.probe.timed("detect"):
            return await self._inner.detect(image)


class _TruthDetector:
    """Returns a synthetic page's ground-truth boxes the way RT-DETR would."""

    def __init__(self, page: SyntheticPage | None, instrumentation: Instrumentation) -> None:
        self._page = page
        self._instrumentation = instrumentation

    async def detect(self, image: np.ndarray) -> list[TextBlock]:
        if self._page is None:
            raise RuntimeError("the truth detector needs a synthetic page with ground truth")
        with self._instrumentation.probe.timed("detect"):
            blocks = []
            for truth in self._page.truth:
                block = TextBlock(list(truth.xyxy), block_type="sfx" if truth.bubble is None else "text")
                block.text = truth.text
                if truth.bubble is not None:
                    block.bubble_xyxy = list(truth.bubble)
                blocks.append(block)
            return blocks


class Instrumentation:
    """Installs the stage wrappers on one pipeline and collects a PageProbe per page."""

    def __init__(
        self,
        pipeline: TranslationPipeline,
        *,
        mask_mode: str,
        translator: str,
        detector: str,
        fill: str = "auto",
    ) -> None:
        self.pipeline = pipeline
        self.mask_mode = mask_mode
        self.translator = translator
        self.detector = detector
        self.fill = fill
        self.probe = PageProbe()
        self.synthetic: SyntheticPage | None = None

    def begin_page(self, synthetic: SyntheticPage | None) -> PageProbe:
        self.probe = PageProbe()
        self.synthetic = synthetic
        return self.probe

    def oracle_ink_mask(self, shape: tuple[int, ...]) -> np.ndarray | None:
        """Diluted truth ink as the whole erase mask, or None without truth."""
        if self.synthetic is None:
            return None
        mask = self.synthetic.ink.astype(np.uint8) * 255
        mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
        return mask[: shape[0], : shape[1]]

    def _record_oracle_blocks(self, mask: np.ndarray) -> None:
        """Log one mask record per truth box so the mask metrics still add up."""
        assert self.synthetic is not None
        for truth in self.synthetic.truth:
            x1, y1, x2, y2 = truth.xyxy
            bounds = (max(0, x1 - 2), max(0, y1 - 2), x2 + 2, y2 + 2)
            self.probe.mask_records.append(
                (mask[bounds[1] : bounds[3], bounds[0] : bounds[2]], bounds, "oracle-ink")
            )
            self.probe.mask_blocks.append(None)

    def oracle_fill_output(self, image: np.ndarray) -> np.ndarray:
        """Paste the clean plate inside the pipeline's own declared erase mask."""
        assert self.synthetic is not None
        erase = np.zeros(image.shape[:2], dtype=bool)
        for local, bounds, _strategy in self.probe.mask_records:
            _place(erase, local, bounds)
        output = image.copy()
        output[erase] = self.synthetic.clean[erase]
        return output

    @contextmanager
    def mask_phase(self) -> Iterator[None]:
        self.probe.in_mask_phase = True
        try:
            yield
        finally:
            self.probe.in_mask_phase = False

    @contextmanager
    def installed(self) -> Iterator[None]:
        pipeline = self.pipeline
        detectors = pipeline._detectors
        inpainters = pipeline._inpainters
        original_get = detectors.get
        original_run_ocr = pipeline._run_ocr
        original_inpaint = pipeline._inpaint
        original_inpaint_page = inpainters.inpaint_page
        original_verify = type(pipeline)._verify_rendered_text
        original_translate = orchestrator_module.translate_texts
        original_prepare_flux = orchestrator_module.prepare_flux_inpainting
        original_render = orchestrator_module.render_text
        original_contour = masks_module.contour_safe_mask_for_block
        original_rectangle = masks_module.rectangular_mask_for_block
        original_fit = rendering_module.auto_fit_font_size
        original_overflow = rendering_module.find_overflow_region

        async def get_detector(detector_type: str) -> Any:
            if self.detector == TRUTH_DETECTOR:
                return _TruthDetector(self.synthetic, self)
            return _TimedDetector(await original_get(detector_type), self)

        async def run_ocr(*args: Any, **kwargs: Any) -> list[str]:
            with self.probe.timed("ocr"):
                return await original_run_ocr(*args, **kwargs)

        async def translate_texts(original_texts: list[str], target_lang: str, **kwargs: Any) -> list[str]:
            with self.probe.timed("translate"):
                if self.translator == STUB_TRANSLATOR:
                    return [stub_translate(text) for text in original_texts]
                return await original_translate(original_texts, target_lang, **kwargs)

        async def inpaint(
            image: np.ndarray,
            blocks: list[TextBlock],
            inpainter: str,
            detector_type: str,
            result: dict[str, Any],
        ) -> np.ndarray:
            # The pipeline picks rectangular masks from the detector name
            # (animetext-yolo). Mask modes reuse that switch so the YOLO mask
            # path can be scored on other detectors' boxes.
            if self.mask_mode == "rectangular":
                detector_type = "animetext-yolo"
            elif self.mask_mode in ("contour", "oracle"):
                detector_type = "rtdetr"
            self.probe.inpaint_input = image
            output = await original_inpaint(image, blocks, inpainter, detector_type, result)
            if self.fill == "oracle" and self.synthetic is not None:
                # Measure the mask alone: whatever the pipeline declared as
                # erased is replaced by the clean plate, fill included.
                output = self.oracle_fill_output(image)
                result["inpainter_used"] = "oracle-fill"
                result["inpainting_fallback"] = False
                result["inpainting_strategy"] = "oracle-fill"
            self.probe.inpaint_output = output
            return output

        def prepare_flux(
            image: np.ndarray, blocks: list[TextBlock], rectangular: bool = False
        ) -> tuple[np.ndarray, np.ndarray, int, int, list[dict[str, Any]]]:
            with self.probe.timed("mask"), self.mask_phase():
                oracle = self.oracle_ink_mask(image.shape) if self.mask_mode == "oracle" else None
                if oracle is not None:
                    # No fill tiers: the model must fill every block through
                    # the perfect mask, so the oracle run measures the fill
                    # alone.
                    self._record_oracle_blocks(oracle)
                    cleaned, flux_mask, white, textured, decisions = (
                        image.copy(),
                        oracle,
                        0,
                        len(blocks),
                        [],
                    )
                else:
                    cleaned, flux_mask, white, textured, decisions = original_prepare_flux(
                        image, blocks, rectangular=rectangular
                    )
                self.probe.fill_decisions = decisions
            self.probe.mask_path = "oracle" if oracle is not None else ("flux-rectangular" if rectangular else "flux")
            self.probe.flux_counts = (int(white), int(textured))
            self.probe.white_filled_source = cleaned
            return cleaned, flux_mask, white, textured, decisions

        def contour_mask(image: np.ndarray, block: TextBlock) -> tuple[np.ndarray, Box, str]:
            local, bounds, strategy = original_contour(image, block)
            if self.probe.in_mask_phase:
                self.probe.mask_records.append((local, bounds, strategy))
                self.probe.mask_blocks.append(block)
            return local, bounds, strategy

        def rectangle_mask(image: np.ndarray, block: TextBlock) -> tuple[np.ndarray, Box, str]:
            local, bounds, strategy = original_rectangle(image, block)
            if self.probe.in_mask_phase:
                self.probe.mask_records.append((local, bounds, strategy))
                self.probe.mask_blocks.append(block)
            return local, bounds, strategy

        async def inpaint_page(inpainter_type: str, image: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, str]:
            if self.fill == "oracle" and self.synthetic is not None:
                # No model runs; the clean plate is pasted after Step 4
                # returns (see the inpaint wrapper above).
                return image, "oracle-fill"
            with self.probe.timed("inpaint_model"):
                output, used = await original_inpaint_page(inpainter_type, image, mask)
            self.probe.model_calls.append((image, mask, output, str(used)))
            return output, used

        def render_text(image: np.ndarray, blocks: list[TextBlock], **options: Any) -> np.ndarray:
            self.probe.preferred_font_size = int(
                options.get("preferred_font_size", API_RENDER_DEFAULTS["preferred_font_size"])
            )
            self.probe.in_render = True
            try:
                with self.probe.timed("render"):
                    return original_render(image, blocks, **options)
            finally:
                self.probe.in_render = False

        def auto_fit(*args: Any, **kwargs: Any) -> int:
            size = original_fit(*args, **kwargs)
            # render_text fits each region once; find_overflow_region then
            # refits candidate boxes, which are attributed to that region.
            if self.probe.in_render and not self.probe.in_overflow:
                self.probe.render_regions.append({"fit": int(size), "overflow": None})
            return size

        def find_overflow(*args: Any, **kwargs: Any) -> Any:
            self.probe.in_overflow = True
            try:
                found = original_overflow(*args, **kwargs)
            finally:
                self.probe.in_overflow = False
            if self.probe.in_render and self.probe.render_regions:
                region = self.probe.render_regions[-1]
                region["overflow"] = found is not None
                if found is not None:
                    region["overflow_fit"] = int(found[1][3])
            return found

        async def verify(ocr_engine: Any, rendered: np.ndarray, blocks: list[TextBlock]) -> None:
            with self.probe.timed("verify"):
                await original_verify(ocr_engine, rendered, blocks)
            self.probe.verified_blocks = list(blocks)

        with ExitStack() as stack:
            stack.enter_context(_swap(detectors, "get", get_detector))
            stack.enter_context(_swap(pipeline, "_run_ocr", run_ocr))
            stack.enter_context(_swap(pipeline, "_inpaint", inpaint))
            stack.enter_context(_swap(pipeline, "_verify_rendered_text", verify))
            stack.enter_context(_swap(inpainters, "inpaint_page", inpaint_page))
            stack.enter_context(_swap(orchestrator_module, "translate_texts", translate_texts))
            stack.enter_context(_swap(orchestrator_module, "prepare_flux_inpainting", prepare_flux))
            stack.enter_context(_swap(orchestrator_module, "render_text", render_text))
            stack.enter_context(_swap(masks_module, "contour_safe_mask_for_block", contour_mask))
            stack.enter_context(_swap(masks_module, "rectangular_mask_for_block", rectangle_mask))
            stack.enter_context(_swap(rendering_module, "auto_fit_font_size", auto_fit))
            stack.enter_context(_swap(rendering_module, "find_overflow_region", find_overflow))
            yield


# ---------------------------------------------------------------------------
# Per-page metrics
# ---------------------------------------------------------------------------

# Hiragana, katakana (without the middle dot and prolonged sound mark) and Han.
_KANA_KANJI = re.compile(r"[ぁ-ゖァ-ヺ㐀-䶿一-鿿豈-﫿々]")
MIN_READABLE_FONT_SIZE = rendering_module.MIN_READABLE_FONT_SIZE
# A pixel that differs by more than this many levels (any channel) counts as visible.
VISIBLE_LEVELS = 40


def has_kana_kanji(text: str | None) -> bool:
    """CJK residue that is actual Japanese text, not full-width Latin.

    manga-ocr converts ASCII to full-width forms, which the pipeline's own
    has_cjk check counts as CJK; NFKC folds those back before this check.
    """
    return bool(text) and _KANA_KANJI.search(unicodedata.normalize("NFKC", str(text))) is not None


def _changed(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    if first.shape != second.shape:
        raise ValueError(f"shape mismatch {first.shape} != {second.shape}")
    diff = first != second
    return diff.any(axis=2) if diff.ndim == 3 else diff


def _place(target: np.ndarray, local: np.ndarray, bounds: Box) -> None:
    if local.size == 0:
        return
    x1, y1, x2, y2 = (int(value) for value in bounds)
    region = target[y1:y2, x1:x2]
    height = min(region.shape[0], local.shape[0])
    width = min(region.shape[1], local.shape[1])
    region[:height, :width] |= local[:height, :width] > 0


def mask_kind(strategy: str, empty: bool) -> str:
    if empty:
        return "skipped"
    if strategy == "rectangle":
        return "rectangular"
    if strategy == "bubble-interior":
        return "bubble_interior"
    if strategy.endswith("glyph"):
        return "contour_safe_glyph"
    return "other"


def _mask_metrics(probe: PageProbe, shape: tuple[int, ...]) -> tuple[dict[str, Any], np.ndarray]:
    erase = np.zeros(shape[:2], dtype=bool)
    strategies: Counter[str] = Counter()
    kinds: Counter[str] = Counter({kind: 0 for kind in MASK_KINDS})
    for local, bounds, strategy in probe.mask_records:
        empty = local.size == 0 or not np.any(local)
        strategies[strategy] += 1
        kinds[mask_kind(strategy, empty)] += 1
        if not empty:
            _place(erase, local, bounds)

    tier_counts: Counter[str] = Counter()
    for decision in probe.fill_decisions:
        tier_counts[str(decision.get("tier_name") or "unknown")] += 1

    router_fill_px = 0
    if probe.white_filled_source is not None and probe.inpaint_input is not None:
        filled = _changed(probe.inpaint_input, probe.white_filled_source)
        router_fill_px = int(np.count_nonzero(filled))
    if probe.flux_counts is not None:
        filled_blocks, model_blocks = probe.flux_counts
    else:
        filled_blocks = 0
        model_blocks = sum(kinds[kind] for kind in MASK_KINDS if kind != "skipped")
    metrics = {
        "path": probe.mask_path,
        "strategies": dict(sorted(strategies.items())),
        "kinds": dict(kinds),
        "white_fill_blocks": int(filled_blocks),
        "model_blocks": int(model_blocks),
        "tier_counts": dict(sorted(tier_counts.items())),
        "fill_decisions": probe.fill_decisions,
        "mask_px": int(np.count_nonzero(erase)),
        "white_fill_px": router_fill_px,
        "mask_fraction": round(float(np.count_nonzero(erase)) / erase.size, 6),
    }
    return metrics, erase


def _inpaint_metrics(probe: PageProbe, erase: np.ndarray, requested: str, result: dict[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "requested": requested,
        "used": result.get("inpainter_used"),
        "fallback": bool(result.get("inpainting_fallback", False)),
        "strategy": result.get("inpainting_strategy") or ("model" if probe.model_calls else "none"),
        "changed_outside_mask_px": 0,
        "changed_inside_mask_px": 0,
        "model_changed_outside_mask_px": 0,
    }
    if probe.inpaint_input is not None and probe.inpaint_output is not None:
        changed = _changed(probe.inpaint_input, probe.inpaint_output)
        metrics["changed_outside_mask_px"] = int(np.count_nonzero(changed & ~erase))
        metrics["changed_inside_mask_px"] = int(np.count_nonzero(changed & erase))
    for source, mask, output, _used in probe.model_calls:
        model_mask = mask[..., 0] if mask.ndim == 3 else mask
        leaked = _changed(source, np.asarray(output)) & (model_mask == 0)
        metrics["model_changed_outside_mask_px"] += int(np.count_nonzero(leaked))
    return metrics


def _render_metrics(probe: PageProbe) -> dict[str, Any]:
    preferred = probe.preferred_font_size
    sizes: list[int] = []
    shrink = below_min = unfit = attempts = overflows = 0
    for region in probe.render_regions:
        size = int(region["fit"])
        if region["overflow"] is not None:
            attempts += 1
        if region["overflow"]:
            overflows += 1
            size = int(region.get("overflow_fit", size))
        if size <= 0:
            unfit += 1
            continue
        sizes.append(size)
        if size < preferred:
            shrink += 1
        if size < MIN_READABLE_FONT_SIZE:
            below_min += 1
    return {
        "regions": len(probe.render_regions),
        "preferred_font_size": preferred,
        "font_sizes": sizes,
        # Per-page spread of the final dialogue font sizes (max - min).
        "font_size_spread": (max(sizes) - min(sizes)) if sizes else None,
        "shrink_events": shrink,
        "below_min_readable": below_min,
        "overflow_attempts": attempts,
        "overflow_events": overflows,
        "unfit_skipped": unfit,
    }


# ---------------------------------------------------------------------------
# Mask reason codes: why a block ended without a bubble interior.
# ---------------------------------------------------------------------------

# Final mask strategies that did not use a bubble interior.
NO_INTERIOR_STRATEGIES = frozenset({"glyph", "no-closed-bubble", "unsafe-sfx"})


def _bubble_interior_reason(image: np.ndarray, block: TextBlock, *, glyphs_present: bool) -> str:
    """Why no bubble interior was used for one block.

    A diagnostic mirror of the candidate filters in
    ``masks.bubble_interior_mask_for_block`` (masks.py:187-305): the same ROI
    construction, the same helpers and the same rule order, so the counts
    attribute each failure to the rule that caused it. The pipeline helpers
    themselves are untouched; keep this function in sync with them (a unit
    test cross-checks the two on real blocks).
    """
    if block.block_type != "text":
        # contour_safe_mask_for_block never tries an interior for these.
        return "text_free"
    height, width = image.shape[:2]
    tx1, ty1, tx2, ty2 = [int(value) for value in block.xyxy]
    tx1, ty1 = max(0, tx1), max(0, ty1)
    tx2, ty2 = min(width, tx2), min(height, ty2)
    if tx2 <= tx1 or ty2 <= ty1:
        return "no_contour"

    detector_bounds: Box | None = None
    bubble = block.bubble_xyxy
    if bubble is not None:
        try:
            candidate = int_box(bubble)
        except (TypeError, ValueError):
            candidate = None
        if candidate is not None and candidate[2] > candidate[0] and candidate[3] > candidate[1]:
            detector_bounds = candidate

    if detector_bounds is not None:
        bx1, by1, bx2, by2 = detector_bounds
        padding = max(6, int(min(bx2 - bx1, by2 - by1) * 0.08))
        x1, y1 = max(0, bx1 - padding), max(0, by1 - padding)
        x2, y2 = min(width, bx2 + padding), min(height, by2 + padding)
    else:
        padding_x = max(32, int((tx2 - tx1) * 1.25), int((ty2 - ty1) * 0.5))
        padding_y = max(32, int((ty2 - ty1) * 0.75), int((tx2 - tx1) * 1.25))
        x1, y1 = max(0, tx1 - padding_x), max(0, ty1 - padding_y)
        x2, y2 = min(width, tx2 + padding_x), min(height, ty2 + padding_y)

    roi = image[y1:y2, x1:x2]
    if roi.size == 0 or min(roi.shape[:2]) < 5:
        return "no_contour"

    gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
    local_text = (tx1 - x1, ty1 - y1, tx2 - x1, ty2 - y1)
    structure_gray = gray.copy()
    sx1, sy1 = max(0, local_text[0]), max(0, local_text[1])
    sx2, sy2 = min(gray.shape[1], local_text[2]), min(gray.shape[0], local_text[3])
    if sx2 > sx1 and sy2 > sy1:
        structure_gray[sy1:sy2, sx1:sx2] = int(np.percentile(gray, 90))
    blurred = cv2.GaussianBlur(structure_gray, (3, 3), 0)
    otsu_threshold, _binary = cv2.threshold(
        blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    dark_threshold = int(np.clip(otsu_threshold, 70, 210))
    dark = np.where(blurred <= dark_threshold, 255, 0).astype(np.uint8)
    edges = cv2.Canny(blurred, 50, 150)
    outline = cv2.bitwise_or(dark, edges)
    outline = cv2.morphologyEx(
        outline, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8), iterations=1
    )
    contours, _hierarchy = cv2.findContours(
        outline, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return "no_contour"

    text_width = max(1, local_text[2] - local_text[0])
    text_height = max(1, local_text[3] - local_text[1])
    text_area = text_width * text_height
    center = (
        (local_text[0] + local_text[2]) / 2.0,
        (local_text[1] + local_text[3]) / 2.0,
    )
    roi_height, roi_width = gray.shape
    candidates: list[tuple[float, Any]] = []
    single_failures: Counter[str] = Counter()
    for contour in contours:
        area = float(abs(cv2.contourArea(contour)))
        failed: list[str] = []
        if area < max(48.0, text_area * 1.15) or area >= roi.size / 3 * 0.9:
            failed.append("area")
        if cv2.pointPolygonTest(contour, center, False) < 0:
            failed.append("center")
        cx, cy, cw, ch = cv2.boundingRect(contour)
        if cx <= 1 or cy <= 1 or cx + cw >= roi_width - 1 or cy + ch >= roi_height - 1:
            failed.append("edge")
        overlap_width = max(0, min(cx + cw, local_text[2]) - max(cx, local_text[0]))
        overlap_height = max(0, min(cy + ch, local_text[3]) - max(cy, local_text[1]))
        if overlap_width * overlap_height < text_area * 0.75:
            failed.append("overlap")
        if not failed:
            candidates.append((area, contour))
        elif len(failed) == 1:
            single_failures[failed[0]] += 1

    if not candidates:
        # No contour survived; report the rule that rejected the near misses.
        if single_failures.get("edge"):
            return "contour_touches_roi_edge"
        if single_failures.get("area"):
            return "area_test_failed"
        return "no_contour"

    _area, contour = min(candidates, key=lambda candidate: candidate[0])
    interior = np.zeros(gray.shape, dtype=np.uint8)
    cv2.drawContours(interior, [contour], -1, 255, thickness=cv2.FILLED)
    interior = cv2.erode(interior, np.ones((3, 3), np.uint8), iterations=2)
    text_roi = interior[
        max(0, local_text[1]) : min(roi_height, local_text[3]),
        max(0, local_text[0]) : min(roi_width, local_text[2]),
    ]
    if text_roi.size == 0 or np.count_nonzero(text_roi) < text_area * 0.35:
        return "coverage_below_35"
    # An interior was found and kept, so a block still on the glyph path lost
    # its glyphs to the interior clipping, not to the contour search.
    return "clipped_ink_empty" if glyphs_present else "interior_found"


def _mask_reason_codes(image: np.ndarray, probe: PageProbe) -> dict[str, int]:
    """Count why every no-interior block never used a bubble interior."""
    reasons: Counter[str] = Counter()
    for record, block in zip(probe.mask_records, probe.mask_blocks):
        _local, _bounds, strategy = record
        if strategy not in NO_INTERIOR_STRATEGIES or block is None:
            continue
        reasons[
            _bubble_interior_reason(image, block, glyphs_present=strategy == "glyph")
        ] += 1
    return dict(sorted(reasons.items()))


# ---------------------------------------------------------------------------
# Texture-aware and geometry diagnostics for synthetic pages.
# ---------------------------------------------------------------------------

# Low-pass sigma, about the tone period (7-10 px on the synthetic pages): dot
# and hatch phase noise vanish after the blur while glyph strokes survive.
TONE_SIGMA = 5.0
# Blurred contrast that still reads as a stroke rather than tone misalignment.
LP_LEVELS = 24


def _gray(image: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)


def _low_pass_residual(inpainted: np.ndarray, clean: np.ndarray, ink: np.ndarray) -> float | None:
    """Residual ink after a low-pass filter: separates strokes from phase noise."""
    if not ink.any():
        return None
    blurred_out = cv2.GaussianBlur(_gray(inpainted).astype(np.float32), (0, 0), TONE_SIGMA)
    blurred_clean = cv2.GaussianBlur(_gray(clean).astype(np.float32), (0, 0), TONE_SIGMA)
    bad = np.abs(blurred_out - blurred_clean) > LP_LEVELS
    return round(float(bad[ink].mean()), 4)


def _hole_in_tone_px(
    inpainted: np.ndarray, clean: np.ndarray, text_region: np.ndarray, ink: np.ndarray
) -> int:
    """Px inside the text boxes where the clean plate is textured but the output is flat.

    A white fill over screentone flattens every dot in the text box; the
    over-erase metric cannot see it because it stays near the text. Truth ink
    pixels (and their one-pixel antialiasing fringe) do not count: leftover
    lettering is residual ink's business, not a fill hole; the hole is the
    texture that vanished between the strokes.
    """
    kernel = np.ones((7, 7), np.uint8)
    plate = _gray(clean)
    output = _gray(inpainted)
    plate_textured = (cv2.dilate(plate, kernel) - cv2.erode(plate, kernel)) > 40
    output_flat = (cv2.dilate(output, kernel) - cv2.erode(output, kernel)) < 20
    not_ink = (
        cv2.dilate(ink.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1) == 0
    )
    return int(
        np.count_nonzero(plate_textured & output_flat & text_region & not_ink)
    )


def _render_ink_outside_bubble_px(
    inpainted: np.ndarray, rendered: np.ndarray, truths: tuple[TruthBox, ...]
) -> int:
    """Px of rendered ink that falls outside a truth bubble's interior.

    The interior is the truth bubble box shrunk by 4 px (the outline sits at
    the box edge); ink between that boundary and the box grown by 12 px has
    crossed the outline. Lettering with no bubble (SFX, narration) never
    counts, because only boxes around the drawn bubbles are inspected.
    """
    bubbles = [truth.bubble for truth in truths if truth.bubble is not None]
    if not bubbles:
        return 0
    ink = _changed(inpainted, rendered)
    height, width = ink.shape
    near = np.zeros((height, width), dtype=bool)
    interior = np.zeros((height, width), dtype=bool)
    for x1, y1, x2, y2 in bubbles:
        near[max(0, y1 - 12) : min(height, y2 + 12), max(0, x1 - 12) : min(width, x2 + 12)] = True
        interior[max(0, y1 + 4) : min(height, y2 - 4), max(0, x1 + 4) : min(width, x2 - 4)] = True
    return int(np.count_nonzero(ink & near & ~interior))


def _strip_for_cer(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", str(text)).split())


def _edit_distance(first: str, second: str) -> int:
    """Levenshtein distance between two short strings."""
    if len(first) < len(second):
        first, second = second, first
    previous = list(range(len(second) + 1))
    for row, a in enumerate(first, 1):
        current = [row]
        for col, b in enumerate(second, 1):
            current.append(
                min(previous[col] + 1, current[col - 1] + 1, previous[col - 1] + (a != b))
            )
        previous = current
    return previous[-1]


def _ocr_cer(
    truths: tuple[TruthBox, ...], detected: Sequence[Sequence[int]], texts: Sequence[str]
) -> dict[str, Any]:
    """Character error rate of the OCR pass against TruthBox.text."""
    matched: list[float] = []
    for truth in truths:
        tx1, ty1, tx2, ty2 = truth.xyxy
        best_iou = 0.0
        best_text: str | None = None
        for box, text in zip(detected, texts):
            dx1, dy1, dx2, dy2 = (int(value) for value in list(box)[:4])
            ix1, iy1 = max(tx1, dx1), max(ty1, dy1)
            ix2, iy2 = min(tx2, dx2), min(ty2, dy2)
            intersection = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            if not intersection:
                continue
            union = (tx2 - tx1) * (ty2 - ty1) + (dx2 - dx1) * (dy2 - dy1) - intersection
            iou = intersection / union
            if iou > best_iou:
                best_iou, best_text = iou, str(text or "")
        reference = _strip_for_cer(truth.text)
        if best_iou >= 0.3 and best_text is not None and best_text.strip() and reference:
            matched.append(_edit_distance(_strip_for_cer(best_text), reference) / len(reference))
    return {
        "ocr_cer": round(float(statistics.fmean(matched)), 4) if matched else None,
        "ocr_cer_blocks": len(matched),
    }


def _verify_metrics(probe: PageProbe) -> dict[str, Any]:
    blocks = probe.verified_blocks
    if blocks is None:
        return {"ran": False, "checked": 0, "step6_flagged": 0, "rendered_checked": 0, "kana_kanji": 0}
    rendered = [
        block
        for block in blocks
        if block.translated_text.strip()
        and not is_unchanged_punctuation(block.text, block.translated_text)
    ]
    return {
        "ran": True,
        "checked": sum(block.verification_passed is not None for block in blocks),
        "step6_flagged": sum(block.verification_passed is False for block in blocks),
        "rendered_checked": len(rendered),
        "kana_kanji": sum(has_kana_kanji(block.verified_text) for block in rendered),
    }


def _truth_metrics(
    synthetic: SyntheticPage,
    detected: list[list[int]],
    inpainted: np.ndarray,
    *,
    erase: np.ndarray,
    rendered: np.ndarray | None,
    ocr_texts: Sequence[str],
) -> dict[str, Any]:
    height, width = synthetic.ink.shape
    detected_mask = np.zeros((height, width), dtype=bool)
    for box in detected:
        x1, y1, x2, y2 = (int(value) for value in box[:4])
        detected_mask[max(0, y1) : max(0, y2), max(0, x1) : max(0, x2)] = True
    text_region = np.zeros((height, width), dtype=bool)
    covered = 0
    for truth in synthetic.truth:
        x1, y1, x2, y2 = truth.xyxy
        text_region[y1:y2, x1:x2] = True
        if detected_mask[y1:y2, x1:x2].mean() >= 0.5:
            covered += 1
    error = np.abs(inpainted.astype(np.int16) - synthetic.clean.astype(np.int16))
    near_text = cv2.dilate(text_region.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    away = ~near_text
    damage = np.abs(inpainted.astype(np.int16) - synthetic.image.astype(np.int16)).max(axis=2)
    ink = synthetic.ink
    ink_total = int(np.count_nonzero(ink))
    bad = error.max(axis=2) > VISIBLE_LEVELS
    # Share of truth ink the erase mask (block masks plus white-fill masks)
    # covers; the residual splits into fill failure (inside the mask) and
    # mask miss (outside it).
    in_mask = ink & erase[:height, :width]
    return {
        "boxes": len(synthetic.truth),
        "recall": round(covered / len(synthetic.truth), 4) if synthetic.truth else None,
        "text_mae": round(float(error.mean(axis=2)[text_region].mean()), 3) if text_region.any() else None,
        "residual_ink_ratio": round(float((error.max(axis=2)[ink] > VISIBLE_LEVELS).mean()), 4)
        if ink.any()
        else None,
        "ink_mask_recall": round(float(np.count_nonzero(in_mask)) / ink_total, 4)
        if ink_total
        else None,
        "residual_in_mask_ratio": round(float(np.count_nonzero(bad & in_mask)) / ink_total, 4)
        if ink_total
        else None,
        "residual_out_of_mask_ratio": round(
            float(np.count_nonzero(bad & ink & ~erase[:height, :width])) / ink_total, 4
        )
        if ink_total
        else None,
        # Fill failure that survives a low-pass blur at the tone period: real
        # strokes rather than tone painted out of phase.
        "residual_ink_lp_ratio": _low_pass_residual(inpainted, synthetic.clean, ink),
        "hole_in_tone_px": _hole_in_tone_px(inpainted, synthetic.clean, text_region, ink),
        "render_ink_outside_bubble_px": (
            _render_ink_outside_bubble_px(inpainted, rendered, synthetic.truth)
            if rendered is not None
            else None
        ),
        **_ocr_cer(synthetic.truth, detected, ocr_texts),
        # Art visibly repainted away from any text (outlines, line art, tone).
        "over_erased_px": int(np.count_nonzero((damage > VISIBLE_LEVELS) & away)),
        # Any change away from text, however slight (re-synthesized flat areas).
        "touched_away_px": int(np.count_nonzero((damage > 0) & away)),
        "glyph_source": synthetic.glyph_source,
    }


def measure_page(
    probe: PageProbe,
    loaded: LoadedPage,
    result: dict[str, Any],
    requested_inpainter: str,
) -> dict[str, Any]:
    image = loaded.image
    mask_metrics, erase = _mask_metrics(probe, image.shape)
    texts = [str(text or "") for text in result.get("original_texts") or []]
    translated = [str(text or "") for text in result.get("translated_texts") or []]
    detected = [list(block["bbox"]) for block in result.get("blocks") or []]
    inpainted = probe.inpaint_output if probe.inpaint_output is not None else image
    timings = {stage: round(probe.timings.get(stage, 0.0), 4) for stage in STAGES}
    to_erase = sum(
        bool(target.strip()) and not is_unchanged_punctuation(source, target)
        for source, target in zip(texts, translated)
    )
    # Blocks the mask step dropped as duplicates of an OCR composite region.
    mask_metrics["suppressed_composites"] = (
        max(0, to_erase - len(probe.mask_records)) if probe.mask_path is not None else 0
    )
    # Why each no-interior block never used a bubble interior.
    if probe.mask_path is not None:
        mask_metrics["reason_codes"] = _mask_reason_codes(image, probe)
    rendered_image = result.get("image")
    rendered = np.asarray(rendered_image) if rendered_image is not None else None
    return {
        "blocks": {
            "detected": len(detected),
            "ocr_nonempty": sum(bool(text.strip()) for text in texts),
            "to_erase": to_erase,
        },
        "mask": mask_metrics,
        "inpaint": _inpaint_metrics(probe, erase, requested_inpainter, result),
        "render": _render_metrics(probe),
        "verify": _verify_metrics(probe),
        "truth": None
        if loaded.synthetic is None
        else _truth_metrics(
            loaded.synthetic,
            detected,
            np.asarray(inpainted),
            erase=erase,
            rendered=rendered,
            ocr_texts=texts,
        ),
        "timings": timings,
    }


# ---------------------------------------------------------------------------
# Running a corpus
# ---------------------------------------------------------------------------


@dataclass
class RunConfig:
    label: str
    detector: str = "rtdetr"
    mask_mode: str = "auto"
    inpainter: str = DEFAULT_INPAINTER
    ocr: str = "manga-ocr"
    translator: str = STUB_TRANSLATOR
    target_lang: str = "English"
    device: str = "cpu"
    verify: bool = True
    warmup: bool = True
    # "oracle" fill pastes the clean plate inside the pipeline's own mask,
    # measuring the mask alone; "auto" runs the real inpainter.
    fill: str = "auto"
    render_options: dict[str, Any] = field(default_factory=lambda: dict(API_RENDER_DEFAULTS))


async def _translate_page(pipeline: TranslationPipeline, image: np.ndarray, config: RunConfig) -> dict[str, Any]:
    return await pipeline.translate(
        image,
        target_lang=config.target_lang,
        detector="rtdetr" if config.detector == TRUTH_DETECTOR else config.detector,
        ocr=config.ocr,
        translator="eval-stub" if config.translator == STUB_TRANSLATOR else config.translator,
        inpainter=config.inpainter,
        render=True,
        verify_translation=config.verify,
        **config.render_options,
    )


def _save_page_images(directory: Path, probe: PageProbe, result: dict[str, Any], erase_shape: tuple[int, ...]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    erase = np.zeros(erase_shape[:2], dtype=bool)
    for local, bounds, _strategy in probe.mask_records:
        _place(erase, local, bounds)
    Image.fromarray(erase.astype(np.uint8) * 255).save(directory / "mask.png")
    if probe.inpaint_output is not None:
        Image.fromarray(np.asarray(probe.inpaint_output)).save(directory / "inpainted.png")
    if result.get("image") is not None:
        Image.fromarray(np.asarray(result["image"])).save(directory / "rendered.png")


async def run_corpus(
    pipeline: TranslationPipeline,
    pages: Sequence[CorpusPage],
    manifest_dir: Path,
    config: RunConfig,
    *,
    images_dir: Path | None = None,
) -> dict[str, Any]:
    """Score every page; the pipeline must already be initialized."""
    instrumentation = Instrumentation(
        pipeline,
        mask_mode=config.mask_mode,
        translator=config.translator,
        detector=config.detector,
        fill=config.fill,
    )
    loaded_pages = [(page, load_page(page, manifest_dir)) for page in pages]
    oracle_modes = config.mask_mode == "oracle" or config.fill == "oracle"
    records: list[dict[str, Any]] = []
    with instrumentation.installed():
        if config.warmup:
            warmup_pages = loaded_pages
            if oracle_modes:
                warmup_pages = [
                    (page, loaded)
                    for page, loaded in loaded_pages
                    if loaded is not None and loaded.synthetic is not None
                ]
            first = next((loaded for _page, loaded in warmup_pages if loaded is not None), None)
            if first is not None:
                instrumentation.begin_page(first.synthetic)
                try:
                    await _translate_page(pipeline, first.image.copy(), config)
                except Exception as error:  # the scored pass reports it
                    logger.warning("Warm-up page %s failed: %s", first.page.id, error)

        for page, loaded in loaded_pages:
            record: dict[str, Any] = {"id": page.id, "tags": list(page.tags)}
            if loaded is None:
                record.update({"status": "missing", "source": f"file:{page.path}"})
                logger.warning("Page %s is missing (%s); skipped", page.id, page.path)
                records.append(record)
                continue
            if oracle_modes and loaded.synthetic is None:
                # Oracle modes need the synthetic clean plate and truth ink.
                record.update({"status": "skipped", "source": loaded.source, "reason": "no clean plate"})
                records.append(record)
                continue
            if config.detector == TRUTH_DETECTOR and loaded.synthetic is None:
                record.update({"status": "skipped", "source": loaded.source, "reason": "no ground truth"})
                records.append(record)
                continue
            record.update(
                {
                    "source": loaded.source,
                    "sha256": image_digest(loaded.image),
                    "size": [int(loaded.image.shape[1]), int(loaded.image.shape[0])],
                }
            )
            probe = instrumentation.begin_page(loaded.synthetic)
            started = time.perf_counter()
            try:
                result = await _translate_page(pipeline, loaded.image.copy(), config)
            except Exception as error:
                record.update({"status": "error", "error": f"{type(error).__name__}: {error}"[:400]})
                logger.exception("Page %s failed", page.id)
                records.append(record)
                continue
            total = time.perf_counter() - started
            record["status"] = "ok"
            record.update(measure_page(probe, loaded, result, config.inpainter))
            record["timings"]["total"] = round(total, 4)
            record["timings"]["other"] = round(
                max(0.0, total - sum(record["timings"][stage] for stage in STAGES)), 4
            )
            if images_dir is not None:
                _save_page_images(images_dir / page.id, probe, result, loaded.image.shape)
            records.append(record)
            logger.info("Scored %s in %.2fs", page.id, total)

    config_payload = asdict(config)
    return {
        "schema_version": SCHEMA_VERSION,
        "label": config.label,
        "created_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config": config_payload,
        "pages": records,
        "summary": summarize(records),
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _mean(values: Sequence[float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return round(statistics.fmean(present), 4) if present else None


def summarize(records: Sequence[dict[str, Any]], *, by_tag: bool = True) -> dict[str, Any]:
    scored = [record for record in records if record.get("status") == "ok"]
    statuses = Counter(str(record.get("status")) for record in records)

    def total(section: str, key: str) -> int:
        return sum(int(record[section][key]) for record in scored)

    checked = total("verify", "checked")
    flagged = total("verify", "step6_flagged")
    rendered_checked = total("verify", "rendered_checked")
    kana_kanji = total("verify", "kana_kanji")
    kinds = Counter({kind: 0 for kind in MASK_KINDS})
    strategies: Counter[str] = Counter()
    reason_codes: Counter[str] = Counter()
    tiers: Counter[str] = Counter()
    for record in scored:
        kinds.update(record["mask"]["kinds"])
        strategies.update(record["mask"]["strategies"])
        reason_codes.update(record["mask"].get("reason_codes") or {})
        tiers.update(record["mask"].get("tier_counts") or {})
    sizes = [size for record in scored for size in record["render"]["font_sizes"]]
    truths = [record["truth"] for record in scored if record.get("truth")]

    def truth_mean(key: str) -> float | None:
        return _mean([truth.get(key) for truth in truths])

    def truth_total(key: str) -> int:
        return sum(int(truth.get(key) or 0) for truth in truths)
    timings = {
        stage: round(sum(float(record["timings"].get(stage, 0.0)) for record in scored), 3)
        for stage in (*STAGES, "other", "total")
    }
    summary: dict[str, Any] = {
        "pages": {
            "total": len(records),
            "scored": len(scored),
            **{status: count for status, count in sorted(statuses.items()) if status != "ok"},
        },
        "blocks": {
            "detected": total("blocks", "detected"),
            "to_erase": total("blocks", "to_erase"),
        },
        "cjk_residue": {
            "step6_checked": checked,
            "step6_flagged": flagged,
            "step6_rate": _rate(flagged, checked),
            "rendered_checked": rendered_checked,
            "kana_kanji": kana_kanji,
            "kana_kanji_rate": _rate(kana_kanji, rendered_checked),
        },
        "inpaint": {
            "changed_outside_mask_px": total("inpaint", "changed_outside_mask_px"),
            "pages_changed_outside_mask": sum(
                record["inpaint"]["changed_outside_mask_px"] > 0 for record in scored
            ),
            "model_changed_outside_mask_px": total("inpaint", "model_changed_outside_mask_px"),
            "fallback_pages": sum(bool(record["inpaint"]["fallback"]) for record in scored),
            "used": dict(Counter(str(record["inpaint"]["used"]) for record in scored)),
        },
        "mask": {
            "kinds": dict(kinds),
            "strategies": dict(sorted(strategies.items())),
            "white_fill_blocks": total("mask", "white_fill_blocks"),
            "model_blocks": total("mask", "model_blocks"),
            "tier_counts": dict(sorted(tiers.items())),
            "suppressed_composites": total("mask", "suppressed_composites"),
            "mask_px": total("mask", "mask_px"),
            "reason_codes": dict(sorted(reason_codes.items())),
        },
        "render": {
            "regions": total("render", "regions"),
            "shrink_events": total("render", "shrink_events"),
            "below_min_readable": total("render", "below_min_readable"),
            "overflow_attempts": total("render", "overflow_attempts"),
            "overflow_events": total("render", "overflow_events"),
            "unfit_skipped": total("render", "unfit_skipped"),
            "font_size_median": statistics.median(sizes) if sizes else None,
            "font_size_min": min(sizes) if sizes else None,
            "font_size_spread": _mean(
                [record["render"].get("font_size_spread") for record in scored]
            ),
        },
        "truth": {
            "pages": len(truths),
            "recall": _mean([truth["recall"] for truth in truths]),
            "text_mae": _mean([truth["text_mae"] for truth in truths]),
            "residual_ink_ratio": _mean([truth["residual_ink_ratio"] for truth in truths]),
            "ink_mask_recall": truth_mean("ink_mask_recall"),
            "residual_in_mask_ratio": truth_mean("residual_in_mask_ratio"),
            "residual_out_of_mask_ratio": truth_mean("residual_out_of_mask_ratio"),
            "residual_ink_lp_ratio": truth_mean("residual_ink_lp_ratio"),
            "hole_in_tone_px": truth_total("hole_in_tone_px"),
            "render_ink_outside_bubble_px": truth_total("render_ink_outside_bubble_px"),
            "ocr_cer": truth_mean("ocr_cer"),
            "ocr_cer_blocks": truth_total("ocr_cer_blocks"),
            "over_erased_px": sum(int(truth["over_erased_px"]) for truth in truths),
            "touched_away_px": sum(int(truth["touched_away_px"]) for truth in truths),
        },
        "timings": timings,
        "seconds_per_page": round(timings["total"] / len(scored), 3) if scored else None,
    }
    if by_tag:
        tags = sorted({tag for record in records for tag in record.get("tags", [])})
        summary["by_tag"] = {
            tag: summarize([record for record in records if tag in record.get("tags", [])], by_tag=False)
            for tag in tags
        }
    return summary


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

# (dotted summary key, label, which direction is better)
METRICS: tuple[tuple[str, str, str | None], ...] = (
    ("pages.scored", "Pages scored", None),
    ("blocks.detected", "Blocks detected", None),
    ("blocks.to_erase", "Blocks to erase", None),
    ("cjk_residue.step6_rate", "CJK residue, Step 6 verdict", "lower"),
    ("cjk_residue.kana_kanji_rate", "CJK residue, kana/kanji only", "lower"),
    ("inpaint.changed_outside_mask_px", "Px changed outside mask", "lower"),
    ("inpaint.model_changed_outside_mask_px", "Px inpainter changed outside its mask", "lower"),
    ("inpaint.fallback_pages", "Pages with inpainter fallback", "lower"),
    ("mask.kinds.contour_safe_glyph", "Masks: contour-safe glyph", None),
    ("mask.kinds.bubble_interior", "Masks: bubble interior", None),
    ("mask.kinds.rectangular", "Masks: rectangular", None),
    ("mask.kinds.skipped", "Masks: skipped (no safe mask)", "lower"),
    ("mask.kinds.other", "Masks: other (incl. oracle ink)", None),
    ("mask.white_fill_blocks", "Router-filled blocks (no model)", None),
    ("mask.model_blocks", "Blocks queued to the model", "lower"),
    ("mask.tier_counts.flat", "Fill tier: flat blocks", None),
    ("mask.tier_counts.smooth", "Fill tier: smooth blocks", None),
    ("mask.tier_counts.lattice", "Fill tier: lattice blocks", None),
    ("mask.suppressed_composites", "Composite duplicates not masked", None),
    ("mask.mask_px", "Masked px", None),
    ("mask.reason_codes.text_free", "Mask reason: text_free block", None),
    ("mask.reason_codes.no_contour", "Mask reason: no contour found", None),
    ("mask.reason_codes.contour_touches_roi_edge", "Mask reason: contour touches ROI edge", None),
    ("mask.reason_codes.area_test_failed", "Mask reason: area test failed", None),
    ("mask.reason_codes.coverage_below_35", "Mask reason: coverage below 35%", None),
    ("mask.reason_codes.clipped_ink_empty", "Mask reason: interior found, clipped ink empty", None),
    ("render.regions", "Render regions", None),
    ("render.shrink_events", "Shrink events (< preferred size)", "lower"),
    ("render.below_min_readable", "Below min readable size", "lower"),
    ("render.overflow_events", "Overflow events", None),
    ("render.unfit_skipped", "Unfit regions skipped", "lower"),
    ("render.font_size_median", "Median font size", "higher"),
    ("render.font_size_spread", "Font size spread per page (mean)", "lower"),
    ("truth.recall", "Synthetic: detection recall", "higher"),
    ("truth.text_mae", "Synthetic: text-region MAE", "lower"),
    ("truth.residual_ink_ratio", "Synthetic: residual ink ratio", "lower"),
    ("truth.ink_mask_recall", "Synthetic: ink-mask recall", "higher"),
    ("truth.residual_in_mask_ratio", "Synthetic: residual ink in mask (fill failure)", "lower"),
    ("truth.residual_out_of_mask_ratio", "Synthetic: residual ink outside mask (mask miss)", "lower"),
    ("truth.residual_ink_lp_ratio", "Synthetic: residual ink after low-pass", "lower"),
    ("truth.hole_in_tone_px", "Synthetic: tone flattened in text boxes (px)", "lower"),
    ("truth.render_ink_outside_bubble_px", "Synthetic: rendered ink outside bubble interior (px)", "lower"),
    ("truth.ocr_cer", "Synthetic: OCR character error rate", "lower"),
    ("truth.over_erased_px", "Synthetic: over-erased px (visible)", "lower"),
    ("truth.touched_away_px", "Synthetic: px touched away from text", "lower"),
    ("seconds_per_page", "Seconds per page", "lower"),
    *((f"timings.{stage}", f"Time: {stage} (s)", "lower") for stage in STAGES),
)


def lookup(data: dict[str, Any], dotted: str) -> Any:
    value: Any = data
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _format(value: Any, key: str = "") -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        if key.endswith("_rate") or key.endswith("ratio") or key.endswith("recall"):
            return f"{value * 100:.1f}%"
        return f"{value:.3f}".rstrip("0").rstrip(".") if value % 1 else f"{value:.0f}"
    return str(value)


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def render_summary_markdown(run: dict[str, Any]) -> str:
    config = run["config"]
    summary = run["summary"]
    environment = run.get("environment", {})
    lines = [
        f"# Pipeline eval: {run['label']}",
        "",
        f"- Created: {run['created_utc']}"
        + (f", commit `{environment['git_commit']}`" if environment.get("git_commit") else "")
        + (" (dirty tree)" if environment.get("git_dirty") else ""),
        f"- Detector `{config['detector']}`, mask mode `{config['mask_mode']}`, "
        f"inpainter `{config['inpainter']}` (used: "
        + ", ".join(f"`{name}` x{count}" for name, count in summary["inpaint"]["used"].items())
        + f"), OCR `{config['ocr']}`, translator `{config['translator']}`, device `{config['device']}`",
    ]
    if "comfyui_reachable" in environment:
        lines.append(f"- ComfyUI reachable: {environment['comfyui_reachable']}")
    pages = summary["pages"]
    lines.append(
        "- Pages: " + ", ".join(f"{name} {count}" for name, count in pages.items())
    )
    lines += ["", "## Overall", ""]
    lines += _table(
        ["Metric", "Value"],
        [[label, _format(lookup(summary, key), key)] for key, label, _better in METRICS],
    )
    tags = summary.get("by_tag") or {}
    if tags:
        lines += ["", "## By tag", ""]
        lines += _table(
            [
                "Tag",
                "Pages",
                "Blocks",
                "Residue (kana/kanji)",
                "Px outside mask",
                "Shrink",
                "Overflow",
                "Residual ink",
                "Ink recall",
                "Resid in->out mask",
            ],
            [
                [
                    tag,
                    str(data["pages"]["scored"]),
                    str(data["blocks"]["detected"]),
                    _format(data["cjk_residue"]["kana_kanji_rate"], "_rate"),
                    str(data["inpaint"]["changed_outside_mask_px"]),
                    str(data["render"]["shrink_events"]),
                    str(data["render"]["overflow_events"]),
                    _format(data["truth"]["residual_ink_ratio"], "ratio"),
                    _format(data["truth"].get("ink_mask_recall"), "recall"),
                    _format(data["truth"].get("residual_in_mask_ratio"), "ratio")
                    + " -> "
                    + _format(data["truth"].get("residual_out_of_mask_ratio"), "ratio"),
                ]
                for tag, data in tags.items()
            ],
        )
    lines += ["", "## Pages", ""]
    page_rows = []
    for record in run["pages"]:
        if record.get("status") != "ok":
            page_rows.append([record["id"], record.get("status", "?"), *["" for _ in range(7)]])
            continue
        kinds = record["mask"]["kinds"]
        page_rows.append(
            [
                record["id"],
                "ok",
                str(record["blocks"]["detected"]),
                f"{record['verify']['kana_kanji']}/{record['verify']['rendered_checked']}"
                f" ({record['verify']['step6_flagged']}/{record['verify']['checked']})",
                str(record["inpaint"]["changed_outside_mask_px"]),
                "/".join(str(kinds[kind]) for kind in ("contour_safe_glyph", "bubble_interior", "rectangular", "skipped"))
                + f" +{record['mask']['white_fill_blocks']}r",
                f"{record['render']['shrink_events']}/{record['render']['overflow_events']}"
                f"/{record['render']['unfit_skipped']}",
                str(record["inpaint"]["used"]) + (" (fallback)" if record["inpaint"]["fallback"] else ""),
                _format(record["timings"]["total"]),
            ]
        )
    lines += _table(
        [
            "Page",
            "Status",
            "Blocks",
            "Residue kana/kanji (Step 6)",
            "Px outside mask",
            "Masks glyph/interior/rect/skip +router",
            "Shrink/overflow/unfit",
            "Inpainter",
            "Seconds",
        ],
        page_rows,
    )
    return "\n".join(lines) + "\n"


def _delta(before: Any, after: Any, key: str, better: str | None) -> str:
    if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
        return ""
    change = float(after) - float(before)
    if change == 0:
        return "="
    if key.endswith("_rate") or key.endswith("ratio") or key.endswith("recall"):
        text = f"{change * 100:+.1f} pt"
    elif isinstance(before, int) and isinstance(after, int):
        text = f"{int(change):+d}"
    else:
        text = f"{change:+.3f}"
    if better is None:
        return text
    improved = change < 0 if better == "lower" else change > 0
    return f"{text} ({'better' if improved else 'worse'})"


def _scored_ids(run: dict[str, Any]) -> set[str]:
    return {record["id"] for record in run["pages"] if record.get("status") == "ok"}


def render_compare_markdown(before: dict[str, Any], after: dict[str, Any]) -> str:
    """Diff two runs: overall metrics, then per-page changes.

    When the runs scored different pages (a machine without the private pages
    compared with a committed baseline, say), the overall table is recomputed
    over the pages scored in both, so the totals stay comparable.
    """
    before_summary, after_summary = before["summary"], after["summary"]
    common = _scored_ids(before) & _scored_ids(after)
    scope = "all scored pages"
    if common != _scored_ids(before) or common != _scored_ids(after):
        before_summary = summarize([r for r in before["pages"] if r["id"] in common], by_tag=False)
        after_summary = summarize([r for r in after["pages"] if r["id"] in common], by_tag=False)
        scope = f"the {len(common)} pages scored in both runs"
    lines = [
        f"# Pipeline eval comparison: {before['label']} -> {after['label']}",
        "",
        f"- A: `{before['label']}` ({before['created_utc']}, detector `{before['config']['detector']}`, "
        f"masks `{before['config']['mask_mode']}`, inpainter `{before['config']['inpainter']}`)",
        f"- B: `{after['label']}` ({after['created_utc']}, detector `{after['config']['detector']}`, "
        f"masks `{after['config']['mask_mode']}`, inpainter `{after['config']['inpainter']}`)",
        "- Timings are wall-clock on one machine; treat small changes as noise.",
        "",
        "## Overall",
        "",
        f"Computed over {scope}.",
        "",
    ]
    rows = []
    for key, label, better in METRICS:
        first = lookup(before_summary, key)
        second = lookup(after_summary, key)
        rows.append([label, _format(first, key), _format(second, key), _delta(first, second, key, better)])
    lines += _table(["Metric", "A", "B", "B - A"], rows)

    before_pages = {record["id"]: record for record in before["pages"]}
    page_rows = []
    only = []
    for record in after["pages"]:
        previous = before_pages.pop(record["id"], None)
        if previous is None:
            only.append(f"{record['id']} (B only)")
            continue
        if previous.get("status") != "ok" or record.get("status") != "ok":
            page_rows.append([record["id"], f"{previous.get('status')} -> {record.get('status')}", *[""] * 5])
            continue
        same_image = previous.get("sha256") == record.get("sha256")

        def pair(section: str, key: str, first: dict[str, Any] = previous, second: dict[str, Any] = record) -> str:
            a = lookup(first, f"{section}.{key}")
            b = lookup(second, f"{section}.{key}")
            return f"{_format(a, key)} -> {_format(b, key)}" if a != b else _format(a, key)

        page_rows.append(
            [
                record["id"],
                "ok" if same_image else "ok (image differs)",
                pair("verify", "kana_kanji"),
                pair("inpaint", "changed_outside_mask_px"),
                pair("render", "shrink_events") + " / " + pair("render", "overflow_events"),
                pair("truth", "residual_ink_ratio") if record.get("truth") else "",
                pair("timings", "total"),
            ]
        )
    only.extend(f"{page_id} (A only)" for page_id in before_pages)
    lines += ["", "## Pages", ""]
    lines += _table(
        ["Page", "Status", "Kana/kanji residue", "Px outside mask", "Shrink / overflow", "Residual ink", "Seconds"],
        page_rows,
    )
    if only:
        lines += ["", "Not in both runs: " + ", ".join(only)]
    return "\n".join(lines) + "\n"


def _json_default(value: Any) -> Any:
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return value.as_posix()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def write_run(run: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    run_path = out_dir / "run.json"
    summary_path = out_dir / "summary.md"
    run_path.write_text(
        json.dumps(run, indent=2, ensure_ascii=False, default=_json_default) + "\n", encoding="utf-8"
    )
    summary_path.write_text(render_summary_markdown(run), encoding="utf-8")
    return run_path, summary_path


def load_run(path: Path) -> dict[str, Any]:
    run_path = path / "run.json" if path.is_dir() else path
    return json.loads(run_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Environment and CLI
# ---------------------------------------------------------------------------


def _git(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip()


def comfyui_reachable(timeout: float = 2.0) -> bool:
    url = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/") + "/system_stats"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            return response.status == 200
    except (OSError, urllib.error.URLError, ValueError):
        return False


def collect_environment(pipeline: TranslationPipeline) -> dict[str, Any]:
    try:
        import onnxruntime

        providers = list(onnxruntime.get_available_providers())
        ort_version = onnxruntime.__version__
    except Exception:  # pragma: no cover - environment probe only
        providers, ort_version = [], None
    git_status = _git("status", "--porcelain", "--untracked-files=no")
    return {
        "git_commit": (_git("rev-parse", "--short=12", "HEAD") or None),
        "git_dirty": bool(git_status),
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "numpy": np.__version__,
        "opencv": cv2.__version__,
        "onnxruntime": ort_version,
        "onnx_providers": providers,
        "comfyui_reachable": comfyui_reachable(),
        "models": {
            name: {"status": entry.get("status"), "name": entry.get("name")}
            for name, entry in pipeline.model_status.items()
        },
        "synthetic_glyph_source": build_synthetic_page("white-bubbles").glyph_source,
    }


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


async def _run_real_pipeline(
    pages: Sequence[CorpusPage], manifest: Path, config: RunConfig, images_dir: Path | None
) -> dict[str, Any]:
    pipeline = TranslationPipeline(device=config.device)
    await pipeline.initialize()
    try:
        if not pipeline.is_ready:
            raise SystemExit(
                "The detector or OCR model failed to load: "
                + json.dumps({k: v.get("status") for k, v in pipeline.model_status.items()})
            )
        if config.detector not in ("rtdetr", TRUTH_DETECTOR):
            try:
                await pipeline._detectors.get(config.detector)
            except Exception as error:
                raise SystemExit(
                    f"Detector {config.detector} is unavailable: {error}\n"
                    "To score its rectangular-mask path on RT-DETR boxes instead, run with "
                    "--detector rtdetr --mask-mode rectangular."
                ) from error
        await pipeline.warm_up()
        run = await run_corpus(pipeline, pages, manifest.parent, config, images_dir=images_dir)
        run["environment"] = collect_environment(pipeline)
    finally:
        pipeline.dispose()
    run["corpus"] = {"manifest": _display_path(manifest), "pages": len(pages)}
    return run


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score the translation pipeline on a page corpus, or compare two runs."
    )
    parser.add_argument("--corpus", type=Path, default=ROOT / "eval" / "corpus.json")
    parser.add_argument("--out", type=Path, help="run output directory (or compare .md file)")
    parser.add_argument("--label", help="run label (default: the output directory name)")
    parser.add_argument(
        "--detector",
        default="rtdetr",
        help="rtdetr, animetext-yolo, or 'truth' (synthetic ground-truth boxes)",
    )
    parser.add_argument(
        "--mask-mode",
        choices=("auto", "contour", "rectangular", "oracle"),
        default="auto",
        help="auto follows the pipeline (rectangular only for animetext-yolo); "
        "'oracle' masks the diluted truth ink (synthetic pages only), measuring the fill alone",
    )
    parser.add_argument(
        "--fill",
        choices=("auto", "oracle"),
        default="auto",
        help="'oracle' pastes the clean plate inside the pipeline's own mask "
        "(synthetic pages only), measuring the mask alone",
    )
    parser.add_argument("--inpainter", default=DEFAULT_INPAINTER)
    parser.add_argument("--ocr", default="manga-ocr")
    parser.add_argument(
        "--translator",
        default=STUB_TRANSLATOR,
        help="'stub' (deterministic, offline) or a real provider name",
    )
    parser.add_argument("--target-lang", default="English")
    parser.add_argument("--device", default=os.environ.get("EVAL_DEVICE", "cpu"))
    parser.add_argument("--pages", nargs="+", help="only these page ids")
    parser.add_argument("--tags", nargs="+", help="only pages with any of these tags")
    parser.add_argument("--no-verify", action="store_true", help="skip the Step 6 re-OCR")
    parser.add_argument("--no-warmup", action="store_true", help="time the first page cold")
    parser.add_argument("--save-images", action="store_true", help="write mask/inpainted/rendered PNGs")
    parser.add_argument("--strict", action="store_true", help="fail when a listed page is missing")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--compare", nargs=2, type=Path, metavar=("RUN_A", "RUN_B"), help="diff two run dirs or run.json files"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    if args.compare:
        report = render_compare_markdown(load_run(args.compare[0]), load_run(args.compare[1]))
        if args.out:
            target = args.out if args.out.suffix == ".md" else args.out / "compare.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(report, encoding="utf-8")
        print(report)
        return 0

    if args.out is None:
        print("--out is required unless --compare is used", file=sys.stderr)
        return 2
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logger.setLevel(logging.INFO)

    pages = load_corpus(args.corpus)
    if args.pages:
        wanted = set(args.pages)
        pages = [page for page in pages if page.id in wanted]
    if args.tags:
        wanted_tags = set(args.tags)
        pages = [page for page in pages if wanted_tags & set(page.tags)]
    if not pages:
        print("No corpus pages match the filters", file=sys.stderr)
        return 2

    config = RunConfig(
        label=args.label or args.out.name,
        detector=args.detector,
        mask_mode=args.mask_mode,
        inpainter=args.inpainter,
        ocr=args.ocr,
        translator=args.translator,
        target_lang=args.target_lang,
        device=args.device,
        verify=not args.no_verify,
        warmup=not args.no_warmup,
        fill=args.fill,
    )
    images_dir = args.out / "images" if args.save_images else None
    run = asyncio.run(_run_real_pipeline(pages, args.corpus, config, images_dir))
    run_path, summary_path = write_run(run, args.out)
    print(render_summary_markdown(run))
    print(f"Wrote {_display_path(run_path)} and {_display_path(summary_path)}")

    statuses = Counter(record["status"] for record in run["pages"])
    if statuses.get("error"):
        return 1
    if args.strict and statuses.get("missing"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
