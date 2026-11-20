"""Translation step: provider call, output alignment and no-op detection."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from manga_backend.models.translator import Translator
from manga_backend.pipeline.text import has_cjk

logger = logging.getLogger(__name__)


@dataclass
class TranslationReport:
    """What the translate step asked the provider for and how the re-ask went.

    Filled in by ``translate_texts`` and recorded by the orchestrator so the
    harness can report the budgets and the one re-ask. A stub run never enters
    this step, so the report stays empty there.
    """

    units: int = 0
    budgeted: int = 0
    budget_chars: list[int] = field(default_factory=list)
    page_image: bool = False
    requests: int = 0
    reask_units: int = 0
    reask_shortened: int = 0
    reask_failed: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Flat, JSON-safe summary of the step's work."""
        budgets = self.budget_chars
        return {
            "ran": self.requests > 0,
            "units": self.units,
            "budgeted": self.budgeted,
            "budget_chars_min": min(budgets) if budgets else None,
            "budget_chars_max": max(budgets) if budgets else None,
            "budget_chars_mean": round(sum(budgets) / len(budgets), 1) if budgets else None,
            "page_image": self.page_image,
            "requests": self.requests,
            "reask_units": self.reask_units,
            "reask_shortened": self.reask_shortened,
            "reask_failed": self.reask_failed,
        }


def align_texts(values: Any, count: int, field_name: str) -> list[str]:
    """Return exactly one safe string per detected block."""
    if values is None:
        normalized: list[str] = []
    elif isinstance(values, (list, tuple)):
        normalized = ["" if value is None else str(value) for value in values]
    else:
        logger.warning(
            "%s provider output was not an array; replacing it with blanks", field_name
        )
        normalized = []

    if len(normalized) != count:
        logger.warning(
            "%s provider output had %s entries but expected %s; aligning cardinality",
            field_name,
            len(normalized),
            count,
        )
    return (normalized + [""] * count)[:count]


def _unfit_indices(texts: Sequence[str], fits: Callable[[int, str], bool]) -> list[int]:
    """Units whose translation cannot be set at the minimum readable size."""
    return [index for index, text in enumerate(texts) if not fits(index, text)]


async def _reask_long_units(
    client: Translator,
    texts: list[str],
    budgets: Sequence[int | None] | None,
    target_lang: str,
    fits: Callable[[int, str], bool],
    report: TranslationReport | None,
) -> list[str]:
    """Ask once for shorter versions of the units that did not fit.

    Otherwise the renderer sets what it can; there is no second re-ask, so a
    page can never loop on a provider.
    """
    unfit = await asyncio.to_thread(_unfit_indices, texts, fits)
    if not unfit:
        return texts
    logger.info(
        "Translation length: %s of %s units cannot be set readable; asking once for shorter versions",
        len(unfit),
        len(texts),
    )
    if report is not None:
        report.reask_units = len(unfit)
    limits = [
        budgets[index] if budgets is not None and index < len(budgets) else None
        for index in unfit
    ]
    if report is not None:
        report.requests += 1
    try:
        shortened = await client.shorten([texts[index] for index in unfit], limits, target_lang)
    except Exception as error:
        logger.warning(
            "Shorten re-ask failed (%s); rendering the first translations", type(error).__name__
        )
        if report is not None:
            report.reask_failed = True
        return texts
    shortened = align_texts(shortened, len(unfit), "shortened translation")
    merged = list(texts)
    for index, value in zip(unfit, shortened):
        value = value.strip()
        if value:
            merged[index] = value
            if report is not None:
                report.reask_shortened += 1
    return merged


async def translate_texts(
    original_texts: list[str],
    target_lang: str,
    *,
    translator: str,
    api_key: str | None,
    model: str | None,
    local_llm_base_url: str | None,
    local_llm_api_key: str | None,
    budgets: Sequence[int | None] | None = None,
    page_image: bytes | None = None,
    fits: Callable[[int, str], bool] | None = None,
    report: TranslationReport | None = None,
) -> list[str]:
    """Translate one string per translation unit with one provider call.

    A unit is one block, or all the blocks of one bubble joined in reading
    order, so the provider receives whole utterances instead of confidence
    ordered fragments. Each unit carries the character budget of its bubble,
    a vision-capable provider also receives the numbered page, and a unit that
    cannot be set at the minimum readable size gets one shorter rewrite.

    Raises RuntimeError when the provider echoed CJK text back unchanged.
    """
    start_time = time.time()
    logger.info("Step 3: Translating text...")
    translator_client = Translator(
        translator_type=translator,
        api_key=api_key,
        model=model,
        local_llm_base_url=local_llm_base_url,
        local_llm_api_key=local_llm_api_key,
    )
    try:
        await translator_client.initialize()
        if report is not None:
            report.units = len(original_texts)
            report.budget_chars = [int(value) for value in (budgets or ()) if value]
            report.budgeted = len(report.budget_chars)
            report.page_image = page_image is not None
        translated = await translator_client.translate(
            original_texts,
            target_lang,
            budgets=budgets,
            page_image=page_image,
        )
        if report is not None:
            report.requests += 1
        translated_texts = align_texts(translated, len(original_texts), "translation")
        if fits is not None:
            translated_texts = await _reask_long_units(
                translator_client, translated_texts, budgets, target_lang, fits, report
            )
    finally:
        await translator_client.close()
    logger.info(
        "Step 3 complete: Translated %s units in %.2fs",
        len(original_texts),
        time.time() - start_time,
    )

    if translated_texts == original_texts and any(
        has_cjk(original) for original in original_texts
    ):
        raise RuntimeError(
            f"Translation produced no changes for {len(original_texts)} CJK text blocks"
        )
    return translated_texts
