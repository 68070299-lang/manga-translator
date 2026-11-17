"""Command-line entry point for headless LanPaint inpainting."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import numpy as np
from PIL import Image

from manga_backend.models.flux_lanpaint_inpainter import DEFAULT_PROMPT, FluxLanPaintInpainter


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inpaint a binary mask through local ComfyUI + Flux.2 Klein + LanPaint."
    )
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--mask", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--server", default=None, help="Loopback ComfyUI URL")
    parser.add_argument("--context", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    return parser


async def _run(args: argparse.Namespace) -> None:
    image = np.asarray(Image.open(args.image).convert("RGB"), dtype=np.uint8)
    mask = np.asarray(Image.open(args.mask).convert("L"), dtype=np.uint8)
    inpainter = FluxLanPaintInpainter(device="cuda")
    if args.server:
        inpainter.server_url = args.server.rstrip("/")
        inpainter.client = type(inpainter.client)(inpainter.server_url)
    if args.context is not None:
        inpainter.context = args.context
    await inpainter.initialize()
    kwargs = {"seed": args.seed} if args.seed is not None else {}
    output = await inpainter.inpaint(image, mask, prompt=args.prompt, **kwargs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(output).save(args.output)
    print(f"Saved {args.output.resolve()}")


def main() -> None:
    asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    main()
