"""Font lookup and caching for text rendering."""

from __future__ import annotations

import threading
from collections.abc import Sequence

from PIL import ImageFont

from manga_backend.paths import FONTS_DIR

FontObject = ImageFont.FreeTypeFont | ImageFont.ImageFont

_ENGLISH_FONT_PATHS = (
    str(FONTS_DIR / "CCWildWords.ttf"),
    str(FONTS_DIR / "ccwildwords.ttf"),
    r"C:\Windows\Fonts\CCWildWords.ttf",
    r"C:\Windows\Fonts\ccwild.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)

_CJK_FONT_PATHS = (
    r"C:\Windows\Fonts\msgothic.ttc",
    r"C:\Windows\Fonts\meiryo.ttc",
    r"C:\Windows\Fonts\YuGothM.ttc",
    r"C:\Windows\Fonts\msjh.ttc",
    r"C:\Windows\Fonts\malgun.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
)

_FALLBACK_FONT_PATHS = (
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)

_FONT_PATHS_BY_CATEGORY = {
    "english": _ENGLISH_FONT_PATHS,
    "cjk": _CJK_FONT_PATHS,
    "fallback": _FALLBACK_FONT_PATHS,
}

# Paths already proven to load and draw. Rendering runs in worker threads, so
# the font-object cache fill is serialized.
_font_cache: dict[str, str] = {}
_font_object_cache: dict[tuple[str, int], FontObject] = {}
_font_object_cache_lock = threading.Lock()


def _resolve_font_path(paths: Sequence[str]) -> str | None:
    for path in paths:
        if path in _font_cache:
            return _font_cache[path]
        try:
            font = ImageFont.truetype(path, 12)
            mask = font.getmask("A")
            if mask and mask.size[0] * mask.size[1] > 0:
                _font_cache[path] = path
                return path
        except Exception:
            pass
    return None


def get_cached_font(category: str) -> str | None:
    """Return the first usable font path for "english", "cjk" or "fallback"."""
    return _resolve_font_path(_FONT_PATHS_BY_CATEGORY.get(category, ()))


def get_font_object(font_path: str | None, font_size: int) -> FontObject:
    """Return a cached PIL font object, loading it once under the cache lock."""
    key = (font_path or "default", font_size)
    with _font_object_cache_lock:
        font = _font_object_cache.get(key)
        if font is None:
            try:
                font = (
                    ImageFont.truetype(font_path, font_size)
                    if font_path
                    else ImageFont.load_default()
                )
            except Exception:
                font = ImageFont.load_default()
            _font_object_cache[key] = font
        return font
