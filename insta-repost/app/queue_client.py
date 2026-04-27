"""
app/queue_client.py
───────────────────
Google Sheets-backed post queue for the Instagram repost bot.

Sheet schema (row 1 = headers, MUST match SHEET_COLUMNS exactly):
    shortcode | media_url | caption | source_user | media_type |
    status | post_id | created_at | posted_at | error

Status lifecycle:
    pending → ready → posted
                    ↘ error

Public API
──────────
    QueueRow  — dataclass representing one sheet row
    QueueClient — all queue operations
"""

from __future__ import annotations

import functools
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from app.logger import get_logger

log = get_logger(__name__)

# ── Schema ─────────────────────────────────────────────────────────────────────

SHEET_COLUMNS = [
    "shortcode",    # col A  (index 0)
    "media_url",    # col B
    "caption",      # col C
    "source_user",  # col D
    "media_type",   # col E
    "status",       # col F  (index 5)
    "post_id",      # col G  (index 6)
    "created_at",   # col H  (index 7)
    "posted_at",    # col I  (index 8)
    "error",        # col J  (index 9)
]

# 1-based column indices (for gspread range notation)
_COL = {name: idx + 1 for idx, name in enumerate(SHEET_COLUMNS)}

STATUS_PENDING = "pending"
STATUS_READY   = "ready"
STATUS_POSTED  = "posted"
STATUS_ERROR   = "error"

_GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# ── Exceptions ─────────────────────────────────────────────────────────────────

class QueueError(Exception):
    """Raised on Google Sheets API failures."""


class DuplicateError(QueueError):
    """Raised when attempting to queue an already-queued shortcode."""


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class QueueRow:
    """One data row from the MemeQueue Google Sheet."""

    row_index: int              # 1-based sheet row number (row 1 = headers)
    shortcode: str
    media_url: str
    caption: str
    source_user: str
    media_type: str             # "IMAGE" or "VIDEO"
    status: str                 # pending | ready | posted | error
    post_id: Optional[str]
    created_at: str
    posted_at: Optional[str]
    error: Optional[str]


# ── Retry decorator ────────────────────────────────────────────────────────────

def _with_retry(fn):
    """Decorator: retry *fn* once after a 2-second pause on gspread exceptions."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.GSpreadException as exc:
            log.warning(
                "%s failed (%s) — retrying in 2 s …", fn.__qualname__, exc
            )
            time.sleep(2)
            try:
                return fn(*args, **kwargs)
            except gspread.exceptions.GSpreadException as exc2:
                raise QueueError(
                    f"{fn.__qualname__} failed after retry: {exc2}"
                ) from exc2
    return wrapper


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now_utc() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_utc() -> str:
    """Return today's UTC date as ``YYYY-MM-DD``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _row_to_queuerow(row_index: int, cells: list[str]) -> QueueRow:
    """
    Convert a flat list of cell values (10 items, matching SHEET_COLUMNS)
    into a :class:`QueueRow`.  Missing trailing cells are treated as empty.
    """
    # Pad to full width so index access is always safe
    padded = (cells + [""] * len(SHEET_COLUMNS))[: len(SHEET_COLUMNS)]

    def _opt(v: str) -> Optional[str]:
        return v if v else None

    return QueueRow(
        row_index=row_index,
        shortcode=padded[0],
        media_url=padded[1],
        caption=padded[2],
        source_user=padded[3],
        media_type=padded[4],
        status=padded[5],
        post_id=_opt(padded[6]),
        created_at=padded[7],
        posted_at=_opt(padded[8]),
        error=_opt(padded[9]),
    )


# ── QueueClient ────────────────────────────────────────────────────────────────

