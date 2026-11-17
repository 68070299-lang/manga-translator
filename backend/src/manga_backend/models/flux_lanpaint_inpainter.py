"""Backend adapter for the loopback ComfyUI + Flux.2 Klein + LanPaint service."""

from __future__ import annotations

import asyncio
import gc
import logging
import os
import secrets
import socket
from dataclasses import replace
from urllib.parse import urlparse

import aiohttp
import cv2
import numpy as np

from manga_backend.comfy_lanpaint.client import ComfyUIClient, ComfyUIError, ComfyUIUnreachableError
from manga_backend.comfy_lanpaint.crop import CropBudgetExceeded, build_crop_regions, stitch_region
from manga_backend.comfy_lanpaint.workflow import WorkflowSettings

logger = logging.getLogger(__name__)

# Transport failures talking to ComfyUI that mean the daemon is gone or hung:
# refused/reset/dropped connections (aiohttp wraps them in ClientConnectionError,
# which also covers its connect and socket-read timeouts) and the client's own
# request deadline (a bare TimeoutError). A job ComfyUI rejects or fails is a
# plain ComfyUIError and stays a per-page error.
UNREACHABLE_ERRORS = (
    ComfyUIUnreachableError,
    aiohttp.ClientConnectionError,
    ConnectionError,
    TimeoutError,
)

PROFILE_DEFAULTS = {
    "fast": {
        "context": 96,
        "steps": 4,
        "lanpaint_steps": 1,
        "max_crop_pixels": 262_144,
    },
    "balanced": {
        "context": 128,
        "steps": 4,
        "lanpaint_steps": 1,
        "max_crop_pixels": 393_216,
    },
    "quality": {
        "context": 160,
        "steps": 4,
        "lanpaint_steps": 1,
        "max_crop_pixels": 524_288,
    },
}

PREFILL_MODES = ("page", "crops")
# How long to wait for the ComfyUI port to accept a connection.
DAEMON_PROBE_SECONDS = 0.75

DEFAULT_PROMPT = (
    "Delete all visible text within the mask and restore what is behind it. "
    "Match the surrounding contours, lines, patterns, tone, material, and lighting. "
    "Continue interrupted edges through the mask. Do not create text, blank patches, "
    "borders, highlights, or new objects."
)


