"""ASGI entry point: `uvicorn manga_backend.main:app`.

The application itself lives in `manga_backend.api`; this module only prepares the
process (environment, logging) and builds the default app instance.
"""

from manga_backend.bootstrap import bootstrap, configure_logging

bootstrap()
configure_logging()

from manga_backend.api.app import create_app  # noqa: E402

app = create_app()

__all__ = ["app"]
