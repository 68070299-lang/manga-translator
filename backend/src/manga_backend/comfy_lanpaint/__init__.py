"""Headless ComfyUI + LanPaint integration for local Flux.2 Klein inpainting."""

from .client import ComfyUIClient, ComfyUIError
from .crop import CropRegion, build_crop_regions, stitch_region
from .workflow import WorkflowSettings, build_workflow

__all__ = [
    "ComfyUIClient",
    "ComfyUIError",
    "CropRegion",
    "WorkflowSettings",
    "build_crop_regions",
    "build_workflow",
    "stitch_region",
]
