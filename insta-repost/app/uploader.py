"""
app/uploader.py
───────────────
Uploads local media files to a public file host and returns a URL
that the Instagram Graph API can access.

Primary host  : Catbox.moe  (https://catbox.moe/user/api.php)
Fallback host : 0x0.st      (https://0x0.st)

Both are free, anonymous, and require no API key.
Files persisted by Catbox are permanent; 0x0.st expires after ~1 year.

Public API
──────────
    upload_to_catbox(file_path: Path, retries: int = 3) -> str
    upload_with_fallback(file_path: Path) -> str
"""

from __future__ import annotations

import time
from pathlib import Path

import requests

from app.logger import get_logger

log = get_logger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

CATBOX_API_URL  = "https://catbox.moe/user/api.php"
ZX0_API_URL     = "https://0x0.st"

MAX_FILE_SIZE_BYTES = 200 * 1024 * 1024   # 200 MB — Instagram's practical limit

# Exponential-backoff base in seconds (doubles each retry: 2 → 4 → 8 …)
_BACKOFF_BASE = 2

# Timeout for upload POST (seconds); large files need generous timeouts
_UPLOAD_TIMEOUT = 120

# Timeout for the reachability HEAD check
_HEAD_TIMEOUT = 10


# ── Exceptions ─────────────────────────────────────────────────────────────────

class UploadError(Exception):
    """Raised when all upload attempts are exhausted or the host returns an error."""


class FileTooLargeError(UploadError):
    """Raised immediately when the file exceeds the 200 MB limit."""


# ── Public API ─────────────────────────────────────────────────────────────────

def upload_to_catbox(file_path: Path, retries: int = 3) -> str:
    """
    Upload a local file to Catbox.moe and return its permanent public HTTPS URL.

    Uses exponential backoff on network errors or non-200 responses.
    After uploading, performs a HEAD request to confirm the URL is reachable
    (the Instagram Graph API rejects URLs that return non-200 on HEAD).

    Args:
        file_path: Path to the local media file.
        retries:   Number of upload attempts before giving up (default: 3).

    Returns:
        A ``https://files.catbox.moe/<hash>.<ext>`` URL string.

    Raises:
        FileNotFoundError: *file_path* does not exist.
        FileTooLargeError: File is larger than 200 MB.
        UploadError:       All retry attempts failed or URL is unreachable.
    """
    path = Path(file_path).resolve()
    _validate_file(path)

    log.info("Uploading '%s' (%.2f MB) to Catbox …", path.name, path.stat().st_size / 1_048_576)
    t0 = time.monotonic()

    last_exc: Exception = UploadError("No attempts made")

    for attempt in range(1, retries + 1):
        try:
            with path.open("rb") as fh:
                resp = requests.post(
                    CATBOX_API_URL,
                    data={"reqtype": "fileupload"},
                    files={"fileToUpload": (path.name, fh)},
                    timeout=_UPLOAD_TIMEOUT,
                )

            if resp.status_code != 200:
                raise UploadError(
                    f"Catbox returned HTTP {resp.status_code}: {resp.text[:200]}"
                )

            url = resp.text.strip()
            _validate_url(url, host_label="Catbox")

            elapsed = time.monotonic() - t0
            log.info("Catbox upload complete in %.1fs → %s", elapsed, url)
            return url

        except (UploadError, requests.RequestException) as exc:
            last_exc = exc
            if attempt < retries:
                wait = _BACKOFF_BASE ** attempt          # 2s, 4s, 8s …
                log.warning(
                    "Catbox upload attempt %d/%d failed (%s) — retrying in %ds …",
                    attempt, retries, exc, wait,
                )
                time.sleep(wait)
            else:
                log.warning(
                    "Catbox upload attempt %d/%d failed (%s) — giving up.",
                    attempt, retries, exc,
                )

    raise UploadError(f"Catbox upload failed after {retries} attempts: {last_exc}") from last_exc


