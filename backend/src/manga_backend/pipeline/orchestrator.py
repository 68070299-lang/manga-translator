"""Translation pipeline: orchestrates detection, OCR, translation, inpainting and rendering."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import numpy as np

from manga_backend.models.detector import TextDetector
from manga_backend.models.ocr import OCREngine
from manga_backend.models.translator import supports_page_image
from manga_backend.pipeline.bubbles import (
    infer_bubble_render_bounds,
    infer_bubble_text_centers,
)
from manga_backend.pipeline.components import (
    component_ready,
    new_model_status,
    preload,
    prepare_paddle_engine,
)
from manga_backend.pipeline.detection import (
    DetectorRegistry,
    normalize_detector_type,
    text_block_to_dict,
)
from manga_backend.pipeline.inpainting import (
    DEFAULT_INPAINTER,
    LAZY_INPAINTERS,
    InpainterManager,
    create_inpainter,
)
from manga_backend.pipeline.layout import (
    group_translation_units,
    sort_reading_order,
    unit_source_text,
)
from manga_backend.pipeline.masks import prepare_flux_inpainting
from manga_backend.pipeline.measure import source_glyph_size
from manga_backend.pipeline.ocr import blocks_from_payload, run_ocr
from manga_backend.pipeline.page_context import (
    PageContext,
    build_numbered_page,
    plan_page_context,
    unit_box,
)
from manga_backend.pipeline.rendering import render_text
from manga_backend.pipeline.text import has_cjk_letters, is_unchanged_punctuation
from manga_backend.pipeline.translation import TranslationReport, translate_texts
from manga_backend.utils.textblock import TextBlock

if TYPE_CHECKING:
    from manga_backend.models.paddle_ocr_onnx import PaddleOCROnnxEngine

logger = logging.getLogger(__name__)

_READING_DIRECTIONS = frozenset({"auto", "rtl", "ltr"})


def erase_candidate(block: TextBlock) -> bool:
    """Whether a block's ink will be erased, decided from its OCR text alone.

    Masking starts before the provider answers, so it cannot know the
    translation. A source text that is only punctuation comes back unchanged
    from a provider and is never rendered, so it is left alone; everything
    else is erased while the translation is in flight. A translation that
    comes back empty (and so fails ``erase_from_translation``) rebuilds the
    mask for the smaller set, which keeps the page's original lettering.
    """
    text = block.text
    return bool(text.strip()) and not is_unchanged_punctuation(text, text)


def erase_from_translation(block: TextBlock) -> bool:
    """The render filter, unchanged: erase the ink of every block that renders text."""
    return bool(block.translated_text.strip()) and not is_unchanged_punctuation(
        block.text, block.translated_text
    )


def _pop_reading_direction(render_options: dict[str, Any]) -> str:
    """Take the reading-direction option out of the render call.

    "auto" reads right to left like a manga page unless the page is a tall
    strip (height >= 1.6 x width), which reads left to right like a webtoon.
    An unknown value warns and falls back to "auto" instead of failing the
    translation.
    """
    direction = str(render_options.pop("reading_direction", "auto") or "auto")
    normalized = direction.strip().lower()
    if normalized not in _READING_DIRECTIONS:
        logger.warning("Unknown reading_direction %r; using auto", direction)
        return "auto"
    return normalized


class TranslationPipeline:
    """Main pipeline for manga translation."""

    def __init__(self, device: str = "cpu") -> None:
        self.device = device
        self.is_initialized = False

        # Model status tracking, shared with the component managers.
        self.model_status = new_model_status()

        # Model instances (lazy loaded)
        self._detectors = DetectorRegistry(device, self.model_status)
        self._ocr: OCREngine | None = None
        self._inpainters = InpainterManager(device, self.model_status)

    @property
    def is_ready(self) -> bool:
        """True when the core detector and OCR components are available."""
        return (
            self.is_initialized
            and self.component_ready("detector")
            and self.component_ready("ocr")
        )

    def component_ready(self, component: str) -> bool:
        """Report readiness independently so optional failures do not mask healthy routes."""
        return component_ready(
            self.model_status,
            component,
            initialized=self.is_initialized,
            inpainter_type=self._inpainters.type,
        )

    def readiness(self) -> dict[str, bool]:
        return {name: self.component_ready(name) for name in self.model_status}

    async def initialize(self) -> None:
        """Initialize the pipeline and pre-load default models."""
        logger.info("Initializing pipeline components...")

        def set_detector(detector: TextDetector) -> None:
            self._detectors.default = detector

        def set_ocr(engine: OCREngine) -> None:
            self._ocr = engine

        def set_inpainter(model: Any) -> None:
            self._inpainters.instance = model

        await preload(
            self.model_status,
            "detector",
            lambda: TextDetector(device=self.device),
            set_detector,
        )
        await preload(
            self.model_status, "ocr", lambda: OCREngine(device=self.device), set_ocr
        )
        # PaddleOCR (about 170 MB, two more sessions) is not preloaded: the
        # default path is ready once the detector and manga-ocr are, and the
        # first paddle-ocr request loads it through _prepare_paddle_engine.
        logger.info("PaddleOCR deferred to first use")

        # Defer large local Flux runtimes to the first translation request.
        inpainter_type = self._inpainters.type
        if inpainter_type in LAZY_INPAINTERS:
            self.model_status["inpainter"]["status"] = "not_loaded"
            self.model_status["inpainter"]["name"] = inpainter_type
            logger.info("Inpainter %s deferred to first translate", inpainter_type)
        else:
            await preload(
                self.model_status,
                "inpainter",
                lambda: create_inpainter(inpainter_type, self.device),
                set_inpainter,
                required=False,
            )

        self.is_initialized = True
        logger.info("Pipeline initialization complete")

    async def warm_up(self) -> None:
        """Load the inpainter a first page will most likely need."""
        await self._inpainters.warm_up()

    def dispose(self) -> None:
        """Release loaded detector and inpainter runtimes."""
        self._detectors.dispose()
        self._inpainters.dispose()

    async def detect(
        self, image: np.ndarray, detector: str = "rtdetr"
    ) -> list[dict[str, Any]]:
        """Detect text regions in an image."""
        detector_type = normalize_detector_type(detector)
        selected_detector = await self._detectors.get(detector_type)
        blocks = await selected_detector.detect(image)
        return [text_block_to_dict(block) for block in blocks]

    async def ocr(
        self,
        image: np.ndarray,
        blocks: Sequence[TextBlock | Mapping[str, Any]],
        ocr: str = "manga-ocr",
        google_ocr_api_key: str | None = None,
        google_ocr_language_hint: str | None = None,
    ) -> list[str]:
        """Extract text from detected regions."""
        return await self._run_ocr(
            image,
            blocks_from_payload(blocks),
            ocr,
            google_ocr_api_key,
            google_ocr_language_hint,
        )

    async def paddle_ocr_full(
        self, image: np.ndarray, lang: str = "japan"
    ) -> list[dict[str, Any]]:
        """Run full Paddle OCR and publish component readiness for status endpoints."""
        engine = await self._prepare_paddle_engine(lang=lang)
        results = await engine.detect_and_recognize(image)
        return results if isinstance(results, list) else []

    async def _prepare_paddle_engine(self, lang: str = "japan") -> PaddleOCROnnxEngine:
        return await prepare_paddle_engine(self.model_status, self.device, lang)

    async def _run_ocr(
        self,
        image: np.ndarray,
        blocks: list[TextBlock],
        ocr: str,
        google_ocr_api_key: str | None,
        google_ocr_language_hint: str | None,
    ) -> list[str]:
        return await run_ocr(
            image,
            blocks,
            ocr,
            google_ocr_api_key,
            google_ocr_language_hint,
            local_engine=self._ocr,
            prepare_paddle_engine=self._prepare_paddle_engine,
        )

    async def translate(
        self,
        image: np.ndarray,
        target_lang: str = "English",
        detector: str = "rtdetr",
        ocr: str = "manga-ocr",
        translator: str = "none",
        inpainter: str = DEFAULT_INPAINTER,
        render: bool = True,
        api_key: str | None = None,
        model: str | None = None,
        google_ocr_api_key: str | None = None,
        google_ocr_language_hint: str | None = None,
        local_llm_base_url: str | None = None,
        local_llm_api_key: str | None = None,
        verify_translation: bool = False,
        **render_options: Any,
    ) -> dict[str, Any]:
        """Full translation pipeline; render_options are passed to render_text."""
        reading_direction = _pop_reading_direction(render_options)
        result: dict[str, Any] = {
            "blocks": [],
            "original_texts": [],
            "translated_texts": [],
            "image": None,
        }

        # Step 1: Detect text regions
        start_time = time.time()
        logger.info("Step 1: Detecting text regions with %s...", detector)
        detector_type = normalize_detector_type(detector)
        selected_detector = await self._detectors.get(detector_type)
        blocks = await selected_detector.detect(image)
        logger.info(
            "Step 1 complete: Detected %s blocks in %.2fs",
            len(blocks),
            time.time() - start_time,
        )
        # The detector emits blocks by confidence; the reader (and so the
        # translator) wants them in reading order: panels first, then the
        # lettering inside each panel.
        blocks = sort_reading_order(image, blocks, reading_direction)

        result["blocks"] = [text_block_to_dict(block) for block in blocks]

        if not blocks:
            logger.info("No text blocks detected")
            result["image"] = image
            return result

        # Step 2: OCR
        start_time = time.time()
        logger.info("Step 2: Running OCR (%s) on %s blocks...", ocr, len(blocks))
        original_texts = await self._run_ocr(
            image, blocks, ocr, google_ocr_api_key, google_ocr_language_hint
        )
        logger.info("Step 2 complete: OCR finished in %.2fs", time.time() - start_time)

        result["original_texts"] = original_texts
        for block, text in zip(blocks, original_texts):
            block.text = text

        # Step 3: Translation (if translator specified). The blocks of one
        # bubble are one translation unit, so a sentence split across two
        # detected blocks is translated as a whole; every member block then
        # carries the unit's translation, and the render path (which merges
        # bubble-mates into one region) draws it once.
        translating = bool(translator) and translator != "none"
        units = group_translation_units(blocks)
        unit_texts = [unit_source_text(unit) for unit in units]

        # Step 4 starts here: masking needs only the detected boxes and the
        # OCR text, so the erase runs while the provider translates and the
        # two are joined before rendering.
        erase_blocks = [block for block in blocks if erase_candidate(block)]
        inpaint_task: asyncio.Task[np.ndarray] | None = None
        if render and inpainter and inpainter != "none":
            logger.info("Step 4: Inpainting using %s (concurrent with translation)...", inpainter)
            inpaint_task = asyncio.create_task(
                self._inpaint(image, erase_blocks, inpainter, detector_type, result)
            )

        if translating:
            # The length budget is the unit's room capacity at the page's
            # lettering size, so it needs the source em the mask stage
            # measures; measure it here first, on the same pixels and for the
            # same blocks (the mask stage measures the identical value).
            def prepare_page_context() -> PageContext:
                for block in erase_blocks:
                    if block.source_font_size is None:
                        block.source_font_size = source_glyph_size(image, block)
                return plan_page_context(
                    image,
                    units,
                    blocks,
                    preferred_font_size=int(render_options.get("preferred_font_size", 16)),
                    line_spacing=float(render_options.get("line_spacing", 1.2)),
                    stroke_width=float(render_options.get("border_size", 0.0)),
                    auto_scale=bool(render_options.get("auto_scale", True)),
                    text_direction=str(render_options.get("text_direction", "auto")),
                )

            # Rooms, font metrics and the flood fill are CPU work; keep them
            # off the event loop while the erase runs beside them.
            page_context = await asyncio.to_thread(prepare_page_context)
            result["length_budgets"] = [
                None if budget is None else budget.as_dict()
                for budget in page_context.budgets
            ]
            # Only a route that can take an image part receives the page; the
            # text-only routes keep the text-only prompt.
            page_image = None
            if supports_page_image(translator, model):
                page_image, _marks = build_numbered_page(
                    image, [unit_box(unit) for unit in units]
                )
            report = TranslationReport()
            translated_units = await translate_texts(
                unit_texts,
                target_lang,
                translator=translator,
                api_key=api_key,
                model=model,
                local_llm_base_url=local_llm_base_url,
                local_llm_api_key=local_llm_api_key,
                budgets=page_context.character_budgets,
                page_image=page_image,
                fits=page_context.fits,
                report=report,
            )
            result["translation_reasks"] = report.as_dict()
        else:
            translated_units = list(unit_texts)
        for unit, unit_translation in zip(units, translated_units):
            for block in unit:
                block.translated_text = unit_translation

        result["translated_texts"] = [block.translated_text for block in blocks]

        # Step 4 (join): the erase that started with translation finishes
        # here. If the translation filter drops a block that the source-side
        # rule erased (an empty translation), the mask is rebuilt for the
        # smaller set so the page keeps that block's original lettering.
        render_image = image.copy()
        if inpaint_task is not None:
            render_image = await inpaint_task
            final_blocks = [block for block in blocks if erase_from_translation(block)]
            if [id(block) for block in final_blocks] != [id(block) for block in erase_blocks]:
                logger.info(
                    "Step 4: Translation kept %s of %s blocks; rebuilding the mask",
                    len(final_blocks),
                    len(erase_blocks),
                )
                render_image = await self._inpaint(
                    image, final_blocks, inpainter, detector_type, result
                )
        else:
            logger.info("Step 4: Inpainting skipped")

        # Step 5: Render translated text
        if render:
            start_time = time.time()
            logger.info("Step 5: Rendering text...")
            result["image"] = await asyncio.to_thread(
                render_text, render_image, blocks, **render_options
            )
            logger.info(
                "Step 5 complete: Rendering finished in %.2fs", time.time() - start_time
            )

            # Validate output is not mostly black (indicates inpainting/rendering failure)
            mean_brightness = result["image"].mean()
            if mean_brightness < 5:
                logger.warning(
                    "Rendered image is almost completely black (mean brightness=%.2f). "
                    "Returning original image to avoid showing a black result.",
                    mean_brightness,
                )
                result["image"] = image
        else:
            result["image"] = render_image

        # Step 6: Verify rendered text (re-OCR to ensure it's not still Japanese)
        if verify_translation and render and translating and self._ocr is not None:
            await self._verify_rendered_text(self._ocr, result["image"], blocks)

        await asyncio.to_thread(self._infer_bubble_geometry, render_image, blocks)
        result["blocks"] = [text_block_to_dict(block) for block in blocks]
        return result

    async def _inpaint(
        self,
        image: np.ndarray,
        blocks: list[TextBlock],
        inpainter: str,
        detector_type: str,
        result: dict[str, Any],
    ) -> np.ndarray:
        """Step 4: erase the original text; record how it was done in `result`.

        ``blocks`` is the erase set: the caller decides it (from the OCR text
        while translating, or from the translation afterwards), because the
        erase starts concurrently with the provider.
        """
        start_time = time.time()
        logger.info("Step 4: Erasing %s blocks...", len(blocks))
        async with self._inpainters.execution_lock:
            inpaint_blocks = blocks
            rectangular_masks = detector_type == "animetext-yolo"
            # Both routes run the fill router first: tiers 0-2 (flat, smooth,
            # lattice) repaint what they can verify on a held-out band, and
            # the model mask only receives what no cheap tier could fill. The
            # FLUX route keeps its contour-safe glyph masks; the LaMa route
            # keeps its detector-selected mask shape (contour or rectangular).
            flux_route = inpainter == "flux2_klein_lanpaint"
            inpaint_source, mask, filled_count, model_count, fill_decisions = (
                await asyncio.to_thread(
                    prepare_flux_inpainting,
                    image,
                    inpaint_blocks,
                    rectangular=rectangular_masks and not flux_route,
                )
            )
            result["fill_decisions"] = fill_decisions
            result["router_filled_regions"] = filled_count
            result["model_regions"] = model_count

            # Skip model initialization when every detected block was safely
            # removed by a verified fill tier.
            if np.any(mask):
                inpainted, result["inpainter_used"] = await self._inpainters.inpaint_page(
                    inpainter, inpaint_source, mask
                )
                result["inpainting_fallback"] = result["inpainter_used"] != inpainter
                # Pipeline-level composite: keep only what changed inside the
                # mask union, so "nothing outside the erased regions changes"
                # no longer depends on each inpainter's own discipline. The
                # union is the block masks handed to the model plus the pixels
                # the fill router already repainted.
                if inpainted.shape == image.shape:
                    mask_union = mask[:, :, None] > 0 if mask.ndim == 2 else mask > 0
                    if inpaint_source is not image:
                        mask_union = mask_union | np.any(
                            inpaint_source != image, axis=2, keepdims=True
                        )
                    render_image = np.where(mask_union, inpainted, image)
                else:  # Malformed backend output: keep the old passthrough.
                    logger.warning(
                        "Inpainter returned shape %s for a page of %s; skipping the "
                        "mask-union composite",
                        inpainted.shape,
                        image.shape,
                    )
                    render_image = inpainted
            else:
                render_image = inpaint_source
                result["inpainter_used"] = inpainter
                result["inpainting_fallback"] = False
                result["inpainting_strategy"] = "fill_tiers"
        logger.info(
            "Step 4 complete: Inpainting finished in %.2fs", time.time() - start_time
        )
        return render_image

    @staticmethod
    async def _verify_rendered_text(
        ocr_engine: OCREngine, rendered: np.ndarray, blocks: list[TextBlock]
    ) -> None:
        """Step 6: re-OCR the rendered page and flag blocks that still show CJK letters.

        Full-width Latin and CJK punctuation do not count: OCR engines return
        rendered English in full-width forms (see ``has_cjk_letters``).
        """
        try:
            start_time = time.time()
            logger.info("Step 6: Verifying rendered text (re-OCR)...")
            verified_texts = await ocr_engine.process(rendered, blocks)
            issue_count = 0
            for index, (block, verified) in enumerate(zip(blocks, verified_texts)):
                block.verified_text = verified
                block.verification_passed = not (verified and has_cjk_letters(verified))
                if not block.verification_passed:
                    issue_count += 1
                    logger.warning(
                        "VERIFY FAIL block %s: rendered text still contains CJK: "
                        "orig=%r translated=%r verified=%r",
                        index,
                        block.text,
                        block.translated_text,
                        verified,
                    )
            logger.info(
                "Step 6 complete: %s/%s blocks still contain CJK text (%.2fs)",
                issue_count,
                len(blocks),
                time.time() - start_time,
            )
        except Exception as error:
            logger.warning("Step 6: Verification skipped due to error: %s", error)

    @staticmethod
    def _infer_bubble_geometry(image: np.ndarray, blocks: list[TextBlock]) -> None:
        infer_bubble_render_bounds(image, blocks)
        infer_bubble_text_centers(image, blocks)
