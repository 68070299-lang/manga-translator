"""Tests for the pipeline evaluation harness (scripts/eval_pipeline.py).

The harness runs the real TranslationPipeline orchestration on the synthetic
corpus. Model components are replaced with small deterministic fakes, so these
tests need no model downloads.
"""

import asyncio
import importlib.util
import inspect
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from manga_backend.pipeline import TranslationPipeline
from manga_backend.pipeline import masks as masks_module
from manga_backend.pipeline import orchestrator as orchestrator_module
from manga_backend.pipeline import rendering as rendering_module
from manga_backend.utils.textblock import TextBlock
from test_support import writable_temp_directory

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "eval_pipeline.py"
SPEC = importlib.util.spec_from_file_location("eval_pipeline", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
eval_pipeline = importlib.util.module_from_spec(SPEC)
sys.modules["eval_pipeline"] = eval_pipeline
SPEC.loader.exec_module(eval_pipeline)

CORPUS = ROOT / "eval" / "corpus.json"


def full_width(text):
    """What manga-ocr returns for ASCII: its post-processing widens it."""
    return "".join(chr(ord(char) + 0xFEE0) if "!" <= char <= "~" else char for char in text)


class EchoOcr:
    """Reads back the truth text first; on the Step 6 re-OCR, reports like manga-ocr.

    Rendered translations come back as full-width Latin, and SFX blocks come
    back still Japanese, so both residue readings have something to count.
    """

    async def process(self, image, blocks):
        if any(block.translated_text for block in blocks):
            return [
                block.text if block.block_type == "sfx" else full_width(block.translated_text[:6])
                for block in blocks
            ]
        return [block.text for block in blocks]


class TeleaInpainter:
    async def initialize(self):
        return None

    async def inpaint(self, image, mask):
        filled = cv2.inpaint(image, mask, 3, cv2.INPAINT_TELEA)
        output = image.copy()
        output[mask > 0] = filled[mask > 0]
        return output


class LeakyInpainter(TeleaInpainter):
    async def inpaint(self, image, mask):
        output = cv2.GaussianBlur(image, (5, 5), 0)
        filled = await super().inpaint(image, mask)
        output[mask > 0] = filled[mask > 0]
        return output


def fake_pipeline(inpainter=None, name="fake-telea"):
    pipeline = TranslationPipeline(device="cpu")
    pipeline._ocr = EchoOcr()
    pipeline._inpainters.instance = inpainter or TeleaInpainter()
    pipeline._inpainters.type = name
    pipeline.model_status["inpainter"] = {"status": "loaded", "name": name}
    pipeline.is_initialized = True
    return pipeline


def synthetic_pages(*ids):
    pages = [page for page in eval_pipeline.load_corpus(CORPUS) if page.synthetic is not None]
    return [page for page in pages if not ids or page.id in ids]


def run(pages, *, inpainter=None, name="fake-telea", **config):
    config.setdefault("label", "test")
    config.setdefault("detector", eval_pipeline.TRUTH_DETECTOR)
    config.setdefault("inpainter", name)
    config.setdefault("warmup", False)
    return asyncio.run(
        eval_pipeline.run_corpus(
            fake_pipeline(inpainter, name),
            pages,
            CORPUS.parent,
            eval_pipeline.RunConfig(**config),
        )
    )


class SyntheticCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run(synthetic_pages())

    def test_manifest_lists_a_synthetic_page_for_every_required_tag(self):
        tags = {tag for page in synthetic_pages() for tag in page.tags}
        required = {
            "white-bubble",
            "textured-background",
            "sfx",
            "vertical-text",
            "conjoined-bubbles",
            "webtoon-strip",
            "adversarial",
        }
        self.assertLessEqual(required, tags)
        real_tags = {
            tag for page in eval_pipeline.load_corpus(CORPUS) if page.path for tag in page.tags
        }
        self.assertIn("bookwalker", real_tags)

    def test_every_synthetic_page_is_scored_with_all_stages(self):
        pages = self.result["pages"]
        self.assertEqual(len(pages), len(eval_pipeline.SYNTHETIC_PAGES))
        for page in pages:
            with self.subTest(page=page["id"]):
                self.assertEqual(page["status"], "ok", page.get("error"))
                self.assertGreater(page["blocks"]["detected"], 0)
                self.assertEqual(page["truth"]["recall"], 1.0)
                self.assertTrue(page["verify"]["ran"])
                self.assertGreater(page["render"]["regions"], 0)
                self.assertEqual(page["inpaint"]["used"], "fake-telea")
                self.assertFalse(page["inpaint"]["fallback"])
                for stage in (*eval_pipeline.STAGES, "other", "total"):
                    self.assertIn(stage, page["timings"])
                self.assertGreater(page["timings"]["render"], 0)

    def test_inpainting_that_respects_its_mask_changes_nothing_outside_it(self):
        summary = self.result["summary"]
        self.assertEqual(summary["inpaint"]["changed_outside_mask_px"], 0)
        self.assertEqual(summary["inpaint"]["model_changed_outside_mask_px"], 0)
        self.assertGreater(summary["mask"]["mask_px"], 0)
        for page in self.result["pages"]:
            self.assertGreater(page["inpaint"]["changed_inside_mask_px"], 0, page["id"])

    def test_default_masks_are_contour_safe_and_counted_per_block(self):
        kinds = self.result["summary"]["mask"]["kinds"]
        self.assertEqual(kinds["rectangular"], 0)
        self.assertGreater(kinds["contour_safe_glyph"], 0)
        self.assertEqual(
            sum(kinds.values()),
            sum(page["blocks"]["to_erase"] for page in self.result["pages"]),
        )
        # The fill router now serves the LaMa route too, so white-bubble
        # blocks are tier-filled instead of being handed to the model.
        self.assertGreater(self.result["summary"]["mask"]["white_fill_blocks"], 0)
        self.assertGreater(
            self.result["summary"]["mask"]["model_blocks"], 0
        )

    def test_residue_counts_only_blocks_that_still_read_as_japanese(self):
        residue = self.result["summary"]["cjk_residue"]
        # EchoOcr reads rendered English back as full-width Latin, like manga-ocr,
        # and free text (no bubble, so RT-DETR's "sfx" type) back as Japanese.
        free_text = sum(
            truth.bubble is None
            for name in eval_pipeline.SYNTHETIC_PAGES
            for truth in eval_pipeline.build_synthetic_page(name).truth
        )
        self.assertGreater(free_text, 0)
        self.assertLess(free_text, residue["step6_checked"])
        # Step 6 ignores full-width Latin, so its verdict and the kana/kanji reading agree.
        self.assertEqual(residue["step6_flagged"], free_text)
        self.assertEqual(residue["kana_kanji"], free_text)

    def test_ground_truth_scores_erasure_against_the_clean_plate(self):
        for page in self.result["pages"]:
            truth = page["truth"]
            with self.subTest(page=page["id"]):
                self.assertGreaterEqual(truth["residual_ink_ratio"], 0.0)
                # The adversarial pages exist to break masking (faint lettering
                # that no dark-glyph threshold catches), so their residual ink
                # is only bounded; the classic pages must come out clean.
                limit = 1.0 if "adversarial" in page["tags"] else 0.5
                self.assertLess(truth["residual_ink_ratio"], limit)
                self.assertEqual(truth["over_erased_px"], 0)

    def test_by_tag_summary_covers_each_tag(self):
        by_tag = self.result["summary"]["by_tag"]
        expected = {
            tag: sum(tag in page.tags for page in synthetic_pages())
            for tag in {tag for page in synthetic_pages() for tag in page.tags}
        }
        self.assertEqual(expected["webtoon-strip"], 1)
        self.assertGreater(expected["white-bubble"], 3)
        for tag, count in expected.items():
            self.assertEqual(by_tag[tag]["pages"]["scored"], count, tag)

    def test_reports_are_written_as_json_and_markdown(self):
        with writable_temp_directory() as directory:
            run_path, summary_path = eval_pipeline.write_run(self.result, Path(directory) / "run")
            data = json.loads(run_path.read_text(encoding="utf-8"))
            markdown = summary_path.read_text(encoding="utf-8")
        self.assertEqual(data["schema_version"], eval_pipeline.SCHEMA_VERSION)
        self.assertEqual(data["summary"], json.loads(json.dumps(self.result["summary"])))
        self.assertIn("CJK residue, kana/kanji only", markdown)
        self.assertIn("syn-webtoon-strip", markdown)

    def test_instrumentation_is_removed_after_the_run(self):
        self.assertEqual(
            orchestrator_module.prepare_flux_inpainting.__module__, masks_module.__name__
        )
        self.assertEqual(orchestrator_module.render_text.__module__, rendering_module.__name__)
        self.assertEqual(
            masks_module.contour_safe_mask_for_block.__qualname__, "contour_safe_mask_for_block"
        )
        self.assertEqual(rendering_module.auto_fit_font_size.__qualname__, "auto_fit_font_size")


class HarnessModeTests(unittest.TestCase):
    def test_rectangular_mask_mode_scores_the_animetext_mask_path(self):
        pages = synthetic_pages("syn-white-bubbles", "syn-sfx")
        contour = run(pages)
        rectangular = run(pages, mask_mode="rectangular", label="rect")
        kinds = rectangular["summary"]["mask"]["kinds"]
        self.assertEqual(kinds["contour_safe_glyph"], 0)
        self.assertGreater(kinds["rectangular"], 0)
        self.assertEqual(rectangular["pages"][0]["mask"]["path"], "flux-rectangular")
        self.assertGreater(
            rectangular["summary"]["mask"]["mask_px"], contour["summary"]["mask"]["mask_px"]
        )

        report = eval_pipeline.render_compare_markdown(contour, rectangular)
        self.assertIn("test -> rect", report)
        rows = {line.split("|")[1].strip(): line for line in report.splitlines() if line.startswith("|")}
        self.assertRegex(rows["Masks: rectangular"], r"\| \+\d+ \|")
        self.assertIn("syn-sfx", report)

    def test_flux_request_records_white_fill_and_fallback(self):
        pipeline = fake_pipeline()
        pipeline._inpainters.instance = None
        loaded = []

        class FailingFlux:
            async def initialize(self):
                raise RuntimeError("ComfyUI is not running")

        def create(inpainter_type, device):
            loaded.append(inpainter_type)
            return FailingFlux() if inpainter_type.startswith("flux") else TeleaInpainter()

        with patch("manga_backend.pipeline.inpainting.create_inpainter", create):
            result = asyncio.run(
                eval_pipeline.run_corpus(
                    pipeline,
                    # Tone and hatching are now lattice-filled without a model,
                    # so the fallback leg needs a page whose band no tier can
                    # verify: lettering that crosses the bubble outline.
                    synthetic_pages("syn-white-bubbles", "syn-text-touching-outline"),
                    CORPUS.parent,
                    eval_pipeline.RunConfig(
                        label="flux",
                        detector=eval_pipeline.TRUTH_DETECTOR,
                        inpainter="flux2_klein_lanpaint",
                        warmup=False,
                    ),
                )
            )
        white, textured = result["pages"]
        self.assertEqual(white["mask"]["path"], "flux")
        self.assertEqual(white["mask"]["white_fill_blocks"], 4)
        self.assertGreater(white["mask"]["white_fill_px"], 0)
        self.assertEqual(white["inpaint"]["strategy"], "fill_tiers")
        self.assertGreater(textured["mask"]["model_blocks"], 0)
        self.assertTrue(textured["inpaint"]["fallback"])
        self.assertEqual(textured["inpaint"]["used"], "lama_large")
        self.assertEqual(result["summary"]["inpaint"]["fallback_pages"], 1)
        self.assertEqual(result["summary"]["inpaint"]["changed_outside_mask_px"], 0)
        self.assertIn("lama_large", loaded)

    def test_leaky_inpainter_yields_no_changed_pixels_outside_the_mask(self):
        """The pipeline's mask-union composite clamps a leaky inpainter.

        The oracle mask routes every block through the model (the router
        would otherwise flat-fill this page and never call it), so the
        harness still observes the model's own leak and a regression in the
        composite would be caught; no pixel outside the erased regions
        reaches the rendered page.
        """
        result = run(
            synthetic_pages("syn-white-bubbles"),
            inpainter=LeakyInpainter(),
            name="fake-leaky",
            mask_mode="oracle",
        )
        page = result["pages"][0]
        self.assertGreater(page["inpaint"]["model_changed_outside_mask_px"], 0)
        self.assertEqual(page["inpaint"]["changed_outside_mask_px"], 0)
        self.assertEqual(page["truth"]["over_erased_px"], 0)
        self.assertEqual(result["summary"]["inpaint"]["pages_changed_outside_mask"], 0)

    def test_missing_private_page_is_reported_and_the_run_continues(self):
        with writable_temp_directory() as directory:
            manifest = Path(directory) / "corpus.json"
            manifest.write_text(
                json.dumps(
                    {
                        "pages": [
                            {"id": "private", "path": "pages/absent.png", "tags": ["bookwalker"]},
                            {"id": "syn", "synthetic": "sfx", "tags": ["sfx"]},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            pages = eval_pipeline.load_corpus(manifest)
            result = asyncio.run(
                eval_pipeline.run_corpus(
                    fake_pipeline(),
                    pages,
                    manifest.parent,
                    eval_pipeline.RunConfig(label="t", detector="truth", inpainter="fake-telea"),
                )
            )
        statuses = {page["id"]: page["status"] for page in result["pages"]}
        self.assertEqual(statuses, {"private": "missing", "syn": "ok"})
        self.assertEqual(result["summary"]["pages"], {"total": 2, "scored": 1, "missing": 1})

    def test_manifest_rejects_unknown_tags_and_ambiguous_entries(self):
        with writable_temp_directory() as directory:
            manifest = Path(directory) / "corpus.json"
            for entry, message in (
                ({"id": "a", "synthetic": "sfx", "tags": ["bubbel"]}, "unknown tags"),
                ({"id": "a", "synthetic": "sfx", "path": "x.png", "tags": []}, "exactly one"),
                ({"id": "a", "synthetic": "nope", "tags": []}, "unknown synthetic"),
            ):
                manifest.write_text(json.dumps({"pages": [entry]}), encoding="utf-8")
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    eval_pipeline.load_corpus(manifest)

    def test_compare_limits_overall_totals_to_pages_scored_in_both_runs(self):
        full = run(synthetic_pages("syn-sfx", "syn-white-bubbles"))
        partial = json.loads(json.dumps(full))
        partial["label"] = "partial"
        partial["pages"] = [
            {"id": page["id"], "tags": [], "status": "missing"} if page["id"] == "syn-white-bubbles" else page
            for page in partial["pages"]
        ]
        partial["summary"] = eval_pipeline.summarize(partial["pages"])

        report = eval_pipeline.render_compare_markdown(full, partial)

        self.assertIn("Computed over the 1 pages scored in both runs.", report)
        rows = {line.split("|")[1].strip(): line for line in report.splitlines() if line.startswith("|")}
        self.assertTrue(rows["Blocks detected"].rstrip().endswith("| = |"), rows["Blocks detected"])
        self.assertIn("ok -> missing", rows["syn-white-bubbles"])

    def test_compare_cli_writes_markdown(self):
        result = run(synthetic_pages("syn-sfx"))
        with writable_temp_directory() as directory:
            first = Path(directory) / "a"
            second = Path(directory) / "b"
            eval_pipeline.write_run(result, first)
            eval_pipeline.write_run(result, second)
            target = Path(directory) / "diff.md"
            code = eval_pipeline.main(["--compare", str(first), str(second / "run.json"), "--out", str(target)])
            report = target.read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        self.assertIn("## Overall", report)
        self.assertIn("| syn-sfx | ok |", report)


class StubAndDefaultsTests(unittest.TestCase):
    def test_stub_translator_is_deterministic_and_keeps_punctuation_only_text(self):
        first = eval_pipeline.stub_translate("今日はどこへ行くの？")
        self.assertEqual(first, eval_pipeline.stub_translate("今日はどこへ行くの？"))
        self.assertTrue(first.endswith("?"))
        self.assertFalse(eval_pipeline.has_kana_kanji(first))
        self.assertEqual(eval_pipeline.stub_translate("！？"), "！？")
        self.assertEqual(eval_pipeline.stub_translate(""), "")
        long_text = eval_pipeline.stub_translate("あ" * 22)
        self.assertEqual(len(long_text.split()), 10)

    def test_kana_kanji_check_ignores_full_width_latin(self):
        self.assertFalse(eval_pipeline.has_kana_kanji(full_width("Wait!")))
        self.assertTrue(eval_pipeline.has_kana_kanji("待って"))
        self.assertFalse(eval_pipeline.has_kana_kanji("ー・"))

    def test_render_defaults_match_the_translate_route(self):
        from manga_backend.api.deps import translate_options

        parameters = inspect.signature(translate_options).parameters
        expected = {
            name: parameters["font_size" if name == "preferred_font_size" else name].default
            for name in eval_pipeline.API_RENDER_DEFAULTS
        }
        self.assertEqual(eval_pipeline.API_RENDER_DEFAULTS, expected)


def synthetic_pages_by_name(*names):
    """Corpus entries for named synthetic generators, independent of the manifest."""
    return [
        eval_pipeline.CorpusPage(id=f"syn-{name}", tags=(), synthetic=name)
        for name in names
    ]


class ResidualSplitTests(unittest.TestCase):
    """ink_mask_recall and the residual in/out-of-mask split."""

    @classmethod
    def setUpClass(cls):
        cls.result = run(synthetic_pages())

    def test_every_synthetic_page_has_the_split(self):
        for page in self.result["pages"]:
            truth = page["truth"]
            with self.subTest(page=page["id"]):
                self.assertIsNotNone(truth["ink_mask_recall"])
                self.assertGreaterEqual(truth["ink_mask_recall"], 0.0)
                self.assertLessEqual(truth["ink_mask_recall"], 1.0)
                for key in ("residual_in_mask_ratio", "residual_out_of_mask_ratio"):
                    self.assertIsNotNone(truth[key], key)
                    self.assertGreaterEqual(truth[key], 0.0)
                    self.assertLessEqual(truth[key], truth["residual_ink_ratio"] + 0.0002)
                self.assertLessEqual(
                    abs(
                        truth["residual_in_mask_ratio"]
                        + truth["residual_out_of_mask_ratio"]
                        - truth["residual_ink_ratio"]
                    ),
                    0.0002,
                )

    def test_summary_and_tags_carry_the_split(self):
        summary = self.result["summary"]["truth"]
        for key in (
            "ink_mask_recall",
            "residual_in_mask_ratio",
            "residual_out_of_mask_ratio",
            "residual_ink_lp_ratio",
            "hole_in_tone_px",
            "render_ink_outside_bubble_px",
            "ocr_cer",
        ):
            self.assertIn(key, summary)
        textured = self.result["summary"]["by_tag"]["textured-background"]["truth"]
        self.assertIn("ink_mask_recall", textured)

    def test_low_pass_residual_ignores_tone_phase_noise(self):
        clean = np.full((60, 80, 3), 255, dtype=np.uint8)
        ys, xs = np.mgrid[0:60, 0:80]
        dots = ((ys % 7) - 3) ** 2 + ((xs % 7) - 3) ** 2 <= 4
        clean[dots] = 110
        phase_shifted = clean.copy()
        phase_shifted[:57] = clean[3:]
        everywhere = np.ones((60, 80), dtype=bool)
        self.assertEqual(
            eval_pipeline._low_pass_residual(phase_shifted, clean, everywhere), 0.0
        )
        stroke = clean.copy()
        stroke[:, 30:36] = 0
        self.assertGreater(
            eval_pipeline._low_pass_residual(stroke, clean, everywhere), 0.05
        )

    def test_hole_in_tone_counts_flattened_dots_inside_text_boxes(self):
        clean = np.full((60, 80, 3), 255, dtype=np.uint8)
        ys, xs = np.mgrid[0:60, 0:80]
        dots = ((ys % 7) - 3) ** 2 + ((xs % 7) - 3) ** 2 <= 4
        clean[dots] = 110
        text_region = np.zeros((60, 80), dtype=bool)
        text_region[10:50, 10:70] = True
        no_ink = np.zeros((60, 80), dtype=bool)
        flattened = np.full_like(clean, 255)
        self.assertGreater(
            eval_pipeline._hole_in_tone_px(flattened, clean, text_region, no_ink), 1000
        )
        self.assertEqual(
            eval_pipeline._hole_in_tone_px(clean, clean, text_region, no_ink), 0
        )
        # Leftover lettering never counts: the ink mask excludes it.
        lettering = clean.copy()
        lettering[10:50, 30:33] = 255
        ink = np.zeros((60, 80), dtype=bool)
        ink[10:50, 30:33] = True
        self.assertEqual(
            eval_pipeline._hole_in_tone_px(lettering, clean, text_region, ink), 0
        )

    def test_font_size_spread_is_recorded_per_page(self):
        for page in self.result["pages"]:
            spread = page["render"]["font_size_spread"]
            self.assertIsNotNone(spread)
            self.assertGreaterEqual(int(spread), 0)
        self.assertIsNotNone(self.result["summary"]["render"]["font_size_spread"])

    def test_render_ink_outside_bubble_is_counted(self):
        for page in self.result["pages"]:
            value = page["truth"]["render_ink_outside_bubble_px"]
            self.assertIsInstance(value, int)
            self.assertGreaterEqual(value, 0)
        # SFX lettering has no bubble, so nothing can count there.
        sfx = next(page for page in self.result["pages"] if page["id"] == "syn-sfx")
        self.assertEqual(sfx["truth"]["render_ink_outside_bubble_px"], 0)

    def test_ocr_character_error_rate_against_truth_text(self):
        # EchoOcr reads back the truth text, so the CER is exactly zero.
        for page in self.result["pages"]:
            self.assertEqual(page["truth"]["ocr_cer"], 0.0, page["id"])
            self.assertEqual(page["truth"]["ocr_cer_blocks"], page["truth"]["boxes"])
        self.assertEqual(self.result["summary"]["truth"]["ocr_cer"], 0.0)


class OracleModeTests(unittest.TestCase):
    def test_oracle_mask_measures_the_fill_alone(self):
        result = run(synthetic_pages(), mask_mode="oracle", label="oracle-mask")
        for page in result["pages"]:
            with self.subTest(page=page["id"]):
                self.assertEqual(page["mask"]["path"], "oracle")
                self.assertEqual(page["mask"]["strategies"], {"oracle-ink": page["truth"]["boxes"]})
                self.assertEqual(page["truth"]["ink_mask_recall"], 1.0)
                # The dilated truth ink covers every lettering pixel, so any
                # residual left is a fill failure, never a mask miss.
                self.assertEqual(page["truth"]["residual_out_of_mask_ratio"], 0.0)
                self.assertEqual(page["mask"]["reason_codes"], {})
        kinds = result["summary"]["mask"]["kinds"]
        self.assertEqual(
            kinds["other"], sum(page["truth"]["boxes"] for page in result["pages"])
        )
        self.assertEqual(result["summary"]["mask"]["white_fill_blocks"], 0)

    def test_oracle_fill_measures_the_mask_alone(self):
        result = run(synthetic_pages(), fill="oracle", label="oracle-fill")
        for page in result["pages"]:
            truth = page["truth"]
            with self.subTest(page=page["id"]):
                self.assertEqual(page["inpaint"]["used"], "oracle-fill")
                self.assertEqual(page["inpaint"]["strategy"], "oracle-fill")
                self.assertFalse(page["inpaint"]["fallback"])
                self.assertEqual(page["inpaint"]["changed_outside_mask_px"], 0)
                self.assertEqual(page["inpaint"]["model_changed_outside_mask_px"], 0)
                # Pasting the clean plate inside the erase mask leaves no fill
                # failure and no flattened tone; all residual ink is a mask miss.
                self.assertEqual(truth["residual_in_mask_ratio"], 0.0)
                self.assertEqual(truth["hole_in_tone_px"], 0)
                self.assertAlmostEqual(
                    truth["residual_ink_ratio"],
                    truth["residual_out_of_mask_ratio"],
                    delta=0.0002,
                )
        used = result["summary"]["inpaint"]["used"]
        self.assertEqual(used, {"oracle-fill": len(result["pages"])})

    def test_oracle_fill_still_covers_white_filled_blocks(self):
        result = asyncio.run(
            eval_pipeline.run_corpus(
                fake_pipeline(),
                synthetic_pages("syn-white-bubbles"),
                CORPUS.parent,
                eval_pipeline.RunConfig(
                    label="oracle-flux",
                    detector=eval_pipeline.TRUTH_DETECTOR,
                    inpainter="flux2_klein_lanpaint",
                    fill="oracle",
                    warmup=False,
                ),
            )
        )
        page = result["pages"][0]
        self.assertEqual(page["mask"]["path"], "flux")
        self.assertEqual(page["mask"]["white_fill_blocks"], 4)
        self.assertEqual(page["inpaint"]["used"], "oracle-fill")
        self.assertEqual(page["truth"]["residual_in_mask_ratio"], 0.0)
        self.assertEqual(page["truth"]["hole_in_tone_px"], 0)

    def test_oracle_modes_skip_pages_without_a_clean_plate(self):
        from PIL import Image

        with writable_temp_directory() as directory:
            real = Path(directory) / "real.png"
            Image.new("RGB", (40, 40), "white").save(real)
            manifest = Path(directory) / "corpus.json"
            manifest.write_text(
                json.dumps(
                    {
                        "pages": [
                            {"id": "real", "path": "real.png", "tags": ["bookwalker"]},
                            {"id": "syn", "synthetic": "sfx", "tags": []},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            for config in ({"fill": "oracle"}, {"mask_mode": "oracle"}):
                with self.subTest(config=config):
                    result = asyncio.run(
                        eval_pipeline.run_corpus(
                            fake_pipeline(),
                            eval_pipeline.load_corpus(manifest),
                            manifest.parent,
                            eval_pipeline.RunConfig(
                                label="t",
                                detector=eval_pipeline.TRUTH_DETECTOR,
                                inpainter="fake-telea",
                                warmup=False,
                                **config,
                            ),
                        )
                    )
                    statuses = {
                        page["id"]: page.get("reason", page["status"]) for page in result["pages"]
                    }
                    self.assertEqual(statuses, {"real": "no clean plate", "syn": "ok"})


class MaskReasonCodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run(synthetic_pages())

    def test_free_text_blocks_are_flagged_as_text_free(self):
        sfx = run(synthetic_pages("syn-sfx"))
        self.assertEqual(sfx["summary"]["mask"]["reason_codes"], {"text_free": 2})

    def test_reason_codes_are_counted_per_page_and_in_summary(self):
        result = self.result
        codes = result["summary"]["mask"]["reason_codes"]
        self.assertGreater(sum(codes.values()), 0)
        for page in result["pages"]:
            self.assertIn("reason_codes", page["mask"])

    def test_reason_agrees_with_the_pipeline_helper_on_real_blocks(self):
        for name in eval_pipeline.SYNTHETIC_PAGES:
            page = eval_pipeline.build_synthetic_page(name)
            for truth in page.truth:
                if truth.bubble is None:
                    continue
                block = TextBlock(list(truth.xyxy), block_type="text")
                block.bubble_xyxy = list(truth.bubble)
                with self.subTest(page=name, box=truth.xyxy):
                    interior = masks_module.bubble_interior_mask_for_block(page.image, block)[0]
                    reason = eval_pipeline._bubble_interior_reason(
                        page.image, block, glyphs_present=True
                    )
                    empty = interior.size == 0 or not np.any(interior)
                    if empty:
                        self.assertIn(
                            reason,
                            {
                                "no_contour",
                                "contour_touches_roi_edge",
                                "area_test_failed",
                                "coverage_below_35",
                            },
                        )
                    else:
                        self.assertIn(reason, {"clipped_ink_empty", "interior_found"})

    def test_crafted_reasons_for_each_rule(self):
        def draw_rect(image, box, width):
            x1, y1, x2, y2 = box
            image[y1 : y1 + width, x1:x2] = 0
            image[y2 - width : y2, x1:x2] = 0
            image[y1:y2, x1 : x1 + width] = 0
            image[y1:y2, x2 - width : x2] = 0
            return image

        # text_free: sfx blocks never try an interior.
        block = TextBlock([10, 10, 20, 20], block_type="sfx")
        self.assertEqual(
            eval_pipeline._bubble_interior_reason(
                np.full((40, 40, 3), 255, np.uint8), block, glyphs_present=True
            ),
            "text_free",
        )

        # no contour: a flat white ROI has no contour at all.
        blank = np.full((260, 260, 3), 255, dtype=np.uint8)
        block = TextBlock([100, 100, 160, 160], block_type="text")
        block.bubble_xyxy = [80, 80, 180, 180]
        self.assertEqual(
            eval_pipeline._bubble_interior_reason(blank, block, glyphs_present=True),
            "no_contour",
        )

        # area test failed: a ring hugs the text box, so both its contours
        # pass the center, overlap and edge rules but stay under the area
        # floor (1.15x the text box area, masks.py:273).
        hug = np.full((260, 260, 3), 255, dtype=np.uint8)
        draw_rect(hug, (56, 56, 204, 204), 10)
        block = TextBlock([60, 60, 200, 200], block_type="text")
        block.bubble_xyxy = [50, 50, 210, 210]
        self.assertEqual(
            eval_pipeline._bubble_interior_reason(hug, block, glyphs_present=True),
            "area_test_failed",
        )

        # contour touches the ROI edge: a rectangular frame around the text
        # box reaches the ROI boundary (the detector bubble box plus 8%
        # padding) on its left side, so findContours sees the outer contour
        # clipped at the edge and the edge rule rejects it (masks.py:278).
        # The hole of the frame is exactly the blanked text rectangle, so
        # blanking cannot open it.
        clipped = np.full((260, 260, 3), 255, dtype=np.uint8)
        clipped[70:80, 38:180] = 0  # top band
        clipped[170:180, 38:180] = 0  # bottom band
        clipped[70:180, 38:80] = 0  # left band, on the ROI edge
        clipped[70:180, 170:180] = 0  # right band, inside the ROI
        block = TextBlock([80, 80, 170, 170], block_type="text")
        block.bubble_xyxy = [50, 50, 200, 200]
        self.assertEqual(
            eval_pipeline._bubble_interior_reason(clipped, block, glyphs_present=True),
            "contour_touches_roi_edge",
        )


class AdversarialPageTests(unittest.TestCase):
    def test_tone_rotated_page_draws_rotated_non_integer_tone(self):
        page = eval_pipeline.build_synthetic_page("tone-rotated")
        gray = cv2.cvtColor(page.clean, cv2.COLOR_RGB2GRAY)
        top = gray[40:460, 40:680]
        kernel = np.ones((7, 7), np.uint8)
        textured = (cv2.dilate(top, kernel) - cv2.erode(top, kernel)) > 40
        self.assertGreater(float(textured.mean()), 0.5)
        self.assertGreater(int(np.count_nonzero(page.ink)), 5000)
        self.assertGreaterEqual(len(page.truth), 4)

    def test_faint_lettering_page_has_faint_ink_and_a_control(self):
        page = eval_pipeline.build_synthetic_page("faint-lettering")
        gray = cv2.cvtColor(page.image, cv2.COLOR_RGB2GRAY)
        faint = np.count_nonzero(page.ink & (gray >= 160) & (gray <= 210))
        dark = np.count_nonzero(page.ink & (gray <= 64))
        self.assertGreater(faint, 2000)
        self.assertGreater(dark, 1000)

    def test_touching_outline_page_lettering_crosses_the_bubble_ring(self):
        page = eval_pipeline.build_synthetic_page("text-touching-outline")
        gray = cv2.cvtColor(page.clean, cv2.COLOR_RGB2GRAY)
        outline = gray < 100
        crossing = []
        for truth in page.truth:
            x1, y1, x2, y2 = truth.xyxy
            in_box = page.ink[y1:y2, x1:x2] & outline[y1:y2, x1:x2]
            crossing.append(int(in_box.sum()))
        # The two oversized blocks cross the outline; the control does not.
        self.assertGreater(crossing[0], 50)
        self.assertGreater(crossing[1], 50)
        self.assertEqual(crossing[2], 0)

    def test_adversarial_pages_score_ok_with_all_stages(self):
        result = run(
            synthetic_pages_by_name("tone-rotated", "faint-lettering", "text-touching-outline"),
            label="adversarial",
        )
        self.assertEqual(len(result["pages"]), 3)
        for page in result["pages"]:
            with self.subTest(page=page["id"]):
                self.assertEqual(page["status"], "ok", page.get("error"))
                self.assertEqual(page["truth"]["recall"], 1.0)
                self.assertGreater(page["blocks"]["detected"], 0)
                for key in (
                    "ink_mask_recall",
                    "residual_in_mask_ratio",
                    "residual_out_of_mask_ratio",
                    "residual_ink_lp_ratio",
                    "hole_in_tone_px",
                    "render_ink_outside_bubble_px",
                    "ocr_cer",
                ):
                    self.assertIn(key, page["truth"])

    def test_faint_lettering_leaves_a_mask_miss(self):
        result = run(synthetic_pages_by_name("faint-lettering"))
        truth = result["pages"][0]["truth"]
        self.assertLess(truth["ink_mask_recall"], 1.0)
        self.assertGreater(truth["residual_out_of_mask_ratio"], 0.0)


class CharacterErrorRateTests(unittest.TestCase):
    def test_edit_distance(self):
        self.assertEqual(eval_pipeline._edit_distance("kitten", "sitting"), 3)
        self.assertEqual(eval_pipeline._edit_distance("", "abc"), 3)
        self.assertEqual(eval_pipeline._edit_distance("abc", "abc"), 0)

    def test_strip_for_cer_folds_full_width_and_drops_spaces(self):
        self.assertEqual(eval_pipeline._strip_for_cer("ちょっと 待って！"), "ちょっと待って!")
        self.assertEqual(eval_pipeline._strip_for_cer(full_width("HEY!")), "HEY!")

    def test_ocr_cer_matches_boxes_by_iou(self):
        truth = eval_pipeline.TruthBox((0, 0, 100, 100), "こんにちは", "bubble")
        detected = [[0, 0, 100, 100]]
        score = eval_pipeline._ocr_cer((truth,), detected, ["こんちには"])
        self.assertEqual(score, {"ocr_cer": 0.4, "ocr_cer_blocks": 1})
        # A box that does not overlap the truth is not scored.
        far = eval_pipeline._ocr_cer((truth,), [[200, 200, 260, 260]], ["こんちには"])
        self.assertEqual(far, {"ocr_cer": None, "ocr_cer_blocks": 0})


if __name__ == "__main__":
    unittest.main()
