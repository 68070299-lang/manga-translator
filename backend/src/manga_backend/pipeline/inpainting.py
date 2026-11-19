"""Inpainter selection, loading, fallback and failure handling."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Protocol

import numpy as np

from manga_backend.comfy_lanpaint.client import ComfyUIUnreachableError
from manga_backend.models.inpainter import Inpainter
from manga_backend.pipeline.errors import InpainterUnavailableError

logger = logging.getLogger(__name__)

DEFAULT_INPAINTER = "flux2_klein_lanpaint"
LAZY_INPAINTERS = frozenset({"flux2_klein_lanpaint", "flux2_klein_q4"})
# An inpainter that needs an external runtime falls back to the bundled one when
# that runtime is not running, so a page still translates. INPAINTER_FALLBACK=off
# restores the strict 503.
INPAINTER_FALLBACKS = {"flux2_klein_lanpaint": "lama_large"}
# How long a failed runtime is left alone before the next request probes it again.
INPAINTER_RETRY_SECONDS = 60.0


class InpaintModel(Protocol):
    async def initialize(self) -> None: ...

    async def inpaint(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray: ...


def inpainter_fallback_disabled() -> bool:
    return os.environ.get("INPAINTER_FALLBACK", "").strip().lower() in ("off", "none", "0", "false")


def inpainter_fallback(inpainter_type: str) -> str | None:
    if inpainter_fallback_disabled():
        return None
    return INPAINTER_FALLBACKS.get(inpainter_type)


def create_inpainter(inpainter_type: str, device: str) -> InpaintModel:
    """Construct (without initializing) the inpainter for an option name."""
    if inpainter_type == "flux2_klein_lanpaint":
        from manga_backend.models.flux_lanpaint_inpainter import FluxLanPaintInpainter

        return FluxLanPaintInpainter(device=device)
    if inpainter_type == "flux2_klein_q4":
        from manga_backend.models.flux_klein_inpainter import FluxKleinInpainter

        return FluxKleinInpainter(device=device)
    return Inpainter(inpainter_type=inpainter_type, device=device)


def dispose_inpainter(instance: object, inpainter_type: str) -> None:
    dispose = getattr(instance, "dispose", None)
    if not callable(dispose):
        return
    try:
        dispose()
    except Exception:
        logger.exception("Failed to dispose inpainter %s", inpainter_type)


class InpainterManager:
    """Owns the active inpainter, its load/fallback policy and execution lock.

    One model loads at a time and only healthy instances are published. The
    shared model_status["inpainter"] entry mirrors what is loaded. Callers hold
    `execution_lock` while inpainting so pages run one at a time.
    """

    def __init__(self, device: str, model_status: dict[str, dict[str, Any]]) -> None:
        self.device = device
        self.instance: InpaintModel | None = None
        self.type = DEFAULT_INPAINTER
        self.retry_at: dict[str, float] = {}
        self.execution_lock = asyncio.Lock()
        self._initialization_lock = asyncio.Lock()
        self._model_status = model_status

    async def get(self, inpainter_type: str) -> InpaintModel:
        """Initialize one inpainting model at a time and publish only healthy instances."""
        async with self._initialization_lock:
            if self.instance is not None and self.type == inpainter_type:
                if self._model_status.get("inpainter", {}).get("status") == "loaded":
                    return self.instance

            previous = (self.instance, self.type)
            status = self._model_status.setdefault("inpainter", {})
            status.update({"status": "loading", "name": inpainter_type})
            candidate = None
            try:
                candidate = create_inpainter(inpainter_type, self.device)
                await candidate.initialize()
            except Exception as error:
                if candidate is not None:
                    dispose_inpainter(candidate, inpainter_type)
                if previous[0] is not None:
                    self.instance, self.type = previous
                    status.update(
                        {
                            "status": "loaded",
                            "name": previous[1],
                            "last_load_error": {
                                "name": inpainter_type,
                                "error": str(error),
                            },
                        }
                    )
                else:
                    status.update(
                        {"status": "error", "name": inpainter_type, "error": str(error)}
                    )
                raise InpainterUnavailableError(
                    f"failed to initialize inpainter {inpainter_type}: {error}",
                    inpainter=inpainter_type,
                ) from error

            self.instance = candidate
            self.type = inpainter_type
            status.update({"status": "loaded", "name": inpainter_type})
            status.pop("error", None)
            if previous[0] is not None and previous[0] is not candidate:
                dispose_inpainter(previous[0], previous[1])
            return candidate

    async def get_or_fallback(self, inpainter_type: str) -> tuple[InpaintModel, str]:
        """Return (inpainter, type actually loaded), falling back when allowed."""
        fallback = inpainter_fallback(inpainter_type)
        if fallback is None:
            return await self.get(inpainter_type), inpainter_type

        if time.monotonic() >= self.retry_at.get(inpainter_type, 0.0):
            try:
                return await self.get(inpainter_type), inpainter_type
            except InpainterUnavailableError as error:
                self._defer_retry(inpainter_type, fallback, error)
        return await self._get_fallback(inpainter_type, fallback), fallback

    def _defer_retry(self, inpainter_type: str, fallback: str, error: Exception) -> None:
        self.retry_at[inpainter_type] = time.monotonic() + INPAINTER_RETRY_SECONDS
        logger.warning(
            "%s is unavailable, using %s instead and retrying in %.0fs: %s",
            inpainter_type,
            fallback,
            INPAINTER_RETRY_SECONDS,
            error,
        )

    async def _get_fallback(self, inpainter_type: str, fallback: str) -> InpaintModel:
        """Load the stand-in for an unavailable runtime.

        If the stand-in cannot load either (LaMa not downloaded yet and the
        machine is offline, say), the error names the requested inpainter so
        the user still gets its setup hint rather than a generic one.
        """
        try:
            return await self.get(fallback)
        except InpainterUnavailableError as error:
            logger.warning(
                "Fallback inpainter %s for %s failed to load: %s",
                fallback,
                inpainter_type,
                error,
            )
            raise InpainterUnavailableError(
                f"{inpainter_type} is unavailable and its fallback {fallback} "
                f"failed to load: {error}",
                inpainter=inpainter_type,
            ) from error

    async def inpaint_page(
        self, inpainter_type: str, image: np.ndarray, mask: np.ndarray
    ) -> tuple[np.ndarray, str]:
        """Inpaint one page; return it with the name of the inpainter that ran.

        The caller holds execution_lock. A runtime that was up when its
        inpainter loaded can stop later (ComfyUI closed or crashed): the
        instance is then dropped so a later request probes the runtime again,
        and this page is inpainted by the fallback, or fails with the
        runtime's 503 hint when fallback is off.
        """
        model, loaded_type = await self.get_or_fallback(inpainter_type)
        try:
            output = await model.inpaint(image, mask)
        except ComfyUIUnreachableError as error:
            model, loaded_type = await self._replace_lost(model, loaded_type, error)
            output = await model.inpaint(image, mask)
        return output, getattr(model, "last_backend_used", loaded_type)

    async def _replace_lost(
        self, instance: InpaintModel, inpainter_type: str, error: Exception
    ) -> tuple[InpaintModel, str]:
        """Drop an inpainter whose runtime went away; return its fallback."""
        async with self._initialization_lock:
            if self.instance is instance:
                self.instance = None
                self._model_status.setdefault("inpainter", {}).update(
                    {"status": "error", "name": inpainter_type, "error": str(error)}
                )
                dispose_inpainter(instance, inpainter_type)

        fallback = inpainter_fallback(inpainter_type)
        if fallback is None:
            raise InpainterUnavailableError(
                f"{inpainter_type} runtime became unreachable: {error}",
                inpainter=inpainter_type,
            ) from error
        self._defer_retry(inpainter_type, fallback, error)
        return await self._get_fallback(inpainter_type, fallback), fallback

    async def warm_up(self) -> None:
        """Load the inpainter a first page will most likely need.

        LaMa is the bundled inpainter and the stand-in for one that needs an
        external runtime, and it takes 10-20 s to load, so startup loads it
        rather than the first translation. A failure here only logs: the first
        request that needs the inpainter tries again and reports the error.
        """
        target = self.type
        if not inpainter_fallback_disabled():
            target = INPAINTER_FALLBACKS.get(target, target)
        if target == "none" or target in LAZY_INPAINTERS or self.instance is not None:
            return
        try:
            model = await self.get(target)
            if isinstance(model, Inpainter):
                await model.warm_up()
            logger.info("Inpainter %s loaded ahead of the first request", target)
        except Exception as error:
            logger.warning("Could not load inpainter %s ahead of time: %s", target, error)

    def dispose(self) -> None:
        if self.instance is not None:
            dispose_inpainter(self.instance, self.type)
            self.instance = None
