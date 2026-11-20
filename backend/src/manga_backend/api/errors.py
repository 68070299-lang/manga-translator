"""Error sanitizing, last-error bookkeeping and translation of failures to HTTP errors."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import NoReturn

from fastapi import HTTPException

from manga_backend.api.sanitize import sanitize_error
from manga_backend.api.state import BackendState
from manga_backend.pipeline import (
    DetectorUnavailableError,
    InpainterUnavailableError,
    PaddleOCRUnavailableError,
)
from manga_backend.models.translator import TranslatorError, TranslatorTimeoutError

logger = logging.getLogger(__name__)

INTERNAL_ERROR_DETAIL = "Internal server error"

# Errors that carry their own public status code and client-safe detail.
PUBLIC_ERROR_TYPES: tuple[type[Exception], ...] = (
    InpainterUnavailableError,
    DetectorUnavailableError,
    PaddleOCRUnavailableError,
    TranslatorTimeoutError,
    TranslatorError,
)

def raise_endpoint_error(
    backend: BackendState,
    message: str,
    error: Exception,
    *,
    request_id: str = "",
    path: str = "",
    sensitive_values: Sequence[str] | None = None,
) -> NoReturn:
    """Preserve intended HTTP errors and convert unexpected failures to 500."""
    if isinstance(error, HTTPException):
        backend.record_last_error(request_id, path, error, error.status_code, sensitive_values)
        raise error
    if isinstance(error, PUBLIC_ERROR_TYPES):
        status_code: int = getattr(error, "status_code")
        detail: str = getattr(error, "public_detail")
        backend.record_last_error(request_id, path, error, status_code, sensitive_values)
        raise HTTPException(status_code=status_code, detail=detail) from error
    if isinstance(error, ValueError):
        backend.record_last_error(request_id, path, error, 400, sensitive_values)
        raise HTTPException(status_code=400, detail=str(error)) from error

    backend.record_last_error(request_id, path, error, 500, sensitive_values)
    logger.error("%s request_id=%s error=%s", message, request_id, sanitize_error(error, sensitive_values))
    raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL) from error


@contextmanager
def endpoint_errors(
    backend: BackendState,
    message: str,
    *,
    request_id: str,
    path: str,
    sensitive_values: Sequence[str] | None = None,
) -> Iterator[None]:
    """Run an endpoint body and report any failure as a sanitized HTTP error."""
    try:
        yield
    except Exception as error:
        raise_endpoint_error(
            backend,
            message,
            error,
            request_id=request_id,
            path=path,
            sensitive_values=sensitive_values,
        )
