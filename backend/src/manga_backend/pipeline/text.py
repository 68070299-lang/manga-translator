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


_CJK_LETTER_PATTERN = re.compile(
    "["
    "㐀-䶿一-鿿豈-﫿\U00020000-\U0002a6df"  # Han
    "々-〇"  # 々 〆 〇
    "ぁ-ゟ"  # Hiragana
    "ァ-ヺー-ヿ"  # Katakana and ー, without the middle dot
    "ᄀ-ᇿㄱ-ㆎ가-힯"  # Hangul
    "]"
)


def has_cjk(text: str) -> bool:
    return _CJK_PATTERN.search(text) is not None


def has_cjk_letters(text: str) -> bool:
    """Return True when text still holds Han, kana or Hangul after NFKC folding.

    Use this to decide whether OCR of rendered output still shows source text.
    OCR engines such as manga-ocr widen ASCII to full-width forms, so rendered
    English comes back as "Ｒｅａｄｙ．". NFKC folds that to "Ready." and
    half-width katakana to full-width katakana. CJK punctuation on its own,
    such as "。" or "「」", is not counted.
    """
    return _CJK_LETTER_PATTERN.search(unicodedata.normalize("NFKC", text)) is not None


def is_unchanged_punctuation(original: object, translated: object) -> bool:
    """Keep punctuation-only blocks untouched when translation changes nothing."""
    source = unicodedata.normalize("NFKC", str(original or "")).strip()
    target = unicodedata.normalize("NFKC", str(translated or "")).strip()
    return bool(source) and source == target and not any(
        character.isalnum() for character in source
    )


def normalize_render_source(text: object) -> str:
    return "".join(unicodedata.normalize("NFKC", str(text)).split())
