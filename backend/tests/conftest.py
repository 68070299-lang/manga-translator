"""Shared pytest setup for backend tests.

MANGA_TRANSLATOR_TESTING turns off the auth token check and keeps backend/.env
out of the process; it must be set before manga_backend.api or manga_backend.main is imported.
"""

import os

os.environ.setdefault("MANGA_TRANSLATOR_TESTING", "1")
