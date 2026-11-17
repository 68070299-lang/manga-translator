"""Benchmark the real loopback LanPaint path with preservation checks."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from comfy_lanpaint.crop import build_crop_regions  # noqa: E402
from models.flux_lanpaint_inpainter import FluxLanPaintInpainter  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--mask", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--profile", choices=("fast", "balanced", "quality"), default="balanced"
    )
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--model")
    parser.add_argument("--steps", type=int)
    parser.add_argument("--guidance", type=float)
    parser.add_argument("--feather", type=int)
    parser.add_argument("--label")
    parser.add_argument(
        "--fill-generation-boxes",
        action="store_true",
        help="Hide each grouped text box from the diffusion model while keeping the commit mask precise.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    if args.runs < 1:
        raise ValueError("--runs must be at least 1")
    os.environ["COMFY_LANPAINT_PROFILE"] = args.profile
    if args.model:
        os.environ["COMFY_LANPAINT_MODEL"] = args.model
    if args.steps is not None:
        os.environ["COMFY_LANPAINT_STEPS"] = str(args.steps)
    if args.guidance is not None:
        os.environ["COMFY_LANPAINT_GUIDANCE"] = str(args.guidance)
    if args.feather is not None:
        os.environ["COMFY_LANPAINT_FEATHER"] = str(args.feather)
    if args.fill_generation_boxes:
        os.environ["COMFY_LANPAINT_FILL_GENERATION_BOXES"] = "1"

    source = np.asarray(Image.open(args.image).convert("RGB"), dtype=np.uint8)
    mask = np.asarray(Image.open(args.mask).convert("L"), dtype=np.uint8)
    inpainter = FluxLanPaintInpainter(device="cuda")
    regions = build_crop_regions(
        source,
        mask,
        context=inpainter.context,
        mask_growth=inpainter.mask_growth,
        horizontal_group_gap=inpainter.horizontal_group_gap,
        vertical_group_gap=inpainter.vertical_group_gap,
        max_crop_pixels=inpainter.max_crop_pixels,
        fill_generation_boxes=inpainter.fill_generation_boxes,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    await inpainter.initialize()

    timings = []
    preservation = []
    label = args.label or args.profile
    for run in range(args.runs):
        started = time.perf_counter()
        result = await inpainter.inpaint(source, mask, seed=args.seed)
        timings.append(time.perf_counter() - started)
        outside = mask < 128
        preservation.append(bool(np.array_equal(result[outside], source[outside])))
        Image.fromarray(result).save(args.output_dir / f"{label}-{run + 1}.png")

    report = {
        "profile": args.profile,
        "label": label,
        "model": inpainter.settings.diffusion_model,
        "steps": inpainter.settings.steps,
        "guidance": inpainter.settings.flux_guidance,
        "runs": args.runs,
        "seconds": timings,
        "median_seconds": statistics.median(timings),
        "outside_mask_exact": all(preservation),
        "regions": [
            {
                "box": region.box,
                "height": region.image.shape[0],
                "width": region.image.shape[1],
                "pixels": region.image.shape[0] * region.image.shape[1],
            }
            for region in regions
        ],
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
