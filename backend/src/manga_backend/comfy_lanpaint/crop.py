"""Crop, pad, and exact-stitch helpers for memory-bounded inpainting."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

import cv2
import numpy as np


class CropBudgetExceeded(ValueError):
    """A masked component cannot fit the crop pixel budget even without context."""


@dataclass(frozen=True)
class CropRegion:
    """One contextual crop and its mapping back into the source image."""

    box: tuple[int, int, int, int]
    padding: tuple[int, int, int, int]
    image: np.ndarray
    generation_mask: np.ndarray
    commit_mask: np.ndarray


def _binary_mask(mask: np.ndarray, image_shape: tuple[int, int]) -> np.ndarray:
    if mask.ndim == 3:
        mask = np.max(mask, axis=2)
    if mask.ndim != 2 or mask.shape != image_shape:
        raise ValueError(
            f"mask shape {mask.shape} does not match image height/width {image_shape}"
        )
    return np.where(mask >= 128, 255, 0).astype(np.uint8)


def _group_component_boxes(
    binary: np.ndarray,
    *,
    horizontal_gap: int,
    vertical_gap: int,
) -> list[tuple[int, int, int, int]]:
    """Group nearby glyphs before adding context, avoiding full-page chain merges."""

    if horizontal_gap < 0 or vertical_gap < 0:
        raise ValueError("component gaps must be non-negative")
    if horizontal_gap or vertical_gap:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT,
            ((horizontal_gap * 2) + 1, (vertical_gap * 2) + 1),
        )
        grouped = cv2.dilate(binary, kernel, iterations=1)
    else:
        grouped = binary

    count, labels, _stats, _centroids = cv2.connectedComponentsWithStats(
        (grouped > 0).astype(np.uint8), connectivity=8
    )
    boxes: list[tuple[int, int, int, int]] = []
    for label in range(1, count):
        ys, xs = np.where((labels == label) & (binary > 0))
        if xs.size:
            boxes.append(
                (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
            )
    return sorted(boxes, key=lambda box: (box[1], box[0]))


def _bounded_context_box(
    box: tuple[int, int, int, int],
    *,
    image_width: int,
    image_height: int,
    context: int,
    multiple: int,
    max_crop_pixels: int | None,
) -> tuple[int, int, int, int]:
    """Reduce context only when the aligned crop would exceed the pixel budget."""

    bx0, by0, bx1, by1 = box

    def expanded(amount: int) -> tuple[int, int, int, int]:
        return (
            max(0, bx0 - amount),
            max(0, by0 - amount),
            min(image_width, bx1 + amount),
            min(image_height, by1 + amount),
        )

    def aligned_area(candidate: tuple[int, int, int, int]) -> int:
        x0, y0, x1, y1 = candidate
        width = ceil((x1 - x0) / multiple) * multiple
        height = ceil((y1 - y0) / multiple) * multiple
        return width * height

    selected = expanded(context)
    if not max_crop_pixels or aligned_area(selected) <= max_crop_pixels:
        return selected
    if aligned_area(expanded(0)) > max_crop_pixels:
        raise CropBudgetExceeded(
            "masked component exceeds COMFY_LANPAINT_MAX_CROP_PIXELS; "
            "split the mask or increase the crop budget"
        )
    low, high = 0, context
    while low < high:
        middle = (low + high + 1) // 2
        if aligned_area(expanded(middle)) <= max_crop_pixels:
            low = middle
        else:
            high = middle - 1
    return expanded(low)


def _aligned_padding(
    width: int, height: int, multiple: int
) -> tuple[int, int, int, int]:
    target_width = max(multiple, ceil(width / multiple) * multiple)
    target_height = max(multiple, ceil(height / multiple) * multiple)
    horizontal = target_width - width
    vertical = target_height - height
    left = horizontal // 2
    top = vertical // 2
    return left, top, horizontal - left, vertical - top


def build_crop_regions(
    image: np.ndarray,
    mask: np.ndarray,
    *,
    context: int = 128,
    multiple: int = 64,
    mask_growth: int = 4,
    minimum_component_area: int = 1,
    horizontal_group_gap: int = 32,
    vertical_group_gap: int = 16,
    max_crop_pixels: int | None = 393_216,
    fill_generation_boxes: bool = False,
) -> list[CropRegion]:
    """Split distant masked areas into aligned contextual crops without resizing."""

    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be an RGB array with shape (height, width, 3)")
    if image.dtype != np.uint8:
        raise ValueError("image must use uint8 pixels")
    if context < 0 or multiple < 8:
        raise ValueError("context must be non-negative and multiple must be at least 8")

    binary = _binary_mask(mask, image.shape[:2])
    if not np.any(binary):
        return []

    height, width = binary.shape
    if minimum_component_area > 1:
        count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
            (binary > 0).astype(np.uint8), connectivity=8
        )
        keep = np.zeros_like(binary)
        for label in range(1, count):
            if int(stats[label, cv2.CC_STAT_AREA]) >= minimum_component_area:
                keep[labels == label] = 255
        binary = keep
        if not np.any(binary):
            return []

    component_boxes = _group_component_boxes(
        binary,
        horizontal_gap=horizontal_group_gap,
        vertical_gap=vertical_group_gap,
    )
    boxes = [
        (
            box,
            _bounded_context_box(
                box,
                image_width=width,
                image_height=height,
                context=context,
                multiple=multiple,
                max_crop_pixels=max_crop_pixels,
            ),
        )
        for box in component_boxes
    ]

    regions: list[CropRegion] = []
    growth_kernel = None
    if mask_growth > 0:
        size = (mask_growth * 2) + 1
        growth_kernel = np.ones((size, size), dtype=np.uint8)

    for component_box, (x0, y0, x1, y1) in boxes:
        source = image[y0:y1, x0:x1]
        commit = np.zeros((y1 - y0, x1 - x0), dtype=np.uint8)
        component_x0, component_y0, component_x1, component_y1 = component_box
        local_x0, local_y0 = component_x0 - x0, component_y0 - y0
        local_x1, local_y1 = component_x1 - x0, component_y1 - y0
        commit[local_y0:local_y1, local_x0:local_x1] = binary[
            component_y0:component_y1, component_x0:component_x1
        ]
        generation = commit
        if fill_generation_boxes:
            generation = np.zeros_like(commit)
            generation[local_y0:local_y1, local_x0:local_x1] = 255
        if growth_kernel is not None:
            generation = cv2.dilate(generation, growth_kernel, iterations=1)

        left, top, right, bottom = _aligned_padding(x1 - x0, y1 - y0, multiple)
        padded_source = np.pad(
            source,
            ((top, bottom), (left, right), (0, 0)),
            mode="edge",
        )
        mask_padding = ((top, bottom), (left, right))
        regions.append(
            CropRegion(
                box=(x0, y0, x1, y1),
                padding=(left, top, right, bottom),
                image=padded_source,
                generation_mask=np.pad(generation, mask_padding, mode="constant"),
                commit_mask=commit,
            )
        )
    return regions


def stitch_region(
    canvas: np.ndarray,
    generated: np.ndarray,
    region: CropRegion,
    *,
    feather: int = 3,
    blend_base: np.ndarray | None = None,
    adaptive_feather: bool = False,
) -> np.ndarray:
    """Commit generated pixels only inside the original binary mask."""

    if generated.shape != region.image.shape:
        raise ValueError(
            f"generated crop shape {generated.shape} does not match {region.image.shape}"
        )
    if blend_base is not None and blend_base.shape != region.image.shape:
        raise ValueError(
            f"blend base shape {blend_base.shape} does not match {region.image.shape}"
        )
    x0, y0, x1, y1 = region.box
    left, top, right, bottom = region.padding
    crop_height = generated.shape[0] - top - bottom
    crop_width = generated.shape[1] - left - right
    unpadded = generated[top : top + crop_height, left : left + crop_width]
    if unpadded.shape[:2] != region.commit_mask.shape:
        raise ValueError("unpadded output and commit mask have different dimensions")

    mask = (region.commit_mask > 0).astype(np.uint8)
    if adaptive_feather:
        alpha = np.zeros(mask.shape, dtype=np.float32)
        count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
            mask, connectivity=8
        )
        base = blend_base if blend_base is not None else region.image
        unpadded_base = base[top : top + crop_height, left : left + crop_width]
        base_gray = cv2.cvtColor(unpadded_base, cv2.COLOR_RGB2GRAY)
        base_edges = cv2.Canny(base_gray, 60, 160) > 0
        boundary_kernel = np.ones((3, 3), dtype=np.uint8)
        for label in range(1, count):
            component = (labels == label).astype(np.uint8)
            width = int(stats[label, cv2.CC_STAT_WIDTH])
            height = int(stats[label, cv2.CC_STAT_HEIGHT])
            short_side = min(width, height)
            target = max(2, round(short_side * 0.08))
            radius = max(1, min(6, target, max(1, short_side // 4)))
            boundary = (
                cv2.dilate(component, boundary_kernel, iterations=1) > 0
            ) & ~(cv2.erode(component, boundary_kernel, iterations=1) > 0)
            if np.any(boundary) and np.mean(base_edges[boundary]) >= 0.12:
                radius = min(radius, 2)
            distance = cv2.distanceTransform(
                np.pad(component, 1, mode="constant"), cv2.DIST_L2, 3
            )[1:-1, 1:-1]
            blend = np.clip(distance / float(radius), 0.0, 1.0)
            blend = blend * blend * (3.0 - (2.0 * blend))
            alpha = np.maximum(alpha, blend)
    elif feather > 0:
        distance = cv2.distanceTransform(
            np.pad(mask, 1, mode="constant"), cv2.DIST_L2, 3
        )[1:-1, 1:-1]
        alpha = np.minimum(distance / float(feather), 1.0)
    else:
        alpha = mask.astype(np.float32)
    alpha = alpha[:, :, None]

    result = canvas.copy()
    original = result[y0:y1, x0:x1].astype(np.float32)
    if blend_base is not None:
        background = blend_base[
            top : top + crop_height, left : left + crop_width
        ].astype(np.float32)
    else:
        background = original
    blended = (unpadded.astype(np.float32) * alpha) + (
        background * (1.0 - alpha)
    )
    committed = original.copy()
    committed[mask > 0] = blended[mask > 0]
    result[y0:y1, x0:x1] = np.clip(np.rint(committed), 0, 255).astype(np.uint8)
    return result
