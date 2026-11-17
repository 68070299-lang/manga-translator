"""Request guard: request ids, origin allow-list, token auth, body-size cap and access log."""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from manga_backend.api.auth import (
    BACKEND_AUTH_HEADER,
    is_allowed_browser_origin,
    is_public_request,
    token_matches,
)
from manga_backend.api.deps import REQUEST_ID_HEADER
from manga_backend.api.responses import OrjsonResponse
from manga_backend.api.sanitize import sanitize_error
from manga_backend.api.state import BackendState

logger = logging.getLogger(__name__)


class RequestGuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, backend: BackendState) -> None:
        super().__init__(app)
        self.backend = backend

    @staticmethod
    def _log_rejected(
        request: Request, request_id: str, status_code: int, started: float, client_host: str
    ) -> None:
        """Log an early rejection (auth/size) without recording last_error or logging any token."""
        logger.info(
            "request complete request_id=%s method=%s path=%s status=%s duration=%.3fs client=%s",
            request_id,
            request.method,
            request.url.path,
            status_code,
            time.monotonic() - started,
            client_host,
        )

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        backend = self.backend
        settings = backend.settings
        limits = backend.limits
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.monotonic()
        client_host = request.client.host if request.client else "unknown"

        def reject(status_code: int, detail: str) -> Response:
            response = OrjsonResponse({"detail": detail}, status_code=status_code)
            response.headers[REQUEST_ID_HEADER] = request_id
            self._log_rejected(request, request_id, status_code, started, client_host)
            return response

        origin = request.headers.get("origin")
        if origin and not is_allowed_browser_origin(origin, settings.allowed_origins):
            return reject(403, "Origin is not allowed for this local backend")

        if not settings.testing and not is_public_request(request.method, request.url.path):
            if not settings.auth_token:
                return reject(503, "Backend authentication is not configured")
            provided_token = request.headers.get(BACKEND_AUTH_HEADER, "")
            if not token_matches(provided_token, settings.auth_token):
                return reject(401, "Invalid backend authentication token")

        # Reject oversized bodies before Starlette spools the multipart upload to disk.
        content_length = request.headers.get("content-length", "")
        # A very long digit string is oversized by definition, and int() refuses to parse it.
        if (
            content_length.isascii()
            and content_length.isdigit()
            and (
                len(content_length) > 18
                or int(content_length) > limits.max_upload_size + limits.upload_overhead_bytes
            )
        ):
            return reject(413, "Request body too large")

        try:
            response = await call_next(request)
        except Exception as exc:
            duration = time.monotonic() - started
            backend.record_last_error(request_id, request.url.path, exc, 500, sensitive_values=[])
            logger.error(
                "request failed request_id=%s method=%s path=%s status=500 duration=%.3fs client=%s error=%s",
                request_id,
                request.method,
                request.url.path,
                duration,
                client_host,
                sanitize_error(exc, sensitive_values=[]),
            )
            raise

        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(
            "request complete request_id=%s method=%s path=%s status=%s duration=%.3fs client=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            time.monotonic() - started,
            client_host,
        )
        return response
