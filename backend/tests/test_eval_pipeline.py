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
        self.assertEqual(self.result["summary"]["mask"]["white_fill_blocks"], 0)

    def test_residue_reports_step6_verdict_and_kana_kanji_separately(self):
        residue = self.result["summary"]["cjk_residue"]
        # Full-width Latin is CJK to the pipeline's has_cjk, not kana/kanji.
        self.assertEqual(residue["step6_flagged"], residue["step6_checked"])
        # EchoOcr reads free text (no bubble, so RT-DETR's "sfx" type) back as Japanese.
        free_text = sum(
            truth.bubble is None
            for name in eval_pipeline.SYNTHETIC_PAGES
            for truth in eval_pipeline.build_synthetic_page(name).truth
        )
        self.assertGreater(free_text, 0)
        self.assertEqual(residue["kana_kanji"], free_text)
        self.assertLess(residue["kana_kanji_rate"], residue["step6_rate"])

    def test_ground_truth_scores_erasure_against_the_clean_plate(self):
        for page in self.result["pages"]:
            truth = page["truth"]
            with self.subTest(page=page["id"]):
                self.assertGreaterEqual(truth["residual_ink_ratio"], 0.0)
                self.assertLess(truth["residual_ink_ratio"], 0.5)
                self.assertEqual(truth["over_erased_px"], 0)

    def test_by_tag_summary_covers_each_tag(self):
        by_tag = self.result["summary"]["by_tag"]
        self.assertEqual(by_tag["webtoon-strip"]["pages"]["scored"], 1)
        self.assertEqual(by_tag["white-bubble"]["pages"]["scored"], 3)

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
        self.assertEqual(orchestrator_module.create_mask.__module__, masks_module.__name__)
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
        self.assertEqual(rectangular["pages"][0]["mask"]["path"], "rectangular")
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
                    synthetic_pages("syn-white-bubbles", "syn-textured-narration"),
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
        self.assertEqual(white["inpaint"]["strategy"], "white_glyph_fill")
        self.assertGreater(textured["mask"]["model_blocks"], 0)
        self.assertTrue(textured["inpaint"]["fallback"])
        self.assertEqual(textured["inpaint"]["used"], "lama_large")
        self.assertEqual(result["summary"]["inpaint"]["fallback_pages"], 1)
        self.assertEqual(result["summary"]["inpaint"]["changed_outside_mask_px"], 0)
        self.assertIn("lama_large", loaded)

    def test_inpainter_changes_outside_the_mask_are_counted(self):
        result = run(synthetic_pages("syn-white-bubbles"), inpainter=LeakyInpainter(), name="fake-leaky")
        page = result["pages"][0]
        self.assertGreater(page["inpaint"]["model_changed_outside_mask_px"], 0)
        self.assertGreater(page["inpaint"]["changed_outside_mask_px"], 0)
        self.assertGreater(page["truth"]["over_erased_px"], 0)
        self.assertEqual(result["summary"]["inpaint"]["pages_changed_outside_mask"], 1)

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


if __name__ == "__main__":
    unittest.main()
