"""Reading-order agreement report for the translation pipeline (WP6a).

The translator now receives blocks in reading order (panels first, then the
lettering inside each panel). This script scores that order against an
expected order per corpus page:

- synthetic pages: the page generator's ground-truth boxes, ordered by the
  documented convention — rows top to bottom, and within a row right to left
  for manga pages, left to right for tall webtoon strips. For the
  white-bubbles, sfx and webtoon pages that convention order is also the
  order the generator's dialogue was authored in, so agreement there is an
  independent check, not a restatement of the implementation;
- real pages: an optional hand-labelled file, ``eval/reading-order-labels.json``
  (see eval/README.md for the format). Pages without labels are reported as
  unlabelled and skipped by the summary.

Agreement is Kendall's tau over the blocks that could be matched to an
expected box (IoU >= 0.3), plus an exact-match flag. Detection misses do not
penalize the order of the blocks that were found.

Usage (from the repository root; MANGA_BACKEND_MODELS_DIR as for the
pipeline harness when using the real detector)::

    uv run --project backend --locked python scripts/eval_reading_order.py
    uv run --project backend --locked python scripts/eval_reading_order.py --detector truth
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import eval_pipeline  # noqa: E402  (loads and bootstraps the backend)

from manga_backend.pipeline.layout import (  # noqa: E402
    reading_order_agreement,
    resolve_reading_direction,
    sort_reading_order,
)
from manga_backend.utils.textblock import TextBlock  # noqa: E402

DEFAULT_LABELS = ROOT / "eval" / "reading-order-labels.json"
# A detected block matches an expected box when their overlap covers at least
# this share of the smaller box. RT-DETR text boxes are usually larger than a
# hand-labelled or ground-truth text box, so plain IoU would refuse good matches.
MATCH_OVERLAP = 0.5
TRUTH_DETECTOR = "truth"


def overlap_ratio(first: list[int], second: list[int]) -> float:
    """Overlap area divided by the smaller box's area."""
    x1, y1 = max(first[0], second[0]), max(first[1], second[1])
    x2, y2 = min(first[2], second[2]), min(first[3], second[3])
    overlap = max(0, x2 - x1) * max(0, y2 - y1)
    if not overlap:
        return 0.0
    area_first = (first[2] - first[0]) * (first[3] - first[1])
    area_second = (second[2] - second[0]) * (second[3] - second[1])
    return overlap / float(min(area_first, area_second))


def match_to_expected(
    blocks: list[TextBlock], expected_boxes: list[list[int]]
) -> dict[int, int]:
    """Greedy one-to-one match of detected blocks to expected boxes by overlap."""
    candidates = sorted(
        (
            (overlap_ratio([int(v) for v in block.xyxy], box), block_index, box_index)
            for block_index, block in enumerate(blocks)
            for box_index, box in enumerate(expected_boxes)
        ),
        reverse=True,
    )
    matched: dict[int, int] = {}
    taken_blocks: set[int] = set()
    taken_boxes: set[int] = set()
    for value, block_index, box_index in candidates:
        if value < MATCH_OVERLAP:
            break
        if block_index in taken_blocks or box_index in taken_boxes:
            continue
        matched[block_index] = box_index
        taken_blocks.add(block_index)
        taken_boxes.add(box_index)
    return matched


def expected_indices(boxes: list[list[int]], direction: str) -> list[int]:
    """Order box indices by the documented convention.

    Rows are chains of overlapping y spans, read top to bottom; inside a row
    the boxes run right to left for manga ("rtl") or left to right for
    webtoons ("ltr"), ties broken by y.
    """
    by_row: list[list[int]] = []
    row_end: int | None = None
    for index in sorted(range(len(boxes)), key=lambda i: (boxes[i][1], boxes[i][3])):
        start, end = boxes[index][1], boxes[index][3]
        if not by_row or (row_end is not None and start >= row_end):
            by_row.append([])
        by_row[-1].append(index)
        row_end = end if row_end is None else max(row_end, end)

    def row_key(index: int) -> tuple[float, ...]:
        center_x = (boxes[index][0] + boxes[index][2]) / 2.0
        first = -center_x if direction == "rtl" else center_x
        return (first, boxes[index][1])

    ordered: list[int] = []
    for row in by_row:
        ordered += sorted(row, key=row_key)
    return ordered


def load_labels(path: Path) -> dict[str, list[dict[str, Any]]]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: labels must be an object keyed by page id")
    for page_id, entries in data.items():
        if not isinstance(entries, list):
            raise ValueError(f"{path}: page {page_id!r} must be a list")
        for entry in entries:
            if not isinstance(entry, dict) or "bbox" not in entry:
                raise ValueError(f"{path}: page {page_id!r} needs bbox entries")
    return data


