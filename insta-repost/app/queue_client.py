"""
app/queue_client.py
───────────────────
Thin wrapper around a Google Sheet that acts as the post queue / audit log.

Sheet schema (row 1 = headers):
    shortcode | media_url | caption | source_user | media_type |
    status | post_id | created_at | posted_at | error

Status lifecycle:
    pending  → downloading → uploading → publishing → done
                                                     ↘ failed

Public API
──────────
    add_to_queue(entry: QueueEntry, cfg: Config) -> None
    get_pending(cfg: Config) -> List[QueueEntry]
    update_status(shortcode: str, status: str, cfg: Config, **kwargs) -> None
    is_duplicate(shortcode: str, cfg: Config) -> bool
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from app.config import Config

SHEET_COLUMNS = [
    "shortcode",
    "media_url",
    "caption",
    "source_user",
    "media_type",
    "status",
    "post_id",
    "created_at",
    "posted_at",
    "error",
]

STATUS_PENDING      = "pending"
STATUS_DOWNLOADING  = "downloading"
STATUS_UPLOADING    = "uploading"
STATUS_PUBLISHING   = "publishing"
STATUS_DONE         = "done"
STATUS_FAILED       = "failed"


@dataclass
class QueueEntry:
    """One row in the MemeQueue Google Sheet."""

    shortcode: str
    media_url: str = ""
    caption: str = ""
    source_user: str = ""
    media_type: str = ""          # IMAGE | VIDEO | CAROUSEL_ALBUM
    status: str = STATUS_PENDING
    post_id: str = ""
    created_at: str = ""
    posted_at: str = ""
    error: str = ""


def get_sheet(cfg: Config):
    """
    Open and return the gspread Worksheet object for the queue.

    Args:
        cfg: Application config holding Google credentials and sheet name.

    Returns:
        A ``gspread.Worksheet`` instance.

    Raises:
        QueueError: If authentication or sheet access fails.
        NotImplementedError: Until Phase 2 is implemented.
    """
    raise NotImplementedError("Phase 2")


def add_to_queue(entry: QueueEntry, cfg: Config) -> None:
    """
    Append a new row to the MemeQueue sheet.

    Args:
        entry: The queue entry to persist.
        cfg:   Application config.

    Raises:
        DuplicateError: If *entry.shortcode* is already in the sheet.
        QueueError:     On any Sheets API error.
        NotImplementedError: Until Phase 2 is implemented.
    """
    raise NotImplementedError("Phase 2")


def get_pending(cfg: Config) -> List[QueueEntry]:
    """
    Return all rows whose status is ``'pending'``, oldest first.

    Args:
        cfg: Application config.

    Returns:
        Ordered list of :class:`QueueEntry` objects ready for processing.

    Raises:
        QueueError: On any Sheets API error.
        NotImplementedError: Until Phase 2 is implemented.
    """
    raise NotImplementedError("Phase 2")


def update_status(
    shortcode: str,
    status: str,
    cfg: Config,
    *,
    post_id: str = "",
    posted_at: str = "",
    error: str = "",
    media_url: str = "",
) -> None:
    """
    Update the status (and optional fields) of a row identified by *shortcode*.

    Args:
        shortcode: The post shortcode to look up.
        status:    New status string (use the STATUS_* constants).
        cfg:       Application config.
        post_id:   IG post ID returned by Graph API after publishing.
        posted_at: ISO timestamp of when the post went live.
        error:     Error message if status is ``'failed'``.
        media_url: Catbox CDN URL once uploaded.

    Raises:
        QueueError:   If the shortcode row is not found or API error.
        NotImplementedError: Until Phase 2 is implemented.
    """
    raise NotImplementedError("Phase 2")


def is_duplicate(shortcode: str, cfg: Config) -> bool:
    """
    Return True if *shortcode* already exists anywhere in the sheet.

    Args:
        shortcode: The Instagram post shortcode to check.
        cfg:       Application config.

    Returns:
        ``True`` if a row with this shortcode already exists.

    Raises:
        QueueError: On any Sheets API error.
        NotImplementedError: Until Phase 2 is implemented.
    """
    raise NotImplementedError("Phase 2")


class QueueError(Exception):
    """Raised on Google Sheets API failures."""


class DuplicateError(QueueError):
    """Raised when attempting to queue an already-queued shortcode."""
