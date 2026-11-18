"""Device and ONNX runtime utilities.

Based on comic-translate's device.py
"""

from __future__ import annotations

import ctypes
import logging
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import onnxruntime as ort

logger = logging.getLogger(__name__)

# A provider is a bare name or a (name, options) pair.
Provider = str | tuple[str, dict[str, Any]]

_gpu_dlls_preloaded = False
_gpu_dll_directory_handles: list[Any] = []
_gpu_library_handles: list[Any] = []


def preload_onnx_gpu_dependencies(providers: Sequence[Provider]) -> None:
    """Load CUDA DLLs shipped as Python packages before ORT creates a session."""
    global _gpu_dlls_preloaded
    if _gpu_dlls_preloaded or not providers or not hasattr(ort, "preload_dlls"):
        return

    provider_names = {
        provider[0] if isinstance(provider, tuple) else provider
        for provider in providers
    }
    if not provider_names.intersection(
        {"CUDAExecutionProvider", "TensorrtExecutionProvider"}
    ):
        return

    try:
        if os.name == "nt" and hasattr(os, "add_dll_directory"):
            site_packages = Path(ort.__file__).resolve().parents[1]
            for dll_dir in (site_packages / "nvidia").glob("*/bin"):
                _gpu_dll_directory_handles.append(
                    os.add_dll_directory(str(dll_dir))
                )
            cudnn_dir = site_packages / "nvidia" / "cudnn" / "bin"
            for dll_path in sorted(cudnn_dir.glob("cudnn*.dll")):
                _gpu_library_handles.append(ctypes.WinDLL(str(dll_path)))
        ort.preload_dlls()
        _gpu_dlls_preloaded = True
    except Exception as error:
        logger.warning("Could not preload ONNX GPU dependencies: %s", error)


def _intra_op_threads(first_provider: str) -> int:
    """Pick intra-op threads: ORT_INTRA_OP_THREADS, else cores/2 on CPU, else 1."""
    raw = os.getenv("ORT_INTRA_OP_THREADS", "").strip()
    if raw:
        try:
            configured = int(raw)
        except ValueError:
            configured = 0
        if configured > 0:
            return configured
        logger.warning("Ignoring invalid ORT_INTRA_OP_THREADS=%r", raw)
    if first_provider == "CPUExecutionProvider":
        return min(8, max(1, (os.cpu_count() or 2) // 2))
    return 1


CUDNN_CONV_ALGO_SEARCH_MODES = {"EXHAUSTIVE", "HEURISTIC", "DEFAULT"}


def cuda_provider_options() -> dict[str, Any]:
    """CUDA settings for a pipeline whose input shapes change with every page.

    ONNX Runtime's default (EXHAUSTIVE) benchmarks every cuDNN convolution
    algorithm each time a session meets a new input shape, and crop sizes, OCR
    batch sizes and page sizes all vary here. HEURISTIC picks an algorithm
    without benchmarking. Growing the arena by the requested size, rather than
    to the next power of two, keeps several sessions inside a small GPU.
    """
    search = os.getenv("ORT_CUDNN_CONV_ALGO_SEARCH", "").strip().upper() or "HEURISTIC"
    if search not in CUDNN_CONV_ALGO_SEARCH_MODES:
        logger.warning("Ignoring invalid ORT_CUDNN_CONV_ALGO_SEARCH=%r", search)
        search = "HEURISTIC"
    return {
        "cudnn_conv_algo_search": search,
        "arena_extend_strategy": "kSameAsRequested",
    }


def with_provider_options(providers: Sequence[Provider]) -> list[Provider]:
    """Attach the shared CUDA options to a bare CUDAExecutionProvider entry."""
    return [
        (provider, cuda_provider_options())
        if isinstance(provider, str) and provider == "CUDAExecutionProvider"
        else provider
        for provider in providers
    ]


def create_onnx_session(
    model_path: str | os.PathLike[str],
    providers: Sequence[Provider] | str | None = None,
) -> ort.InferenceSession:
    """Create an ONNX session with shared optimization settings; providers default to CPU."""
    if providers is None:
        providers = ["CPUExecutionProvider"]
    elif isinstance(providers, str):
        providers = [providers]
    providers = with_provider_options(providers)

    preload_onnx_gpu_dependencies(providers)

    opts = ort.SessionOptions()
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    requested = providers[0][0] if isinstance(providers[0], tuple) else providers[0]
    opts.inter_op_num_threads = 1
    opts.intra_op_num_threads = _intra_op_threads(requested)
    session = ort.InferenceSession(model_path, opts, providers=providers)
    actual = session.get_providers()
    if requested != "CPUExecutionProvider" and (not actual or actual[0] != requested):
        raise RuntimeError(
            f"Requested ONNX provider {requested} failed to initialize; active providers: {actual}"
        )
    return session


PROVIDER_BY_DEVICE = {
    "cuda": "CUDAExecutionProvider",
    "tensorrt": "TensorrtExecutionProvider",
    "coreml": "CoreMLExecutionProvider",
    "rocm": "ROCMExecutionProvider",
    "directml": "DmlExecutionProvider",
    "dml": "DmlExecutionProvider",
    "openvino": "OpenVINOExecutionProvider",
}


def get_device(use_gpu: bool = False) -> str:
    if not use_gpu:
        return "cpu"

    try:
        providers = ort.get_available_providers()
    except Exception:
        logger.warning("Failed to query ONNX Runtime providers, falling back to CPU")
        return "cpu"

    for device, provider in PROVIDER_BY_DEVICE.items():
        if provider in providers:
            return device

    logger.warning("No GPU provider available, falling back to CPU")
    return "cpu"


# Provider preference when no device is requested.
AUTO_PROVIDER_ORDER = (
    "TensorrtExecutionProvider",
    "CUDAExecutionProvider",
    "DmlExecutionProvider",
    "CoreMLExecutionProvider",
    "ROCMExecutionProvider",
    "OpenVINOExecutionProvider",
)


def get_onnx_providers(device: str | None = None) -> list[Provider]:
    """Get ONNX Runtime execution providers for a device, or auto-select when None."""
    try:
        available = ort.get_available_providers()
    except Exception:
        available = []

    if not available:
        return ["CPUExecutionProvider"]

    if device:
        device = device.strip().lower()

    if device == "cpu":
        return ["CPUExecutionProvider"]

    if device in PROVIDER_BY_DEVICE:
        requested = PROVIDER_BY_DEVICE[device]
        if requested not in available:
            logger.warning(
                "Requested ONNX provider %s is not available, falling back to CPU", requested
            )
            return ["CPUExecutionProvider"]
        providers: list[Provider] = [requested]
        if "CPUExecutionProvider" in available:
            providers.append("CPUExecutionProvider")
    else:
        providers = [provider for provider in AUTO_PROVIDER_ORDER if provider in available]
        providers.append("CPUExecutionProvider")

    logger.info("ONNX providers: %s", providers)
    return providers
