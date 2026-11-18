"""Filesystem locations shared by the backend.

The package lives in backend/src/manga_backend, but runtime data (models_cache,
.env) stays in the backend/ project root so existing downloads keep working.
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = PACKAGE_DIR.parents[1]
MODELS_CACHE_DIR = Path(os.environ.get("MANGA_BACKEND_MODELS_DIR") or BACKEND_ROOT / "models_cache")
ENV_FILE = BACKEND_ROOT / ".env"
FONTS_DIR = PACKAGE_DIR / "fonts"
