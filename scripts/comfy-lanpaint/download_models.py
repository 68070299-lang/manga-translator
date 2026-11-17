"""Install the minimal Flux.2 Klein 4B model set into a ComfyUI runtime."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

# Xet's parallel CAS transport is unreliable on some Windows networks.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "300")

from huggingface_hub import hf_hub_download

MODELS = (
    (
        "black-forest-labs/FLUX.2-klein-base-4b-fp8",
        "flux-2-klein-base-4b-fp8.safetensors",
        "diffusion_models/flux-2-klein-base-4b-fp8.safetensors",
        4_000_000_000,
    ),
    (
        "Comfy-Org/flux2-klein",
        "split_files/text_encoders/qwen_3_4b_fp4_flux2.safetensors",
        "text_encoders/qwen_3_4b_fp4_flux2.safetensors",
        3_700_000_000,
    ),
)

DISTILLED_MODEL = (
    "black-forest-labs/FLUX.2-klein-4b-fp8",
    "flux-2-klein-4b-fp8.safetensors",
    "diffusion_models/flux-2-klein-4b-fp8.safetensors",
    4_000_000_000,
    "5b4408e59397a4a37ccb46afe426d8ed86379441",
)


def link_or_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return
    try:
        os.link(source, target)
        print(f"Hard-linked {target.name}")
    except OSError:
        shutil.copy2(source, target)
        print(f"Copied {target.name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comfy-root", required=True, type=Path)
    parser.add_argument("--vae-source", type=Path)
    parser.add_argument("--include-distilled", action="store_true")
    args = parser.parse_args()

    model_root = args.comfy_root.resolve() / "models"
    models = [(*model, None) for model in MODELS]
    if args.include_distilled:
        models.append(DISTILLED_MODEL)
    for repo_id, filename, relative_target, minimum_size, revision in models:
        target = model_root / relative_target
        if target.exists() and target.stat().st_size >= minimum_size:
            print(f"Already present: {target}")
            continue
        if target.exists():
            target.unlink()
        print(f"Downloading {repo_id}/{filename}")
        cached = Path(
            hf_hub_download(repo_id=repo_id, filename=filename, revision=revision)
        )
        link_or_copy(cached, target)
        if target.stat().st_size < minimum_size:
            raise RuntimeError(f"Downloaded model is unexpectedly small: {target}")

    vae_target = model_root / "vae" / "flux2-vae.safetensors"
    if vae_target.exists() and vae_target.stat().st_size > 300_000_000:
        print(f"Already present: {vae_target}")
    elif args.vae_source and args.vae_source.exists():
        if vae_target.exists():
            vae_target.unlink()
        link_or_copy(args.vae_source.resolve(), vae_target)
    else:
        cached = Path(
            hf_hub_download(
                repo_id="Comfy-Org/flux2-dev",
                filename="split_files/vae/flux2-vae.safetensors",
            )
        )
        link_or_copy(cached, vae_target)

    print(f"Model installation complete: {model_root}")


if __name__ == "__main__":
    main()
