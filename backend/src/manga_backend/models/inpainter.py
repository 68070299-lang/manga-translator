"""Inpainting using LaMa ONNX model.

Based on comic-translate's inpainting implementation.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import subprocess
from collections.abc import Callable
from typing import cast

import cv2
import numpy as np
import onnxruntime as ort

from manga_backend.comfy_lanpaint.crop import CropRegion, build_crop_regions, stitch_region
from manga_backend.paths import MODELS_CACHE_DIR
from manga_backend.utils.device import create_onnx_session, get_onnx_providers
from manga_backend.utils.download import download_lama_onnx

logger = logging.getLogger(__name__)

# LaMa's activations need roughly 2.2-2.8 GiB of GPU memory per megapixel. A
# crop that does not fit spills into shared system memory and gets several
# times slower, so oversized crops are split into equal tiles that each fit.
# Equal sizes matter: every change of input shape costs the session most of a
# second, which is also why equal-shaped crops share one batched run.
GPU_GIB_PER_MEGAPIXEL = 3.0
# Left for the detector and OCR sessions, the LaMa weights and the desktop.
GPU_RESERVED_GIB = 2.5
# Used when the GPU's memory size cannot be read.
DEFAULT_GPU_MAX_PIXELS = 1_000_000
MIN_GPU_MAX_PIXELS = 400_000
# Neighbouring tiles share this many pixels, so text near a tile edge still has
# real surroundings on both sides.
TILE_OVERLAP = 192
# An axis is not split further once its tiles are this small.
MIN_TILE_SIDE = 2 * TILE_OVERLAP + 64
# Context added around each masked component group before LaMa runs: about
# twice the glyph size, but never less than this.
MIN_CROP_CONTEXT = 64
# Rectangular block masks are component-sized, so an uncapped "glyph" would
# balloon their crops to page scale; the cap keeps the margin reasonable.
MAX_GLYPH_EXTENT = 128

Span = tuple[int, int]


@functools.lru_cache(maxsize=1)
def _gpu_total_gib() -> float | None:
    """Total memory of the first NVIDIA GPU, or None when it cannot be read.

    nvidia-smi takes about a tenth of a second and LanPaint builds a new LaMa
    for every page, so the answer (a failure included) is kept for the life of
    the process.
    """
    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        return float(output.strip().splitlines()[0]) / 1024.0
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def resolve_max_pixels(device: str) -> int | None:
    """Largest array LaMa should see in one pass; None means no limit.

    LAMA_MAX_PIXELS overrides it (0 removes the limit). The CPU has no limit.
    """
    raw = os.getenv("LAMA_MAX_PIXELS", "").strip()
    if raw:
        try:
            configured = int(raw)
        except ValueError:
            configured = -1
        if configured == 0:
            return None
        if configured > 0:
            return configured
        logger.warning("Ignoring invalid LAMA_MAX_PIXELS=%r", raw)

    if device == "cpu":
        return None
    total_gib = _gpu_total_gib()
    if total_gib is None:
        return DEFAULT_GPU_MAX_PIXELS
    usable = (total_gib - GPU_RESERVED_GIB) / GPU_GIB_PER_MEGAPIXEL
    return max(MIN_GPU_MAX_PIXELS, int(usable * 1_000_000))


def _equal_spans(length: int, count: int, multiple: int) -> list[Span]:
    """Split a length into `count` equal spans that overlap by TILE_OVERLAP."""
    if count <= 1:
        return [(0, length)]
    size = -(-(length + (count - 1) * TILE_OVERLAP) // count)
    size = min(length, -(-size // multiple) * multiple)
    return [
        (start, start + size)
        for start in (
            round(index * (length - size) / (count - 1)) for index in range(count)
        )
    ]


def plan_tiles(
    height: int, width: int, max_pixels: int | None, multiple: int = 8
) -> tuple[list[Span], list[Span]]:
    """Return row and column spans of equal tiles that each fit `max_pixels`.

    Height and width must already be multiples of `multiple`. One span per axis
    means a single full-page pass.
    """
    rows, cols = 1, 1
    while max_pixels is not None:
        row_spans = _equal_spans(height, rows, multiple)
        col_spans = _equal_spans(width, cols, multiple)
        tile_h = row_spans[0][1] - row_spans[0][0]
        tile_w = col_spans[0][1] - col_spans[0][0]
        if tile_h * tile_w <= max_pixels:
            break
        can_split_rows = tile_h > MIN_TILE_SIDE
        can_split_cols = tile_w > MIN_TILE_SIDE
        if can_split_rows and (tile_h >= tile_w or not can_split_cols):
            rows += 1
        elif can_split_cols:
            cols += 1
        else:
            break
    return _equal_spans(height, rows, multiple), _equal_spans(width, cols, multiple)


def _owned_spans(spans: list[Span]) -> list[Span]:
    """Give each pixel to one span by cutting every overlap at its middle."""
    cuts = [0]
    for (_start, end), (next_start, _next_end) in zip(spans, spans[1:]):
        cuts.append((next_start + end) // 2)
    cuts.append(spans[-1][1])
    return list(zip(cuts, cuts[1:]))


def crop_context(binary: np.ndarray) -> int:
    """Context margin around a mask group: about 2x the glyph size, min 64 px.

    The glyph size is the median extent of the mask's connected components,
    capped so a rectangular block mask (one page-scale component) still gets a
    bounded margin instead of a crop the size of the page.
    """
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
        (binary > 0).astype(np.uint8), connectivity=8
    )
    extents = [
        max(int(stats[label, cv2.CC_STAT_WIDTH]), int(stats[label, cv2.CC_STAT_HEIGHT]))
        for label in range(1, count)
        if int(stats[label, cv2.CC_STAT_AREA]) >= 2
    ]
    if not extents:
        return MIN_CROP_CONTEXT
    glyph_size = min(int(np.median(extents)), MAX_GLYPH_EXTENT)
    return max(MIN_CROP_CONTEXT, 2 * glyph_size)


class Inpainter:
    """LaMa inpainting using ONNX runtime."""

    def __init__(self, inpainter_type: str = "lama", device: str = "cpu") -> None:
        self.inpainter_type = inpainter_type
        self.device = device
        self.session: ort.InferenceSession | None = None
        self.pad_mod = 8
        self.max_pixels: int | None = None
        self._release_options: ort.RunOptions | None = None
        self.model_dir = MODELS_CACHE_DIR / "inpainting"
        self._initialized = False
        self._inference_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Download and load the inpainting model."""
        if self._initialized:
            return

        self.model_dir.mkdir(parents=True, exist_ok=True)

        if self.inpainter_type == "none":
            logger.info("Inpainting disabled")
            self._initialized = True
            return

        if (self.model_dir / "lama-manga-dynamic.onnx").exists():
            logger.info("Loading cached %s inpainting model...", self.inpainter_type)
        else:
            logger.info(
                "Downloading %s inpainting model (this may take a while)...",
                self.inpainter_type,
            )

        model_path = await download_lama_onnx(self.model_dir)

        providers = get_onnx_providers(self.device)
        logger.info("Inpainting session providers: %s", providers)

        self.session = await asyncio.to_thread(
            create_onnx_session, model_path, providers
        )
        self.max_pixels = await asyncio.to_thread(resolve_max_pixels, self.device)
        self._release_options = self._arena_release_options()
        self._initialized = True
        logger.info(
            "Inpainting model loaded successfully (max pixels per pass: %s)",
            self.max_pixels or "unlimited",
        )

    def _arena_release_options(self) -> ort.RunOptions | None:
        """Run options that hand the GPU arena's free memory back after a run.

        ONNX Runtime keeps every block it ever allocated. A page of a new size
        cannot reuse the blocks of the previous size, so without this the arena
        grows with each size until the GPU is full and everything slows down.
        """
        get_providers = getattr(self.session, "get_providers", None)
        if not callable(get_providers) or cast(list[str], get_providers())[:1] != [
            "CUDAExecutionProvider"
        ]:
            return None

        options = ort.RunOptions()
        options.add_run_config_entry("memory.enable_memory_arena_shrinkage", "gpu:0")
        return options

    async def inpaint(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """Inpaint an RGB image where the (H, W) mask is set; other pixels are kept."""
        # Inpainting is disabled.
        if self.session is None:
            return image

        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("Image must be an RGB array with shape (H, W, 3)")

        orig_h, orig_w = image.shape[:2]
        if orig_h <= 0 or orig_w <= 0:
            raise ValueError("Image dimensions must be positive")

        if mask.ndim == 3:
            mask = mask[:, :, 0]
        if mask.shape[:2] != (orig_h, orig_w):
            raise ValueError("Mask dimensions must match image dimensions")

        binary = np.where(mask > 0, 255, 0).astype(np.uint8)
        if not binary.any():
            return image.copy()

        async with self._inference_lock:
            return await asyncio.to_thread(self._inpaint_blocking, image, binary)

    def _inpaint_blocking(self, image: np.ndarray, binary: np.ndarray) -> np.ndarray:
        """Inpaint per contextual crop; paste back only masked pixels.

        LaMa sees the smallest aligned crops that give every masked component
        at least ``crop_context`` pixels of real surroundings (the model was
        trained on crops), so a page with a few small blocks no longer pays
        for the whole page. Crops of equal shape share one batched session
        run, and a crop too large for the device limit falls back to tiles.
        """
        regions = build_crop_regions(
            image,
            binary,
            context=crop_context(binary),
            multiple=self.pad_mod,
            mask_growth=0,
            max_crop_pixels=None,
        )
        if not regions:
            return image.copy()

        oversized: list[CropRegion] = []
        batches: dict[tuple[int, int], list[CropRegion]] = {}
        for region in regions:
            height, width = region.image.shape[:2]
            if self.max_pixels is not None and height * width > self.max_pixels:
                oversized.append(region)
            else:
                batches.setdefault((height, width), []).append(region)

        # One batched run per crop shape (and per device pixel budget).
        runs: list[tuple[np.ndarray, np.ndarray, list[CropRegion]]] = []
        for (height, width), group in batches.items():
            per_run = (
                len(group)
                if self.max_pixels is None
                else max(1, self.max_pixels // (height * width))
            )
            for start in range(0, len(group), per_run):
                chunk = group[start : start + per_run]
                runs.append(
                    (
                        np.stack([region.image for region in chunk]),
                        np.stack([region.generation_mask for region in chunk]),
                        chunk,
                    )
                )
        tiled_jobs = [
            (region, self._tile_jobs(region.image, region.generation_mask))
            for region in oversized
        ]
        remaining = len(runs) + sum(len(jobs) for _region, jobs in tiled_jobs)

        def take_run(images: np.ndarray, masks: np.ndarray) -> np.ndarray:
            nonlocal remaining
            remaining -= 1
            return self._run_model(images, masks, release_memory=remaining == 0)

        result = image.copy()
        for images, masks, chunk in runs:
            filled = take_run(images, masks)
            for index, region in enumerate(chunk):
                result = stitch_region(result, filled[index], region, feather=0)
        for region, jobs in tiled_jobs:
            generated = self._inpaint_tiled_region(region, jobs, take_run)
            result = stitch_region(result, generated, region, feather=0)
        return result

    def _tile_jobs(
        self, crop_image: np.ndarray, crop_mask: np.ndarray
    ) -> list[tuple[tuple[int, int, int, int], tuple[int, int, int, int]]]:
        """Tiles (and their owned spans) of one oversized aligned crop."""
        row_spans, col_spans = plan_tiles(
            crop_image.shape[0], crop_image.shape[1], self.max_pixels, self.pad_mod
        )
        jobs: list[tuple[tuple[int, int, int, int], tuple[int, int, int, int]]] = []
        for (y0, y1), (own_y0, own_y1) in zip(row_spans, _owned_spans(row_spans)):
            for (x0, x1), (own_x0, own_x1) in zip(col_spans, _owned_spans(col_spans)):
                if crop_mask[own_y0:own_y1, own_x0:own_x1].any():
                    jobs.append(((y0, y1, x0, x1), (own_y0, own_y1, own_x0, own_x1)))
        return jobs

    def _inpaint_tiled_region(
        self,
        region: CropRegion,
        jobs: list[tuple[tuple[int, int, int, int], tuple[int, int, int, int]]],
        take_run: Callable[[np.ndarray, np.ndarray], np.ndarray],
    ) -> np.ndarray:
        """Run the model tile by tile over one oversized crop."""
        crop_image = region.image
        crop_mask = region.generation_mask
        out = crop_image.copy()
        for (y0, y1, x0, x1), (own_y0, own_y1, own_x0, own_x1) in jobs:
            filled = take_run(
                crop_image[y0:y1, x0:x1][np.newaxis, ...],
                crop_mask[y0:y1, x0:x1][np.newaxis, ...],
            )[0]
            masked = crop_mask[own_y0:own_y1, own_x0:own_x1] > 0
            out[own_y0:own_y1, own_x0:own_x1][masked] = filled[
                own_y0 - y0 : own_y1 - y0, own_x0 - x0 : own_x1 - x0
            ][masked]
        return out

    def _run_model(
        self,
        images: np.ndarray,
        masks: np.ndarray,
        release_memory: bool = False,
    ) -> np.ndarray:
        """Run LaMa on a batch of equal-shaped arrays with stride-multiple sides.

        ``images`` is (B, H, W, 3) uint8 and ``masks`` is (B, H, W); the
        return value is (B, H, W, 3) uint8.
        """
        session = self.session
        if session is None:
            raise RuntimeError("Inpainting model not initialized")
        session_inputs = session.get_inputs()
        inputs = {
            session_inputs[0].name: (images.astype(np.float32) / 255.0).transpose(
                0, 3, 1, 2
            ),
            session_inputs[1].name: (masks > 0).astype(np.float32)[:, np.newaxis, ...],
        }
        if release_memory and self._release_options is not None:
            output = np.asarray(session.run(None, inputs, self._release_options)[0])
        else:
            output = np.asarray(session.run(None, inputs)[0])

        # (B, C, H, W) -> (B, H, W, C)
        return np.clip(output.transpose(0, 2, 3, 1) * 255, 0, 255).astype(np.uint8)

    def _normalize_mask(self, mask: np.ndarray) -> np.ndarray:
        """Binarize a mask into the model's (1, H, W) float input."""
        return (mask > 0).astype(np.float32)[np.newaxis, ...]

    def dispose(self) -> None:
        self.session = None
        self._initialized = False
