"""Compare RT-DETR and AnimeText YOLO rectangle detections on one image."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend" / "src"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from manga_backend.models.animetext_yolo_detector import AnimeTextYoloDetector  # noqa: E402
from manga_backend.models.detector import TextDetector  # noqa: E402


COLORS = {
    "rtdetr": (220, 55, 55),
    "animetext-yolo": (0, 150, 135),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts" / "detector-comparison",
    )
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--mask-margin", type=int, default=8)
    parser.add_argument("--animetext-confidence", type=float)
    return parser.parse_args()


async def benchmark(detector, image: np.ndarray, runs: int):
    await detector.detect(image)
    timings = []
    blocks = []
    for _ in range(max(1, runs)):
        started = time.perf_counter()
        blocks = await detector.detect(image)
        timings.append(time.perf_counter() - started)
    return blocks, timings


def make_mask(shape, blocks, margin: int) -> np.ndarray:
    height, width = shape[:2]
    mask = np.zeros((height, width), dtype=np.uint8)
    for block in blocks:
        x1, y1, x2, y2 = [int(value) for value in block.xyxy]
        x1, y1 = max(0, x1 - margin), max(0, y1 - margin)
        x2, y2 = min(width, x2 + margin), min(height, y2 + margin)
        mask[y1:y2, x1:x2] = 255
    return mask


def overlay(image: np.ndarray, blocks, name: str) -> Image.Image:
    output = Image.fromarray(image).copy()
    draw = ImageDraw.Draw(output)
    color = COLORS[name]
    width = max(2, round(min(image.shape[:2]) / 350))
    font = ImageFont.load_default()
    for index, block in enumerate(blocks, start=1):
        box = [int(value) for value in block.xyxy]
        draw.rectangle(box, outline=color, width=width)
        label = f"{index} {block.confidence:.2f}"
        label_box = draw.textbbox((box[0], box[1]), label, font=font)
        label_height = label_box[3] - label_box[1] + 4
        label_width = label_box[2] - label_box[0] + 6
        label_y = max(0, box[1] - label_height)
        draw.rectangle(
            [box[0], label_y, box[0] + label_width, label_y + label_height],
            fill=color,
        )
        draw.text((box[0] + 3, label_y + 2), label, fill="white", font=font)
    return output


def with_header(image: Image.Image, title: str) -> Image.Image:
    header_height = 38
    output = Image.new("RGB", (image.width, image.height + header_height), "white")
    output.paste(image, (0, header_height))
    draw = ImageDraw.Draw(output)
    draw.text((12, 12), title, fill="black", font=ImageFont.load_default())
    return output


def box_iou(first, second) -> float:
    ax1, ay1, ax2, ay2 = [float(value) for value in first]
    bx1, by1, bx2, by2 = [float(value) for value in second]
    intersection = max(0, min(ax2, bx2) - max(ax1, bx1)) * max(
        0, min(ay2, by2) - max(ay1, by1)
    )
    union = (
        max(0, ax2 - ax1) * max(0, ay2 - ay1)
        + max(0, bx2 - bx1) * max(0, by2 - by1)
        - intersection
    )
    return intersection / union if union else 0.0


def detector_metrics(blocks, timings, mask: np.ndarray) -> dict:
    pixels = int(np.count_nonzero(mask))
    total_pixels = int(mask.size)
    return {
        "block_count": len(blocks),
        "mask_pixels": pixels,
        "mask_percent": round(100 * pixels / total_pixels, 3),
        "median_seconds": round(statistics.median(timings), 4),
        "min_seconds": round(min(timings), 4),
        "boxes": [
            {
                "bbox": [int(value) for value in block.xyxy],
                "confidence": round(float(block.confidence), 5),
                "type": block.block_type,
            }
            for block in blocks
        ],
    }


async def main() -> None:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    image = np.asarray(Image.open(args.image).convert("RGB"))

    rtdetr = TextDetector(device=args.device)
    anime = AnimeTextYoloDetector(device=args.device)
    if args.animetext_confidence is not None:
        anime.confidence_threshold = args.animetext_confidence

    load_started = time.perf_counter()
    await rtdetr.initialize()
    rtdetr_load = time.perf_counter() - load_started
    load_started = time.perf_counter()
    await anime.initialize()
    anime_load = time.perf_counter() - load_started
    active_providers = {
        "rtdetr": list(rtdetr.session.get_providers()),
        "animetext_yolo": list(anime.session.get_providers()),
    }
    try:
        rtdetr_blocks, rtdetr_times = await benchmark(rtdetr, image, args.runs)
        anime_blocks, anime_times = await benchmark(anime, image, args.runs)
    finally:
        rtdetr.dispose()
        anime.dispose()

    rtdetr_mask = make_mask(image.shape, rtdetr_blocks, args.mask_margin)
    anime_mask = make_mask(image.shape, anime_blocks, args.mask_margin)
    rtdetr_overlay = overlay(image, rtdetr_blocks, "rtdetr")
    anime_overlay = overlay(image, anime_blocks, "animetext-yolo")
    rtdetr_overlay.save(output_dir / "rtdetr_overlay.png")
    anime_overlay.save(output_dir / "animetext_yolo_overlay.png")
    Image.fromarray(rtdetr_mask).save(output_dir / "rtdetr_mask.png")
    Image.fromarray(anime_mask).save(output_dir / "animetext_yolo_mask.png")

    columns = [
        with_header(rtdetr_overlay, f"RT-DETR | {len(rtdetr_blocks)} blocks"),
        with_header(
            anime_overlay,
            f"AnimeText YOLO12{anime.variant} | {len(anime_blocks)} blocks",
        ),
    ]
    comparison = Image.new(
        "RGB", (sum(column.width for column in columns), max(c.height for c in columns))
    )
    offset = 0
    for column in columns:
        comparison.paste(column, (offset, 0))
        offset += column.width
    comparison.save(output_dir / "comparison.png")

    matched_anime = sum(
        any(box_iou(anime_block.xyxy, rt_block.xyxy) >= 0.5 for rt_block in rtdetr_blocks)
        for anime_block in anime_blocks
    )
    metrics = {
        "image": str(args.image.resolve()),
        "image_shape": list(image.shape),
        "requested_device": args.device,
        "active_providers": active_providers,
        "comparison_scope": "single-image observation; no ground-truth accuracy",
        "runs": max(1, args.runs),
        "mask_margin": args.mask_margin,
        "rtdetr": detector_metrics(rtdetr_blocks, rtdetr_times, rtdetr_mask),
        "animetext_yolo": detector_metrics(anime_blocks, anime_times, anime_mask),
        "animetext_variant": anime.variant,
        "animetext_confidence_threshold": anime.confidence_threshold,
        "animetext_iou_threshold": anime.iou_threshold,
        "load_seconds": {
            "rtdetr": round(rtdetr_load, 4),
            "animetext_yolo": round(anime_load, 4),
        },
        "anime_boxes_matching_rtdetr_at_iou_0_5": matched_anime,
    }
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="ascii"
    )
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
