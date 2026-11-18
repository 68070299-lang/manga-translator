"""Translation step: provider call, output alignment and no-op detection."""

from __future__ import annotations

import logging
import time
from typing import Any

from manga_backend.models.translator import Translator
from manga_backend.pipeline.text import has_cjk

logger = logging.getLogger(__name__)


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


async def translate_texts(
    original_texts: list[str],
    target_lang: str,
    *,
    translator: str,
    api_key: str | None,
    model: str | None,
    local_llm_base_url: str | None,
    local_llm_api_key: str | None,
) -> list[str]:
    """Translate one string per translation unit with one provider call.

    A unit is one block, or all the blocks of one bubble joined in reading
    order, so the provider receives whole utterances instead of confidence
    ordered fragments.

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
        translated = await translator_client.translate(original_texts, target_lang)
    finally:
        await translator_client.close()
    translated_texts = align_texts(translated, len(original_texts), "translation")
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
