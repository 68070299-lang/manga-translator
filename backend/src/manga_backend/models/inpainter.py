"""
Inpainting using LaMa ONNX model.

Based on comic-translate's inpainting implementation.
"""

import asyncio
import functools
import logging
import os
import subprocess
from typing import List, Optional, Tuple

import numpy as np

from manga_backend.utils.device import get_onnx_providers, create_onnx_session
from manga_backend.utils.download import download_lama_onnx
from manga_backend.paths import MODELS_CACHE_DIR

logger = logging.getLogger(__name__)

MODELS_DIR = str(MODELS_CACHE_DIR)

# LaMa's activations need roughly 2.2-2.8 GiB of GPU memory per megapixel. A page
# that does not fit spills into shared system memory and gets several times
# slower, so larger pages are split into equal tiles that each fit. Equal sizes
# matter: every change of input shape costs the session most of a second.
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

Span = Tuple[int, int]


@functools.lru_cache(maxsize=1)
def _gpu_total_gib() -> Optional[float]:
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


def resolve_max_pixels(device: str) -> Optional[int]:
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


def _equal_spans(length: int, count: int, multiple: int) -> List[Span]:
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
    height: int, width: int, max_pixels: Optional[int], multiple: int = 8
) -> Tuple[List[Span], List[Span]]:
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


def _owned_spans(spans: List[Span]) -> List[Span]:
    """Give each pixel to one span by cutting every overlap at its middle."""
    cuts = [0]
    for (_start, end), (next_start, _next_end) in zip(spans, spans[1:]):
        cuts.append((next_start + end) // 2)
    cuts.append(spans[-1][1])
    return list(zip(cuts, cuts[1:]))


class Inpainter:
    """LaMa inpainting using ONNX runtime."""

    def __init__(self, inpainter_type: str = "lama", device: str = "cpu"):
        self.inpainter_type = inpainter_type
        self.device = device
        self.session = None
        self.pad_mod = 8
        self.max_pixels: Optional[int] = None
        self._release_options = None
        self.model_dir = os.path.join(MODELS_DIR, "inpainting")
        self._initialized = False
        self._inference_lock = asyncio.Lock()

    async def initialize(self):
        """Download and load the inpainting model."""
        if self._initialized:
            return

        os.makedirs(self.model_dir, exist_ok=True)

        if self.inpainter_type == "none":
            logger.info("Inpainting disabled")
            self._initialized = True
            return

        model_path = os.path.join(self.model_dir, "lama-manga-dynamic.onnx")
        if os.path.exists(model_path):
            logger.info(f"Loading cached {self.inpainter_type} inpainting model...")
        else:
            logger.info(f"Downloading {self.inpainter_type} inpainting model (this may take a while)...")

        model_path = await download_lama_onnx(self.model_dir)

        providers = get_onnx_providers(self.device)
        logger.info(f"Inpainting session providers: {providers}")

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

    def _arena_release_options(self):
        """Run options that hand the GPU arena's free memory back after a run.

        ONNX Runtime keeps every block it ever allocated. A page of a new size
        cannot reuse the blocks of the previous size, so without this the arena
        grows with each size until the GPU is full and everything slows down.
        """
        get_providers = getattr(self.session, "get_providers", None)
        if not callable(get_providers) or get_providers()[:1] != ["CUDAExecutionProvider"]:
            return None
        import onnxruntime as ort

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
        orig_h, orig_w = image.shape[:2]
        pad_h = (self.pad_mod - orig_h % self.pad_mod) % self.pad_mod
        pad_w = (self.pad_mod - orig_w % self.pad_mod) % self.pad_mod
        padded, padded_mask = image, binary
        if pad_h > 0 or pad_w > 0:
            pad_mode = 'reflect' if orig_h > 1 and orig_w > 1 else 'edge'
            padded = np.pad(image, ((0, pad_h), (0, pad_w), (0, 0)), mode=pad_mode)
            padded_mask = np.pad(binary, ((0, pad_h), (0, pad_w)), mode='constant', constant_values=0)

        row_spans, col_spans = plan_tiles(
            padded.shape[0], padded.shape[1], self.max_pixels, self.pad_mod
        )
        # Tiles the model must see, each with the region whose pixels it owns.
        jobs = []
        for (y0, y1), (own_y0, own_y1) in zip(row_spans, _owned_spans(row_spans)):
            for (x0, x1), (own_x0, own_x1) in zip(col_spans, _owned_spans(col_spans)):
                owned = (own_y0, min(own_y1, orig_h), own_x0, min(own_x1, orig_w))
                if binary[owned[0]:owned[1], owned[2]:owned[3]].any():
                    jobs.append(((y0, y1, x0, x1), owned))

        result = image.copy()
        for index, ((y0, y1, x0, x1), (own_y0, own_y1, own_x0, own_x1)) in enumerate(jobs):
            # Tiles of one page share a shape and reuse the arena; only the last
            # run of the page gives the memory back.
            filled = self._run_model(
                padded[y0:y1, x0:x1],
                padded_mask[y0:y1, x0:x1],
                release_memory=index == len(jobs) - 1,
            )
            masked = binary[own_y0:own_y1, own_x0:own_x1] > 0
            result[own_y0:own_y1, own_x0:own_x1][masked] = filled[
                own_y0 - y0 : own_y1 - y0, own_x0 - x0 : own_x1 - x0
            ][masked]
        return result

    def _run_model(
        self, image: np.ndarray, mask: np.ndarray, release_memory: bool = False
    ) -> np.ndarray:
        """Run LaMa on one array whose sides are multiples of the model stride."""
        session_inputs = self.session.get_inputs()
        inputs = {
            session_inputs[0].name: (image.astype(np.float32) / 255.0).transpose(2, 0, 1)[np.newaxis, ...],
            session_inputs[1].name: self._normalize_mask(mask)[np.newaxis, ...]
        }
        if release_memory and self._release_options is not None:
            output = self.session.run(None, inputs, self._release_options)[0]
        else:
            output = self.session.run(None, inputs)[0]

        # (C, H, W) -> (H, W, C)
        return np.clip(output[0].transpose(1, 2, 0) * 255, 0, 255).astype(np.uint8)

    def _normalize_mask(self, mask: np.ndarray) -> np.ndarray:
        """Binarize a mask into the model's (1, H, W) float input."""
        return (mask > 0).astype(np.float32)[np.newaxis, ...]

    def dispose(self):
        self.session = None
        self._initialized = False
