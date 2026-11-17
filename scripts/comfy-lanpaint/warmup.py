"""Preload the fixed LanPaint graph without changing production quality settings."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from comfy_lanpaint.client import ComfyUIClient
from comfy_lanpaint.workflow import WorkflowSettings
from models.flux_lanpaint_inpainter import DEFAULT_PROMPT


async def main() -> None:
    source = np.full((256, 256, 3), 245, dtype=np.uint8)
    source[::16, :, :] = 225
    source[:, ::16, :] = 225
    mask = np.zeros((256, 256), dtype=np.uint8)
    mask[96:160, 72:184] = 255
    settings = WorkflowSettings(steps=1, lanpaint_steps=1)
    client = ComfyUIClient()

    started = time.perf_counter()
    await client.validate()
    await client.inpaint_crop(source, mask, DEFAULT_PROMPT, seed=0, settings=settings)
    print(f"LanPaint warmup completed in {time.perf_counter() - started:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
