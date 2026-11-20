"""Scrub secrets and filesystem paths from text that reaches clients or logs."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

# Absolute filesystem paths (drive, UNC and common POSIX roots) must not reach
# clients or logs; the trailing class keeps sentence punctuation out of the match.
_FILESYSTEM_PATH_RE = re.compile(
    r"(?:(?<!\w)[A-Za-z]:[\\/]|\\\\)(?:[^\s'\"<>|*?]*[^\s'\"<>|*?.,;:)\]])?"
    r"|(?<![\w.~])/(?:home|Users|tmp|var|opt|usr|mnt|root|etc|private|srv|data)\b"
    r"(?:/[^\s'\"<>|*?]*[^\s'\"<>|*?.,;:)\]])?"
)

_REDACTED_FIELD_NAMES = ("api_key", "local_llm_api_key")


def scrub_filesystem_paths(text: str) -> str:
    return _FILESYSTEM_PATH_RE.sub("[path]", text)


def scrub_model_status(value: Any) -> Any:
    """Copy model status with filesystem paths removed from every error string."""
    if isinstance(value, dict):
        return {
            key: scrub_filesystem_paths(item)
            if key == "error" and isinstance(item, str)
            else scrub_model_status(item)
            for key, item in value.items()
        }
    return value


def sanitize_error(error: BaseException, sensitive_values: Sequence[str] | None = None) -> str:
    text = str(error) or error.__class__.__name__
    for token in _REDACTED_FIELD_NAMES:
        text = re.sub(token, "[redacted_key_field]", text, flags=re.IGNORECASE)
    for value in sensitive_values or ():
        if value:
            text = text.replace(value, "[redacted_secret]")
    return scrub_filesystem_paths(text)[:500]
