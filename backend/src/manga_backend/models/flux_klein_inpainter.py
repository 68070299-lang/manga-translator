"""
Optional Flux.2 Klein Q4_K_M GGUF inpainter driven by a local sd-cli (stable-diffusion.cpp).

Used as the local fallback for the ComfyUI LanPaint path and selectable as the
`flux2_klein_q4` inpainter. White mask = regenerate, black = preserve.

Configuration (all optional; set in backend/.env):
  SD_CPP_CLI             path to sd-cli (default: <models_cache>/flux2_klein_q4/sd-cli.exe)
  SD_CPP_MODELS_DIR      directory holding the Qwen3 LLM and Flux.2 VAE files
                         (default: <models_cache>/flux2_klein_q4)
  SD_CPP_TIMEOUT_SECONDS sd-cli run timeout in seconds (default: 120)
The diffusion GGUF itself is read from <models_cache>/flux2_klein_q4.
"""

import asyncio
import logging
import os
import subprocess
import tempfile
import numpy as np
from PIL import Image, ImageFilter
from manga_backend.paths import MODELS_CACHE_DIR

logger = logging.getLogger(__name__)

MODELS_DIR = str(MODELS_CACHE_DIR)
FLUX_DIR = os.path.join(MODELS_DIR, "flux2_klein_q4")
GGUF_NAME = "flux-2-klein-4b-Q4_K_M.gguf"

DEFAULT_SD_TIMEOUT_SECONDS = 120.0
LLM_NAME = "qwen3-4b-Q4_K_M.gguf"
VAE_NAME = "flux2-vae.safetensors"


def _sd_cli_path() -> str:
    return os.getenv("SD_CPP_CLI", "").strip() or os.path.join(FLUX_DIR, "sd-cli.exe")


def _sd_models_dir() -> str:
    return os.getenv("SD_CPP_MODELS_DIR", "").strip() or FLUX_DIR