class FluxLanPaintInpainter:
    """Cropped LanPaint inpainting with an optional local Q4 safety fallback."""

    def __init__(self, device: str = "cuda"):
        self.device = device
        self.profile = os.getenv("COMFY_LANPAINT_PROFILE", "fast").strip().lower()
        if self.profile not in PROFILE_DEFAULTS:
            raise ValueError(
                f"unknown COMFY_LANPAINT_PROFILE {self.profile!r}; "
                f"expected one of {', '.join(PROFILE_DEFAULTS)}"
            )
        defaults = PROFILE_DEFAULTS[self.profile]
        self.server_url = os.getenv("COMFYUI_URL", "http://127.0.0.1:8188")
        self.context = int(
            os.getenv("COMFY_LANPAINT_CONTEXT", str(defaults["context"]))
        )
        self.mask_growth = int(os.getenv("COMFY_LANPAINT_MASK_GROWTH", "2"))
        self.feather = int(os.getenv("COMFY_LANPAINT_FEATHER", "1"))
        self.horizontal_group_gap = int(os.getenv("COMFY_LANPAINT_GROUP_X", "32"))
        self.vertical_group_gap = int(os.getenv("COMFY_LANPAINT_GROUP_Y", "16"))
        self.max_crop_pixels = int(
            os.getenv(
                "COMFY_LANPAINT_MAX_CROP_PIXELS", str(defaults["max_crop_pixels"])
            )
        )
        self.allow_q4_fallback = os.getenv("COMFY_LANPAINT_FALLBACK_Q4", "0") != "0"
        self.adaptive_refinement = (
            os.getenv("COMFY_LANPAINT_ADAPTIVE_REFINEMENT", "0") != "0"
        )
        self.adaptive_prompt_mode = (
            os.getenv("COMFY_LANPAINT_ADAPTIVE_PROMPT_MODE", "0") != "0"
        )
        self.hard_inner_steps = int(os.getenv("COMFY_LANPAINT_HARD_INNER_STEPS", "2"))
        self.hard_mask_ratio = float(
            os.getenv("COMFY_LANPAINT_HARD_MASK_RATIO", "0.25")
        )
        self.hard_mask_pixels = int(
            os.getenv("COMFY_LANPAINT_HARD_MASK_PIXELS", "65536")
        )
        self.prefill_masked_source = os.getenv("COMFY_LANPAINT_PREFILL", "1") != "0"
        self.prefill_radius = float(os.getenv("COMFY_LANPAINT_PREFILL_RADIUS", "5"))
        self.fill_generation_boxes = (
            os.getenv("COMFY_LANPAINT_FILL_GENERATION_BOXES", "0") != "0"
        )
        self.lama_structural_prefill = (
            os.getenv("COMFY_LANPAINT_LAMA_PREFILL", "1") != "0"
        )
        self.prefill_mode = (
            os.getenv("COMFY_LANPAINT_PREFILL_MODE", "page").strip().lower()
        )
        if self.prefill_mode not in PREFILL_MODES:
            raise ValueError(
                f"unknown COMFY_LANPAINT_PREFILL_MODE {self.prefill_mode!r}; "
                f"expected one of {', '.join(PREFILL_MODES)}"
            )
        default_denoise = "0.25" if self.lama_structural_prefill else "1.0"
        self.client = ComfyUIClient(self.server_url)
        self.settings = WorkflowSettings(
            diffusion_model=os.getenv(
                "COMFY_LANPAINT_MODEL", "flux-2-klein-4b-fp8.safetensors"
            ),
            text_encoder=os.getenv(
                "COMFY_LANPAINT_ENCODER", "qwen_3_4b_fp4_flux2.safetensors"
            ),
            vae=os.getenv("COMFY_LANPAINT_VAE", "flux2-vae.safetensors"),
            steps=int(os.getenv("COMFY_LANPAINT_STEPS", str(defaults["steps"]))),
            cfg=float(os.getenv("COMFY_LANPAINT_CFG", "1.0")),
            flux_guidance=float(os.getenv("COMFY_LANPAINT_GUIDANCE", "1.0")),
            lanpaint_steps=int(
                os.getenv("COMFY_LANPAINT_INNER_STEPS", str(defaults["lanpaint_steps"]))
            ),
            clip_device=os.getenv("COMFY_LANPAINT_CLIP_DEVICE", "default"),
            denoise=float(os.getenv("COMFY_LANPAINT_DENOISE", default_denoise)),
            native_flux2_schedule=os.getenv("COMFY_LANPAINT_NATIVE_SCHEDULE", "0")
            != "0",
            prompt_mode=os.getenv("COMFY_LANPAINT_PROMPT_MODE", "Image First"),
        )
        self._fallback = None
        self._initialized = False
        self._inference_lock = asyncio.Lock()
        self.last_backend_used = "flux2_klein_lanpaint"
        self.last_structural_prefill_used = False

    async def initialize(self):
        if self._initialized:
            return
        try:
            if not await self._daemon_is_listening():
                raise ComfyUIError("nothing is listening on its port")
            await self.client.validate(settings=self.settings)
            logger.info("ComfyUI LanPaint service ready at %s", self.server_url)
        except (ComfyUIError, aiohttp.ClientError, TimeoutError) as error:
            if not self.allow_q4_fallback:
                raise RuntimeError(
                    f"ComfyUI LanPaint is unavailable at {self.server_url}: {error}"
                ) from error
            logger.warning(
                "ComfyUI LanPaint is not ready yet; Q4 fallback will be available: %s",
                error,
            )
            await self._initialize_fallback()
        self._initialized = True

    async def _daemon_is_listening(self, timeout: float = DAEMON_PROBE_SECONDS) -> bool:
        """Cheap check that something accepts connections at the ComfyUI address.

        A refused loopback connection takes Windows about two seconds to report,
        and the HTTP client waits for it. This bounded probe keeps a page from
        paying that each time the daemon is retried while it is not running.

        Every resolved address is tried at once: `localhost` resolves to ::1
        before 127.0.0.1 on Windows, and trying them in turn would spend the
        whole bound on the refused IPv6 attempt.
        """
        parsed = urlparse(self.server_url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        loop = asyncio.get_running_loop()
        try:
            infos = await asyncio.wait_for(
                loop.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM),
                timeout,
            )
        except (OSError, asyncio.TimeoutError):
            return False
        addresses = list(dict.fromkeys(info[4][:2] for info in infos))
        attempts = [
            asyncio.ensure_future(self._accepts_connection(host, address_port))
            for host, address_port in addresses
        ]
        try:
            for attempt in asyncio.as_completed(attempts, timeout=timeout):
                if await attempt:
                    return True
        except asyncio.TimeoutError:
            pass
        finally:
            for attempt in attempts:
                attempt.cancel()
            await asyncio.gather(*attempts, return_exceptions=True)
        return False

    @staticmethod
    async def _accepts_connection(host: str, port: int) -> bool:
        try:
            _reader, writer = await asyncio.open_connection(host, port)
        except OSError:
            return False
        writer.close()
        return True

    async def _initialize_fallback(self):
        if self._fallback is None:
            from manga_backend.models.flux_klein_inpainter import FluxKleinInpainter

            self._fallback = FluxKleinInpainter(device=self.device)
            await self._fallback.initialize()

    async def inpaint(
        self,
        image: np.ndarray,
        mask: np.ndarray,
        prompt: str = DEFAULT_PROMPT,
        **kwargs,
    ) -> np.ndarray:
        if not self._initialized:
            raise RuntimeError("FluxLanPaintInpainter is not initialized")
        if not np.any(mask >= 128):
            self.last_backend_used = "flux2_klein_lanpaint"
            return image.copy()

        async with self._inference_lock:
            structural_source, structural_prefill_used = await self._structure_prefill(
                image, mask
            )
            self.last_structural_prefill_used = structural_prefill_used
            try:
                regions = build_crop_regions(
                    structural_source,
                    mask,
                    context=self.context,
                    mask_growth=self.mask_growth,
                    horizontal_group_gap=self.horizontal_group_gap,
                    vertical_group_gap=self.vertical_group_gap,
                    max_crop_pixels=self.max_crop_pixels,
                    fill_generation_boxes=self.fill_generation_boxes,
                )
            except CropBudgetExceeded as error:
                # A masked component larger than the crop budget is a server-side
                # capacity limit, not a bad request: keep the page translatable.
                logger.warning(
                    "Skipping LanPaint generation for this page: %s; "
                    "returning the %s result",
                    error,
                    "LaMa structural" if structural_prefill_used else "original",
                )
                self.last_backend_used = (
                    "lama_large" if structural_prefill_used else "none"
                )
                return structural_source.copy()
            if not regions:
                self.last_backend_used = "flux2_klein_lanpaint"
                return image.copy()

            result = image.copy()
            first_seed = int(kwargs.get("seed", secrets.randbits(63)))
            try:
                async with self.client.session() as session:
                    for index, region in enumerate(regions):
                        settings = self._settings_for_region(region.generation_mask)
                        if structural_prefill_used:
                            source = region.image
                        else:
                            source = self._prefill_source(
                                region.image, region.generation_mask
                            )
                            if self.lama_structural_prefill:
                                settings = replace(settings, denoise=1.0)
                        generated = await self.client.inpaint_crop(
                            source,
                            region.generation_mask,
                            prompt,
                            seed=first_seed + index,
                            settings=settings,
                            session=session,
                        )
                        result = stitch_region(
                            result,
                            generated,
                            region,
                            feather=self.feather,
                            blend_base=source,
                            adaptive_feather=True,
                        )
                self.last_backend_used = "flux2_klein_lanpaint"
                return result
            except (ComfyUIError, aiohttp.ClientError, *UNREACHABLE_ERRORS) as error:
                if not self.allow_q4_fallback:
                    if isinstance(error, UNREACHABLE_ERRORS) and not isinstance(
                        error, ComfyUIUnreachableError
                    ):
                        # One type for "the daemon is gone" lets the pipeline
                        # drop this instance and fall back without knowing aiohttp.
                        raise ComfyUIUnreachableError(
                            f"ComfyUI at {self.server_url} is unreachable: "
                            f"{type(error).__name__}: {error}"
                        ) from error
                    raise
                logger.exception(
                    "LanPaint failed; using the local Flux.2 Klein Q4 fallback"
                )
                await self._initialize_fallback()
                fallback_result = await self._fallback.inpaint(
                    structural_source, mask, prompt=prompt, **kwargs
                )
                self.last_backend_used = "flux2_klein_q4"
                return fallback_result

    def _create_structure_inpainter(self):
        from manga_backend.models.inpainter import Inpainter

        return Inpainter(inpainter_type="lama_large", device=self.device)

    async def _structure_prefill(
        self, image: np.ndarray, mask: np.ndarray
    ) -> tuple[np.ndarray, bool]:
        if not self.lama_structural_prefill:
            return image, False

        inpainter = self._create_structure_inpainter()
        try:
            await inpainter.initialize()
            structural_mask = np.where(mask >= 128, 255, 0).astype(np.uint8)
            if structural_mask.ndim == 3:
                structural_mask = np.max(structural_mask, axis=2)
            if self.mask_growth > 0:
                size = (self.mask_growth * 2) + 1
                structural_mask = cv2.dilate(
                    structural_mask,
                    np.ones((size, size), dtype=np.uint8),
                    iterations=1,
                )
            if self.prefill_mode == "crops":
                prefilled = await self._crop_structure_prefill(
                    inpainter, image, structural_mask
                )
            else:
                prefilled = await inpainter.inpaint(image, structural_mask)
            return prefilled, True
        except Exception as error:
            logger.warning(
                "LaMa structural prefill failed; using local Telea prefill: %s",
                error,
            )
            return image, False
        finally:
            inpainter.dispose()
            gc.collect()

    async def _crop_structure_prefill(
        self, inpainter, image: np.ndarray, structural_mask: np.ndarray
    ) -> np.ndarray:
        """Run LaMa on each padded crop (context included) and stitch it back."""

        regions = build_crop_regions(
            image,
            structural_mask,
            context=self.context,
            mask_growth=0,
            horizontal_group_gap=self.horizontal_group_gap,
            vertical_group_gap=self.vertical_group_gap,
            max_crop_pixels=self.max_crop_pixels,
        )
        result = image.copy()
        for region in regions:
            x0, y0, x1, y1 = region.box
            left, top, right, bottom = region.padding
            crop_mask = np.pad(
                structural_mask[y0:y1, x0:x1],
                ((top, bottom), (left, right)),
                mode="constant",
            )
            filled = await inpainter.inpaint(region.image, crop_mask)
            filled = filled[top : top + (y1 - y0), left : left + (x1 - x0)]
            commit = region.commit_mask > 0
            result[y0:y1, x0:x1][commit] = filled[commit]
        return result

    def _settings_for_region(self, generation_mask: np.ndarray) -> WorkflowSettings:
        masked_pixels = int(np.count_nonzero(generation_mask))
        mask_ratio = masked_pixels / generation_mask.size
        is_hard = (
            masked_pixels >= self.hard_mask_pixels or mask_ratio >= self.hard_mask_ratio
        )
        settings = self.settings
        if is_hard and self.adaptive_prompt_mode:
            settings = replace(settings, prompt_mode="Prompt First")
        if (
            is_hard
            and self.adaptive_refinement
            and settings.lanpaint_steps < self.hard_inner_steps
        ):
            settings = replace(settings, lanpaint_steps=self.hard_inner_steps)
        return settings

    def _prefill_source(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        if not self.prefill_masked_source or self.prefill_radius <= 0:
            return image
        binary_mask = np.where(mask >= 128, 255, 0).astype(np.uint8)
        if not np.any(binary_mask):
            return image
        return cv2.inpaint(image, binary_mask, self.prefill_radius, cv2.INPAINT_TELEA)

    def dispose(self):
        if self._fallback is not None:
            self._fallback.dispose()
        self._initialized = False
