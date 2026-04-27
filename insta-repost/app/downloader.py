"""
app/downloader.py
─────────────────
Downloads Instagram media (images, videos, carousels) given a post URL.

Primary downloader : instaloader  (logs in with burner account, caches session)
Fallback downloader: yt-dlp       (anonymous, videos/images only)

Public API
──────────
    download_from_url(url: str, out_dir: Path) -> DownloadResult
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.logger import get_logger

log = get_logger(__name__)

# Top-level imports so they can be patched in tests.
# Both are in requirements.txt — will always be installed in production.
try:
    import instaloader
    import instaloader.exceptions as _il_exceptions
except ImportError:  # pragma: no cover
    instaloader = None  # type: ignore[assignment]
    _il_exceptions = None  # type: ignore[assignment]

try:
    import yt_dlp
except ImportError:  # pragma: no cover
    yt_dlp = None  # type: ignore[assignment]

# ── Constants ──────────────────────────────────────────────────────────────────

# Accepted URL patterns:  /p/  /reel/  /reels/  /tv/
_SHORTCODE_RE = re.compile(
    r"instagram\.com/(?:p|reel|reels|tv)/([A-Za-z0-9_\-]+)/?",
    re.IGNORECASE,
)

# Media priority when multiple files are present in the download folder
_MEDIA_PRIORITY = [".mp4", ".jpg", ".jpeg", ".png", ".webp"]

# Caption hard cap (Instagram allows 2 200 chars; we leave headroom for credit)
_CAPTION_MAX_CHARS = 2_000

# Path where instaloader session files are persisted between runs
_SESSIONS_DIR = Path("sessions")


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class DownloadResult:
    """Container for a successfully downloaded post."""

    shortcode: str
    media_path: Path          # absolute path to the primary media file on disk
    caption: str              # normalised caption (≤2 000 chars, no null bytes)
    source_user: str          # original poster's username (no @)
    media_type: str           # "IMAGE" or "VIDEO"
    is_carousel: bool         # True when the post contained multiple slides


# ── Exceptions ─────────────────────────────────────────────────────────────────

class DownloaderError(Exception):
    """Base exception — both instaloader and yt-dlp failed."""


class PostNotFoundError(DownloaderError):
    """The post is private, deleted, or the shortcode doesn't exist."""


class RateLimitedError(DownloaderError):
    """Instagram returned HTTP 429 or an equivalent rate-limit signal."""


# ── Public API ─────────────────────────────────────────────────────────────────

def download_from_url(url: str, out_dir: Path) -> DownloadResult:
    """
    Download an Instagram post given its URL and return a :class:`DownloadResult`.

    Tries instaloader first (authenticated, higher rate limits).
    Falls back to yt-dlp on any instaloader failure.

    Args:
        url:     Full Instagram post URL (``/p/``, ``/reel/``, ``/reels/``,
                 or ``/tv/`` paths accepted).
        out_dir: Root directory; media is saved under ``out_dir/<shortcode>/``.

    Returns:
        A :class:`DownloadResult` with the local file path and post metadata.

    Raises:
        ValueError:         URL is not a supported Instagram post link.
        PostNotFoundError:  Post is private, deleted, or shortcode is invalid.
        RateLimitedError:   Instagram is throttling requests.
        DownloaderError:    All download strategies exhausted.
    """
    shortcode = _extract_shortcode(url)
    dest = out_dir / shortcode
    dest.mkdir(parents=True, exist_ok=True)

    # ── Try instaloader ────────────────────────────────────────────────────────
    try:
        result = _download_with_instaloader(shortcode, dest)
        log.info("instaloader succeeded for %s", shortcode)
        return result
    except (PostNotFoundError, RateLimitedError):
        raise  # propagate typed errors immediately — no point trying yt-dlp
    except Exception as exc:
        log.warning(
            "instaloader failed for %s (%s: %s) — trying yt-dlp fallback",
            shortcode,
            type(exc).__name__,
            exc,
        )

    # ── Fallback: yt-dlp ──────────────────────────────────────────────────────
    try:
        result = _download_with_ytdlp(shortcode, dest)
        log.info("yt-dlp fallback succeeded for %s", shortcode)
        return result
    except (PostNotFoundError, RateLimitedError):
        raise
    except Exception as exc:
        raise DownloaderError(
            f"All download strategies failed for shortcode '{shortcode}': {exc}"
        ) from exc


# ── Shortcode extraction ───────────────────────────────────────────────────────

