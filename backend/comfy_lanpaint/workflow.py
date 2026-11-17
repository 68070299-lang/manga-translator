"""ComfyUI API-format workflow generation for Flux.2 Klein + LanPaint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkflowSettings:
    diffusion_model: str = "flux-2-klein-4b-fp8.safetensors"
    text_encoder: str = "qwen_3_4b_fp4_flux2.safetensors"
    vae: str = "flux2-vae.safetensors"
    steps: int = 4
    cfg: float = 1.0
    flux_guidance: float = 1.0
    lanpaint_steps: int = 1
    clip_device: str = "default"
    denoise: float = 1.0
    sampler: str = "euler"
    scheduler: str = "simple"
    native_flux2_schedule: bool = False
    prompt_mode: str = "Image First"


def build_workflow(
    source_name: str,
    mask_name: str,
    prompt: str,
    *,
    seed: int,
    settings: WorkflowSettings | None = None,
    filename_prefix: str = "headless_lanpaint",
    websocket_output: bool = False,
    embedded_mask: bool = False,
    image_width: int | None = None,
    image_height: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Build the prompt graph accepted by ComfyUI's ``POST /prompt`` API."""

    config = settings or WorkflowSettings()
    mask_input: list[str | int] = ["1", 1] if embedded_mask else ["3", 0]
    workflow = {
        "1": {"class_type": "LoadImage", "inputs": {"image": source_name}},
        "4": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": config.diffusion_model,
                "weight_dtype": "default",
            },
        },
        "5": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": config.text_encoder,
                "type": "flux2",
                "device": config.clip_device,
            },
        },
        "6": {"class_type": "VAELoader", "inputs": {"vae_name": config.vae}},
        "7": {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["1", 0], "vae": ["6", 0]},
        },
        "8": {
            "class_type": "SetLatentNoiseMask",
            "inputs": {"samples": ["7", 0], "mask": mask_input},
        },
        "9": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["5", 0]},
        },
        "10": {
            "class_type": "ReferenceLatent",
            "inputs": {"conditioning": ["9", 0], "latent": ["7", 0]},
        },
        "11": {
            "class_type": "FluxGuidance",
            "inputs": {
                "conditioning": ["10", 0],
                "guidance": config.flux_guidance,
            },
        },
        "12": {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["9", 0]},
        },
        "13": {
            "class_type": "LanPaint_KSampler",
            "inputs": {
                "model": ["4", 0],
                "seed": int(seed),
                "steps": config.steps,
                "cfg": config.cfg,
                "sampler_name": config.sampler,
                "scheduler": config.scheduler,
                "positive": ["11", 0],
                "negative": ["12", 0],
                "latent_image": ["8", 0],
                "denoise": config.denoise,
                "LanPaint_NumSteps": config.lanpaint_steps,
                "LanPaint_PromptMode": config.prompt_mode,
                "LanPaint_Info": "Headless LanPaint inpainting",
                "Inpainting_mode": "\U0001f5bc\ufe0f Image Inpainting",
            },
        },
        "14": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["13", 0], "vae": ["6", 0]},
        },
    }
    if config.native_flux2_schedule:
        if not image_width or not image_height:
            raise ValueError("native Flux.2 scheduling requires image dimensions")
        workflow["13"] = {
            "class_type": "LanPaint_SamplerCustom",
            "inputs": {
                "model": ["4", 0],
                "add_noise": True,
                "noise_seed": int(seed),
                "cfg": config.cfg,
                "positive": ["11", 0],
                "negative": ["12", 0],
                "sampler": ["16", 0],
                "sigmas": ["17", 0],
                "latent_image": ["8", 0],
                "LanPaint_NumSteps": config.lanpaint_steps,
                "LanPaint_PromptMode": config.prompt_mode,
                "LanPaint_Info": "Headless LanPaint with native Flux.2 schedule",
            },
        }
        workflow["16"] = {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": config.sampler},
        }
        workflow["17"] = {
            "class_type": "Flux2Scheduler",
            "inputs": {
                "steps": config.steps,
                "width": image_width,
                "height": image_height,
            },
        }
    if not embedded_mask:
        workflow["2"] = {"class_type": "LoadImage", "inputs": {"image": mask_name}}
        workflow["3"] = {
            "class_type": "ImageToMask",
            "inputs": {"image": ["2", 0], "channel": "red"},
        }
    if websocket_output:
        workflow["15"] = {
            "class_type": "SaveImageWebsocket",
            "inputs": {"images": ["14", 0]},
        }
    else:
        workflow["15"] = {
            "class_type": "SaveImage",
            "inputs": {"images": ["14", 0], "filename_prefix": filename_prefix},
        }
    return workflow
