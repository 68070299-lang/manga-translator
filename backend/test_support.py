"""Portable helpers shared by backend tests."""

import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def writable_temp_directory():
    """Create a writable temp directory without Windows' restrictive 0o700 ACL."""
    directory = Path(tempfile.gettempdir()) / f"manga-translator-{uuid.uuid4().hex}"
    directory.mkdir(parents=True)
    try:
        yield str(directory)
    except BaseException as original_error:
        try:
            shutil.rmtree(directory)
        except Exception as cleanup_error:
            original_error.add_note(
                f"Temporary directory cleanup also failed for {directory}: {cleanup_error!r}"
            )
        raise
    else:
        shutil.rmtree(directory)