class QueueClient:
    """
    Thin wrapper around a Google Sheet used as an append-only post queue.

    All public methods handle transient Sheets API errors with one automatic
    retry.  The client opens the sheet lazily on first use so that
    ``__init__`` itself never raises a network error.
    """

    def __init__(self, credentials_dict: dict, sheet_name: str) -> None:
        """
        Args:
            credentials_dict: Parsed service-account JSON key (the dict,
                              not a file path).
            sheet_name:       Exact name of the Google Sheet to open.
        """
        self._creds_dict  = credentials_dict
        self._sheet_name  = sheet_name
        self._worksheet: Optional[gspread.Worksheet] = None

    # ── Connection ─────────────────────────────────────────────────────────────

    def _ws(self) -> gspread.Worksheet:
        """Return the cached worksheet, opening it on first call."""
        if self._worksheet is None:
            self._worksheet = self._open_worksheet()
        return self._worksheet

    def _open_worksheet(self) -> gspread.Worksheet:
        """
        Authenticate with Google and open the first worksheet of the sheet.

        Raises:
            QueueError: If authentication or the sheet cannot be opened.
        """
        try:
            creds = Credentials.from_service_account_info(
                self._creds_dict, scopes=_GOOGLE_SCOPES
            )
            gc = gspread.authorize(creds)
            sh = gc.open(self._sheet_name)
            ws = sh.get_worksheet(0)
            log.info(
                "Opened Google Sheet '%s' (worksheet '%s')",
                self._sheet_name, ws.title,
            )
            return ws
        except gspread.exceptions.GSpreadException as exc:
            raise QueueError(f"Cannot open sheet '{self._sheet_name}': {exc}") from exc
        except Exception as exc:
            raise QueueError(
                f"Unexpected error opening sheet '{self._sheet_name}': {exc}"
            ) from exc

    # ── Public API ─────────────────────────────────────────────────────────────

    @_with_retry
    def append(
        self,
        shortcode: str,
        media_url: str,
        caption: str,
        source_user: str,
        media_type: str,
    ) -> int:
        """
        Append a new row with ``status='pending'`` and return its 1-based index.

        Args:
            shortcode:   Instagram post shortcode.
            media_url:   Public CDN URL of the uploaded media (may be empty
                         at append time, filled later).
            caption:     Normalised caption text.
            source_user: Original poster username (no @).
            media_type:  ``"IMAGE"`` or ``"VIDEO"``.

        Returns:
            The 1-based row index of the newly appended row.

        Raises:
            DuplicateError: A row with this shortcode already exists.
            QueueError:     On Sheets API error.
        """
        ws = self._ws()

        # Duplicate guard — search shortcode column only
        existing = ws.col_values(1)   # col A, 1-indexed
        # existing[0] is the header; skip it
        if shortcode in existing[1:]:
            raise DuplicateError(
                f"Shortcode '{shortcode}' is already in the queue."
            )

        row = [
            shortcode,
            media_url,
            caption,
            source_user,
            media_type,
            STATUS_PENDING,  # status
            "",              # post_id
            _now_utc(),      # created_at
            "",              # posted_at
            "",              # error
        ]
        result = ws.append_row(row, value_input_option="RAW")

        # gspread returns the updated range, e.g. "Sheet1!A5:J5"
        # Parse out the row number from that string
        try:
            updated_range: str = result["updates"]["updatedRange"]
            # e.g. 'MemeQueue!A5:J5'  →  row 5
            row_index = int(updated_range.split("!")[1].split(":")[0].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
        except Exception:
            # Fallback: count all rows (header + data)
            row_index = len(ws.col_values(1))

        log.info("Appended shortcode '%s' at row %d (status=pending)", shortcode, row_index)
        return row_index

    @_with_retry
    def get_next_ready(self) -> Optional[QueueRow]:
        """
        Return the oldest row with ``status='ready'``, or ``None``.

        Uses ``batch_get`` to fetch only the status column and the full
        row data in two API calls, avoiding pulling the whole sheet each time.

        Returns:
            A :class:`QueueRow` or ``None`` if nothing is ready.

        Raises:
            QueueError: On Sheets API error.
        """
        ws = self._ws()

        # Fetch status column to find the first ready row index
        status_values = ws.col_values(_COL["status"])   # col F, 1-indexed
        if not status_values:
            return None

        ready_row_index: Optional[int] = None
        for idx, val in enumerate(status_values):
            if idx == 0:
                continue   # skip header row
            if val.strip().lower() == STATUS_READY:
                ready_row_index = idx + 1   # convert to 1-based
                break

        if ready_row_index is None:
            return None

        # Fetch that entire row efficiently
        row_range = f"A{ready_row_index}:J{ready_row_index}"
        raw = ws.batch_get([row_range])
        # batch_get returns list[list[list[str]]] — first range, first row
        cells: list[str] = raw[0][0] if raw and raw[0] else []

        return _row_to_queuerow(ready_row_index, cells)

    @_with_retry
    def get_all(self, status: Optional[str] = None) -> list[QueueRow]:
        """
        Return all data rows, optionally filtered by ``status``.

        Args:
            status: If given, only rows whose status equals this value
                    are returned.  Pass ``None`` for all rows.

        Returns:
            List of :class:`QueueRow` ordered by row index (oldest first).

        Raises:
            QueueError: On Sheets API error.
        """
        ws = self._ws()
        all_rows = ws.get_all_values()   # list[list[str]], row 0 = headers
        if not all_rows:
            return []

        result: list[QueueRow] = []
        for sheet_row_idx, cells in enumerate(all_rows):
            if sheet_row_idx == 0:
                continue   # skip header
            row = _row_to_queuerow(sheet_row_idx + 1, cells)
            if status is None or row.status == status:
                result.append(row)

        return result

    @_with_retry
    def mark_posted(self, row_index: int, post_id: str) -> None:
        """
        Set ``status='posted'``, write ``post_id`` and ``posted_at=now``.

        Args:
            row_index: 1-based row number returned by :meth:`append`.
            post_id:   Instagram post ID from Graph API publish response.

        Raises:
            QueueError: On Sheets API error.
        """
        ws = self._ws()
        now = _now_utc()
        ws.batch_update([
            {
                "range": _cell(row_index, "status"),
                "values": [[STATUS_POSTED]],
            },
            {
                "range": _cell(row_index, "post_id"),
                "values": [[post_id]],
            },
            {
                "range": _cell(row_index, "posted_at"),
                "values": [[now]],
            },
        ])
        log.info("Row %d marked as posted (post_id=%s, posted_at=%s)", row_index, post_id, now)

    @_with_retry
    def mark_error(self, row_index: int, error_msg: str) -> None:
        """
        Set ``status='error'`` and record the error message.

        Args:
            row_index: 1-based row number.
            error_msg: Human-readable description of what went wrong.

        Raises:
            QueueError: On Sheets API error.
        """
        ws = self._ws()
        # Truncate to avoid hitting Sheets cell character limit (50 000)
        truncated = error_msg[:500]
        ws.batch_update([
            {
                "range": _cell(row_index, "status"),
                "values": [[STATUS_ERROR]],
            },
            {
                "range": _cell(row_index, "error"),
                "values": [[truncated]],
            },
        ])
        log.warning("Row %d marked as error: %s", row_index, truncated)

    @_with_retry
    def update_status(self, row_index: int, status: str) -> None:
        """
        Update only the ``status`` cell of a row.

        Args:
            row_index: 1-based row number.
            status:    New status string (use the STATUS_* constants).

        Raises:
            QueueError: On Sheets API error.
        """
        ws = self._ws()
        ws.update(_cell(row_index, "status"), [[status]])
        log.debug("Row %d status → %s", row_index, status)

    @_with_retry
    def count_today(self, status: str = STATUS_POSTED) -> int:
        """
        Count rows where ``status`` matches and ``posted_at`` is today (UTC).

        Used by the scheduler to enforce ``MAX_POSTS_PER_DAY``.

        Args:
            status: Status value to count (default: ``'posted'``).

        Returns:
            Integer count of matching rows for today.

        Raises:
            QueueError: On Sheets API error.
        """
        ws = self._ws()
        today = _today_utc()

        # Fetch only the two columns we need: status (F) and posted_at (I)
        status_col  = ws.col_values(_COL["status"])    # col F
        posted_col  = ws.col_values(_COL["posted_at"]) # col I

        count = 0
        # Both columns may differ in length if trailing cells are empty
        max_len = max(len(status_col), len(posted_col))
        for i in range(1, max_len):   # skip row 0 (header)
            row_status    = status_col[i]  if i < len(status_col)  else ""
            row_posted_at = posted_col[i]  if i < len(posted_col)  else ""
            if row_status == status and row_posted_at.startswith(today):
                count += 1

        return count


# ── Column address helper ──────────────────────────────────────────────────────

def _cell(row_index: int, col_name: str) -> str:
    """
    Return an A1-notation cell address for a given 1-based row and column name.

    Example: ``_cell(5, "status")`` → ``"F5"``

    Args:
        row_index: 1-based row number.
        col_name:  One of the :data:`SHEET_COLUMNS` names.

    Returns:
        A1-notation string such as ``"F5"``.

    Raises:
        KeyError: If *col_name* is not a recognised column.
    """
    col_letter = _col_letter(_COL[col_name])
    return f"{col_letter}{row_index}"


def _col_letter(col_num: int) -> str:
    """
    Convert a 1-based column number to an Excel-style column letter.

    Supports up to column Z (26).  Sufficient for our 10-column sheet.

    Args:
        col_num: 1-based column number (1 = A, 26 = Z).

    Returns:
        Single uppercase letter string.
    """
    return chr(ord("A") + col_num - 1)