def _extract_shortcode(url: str) -> str:
    """
    Parse an Instagram post URL and return the shortcode portion.

    Accepted path types: ``/p/``, ``/reel/``, ``/reels/``, ``/tv/``.

    Args:
        url: Raw user-supplied string (URL or bare shortcode).

    Returns:
        The shortcode string (e.g. ``"CxYz123abcd"``).

    Raises:
        ValueError: If the URL cannot be parsed as a supported Instagram post.
    """
    # Bare shortcode (no slash or dot) — pass through directly
    if re.match(r"^[A-Za-z0-9_\-]+$", url.strip()):
        return url.strip()

    match = _SHORTCODE_RE.search(url)
    if not match:
        raise ValueError(
            f"Cannot extract a shortcode from '{url}'. "
            "Accepted URL formats: /p/<code>, /reel/<code>, /reels/<code>, /tv/<code>."
        )
    return match.group(1)


# ── Caption normalisation ──────────────────────────────────────────────────────

def _normalize_caption(raw: str) -> str:
    """
    Clean a raw caption string for safe storage and later publishing.

    Steps:
      1. Strip leading / trailing whitespace.
      2. Remove null bytes (\\x00) that can appear in scraped text.
      3. Truncate to :data:`_CAPTION_MAX_CHARS`.

    Args:
        raw: The unprocessed caption string.

    Returns:
        A clean string of at most :data:`_CAPTION_MAX_CHARS` characters.
    """
    caption = raw.strip().replace("\x00", "")
    if len(caption) > _CAPTION_MAX_CHARS:
        caption = caption[:_CAPTION_MAX_CHARS]
        log.debug("Caption truncated to %d chars.", _CAPTION_MAX_CHARS)
    return caption


# ── Primary media file selection ───────────────────────────────────────────────

def _pick_media_file(folder: Path) -> tuple[Path, bool]:
    """
    Choose the primary media file from a download folder.

    Priority order: ``.mp4`` → ``.jpg`` / ``.jpeg`` → ``.png`` → ``.webp``.

    Carousel detection: a post is considered a carousel when there are
    **multiple files sharing the same (winning) extension** in the folder.
    A single ``.mp4`` alongside a ``.jpg`` (e.g. thumbnail) is **not** a
    carousel — it's a single video post.

    Args:
        folder: Directory that instaloader or yt-dlp wrote into.

    Returns:
        ``(file_path, is_carousel)`` tuple.

    Raises:
        DownloaderError: If no usable media file is found.
    """
    # Walk priority list; stop at the first extension that has ≥1 file
    winning_files: list[Path] = []
    for ext in _MEDIA_PRIORITY:
        candidates = sorted(folder.glob(f"*{ext}"))
        if candidates:
            winning_files = candidates
            break

    if not winning_files:
        raise DownloaderError(
            f"No usable media file found in '{folder}'. "
            f"Contents: {list(folder.iterdir())}"
        )

    is_carousel = len(winning_files) > 1
    if is_carousel:
        log.warning(
            "Carousel detected (%d files) in '%s'. "
            "Using first file only — Phase 6 will add full carousel support.",
            len(winning_files),
            folder,
        )

    return winning_files[0], is_carousel


# ── instaloader implementation ─────────────────────────────────────────────────

def _get_instaloader_instance():
    """
    Build and return a configured :class:`instaloader.Instaloader` instance.

    Reads ``IG_LOGIN_USER`` / ``IG_LOGIN_PASS`` from the environment.
    Caches the session to ``./sessions/{user}.session`` so subsequent
    calls reuse the authenticated session without re-logging in.

    Returns:
        An authenticated :class:`instaloader.Instaloader` object.

    Raises:
        DownloaderError: If login fails and no cached session is available.
    """
    username = os.getenv("IG_LOGIN_USER", "").strip()
    password = os.getenv("IG_LOGIN_PASS", "").strip()

    IL = instaloader.Instaloader(
        download_videos=True,
        download_video_thumbnails=False,
        download_comments=False,
        save_metadata=False,
        post_metadata_txt_pattern="",
        compress_json=False,
        quiet=True,
    )

    if not username:
        log.warning("IG_LOGIN_USER not set — using instaloader anonymously (may hit rate limits).")
        return IL

    session_file = _SESSIONS_DIR / f"{username}.session"

    # Try loading cached session first
    if session_file.exists():
        try:
            IL.load_session_from_file(username, str(session_file))
            log.debug("Loaded instaloader session from %s", session_file)
            return IL
        except Exception as exc:
            log.warning("Could not load cached session (%s) — will re-login.", exc)

    # Fresh login
    if not password:
        log.warning("IG_LOGIN_PASS not set — cannot log in. Running anonymously.")
        return IL

    try:
        _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        IL.login(username, password)
        IL.save_session_to_file(str(session_file))
        log.info("Logged in to Instagram as @%s and cached session.", username)
    except instaloader.exceptions.BadCredentialsException as exc:  # type: ignore[union-attr]
        raise DownloaderError(f"Instagram login failed for @{username}: {exc}") from exc
    except Exception as exc:
        log.warning("Login failed (%s) — continuing anonymously.", exc)

    return IL


