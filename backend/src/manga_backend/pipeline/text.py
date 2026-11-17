"""Text predicates shared by translation checks, masking and rendering."""

from __future__ import annotations

import re
import unicodedata

# Han, Hiragana, Katakana, Hangul, CJK punctuation and full/half-width forms.
_CJK_PATTERN = re.compile(
    r"[一-鿿㐀-䶿\U00020000-\U0002a6df豈-﫿"
    r"぀-ゟ゠-ヿ가-힯ᄀ-ᇿ"
    r"　-〿＀-￯]"
)


def has_cjk(text: str) -> bool:
    return _CJK_PATTERN.search(text) is not None


def is_unchanged_punctuation(original: object, translated: object) -> bool:
    """Keep punctuation-only blocks untouched when translation changes nothing."""
    source = unicodedata.normalize("NFKC", str(original or "")).strip()
    target = unicodedata.normalize("NFKC", str(translated or "")).strip()
    return bool(source) and source == target and not any(
        character.isalnum() for character in source
    )


def normalize_render_source(text: object) -> str:
    return "".join(unicodedata.normalize("NFKC", str(text)).split())
