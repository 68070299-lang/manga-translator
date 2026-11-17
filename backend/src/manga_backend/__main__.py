"""`python -m manga_backend`: run the API with uvicorn."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from manga_backend.bootstrap import bootstrap, configure_logging

bootstrap()

from manga_backend.config import load_settings  # noqa: E402


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    settings = load_settings()
    parser = argparse.ArgumentParser(prog="manga_backend", description="Run the Manga Translator backend.")
    parser.add_argument("--host", default=settings.host, help="Bind address (default: %(default)s).")
    parser.add_argument(
        "--port", type=int, default=settings.port, help="Listen port (default: %(default)s)."
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    configure_logging()

    import uvicorn

    uvicorn.run(
        "manga_backend.api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