class BlockSource:
    """Yields detected blocks per page: the real RT-DETR or synthetic truth."""

    def __init__(self, detector: str) -> None:
        self.detector = detector
        self._detector: Any = None

    async def blocks(self, loaded: eval_pipeline.LoadedPage) -> list[TextBlock]:
        if self.detector == TRUTH_DETECTOR:
            if loaded.synthetic is None:
                raise RuntimeError("the truth detector needs a synthetic page")
            made: list[TextBlock] = []
            for truth in loaded.synthetic.truth:
                block = TextBlock(
                    list(truth.xyxy),
                    block_type="sfx" if truth.bubble is None else "text",
                )
                block.text = truth.text
                if truth.bubble is not None:
                    block.bubble_xyxy = list(truth.bubble)
                made.append(block)
            return made
        if self._detector is None:
            from manga_backend.models.detector import TextDetector

            self._detector = TextDetector(device="cpu")
            await self._detector.initialize()
        return await self._detector.detect(loaded.image.copy())


async def score_page(
    page: eval_pipeline.CorpusPage,
    manifest_dir: Path,
    source: BlockSource,
    direction: str,
    labels: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    loaded = eval_pipeline.load_page(page, manifest_dir)
    if loaded is None:
        return {"id": page.id, "status": "missing"}
    if source.detector == TRUTH_DETECTOR and loaded.synthetic is None:
        return {"id": page.id, "status": "skipped", "reason": "no ground truth"}
    blocks = await source.blocks(loaded)
    resolved = resolve_reading_direction(direction, loaded.image.shape)

    if loaded.synthetic is not None:
        expected_boxes = [list(truth.xyxy) for truth in loaded.synthetic.truth]
        source = "synthetic-truth"
    elif page.id in labels:
        expected_boxes = [[int(v) for v in entry["bbox"]] for entry in labels[page.id]]
        source = "labels"
    else:
        return {
            "id": page.id,
            "status": "unlabelled",
            "blocks": len(blocks),
            "direction": resolved,
        }

    ordered = sort_reading_order(loaded.image, blocks, direction)
    matched = match_to_expected(ordered, expected_boxes)
    expected_labels = [f"box-{index}" for index in expected_indices(expected_boxes, resolved)]
    actual_labels = [
        f"box-{matched[position]}"
        for position in range(len(ordered))
        if position in matched
    ]
    agreement = reading_order_agreement(expected_labels, actual_labels)
    return {
        "id": page.id,
        "status": "ok",
        "source": source,
        "direction": resolved,
        "blocks": len(blocks),
        **agreement,
    }


async def run(args: argparse.Namespace) -> int:
    corpus = eval_pipeline.load_corpus(args.corpus)
    if args.pages:
        wanted = set(args.pages)
        corpus = [page for page in corpus if page.id in wanted]
    labels = load_labels(args.labels) if args.labels else load_labels(DEFAULT_LABELS)

    records = []
    source = BlockSource(args.detector)
    for page in corpus:
        records.append(
            await score_page(page, args.corpus.parent, source, args.direction, labels)
        )

    scored = [record for record in records if record.get("status") == "ok"]
    taus = [record["kendall_tau"] for record in scored if record["kendall_tau"] is not None]
    report: dict[str, Any] = {
        "detector": args.detector,
        "direction": args.direction,
        "labels_file": str(args.labels) if args.labels and args.labels.is_file() else None,
        "pages": records,
        "summary": {
            "scored": len(scored),
            "unlabelled": sum(record.get("status") == "unlabelled" for record in records),
            "exact": sum(bool(record.get("exact")) for record in scored),
            "mean_kendall_tau": round(sum(taus) / len(taus), 4) if taus else None,
        },
    }

    header = f"{'Page':34s} {'Dir':4s} {'Blocks':>6s} {'Matched':>8s} {'Tau':>7s} Exact"
    print(header)
    print("-" * len(header))
    for record in records:
        if record.get("status") != "ok":
            print(f"{record['id']:34s} {record['status']}")
            continue
        tau = record["kendall_tau"]
        print(
            f"{record['id']:34s} {record['direction']:4s} {record['blocks']:6d} "
            f"{record['matched']:8d} {tau if tau is not None else 'n/a':>7} "
            f"{'yes' if record['exact'] else 'no'}"
        )
    summary = report["summary"]
    print(
        f"\nScored {summary['scored']} pages ({summary['unlabelled']} unlabelled): "
        f"{summary['exact']} exact, mean Kendall tau {summary['mean_kendall_tau']}"
    )

    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {target}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", type=Path, default=ROOT / "eval" / "corpus.json")
    parser.add_argument(
        "--labels",
        type=Path,
        help=f"hand-labelled reading order (default: {DEFAULT_LABELS} when present)",
    )
    parser.add_argument(
        "--detector",
        default="rtdetr",
        help="'rtdetr' (real model) or 'truth' (synthetic ground-truth boxes)",
    )
    parser.add_argument(
        "--direction",
        default="auto",
        choices=("auto", "rtl", "ltr"),
        help="reading direction override; auto uses the page aspect rule",
    )
    parser.add_argument("--pages", nargs="+", help="only these page ids")
    parser.add_argument("--out", type=Path, help="write the JSON report to this path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