def upload_with_fallback(file_path: Path) -> str:
    """
    Upload to Catbox; fall back to 0x0.st if Catbox fails.

    Args:
        file_path: Path to the local media file.

    Returns:
        A public HTTPS URL string from whichever host succeeded.

    Raises:
        FileNotFoundError: *file_path* does not exist.
        FileTooLargeError: File is larger than 200 MB.
        UploadError:       Both Catbox and 0x0.st failed.
    """
    path = Path(file_path).resolve()
    _validate_file(path)   # fail fast before any network attempt

    try:
        return upload_to_catbox(path)
    except UploadError as catbox_exc:
        log.warning(
            "Catbox failed (%s) — attempting 0x0.st fallback …", catbox_exc
        )

    return _upload_to_0x0(path)


# ── Internal helpers ───────────────────────────────────────────────────────────

def _validate_file(path: Path) -> None:
    """
    Assert the file exists and is within the upload size limit.

    Args:
        path: Resolved :class:`pathlib.Path` to the local file.

    Raises:
        FileNotFoundError: File does not exist.
        FileTooLargeError: File exceeds :data:`MAX_FILE_SIZE_BYTES`.
    """
    if not path.exists():
        raise FileNotFoundError(f"Media file not found: '{path}'")

    size = path.stat().st_size
    if size > MAX_FILE_SIZE_BYTES:
        raise FileTooLargeError(
            f"File '{path.name}' is {size / 1_048_576:.1f} MB, "
            f"which exceeds the 200 MB limit."
        )


def _validate_url(url: str, host_label: str = "host") -> None:
    """
    Confirm the returned URL looks valid and responds to a HEAD request.

    Args:
        url:        The URL string returned by the file host.
        host_label: Human-readable name used in log/error messages.

    Raises:
        UploadError: URL doesn't start with ``https://`` or HEAD returns non-200.
    """
    if not url.startswith("https://"):
        raise UploadError(
            f"{host_label} returned an unexpected response (not an HTTPS URL): {url!r}"
        )

    try:
        head = requests.head(url, timeout=_HEAD_TIMEOUT, allow_redirects=True)
    except requests.RequestException as exc:
        raise UploadError(
            f"Uploaded URL '{url}' is unreachable (HEAD failed: {exc})"
        ) from exc

    if head.status_code >= 400:
        raise UploadError(
            f"Uploaded URL '{url}' returned HTTP {head.status_code} on HEAD check. "
            "Instagram Graph API requires a reachable URL."
        )


def _upload_to_0x0(path: Path, retries: int = 3) -> str:
    """
    Upload a file to 0x0.st as a fallback.

    Args:
        path:    Resolved path to the local file.
        retries: Number of attempts before raising.

    Returns:
        A public HTTPS URL string from 0x0.st.

    Raises:
        UploadError: All attempts failed.
    """
    log.info("Uploading '%s' to 0x0.st …", path.name)
    t0 = time.monotonic()

    last_exc: Exception = UploadError("No attempts made")

    for attempt in range(1, retries + 1):
        try:
            with path.open("rb") as fh:
                resp = requests.post(
                    ZX0_API_URL,
                    files={"file": (path.name, fh)},
                    timeout=_UPLOAD_TIMEOUT,
                )

            if resp.status_code != 200:
                raise UploadError(
                    f"0x0.st returned HTTP {resp.status_code}: {resp.text[:200]}"
                )

            url = resp.text.strip()
            _validate_url(url, host_label="0x0.st")

            elapsed = time.monotonic() - t0
            log.info("0x0.st upload complete in %.1fs → %s", elapsed, url)
            return url

        except (UploadError, requests.RequestException) as exc:
            last_exc = exc
            if attempt < retries:
                wait = _BACKOFF_BASE ** attempt
                log.warning(
                    "0x0.st upload attempt %d/%d failed (%s) — retrying in %ds …",
                    attempt, retries, exc, wait,
                )
                time.sleep(wait)
            else:
                log.warning(
                    "0x0.st upload attempt %d/%d failed (%s) — giving up.",
                    attempt, retries, exc,
                )

    raise UploadError(
        f"0x0.st upload failed after {retries} attempts: {last_exc}"
    ) from last_exc
