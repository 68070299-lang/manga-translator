"""Per-app runtime state, attached to `app.state.backend` by `create_app`."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager

from fastapi import HTTPException

from manga_backend.api.sanitize import sanitize_error
from manga_backend.api.schemas import LastError, SanitizedConfig
from manga_backend.config import Limits, Settings
from manga_backend.pipeline import TranslationPipeline

# Builds the pipeline on startup from the "use GPU" setting.
PipelineFactory = Callable[[bool], TranslationPipeline]


class BackendState:
    """Everything that used to be module-level globals in the old main.py."""

    def __init__(
        self,
        settings: Settings,
        limits: Limits,
        *,
        pipeline: TranslationPipeline | None = None,
        pipeline_factory: PipelineFactory,
    ) -> None:
        self.settings = settings
        self.limits = limits
        self.pipeline = pipeline
        self.pipeline_factory = pipeline_factory
        self.pipeline_init_task: asyncio.Task[None] | None = None
        self.started_at = time.monotonic()
        self.active_requests = 0
        self.last_error: LastError | None = None
        self._limiter: tuple[asyncio.AbstractEventLoop, int, asyncio.Semaphore] | None = None

    # --- health ---------------------------------------------------------

    def pipeline_is_loading(self) -> bool:
        """True while the background pipeline initialization task is still running."""
        task = self.pipeline_init_task
        return task is not None and not task.done()

    def sanitized_config(self) -> SanitizedConfig:
        return SanitizedConfig(
            allowed_browser_origins_count=len(self.settings.allowed_origins),
            max_upload_size=self.limits.max_upload_size,
            max_image_pixels=self.limits.max_image_pixels,
            use_gpu=self.settings.use_gpu,
        )

    def record_last_error(
        self,
        request_id: str,
        path: str,
        error: BaseException,
        status_code: int = 500,
        sensitive_values: Sequence[str] | None = None,
    ) -> None:
        self.last_error = LastError(
            request_id=request_id,
            path=path,
            status=status_code,
            error=sanitize_error(error, sensitive_values),
            timestamp=round(time.time(), 3),
        )

    # --- admission control ------------------------------------------------

    def _semaphore(self) -> asyncio.Semaphore:
        """Return the shared pipeline-concurrency semaphore for the running event loop."""
        loop = asyncio.get_running_loop()
        limit = self.settings.max_concurrent
        if self._limiter is None or self._limiter[0] is not loop or self._limiter[1] != limit:
            self._limiter = (loop, limit, asyncio.Semaphore(limit))
        return self._limiter[2]

    @asynccontextmanager
    async def request_slot(self) -> AsyncIterator[None]:
        """Count an expensive request and queue it behind the concurrency limit.

        Requests wait up to `queue_timeout_seconds` for a slot (the extension has
        no 429 handling, so queueing is the normal path) and only then get a 429.
        """
        self.active_requests += 1
        try:
            semaphore = self._semaphore()
            try:
                async with asyncio.timeout(self.settings.queue_timeout_seconds):
                    await semaphore.acquire()
            except TimeoutError:
                raise HTTPException(
                    status_code=429,
                    detail="Backend is busy; try again later",
                    headers={"Retry-After": str(self.limits.queue_retry_after_seconds)},
                ) from None
            try:
                yield
            finally:
                semaphore.release()
        finally:
            self.active_requests = max(0, self.active_requests - 1)
