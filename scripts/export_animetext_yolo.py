"""Export an AnimeText YOLO SafeTensors state dict to runtime-only ONNX."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = ROOT / "backend" / "models_cache" / "animetext_yolo"
MIRROR_REPO = "mayocream/anime-text-yolo"
MIRROR_REVISION = "937f67dfe61fc4793549782e103751fdc1f0a8d9"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variant",
        choices=("n", "s", "m", "l", "x"),
        default="m",
        help="YOLO12 model size (default: m)",
    )
    parser.add_argument(
        "--weights",
        type=Path,
        help="Local AnimeText SafeTensors state dict",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Destination ONNX path",
    )
    parser.add_argument(
        "--revision",
        default=MIRROR_REVISION,
        help="Pinned Hugging Face mirror revision",
    )
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_onnx_parity(reference, candidate) -> float:
    import numpy as np

    if candidate.shape != reference.shape:
        raise RuntimeError(
            f"ONNX parity shape mismatch: PyTorch {reference.shape}, ONNX {candidate.shape}"
        )
    np.testing.assert_allclose(candidate, reference, rtol=1e-3, atol=1e-4)
    return float(np.max(np.abs(candidate - reference)))


def main() -> None:
    args = parse_args()
    filename = f"yolo12{args.variant}_animetext.safetensors"
    weights_path = args.weights or DEFAULT_CACHE / filename
    output_path = args.output or DEFAULT_CACHE / filename.replace(
        ".safetensors", ".onnx"
    )
    weights_path = weights_path.resolve()
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path = output_path.with_suffix(".json")
    sidecar_path.unlink(missing_ok=True)

    if not weights_path.exists():
        from huggingface_hub import hf_hub_download

        downloaded = hf_hub_download(
            repo_id=MIRROR_REPO,
            filename=filename,
            revision=args.revision,
        )
        weights_path = Path(downloaded)

    # Ultralytics otherwise writes settings under the user's roaming profile.
    os.environ.setdefault(
        "YOLO_CONFIG_DIR", str(ROOT / "backend" / ".runtime" / "ultralytics")
    )

    import onnx
    import onnxruntime as ort
    import torch
    import yaml
    from safetensors import safe_open
    from safetensors.torch import load_file
    from ultralytics.nn.tasks import DetectionModel

    with safe_open(weights_path, framework="pt", device="cpu") as handle:
        metadata = handle.metadata() or {}
    model_yaml = yaml.safe_load(metadata["yaml"])
    metadata_variant = str(model_yaml.get("scale", "")).strip().lower()
    if metadata_variant and metadata_variant != args.variant:
        raise ValueError(
            f"Weights contain YOLO12{metadata_variant}, not requested YOLO12{args.variant}"
        )
    model = DetectionModel(cfg=model_yaml, ch=3, nc=1, verbose=False)
    model.load_state_dict(load_file(weights_path, device="cpu"), strict=True)
    model.eval()

    class PredictionsOnly(torch.nn.Module):
        def __init__(self, detector: torch.nn.Module):
            super().__init__()
            self.detector = detector

        def forward(self, images: torch.Tensor) -> torch.Tensor:
            return self.detector(images)[0]

    wrapper = PredictionsOnly(model).eval()
    generator = torch.Generator(device="cpu").manual_seed(0)
    sample = torch.rand((1, 3, 640, 640), generator=generator, dtype=torch.float32)
    with torch.inference_mode():
        reference = wrapper(sample).detach().cpu().numpy()
        torch.onnx.export(
            wrapper,
            (sample,),
            output_path,
            input_names=["images"],
            output_names=["predictions"],
            opset_version=17,
            dynamo=False,
            do_constant_folding=True,
        )

    onnx.checker.check_model(onnx.load(output_path))
    session = ort.InferenceSession(
        str(output_path), providers=["CPUExecutionProvider"]
    )
    candidate = session.run(
        [session.get_outputs()[0].name],
        {session.get_inputs()[0].name: sample.numpy()},
    )[0]
    parity_max_abs_error = validate_onnx_parity(reference, candidate)
    sidecar = {
        "architecture": "YOLO12",
        "variant": args.variant,
        "input_size": 640,
        "class_names": {"0": "text_block"},
        "source_repo": MIRROR_REPO,
        "source_revision": args.revision,
        "source_file": filename,
        "source_sha256": sha256(weights_path),
        "output_sha256": sha256(output_path),
        "parity": {
            "provider": "CPUExecutionProvider",
            "rtol": 1e-3,
            "atol": 1e-4,
            "max_abs_error": parity_max_abs_error,
        },
        "ultralytics_version": metadata.get("ultralytics_version"),
    }
    sidecar_path.write_text(
        json.dumps(sidecar, indent=2) + "\n", encoding="ascii"
    )
    print(output_path)


if __name__ == "__main__":
    main()