def _download_with_instaloader(shortcode: str, dest: Path) -> DownloadResult:
    """
    Download a post via instaloader.

    Args:
        shortcode: Instagram post shortcode.
        dest:      Directory to save media into (created if absent).

    Returns:
        :class:`DownloadResult` on success.

    Raises:
        PostNotFoundError:  Post is private or does not exist.
        RateLimitedError:   Rate limit detected.
        DownloaderError:    Other instaloader failure.
    """
    IL = _get_instaloader_instance()

    try:
        post = instaloader.Post.from_shortcode(IL.context, shortcode)  # type: ignore[union-attr]
    except instaloader.exceptions.InstaloaderException as exc:  # type: ignore[union-attr]
        msg = str(exc).lower()
        if "login required" in msg or "private" in msg or "not found" in msg:
            raise PostNotFoundError(
                f"Post '{shortcode}' is private, deleted, or requires login: {exc}"
            ) from exc
        if "429" in msg or "rate" in msg or "too many" in msg:
            raise RateLimitedError(f"Rate limited while fetching '{shortcode}': {exc}") from exc
        raise  # let the caller fall through to yt-dlp

    try:
        IL.download_post(post, target=str(dest))
    except instaloader.exceptions.InstaloaderException as exc:  # type: ignore[union-attr]
        msg = str(exc).lower()
        if "429" in msg or "rate" in msg or "too many" in msg:
            raise RateLimitedError(f"Rate limited while downloading '{shortcode}': {exc}") from exc
        raise

    media_path, is_carousel = _pick_media_file(dest)
    suffix = media_path.suffix.lower()
    media_type = "VIDEO" if suffix == ".mp4" else "IMAGE"

    raw_caption: str = post.caption or ""
    source_user: str = post.owner_username or ""

    return DownloadResult(
        shortcode=shortcode,
        media_path=media_path,
        caption=_normalize_caption(raw_caption),
        source_user=source_user,
        media_type=media_type,
        is_carousel=is_carousel,
    )


# ── yt-dlp fallback ────────────────────────────────────────────────────────────

def _download_with_ytdlp(shortcode: str, dest: Path) -> DownloadResult:
    """
    Download a post via yt-dlp (anonymous fallback).

    yt-dlp cannot reliably retrieve Instagram captions, so ``caption``
    is set to ``""`` and a warning is logged.

    Args:
        shortcode: Instagram post shortcode.
        dest:      Directory to save media into.

    Returns:
        :class:`DownloadResult` on success.

    Raises:
        PostNotFoundError:  Post is private or does not exist.
        RateLimitedError:   Rate limit detected.
        DownloaderError:    yt-dlp failure.
    """
    post_url = f"https://www.instagram.com/p/{shortcode}/"

    ydl_opts: dict = {
        "outtmpl": str(dest / "%(id)s.%(ext)s"),
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best[ext=jpg]/best",
        "quiet": True,
        "no_warnings": False,
        "ignoreerrors": False,
        # Merge mp4+audio into single file
        "merge_output_format": "mp4",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore[union-attr]
            info = ydl.extract_info(post_url, download=True)
    except yt_dlp.utils.DownloadError as exc:  # type: ignore[union-attr]
        msg = str(exc).lower()
        if "private" in msg or "login" in msg or "not found" in msg or "404" in msg:
            raise PostNotFoundError(
                f"yt-dlp: post '{shortcode}' is private, deleted, or not found: {exc}"
            ) from exc
        if "429" in msg or "rate" in msg or "too many" in msg:
            raise RateLimitedError(f"yt-dlp: rate limited on '{shortcode}': {exc}") from exc
        raise DownloaderError(f"yt-dlp download failed for '{shortcode}': {exc}") from exc

    media_path, is_carousel = _pick_media_file(dest)
    suffix = media_path.suffix.lower()
    media_type = "VIDEO" if suffix == ".mp4" else "IMAGE"

    # Extract uploader name from yt-dlp info dict (best-effort)
    source_user = ""
    if info:
        source_user = (
            info.get("uploader_id")
            or info.get("uploader")
            or ""
        ).lstrip("@")

    log.warning(
        "yt-dlp fallback used for '%s' — caption will be empty (IG captions "
        "are not reliably available without authentication).",
        shortcode,
    )

    return DownloadResult(
        shortcode=shortcode,
        media_path=media_path,
        caption="",
        source_user=source_user,
        media_type=media_type,
        is_carousel=is_carousel,
    )
