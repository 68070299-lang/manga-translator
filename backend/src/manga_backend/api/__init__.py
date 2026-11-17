"""HTTP API for the Manga Translator extension.

The process environment (CUDA DLL search path, Paddle flags, backend/.env) must be
prepared before onnxruntime is imported, so any import of this package does it first.
"""

from manga_backend.bootstrap import bootstrap

bootstrap()
