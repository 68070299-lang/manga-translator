"""Process-environment setup that must happen before onnxruntime is imported.

`bootstrap()` is idempotent and is called from `manga_backend.api` (so any import
of the HTTP layer is covered), `manga_backend.main` and `manga_backend.__main__`.
It deliberately imports nothing heavy: only the standard library, python-dotenv
and `manga_backend.paths`.
"""

from __future__ import annotations

import logging
import os
import site
import sys
from collections.abc import Iterator
from typing import Any

from dotenv import load_dotenv

from manga_backend.paths import ENV_FILE

# Component directories shipped by the nvidia-* wheels that onnxruntime-gpu needs.
_CUDA_COMPONENTS = ("cublas", "cuda_runtime", "cudnn", "cuda_nvrtc", "cufft", "nvjitlink")

# os.add_dll_directory handles must stay referenced or the directories are removed again.
_dll_directory_handles: list[Any] = []
_bootstrapped = False


def is_testing_environment() -> bool:
    """True when the hermetic test flag asks for no auth and no backend/.env."""
    return os.environ.get("MANGA_TRANSLATOR_TESTING") == "1"


def load_backend_env() -> bool:
    """Load backend/.env into os.environ unless the test flag asks for a hermetic process.

    The translator, pipeline and model modules read provider keys and tuning knobs
    straight from os.environ, so the file is exported to the process in addition to
    being read by `manga_backend.config.Settings`. Existing variables win.
    """
    if is_testing_environment():
        return False
    load_dotenv(ENV_FILE)
    return True


def _disable_paddle_pir() -> None:
    """Disable PaddlePaddle PIR and OneDNN.

    Fixes the "ConvertPirAttribute2RuntimeAttribute not support" error.
    """
    os.environ["FLAGS_enable_pir_api"] = "0"
    os.environ["FLAGS_enable_pir_in_executor"] = "0"
    os.environ["FLAGS_use_mkldnn"] = "0"


def _site_package_roots() -> Iterator[str]:
    """Resolve site-packages from the active interpreter rather than assuming backend/venv."""
    roots: list[str] = []
    try:
        roots.extend(site.getsitepackages())
    except AttributeError:
        pass
    try:
        roots.append(site.getusersitepackages())
    except AttributeError:
        pass
    roots.append(os.path.join(sys.prefix, "Lib", "site-packages"))
    yield from dict.fromkeys(roots)


def add_cuda_dll_directories() -> list[str]:
    """Put the pip-installed CUDA DLL directories on PATH and the Windows DLL search path."""
    extra_paths = [
        directory
        for root in _site_package_roots()
        for component in _CUDA_COMPONENTS
        if os.path.isdir(directory := os.path.join(root, "nvidia", component, "bin"))
    ]
    for directory in extra_paths:
        if sys.platform == "win32":
            _dll_directory_handles.append(os.add_dll_directory(directory))
    if extra_paths:
        os.environ["PATH"] = ";".join([*extra_paths, os.environ.get("PATH", "")])
    return extra_paths


def configure_logging() -> None:
    """Make application loggers visible when running under uvicorn."""
    logging.basicConfig(level=logging.INFO)


def bootstrap() -> None:
    """Prepare the process environment once, before any model library is imported."""
    global _bootstrapped
    if _bootstrapped:
        return
    _bootstrapped = True
    _disable_paddle_pir()
    add_cuda_dll_directories()
    load_backend_env()
