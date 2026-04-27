"""
app/uploader.py
───────────────
Uploads local media files to Catbox.moe and returns a public URL
that the Instagram Graph API can access.

Catbox.moe is a free, anonymous file host — no API key required.
Max file size: 200 MB.  Files persist indefinitely.

Public API
──────────
    upload_to_catbox(file_path: str | Path) -> str   # returns CDN URL
"""

from __future__ import annotations

from pathlib import Path
from typing import Union


CATBOX_API_URL = "https://catbox.moe/user/api.php"
MAX_FILE_SIZE_BYTES = 200 * 1024 * 1024  # 200 MB


def upload_to_catbox(file_path: Union[str, Path]) -> str:
    """
    Upload a local file to Catbox.moe and return its public HTTPS URL.

    The URL is suitable for passing directly to the Instagram Graph API
    (``/media`` endpoint ``image_url`` / ``video_url`` fields).

    Args:
        file_path: Absolute or relative path to the file to upload.

    Returns:
        A public ``https://files.catbox.moe/<hash>.<ext>`` URL string.

    Raises:
        FileNotFoundError: If *file_path* does not exist.
        FileTooLargeError: If the file exceeds Catbox's 200 MB limit.
        UploadError:       If the Catbox API returns an error response.
        NotImplementedError: Until Phase 3 is implemented.
    """
    raise NotImplementedError("Phase 3")


def _validate_file(path: Path) -> None:
    """
    Assert the file exists and is within the Catbox size limit.

    Args:
        path: Resolved :class:`pathlib.Path` to the local file.

    Raises:
        FileNotFoundError: If the file does not exist.
        FileTooLargeError: If the file is too large for Catbox.
        NotImplementedError: Until Phase 3 is implemented.
    """
    raise NotImplementedError("Phase 3")


class UploadError(Exception):
    """Raised when Catbox.moe returns an unexpected response."""


class FileTooLargeError(UploadError):
    """Raised when the file exceeds Catbox's maximum size."""
