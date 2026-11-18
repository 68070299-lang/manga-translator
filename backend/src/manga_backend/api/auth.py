"""Origin and token checks for the local backend."""

from __future__ import annotations

import re
import secrets
from collections.abc import Collection

BACKEND_AUTH_HEADER = "X-Manga-Translator-Token"
EXTENSION_ORIGIN_RE = re.compile(r"^chrome-extension://[a-z]{32}$", re.IGNORECASE)


def is_allowed_browser_origin(origin: str, allowed_origins: Collection[str]) -> bool:
    """Allow extension requests and explicitly configured development origins."""
    return origin in allowed_origins or bool(EXTENSION_ORIGIN_RE.match(origin))


def is_public_request(method: str, path: str) -> bool:
    """Default-deny: only the liveness probe (GET /) and CORS preflights skip the token."""
    return method == "OPTIONS" or (method == "GET" and path == "/")


def token_matches(provided: str, expected: str) -> bool:
    try:
        return secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
    except (TypeError, UnicodeError):
        return False
