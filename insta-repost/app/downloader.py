"""
app/downloader.py
─────────────────
Responsible for downloading Instagram media (images / videos / carousels)
given a post shortcode or URL.

Primary downloader : instaloader
Fallback downloader: yt-dlp

Public API
──────────
    download_post(shortcode: str, dest_dir: str, cfg: Config) -> DownloadResult
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from app.config import Config


@dataclass
class DownloadResult:
    """Container for a successfully downloaded post."""

    shortcode: str
    media_type: str          # "IMAGE" | "VIDEO" | "CAROUSEL_ALBUM"
    local_paths: List[Path]  # one file per slide / single file
    caption: str
    source_user: str
    thumbnail_path: Optional[Path] = None


def download_post(
    shortcode: str,
    dest_dir: str,
    cfg: Config,
) -> DownloadResult:
    """
    Download all media for an Instagram post identified by *shortcode*.

    Tries instaloader first; if that fails, falls back to yt-dlp.
    All files are saved under *dest_dir*/<shortcode>/.

    Args:
        shortcode: The Instagram post shortcode (e.g. ``"CxYz123abcd"``).
        dest_dir:  Root directory where downloaded files are stored.
        cfg:       Application configuration (burner account credentials).

    Returns:
        A :class:`DownloadResult` with local file paths and post metadata.

    Raises:
        DownloadError: If both instaloader and yt-dlp fail.
        NotImplementedError: Until Phase 2 is implemented.
    """
    raise NotImplementedError("Phase 2")


def _download_with_instaloader(
    shortcode: str,
    dest_dir: Path,
    cfg: Config,
) -> DownloadResult:
    """
    Internal: attempt download via instaloader.

    Args:
        shortcode: Instagram post shortcode.
        dest_dir:  Destination directory (will be created if absent).
        cfg:       Config holding burner-account credentials.

    Returns:
        :class:`DownloadResult` on success.

    Raises:
        Exception: Any instaloader error (caller decides whether to fall back).
        NotImplementedError: Until Phase 2 is implemented.
    """
    raise NotImplementedError("Phase 2")


def _download_with_ytdlp(
    shortcode: str,
    dest_dir: Path,
) -> DownloadResult:
    """
    Internal: fallback download via yt-dlp.

    Args:
        shortcode: Instagram post shortcode.
        dest_dir:  Destination directory.

    Returns:
        :class:`DownloadResult` on success.

    Raises:
        Exception: Any yt-dlp error.
        NotImplementedError: Until Phase 2 is implemented.
    """
    raise NotImplementedError("Phase 2")


class DownloadError(Exception):
    """Raised when all download strategies have been exhausted."""