def _sd_timeout_seconds() -> float:
    raw = os.getenv("SD_CPP_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_SD_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if value <= 0:
        logger.warning(
            "Ignoring invalid SD_CPP_TIMEOUT_SECONDS; using %ss",
            DEFAULT_SD_TIMEOUT_SECONDS,
        )
        return DEFAULT_SD_TIMEOUT_SECONDS
    return value


MODEL_ID = "black-forest-labs/FLUX.2-klein-4B (Q4_K_M GGUF applied)"


class FluxKleinInpainter:
    """Q4_K_M GGUF inpaint through a local sd-cli process."""

    def __init__(self, device: str = "cuda"):
        self.device = device
        self._initialized = False
        self._inference_lock = asyncio.Lock()

    def blur(self, mask, blur_factor=8):
        try:
            if blur_factor > 0:
                # blur_factor=8 ~ radius ~8
                return mask.filter(ImageFilter.GaussianBlur(radius=blur_factor))
        except Exception:
            pass
        return mask

    async def initialize(self):
        if self._initialized:
            return
        gguf_path = os.path.join(FLUX_DIR, GGUF_NAME)
        sd_cli = _sd_cli_path()
        llm_path = os.path.join(_sd_models_dir(), LLM_NAME)
        vae_path = os.path.join(_sd_models_dir(), VAE_NAME)
        if not os.path.exists(sd_cli):
            raise RuntimeError(
                "sd-cli not found; set SD_CPP_CLI (flux2_klein_q4 is an optional inpainter)"
            )
        if not os.path.exists(gguf_path):
            raise RuntimeError(
                f"Q4 GGUF {GGUF_NAME} missing from the backend models_cache/flux2_klein_q4 "
                "directory (flux2_klein_q4 is an optional inpainter)"
            )
        if not os.path.exists(llm_path):
            raise RuntimeError(
                f"LLM {LLM_NAME} not found; set SD_CPP_MODELS_DIR "
                "(flux2_klein_q4 is an optional inpainter)"
            )
        if not os.path.exists(vae_path):
            raise RuntimeError(
                f"VAE {VAE_NAME} not found; set SD_CPP_MODELS_DIR "
                "(flux2_klein_q4 is an optional inpainter)"
            )
        self._initialized = True
        logger.info(
            "Flux2Klein Q4_K_M ready: GGUF %s (%d bytes), LLM %s, VAE %s",
            GGUF_NAME,
            os.path.getsize(gguf_path),
            LLM_NAME,
            VAE_NAME,
        )

    async def inpaint(self, image: np.ndarray, mask: np.ndarray, prompt: str = "plain manga paper background, clean empty background matching surrounding texture", **kwargs) -> np.ndarray:
        """
        Inpaint the white mask area of an RGB image with sd-cli.
        Optional kwargs: num_inference_steps, guidance_scale, strength, prompt
        (mapped to sd-cli --steps/--cfg-scale/--strength/--prompt); others are ignored.
        """
        if not self._initialized:
            raise RuntimeError("not initialized")
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("RGB only")
        if mask.ndim == 3:
            mask = np.max(mask, axis=2)
        if mask.ndim != 2 or mask.shape != image.shape[:2]:
            raise ValueError("mask shape must match image height and width")
        commit_mask = mask >= 128
        if not np.any(commit_mask):
            return image.copy()
        orig_h, orig_w = image.shape[:2]

        # Extract pipeline-style params
        num_inference_steps = int(kwargs.get("num_inference_steps", 4))
        guidance_scale = float(kwargs.get("guidance_scale", 1.0))
        strength = float(kwargs.get("strength", 1.0))
        # prompt override if passed
        if "prompt" in kwargs and kwargs["prompt"]:
            prompt = kwargs["prompt"]

        async with self._inference_lock:
            def _run():
                import cv2

                # Prepare mask: blur like pipeline + padding crop handling
                mask_pil = Image.fromarray((mask > 127).astype(np.uint8) * 255).convert("L")
                if mask_pil.size != (orig_w, orig_h):
                    mask_pil = mask_pil.resize((orig_w, orig_h), Image.Resampling.NEAREST)
                # blur factor 8 as in snippet
                try:
                    mask_pil = self.blur(mask_pil, blur_factor=8)
                except Exception:
                    pass
                mask_np = np.array(mask_pil)
                # Dilate slightly for sd-cli (inpaint needs solid white)
                kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
                mask_dilated = cv2.dilate((mask_np > 127).astype(np.uint8) * 255, kernel, iterations=1)

                with tempfile.TemporaryDirectory() as td:
                    init_path = os.path.join(td, "init.png")
                    mask_path = os.path.join(td, "mask.png")
                    out_path = os.path.join(td, "out.png")
                    Image.fromarray(image).save(init_path)
                    Image.fromarray(mask_dilated).save(mask_path)
                    gguf_path = os.path.join(FLUX_DIR, GGUF_NAME)
                    models_dir = _sd_models_dir()
                    # Map pipeline params to sd-cli
                    # strength 1.0 -> sd strength 0.85-1.0, guidance ignored for distilled but pass cfg
                    sd_strength = max(0.4, min(1.0, strength * 0.85 + 0.15))  # 1.0 -> 1.0, 0.5 -> 0.575
                    cmd = [
                        _sd_cli_path(),
                        "--diffusion-model", gguf_path,
                        "--llm", os.path.join(models_dir, LLM_NAME),
                        "--vae", os.path.join(models_dir, VAE_NAME),
                        "--init-img", init_path,
                        "--mask", mask_path,
                        "--prompt", prompt,
                        "--strength", str(sd_strength),
                        "--steps", str(num_inference_steps),
                        "--cfg-scale", str(guidance_scale),
                        "--diffusion-fa",
                        "--offload-to-cpu",
                        "-o", out_path,
                        "-v",
                    ]
                    timeout = _sd_timeout_seconds()
                    try:
                        result = subprocess.run(
                            cmd, capture_output=True, text=True, timeout=timeout
                        )
                    except subprocess.TimeoutExpired as error:
                        raise RuntimeError(
                            f"sd-cli Q4 timed out after {timeout:g}s; "
                            "raise SD_CPP_TIMEOUT_SECONDS"
                        ) from error
                    if result.returncode != 0:
                        raise RuntimeError(f"sd-cli Q4 failed {result.returncode}: {result.stderr[-800:]}")
                    if not os.path.exists(out_path):
                        # sd-cli may add suffix like out.png -> out_0.png
                        candidates = [os.path.join(td, f) for f in os.listdir(td) if f.startswith("out")]
                        if candidates:
                            out_path = candidates[0]
                        else:
                            raise RuntimeError(f"sd-cli no output: {result.stdout[-800:]} {result.stderr[-800:]}")
                    res = np.array(Image.open(out_path).convert("RGB"))
                    return res

            result = await asyncio.to_thread(_run)

        if result.shape[:2] != (orig_h, orig_w):
            result = np.array(Image.fromarray(result).resize((orig_w, orig_h), Image.LANCZOS))
        composite = image.copy()
        composite[commit_mask] = result.astype(np.uint8)[commit_mask]
        return composite

    def dispose(self):
        self._initialized = False
