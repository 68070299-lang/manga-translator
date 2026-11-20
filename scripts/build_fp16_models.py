"""Build optional FP16 GPU ONNX sidecar models alongside cached FP32 models.

Sidecars are named ``<stem>.fp16.onnx`` inside ``backend/models_cache/`` so the
original pinned FP32 files stay untouched for SHA256 verification and CPU runs.
At runtime, ``manga_backend.utils.device.create_onnx_session`` loads a sidecar
automatically on CUDA/TensorRT unless ``ORT_DISABLE_FP16=1`` is set.

Usage (no extra packages needed in backend's venv):
    uv run --python 3.12 --with onnx --with numpy --with packaging \\
        python scripts/build_fp16_models.py
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import onnx
from onnx import helper, numpy_helper

ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

# Ops kept in FP32 to avoid CPU fallback Memcpy nodes (e.g. GridSample in RT-DETR)
# and precision-sensitive index/shape ops. LayerNormalization and Softmax stay FP16
# because ORT CUDA provides native FP16 kernels for both.
FP16_OP_BLOCK_LIST: tuple[str, ...] = (
    "ArrayFeatureExtractor",
    "Binarizer",
    "CastMap",
    "CategoryMapper",
    "DictVectorizer",
    "FeatureVectorizer",
    "Imputer",
    "LabelEncoder",
    "LinearClassifier",
    "LinearRegressor",
    "Normalizer",
    "OneHotEncoder",
    "RandomUniformLike",
    "SVMClassifier",
    "SVMRegressor",
    "Scaler",
    "TreeEnsembleClassifier",
    "TreeEnsembleRegressor",
    "ZipMap",
    "NonMaxSuppression",
    "TopK",
    "RoiAlign",
    "Resize",
    "Range",
    "CumSum",
    "Upsample",
    "GridSample",
)

# Output tensor of layer.1 cross-attention in mayocream/manga-ocr-onnx decoder.
# Everything downstream (3072-wide FFN + 6144-vocab LM head) is strictly
# token-independent, so slicing [:, -1:, :] here keeps greedy decoding exact
# while cutting downstream FLOPs and GPU->CPU logit transfers by ~L x.
DECODER_LAST_TOKEN_SLICE_TARGET = (
    "/decoder/bert/encoder/layer.1/crossattention/output/LayerNorm/Add_1_output_0"
)


def _import_fp16_converter():
    """Import ORT's bundled float16 converter from the active or GPU venv."""
    try:
        from onnxruntime.transformers.float16 import convert_float_to_float16

        return convert_float_to_float16
    except ImportError:
        pass
    for venv_name in (".venv-gpu", ".venv"):
        site_packages = ROOT / "backend" / venv_name / "Lib" / "site-packages"
        if site_packages.is_dir() and str(site_packages) not in sys.path:
            sys.path.append(str(site_packages))
    from onnxruntime.transformers.float16 import convert_float_to_float16

    return convert_float_to_float16


def slice_decoder_last_token(model: onnx.ModelProto) -> onnx.ModelProto:
    """Slice the manga-ocr decoder's final FFN + LM head to the last token only."""
    target = DECODER_LAST_TOKEN_SLICE_TARGET
    prod_idx: int | None = None
    for idx, node in enumerate(model.graph.node):
        if target in node.output:
            prod_idx = idx
            break
    if prod_idx is None:
        return model

    starts = numpy_helper.from_array(np.array([-1], dtype=np.int64), name="_last_tok_starts")
    ends = numpy_helper.from_array(
        np.array([9223372036854775807], dtype=np.int64), name="_last_tok_ends"
    )
    axes = numpy_helper.from_array(np.array([1], dtype=np.int64), name="_last_tok_axes")
    model.graph.initializer.extend([starts, ends, axes])

    sliced_name = f"{target}_last_token"
    slice_node = helper.make_node(
        "Slice",
        inputs=[target, "_last_tok_starts", "_last_tok_ends", "_last_tok_axes"],
        outputs=[sliced_name],
        name="_slice_last_token_before_ffn",
    )
    for node in model.graph.node[prod_idx + 1 :]:
        for idx, inp in enumerate(node.input):
            if inp == target:
                node.input[idx] = sliced_name
    model.graph.node.insert(prod_idx + 1, slice_node)

    if model.graph.output:
        out_tensor = model.graph.output[0].type.tensor_type
        if len(out_tensor.shape.dim) >= 2:
            out_tensor.shape.dim[1].ClearField("dim_param")
            out_tensor.shape.dim[1].dim_value = 1
    return model


def build_sidecars(cache_dir: Path, *, force: bool = False) -> None:
    convert_float_to_float16 = _import_fp16_converter()
    # LaMa is intentionally excluded: its 216 CPU-fallback DFT ops double
    # GPU<->CPU Memcpy nodes in FP16 and run slower than FP32 TF32 on CUDA.
    targets = [
        ("detector", cache_dir / "detection" / "detector.onnx", False),
        ("ocr-encoder", cache_dir / "manga_ocr" / "encoder_model.onnx", False),
        ("ocr-decoder", cache_dir / "manga_ocr" / "decoder_model.onnx", True),
        ("text-segmenter", cache_dir / "text_segmenter" / "comic-text-detector.onnx", False),
    ]
    for label, src, slice_decoder in targets:
        if not src.is_file():
            print(f"[skip] {label}: missing {src}", flush=True)
            continue
        dst = src.with_name(f"{src.stem}.fp16.onnx")
        if dst.is_file() and not force:
            print(f"[keep] {label}: {dst.name} already exists ({dst.stat().st_size/1e6:.1f} MB)", flush=True)
            continue
        t0 = time.perf_counter()
        print(
            f"[build] {label}: {src.name} ({src.stat().st_size/1e6:.1f} MB) -> {dst.name}...",
            flush=True,
        )
        model = onnx.load(str(src))
        if slice_decoder:
            model = slice_decoder_last_token(model)
        fp16_model = convert_float_to_float16(
            model,
            keep_io_types=True,
            op_block_list=list(FP16_OP_BLOCK_LIST),
        )
        tmp = dst.with_suffix(".onnx.tmp")
        onnx.save(fp16_model, str(tmp))
        tmp.replace(dst)
        print(
            f"[done] {label}: {dst.name} ({dst.stat().st_size/1e6:.1f} MB) in {time.perf_counter() - t0:.2f}s",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=ROOT / "backend" / "models_cache",
        help="Path to backend/models_cache",
    )
    parser.add_argument("--force", action="store_true", help="Rebuild existing sidecars")
    args = parser.parse_args()
    build_sidecars(args.cache_dir, force=args.force)


if __name__ == "__main__":
    main()
