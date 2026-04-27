"""
tests/test_queue_client.py
──────────────────────────
Unit tests for app/queue_client.py.

All gspread / Google API calls are mocked — no real network traffic.
Run with:  pytest tests/test_queue_client.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from unittest.mock import MagicMock, call, patch

import pytest
import gspread

from app.queue_client import (
    STATUS_ERROR,
    STATUS_PENDING,
    STATUS_POSTED,
    STATUS_READY,
    DuplicateError,
    QueueClient,
    QueueError,
    QueueRow,
    _cell,
    _col_letter,
    _row_to_queuerow,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures & helpers
# ═══════════════════════════════════════════════════════════════════════════════

FAKE_CREDS = {
    "type": "service_account",
    "project_id": "test-project",
    "private_key_id": "key-id",
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n",
    "client_email": "bot@test-project.iam.gserviceaccount.com",
    "client_id": "123",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
}
SHEET_NAME = "MemeQueue"


def _make_client(ws_mock: MagicMock) -> QueueClient:
    """Return a QueueClient that skips real auth and uses *ws_mock* directly."""
    client = QueueClient(FAKE_CREDS, SHEET_NAME)
    client._worksheet = ws_mock     # inject mock worksheet — bypasses _open_worksheet
    return client


def _ws() -> MagicMock:
    """Build a fresh worksheet mock with sensible defaults."""
    ws = MagicMock(spec=gspread.Worksheet)
    ws.title = "Sheet1"
    return ws


def _make_row(
    shortcode: str = "ABC123",
    media_url: str = "https://files.catbox.moe/a.jpg",
    caption: str   = "funny meme",
    source_user: str = "memegod",
    media_type: str  = "IMAGE",
    status: str      = STATUS_PENDING,
    post_id: str     = "",
    created_at: str  = "2026-04-27T10:00:00Z",
    posted_at: str   = "",
    error: str       = "",
) -> list[str]:
    return [
        shortcode, media_url, caption, source_user, media_type,
        status, post_id, created_at, posted_at, error,
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# 1.  Helpers
# ═══════════════════════════════════════════════════════════════════════════════

class TestHelpers:

    def test_col_letter(self):
        assert _col_letter(1)  == "A"
        assert _col_letter(6)  == "F"
        assert _col_letter(10) == "J"
        assert _col_letter(26) == "Z"

    def test_cell_shortcode(self):
        assert _cell(1,  "shortcode") == "A1"
        assert _cell(5,  "status")    == "F5"
        assert _cell(10, "error")     == "J10"
        assert _cell(3,  "post_id")   == "G3"
        assert _cell(7,  "posted_at") == "I7"

    def test_row_to_queuerow_full(self):
        cells = _make_row(
            shortcode="XYZ",
            status=STATUS_READY,
            post_id="",
            posted_at="",
            error="",
        )
        row = _row_to_queuerow(5, cells)
        assert row.row_index   == 5
        assert row.shortcode   == "XYZ"
        assert row.status      == STATUS_READY
        assert row.post_id     is None
        assert row.posted_at   is None
        assert row.error       is None

    def test_row_to_queuerow_with_optional_fields(self):
        cells = _make_row(
            post_id="17855649574791001",
            posted_at="2026-04-27T12:00:00Z",
            error="",
        )
        row = _row_to_queuerow(2, cells)
        assert row.post_id   == "17855649574791001"
        assert row.posted_at == "2026-04-27T12:00:00Z"
        assert row.error     is None

    def test_row_to_queuerow_short_cells_padded(self):
        """Fewer than 10 cells must not raise."""
        row = _row_to_queuerow(3, ["code", "url"])
        assert row.shortcode == "code"
        assert row.media_url == "url"
        assert row.caption   == ""
        assert row.error     is None

    def test_row_to_queuerow_error_filled(self):
        cells = _make_row(status=STATUS_ERROR, error="network timeout")
        row = _row_to_queuerow(9, cells)
        assert row.status == STATUS_ERROR
        assert row.error  == "network timeout"


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  append()
# ═══════════════════════════════════════════════════════════════════════════════

class TestAppend:

    def test_appends_row_and_returns_index(self):
        ws = _ws()
        ws.col_values.return_value = ["shortcode"]   # header only → no duplicates
        ws.append_row.return_value = {
            "updates": {"updatedRange": "MemeQueue!A2:J2"}
        }
        client = _make_client(ws)

        idx = client.append(
            shortcode="ABC123",
            media_url="https://files.catbox.moe/a.jpg",
            caption="Test caption",
            source_user="testuser",
            media_type="IMAGE",
        )

        assert idx == 2
        ws.append_row.assert_called_once()
        row_written = ws.append_row.call_args[0][0]
        assert row_written[0] == "ABC123"
        assert row_written[5] == STATUS_PENDING

    def test_sets_created_at_to_utc_iso(self):
        ws = _ws()
        ws.col_values.return_value = ["shortcode"]
        ws.append_row.return_value = {
            "updates": {"updatedRange": "MemeQueue!A3:J3"}
        }
        client = _make_client(ws)
        client.append("SC1", "url", "cap", "user", "IMAGE")

        row_written = ws.append_row.call_args[0][0]
        created_at = row_written[7]   # index 7 = created_at
        # Should be a valid ISO datetime ending in Z
        assert "T" in created_at and created_at.endswith("Z")

    def test_status_is_pending(self):
        ws = _ws()
        ws.col_values.return_value = ["shortcode"]
        ws.append_row.return_value = {"updates": {"updatedRange": "MemeQueue!A4:J4"}}
        client = _make_client(ws)
        client.append("SC2", "", "", "", "VIDEO")

        row_written = ws.append_row.call_args[0][0]
        assert row_written[5] == STATUS_PENDING

    def test_raises_duplicate_if_shortcode_exists(self):
        ws = _ws()
        ws.col_values.return_value = ["shortcode", "ALREADY_HERE", "OTHER"]
        client = _make_client(ws)

        with pytest.raises(DuplicateError, match="ALREADY_HERE"):
            client.append("ALREADY_HERE", "url", "cap", "user", "IMAGE")

        ws.append_row.assert_not_called()

    def test_trailing_empty_fields_in_row(self):
        ws = _ws()
        ws.col_values.return_value = ["shortcode"]
        ws.append_row.return_value = {"updates": {"updatedRange": "MemeQueue!A2:J2"}}
        client = _make_client(ws)
        client.append("SC3", "url", "cap", "user", "IMAGE")

        row = ws.append_row.call_args[0][0]
        assert row[6] == ""   # post_id
        assert row[8] == ""   # posted_at
        assert row[9] == ""   # error

    def test_fallback_row_index_when_range_unparseable(self):
        ws = _ws()
        ws.col_values.return_value = ["shortcode", "PREV"]
        ws.append_row.return_value = {"updates": {"updatedRange": "BADFORMAT"}}
        # Fallback uses len(col_values); col_values is called twice:
        # once for duplicate check (col A), once for fallback count.
        # We need to make the second call return a sensible list.
        ws.col_values.side_effect = [
            ["shortcode", "PREV"],    # first call: duplicate check
            ["shortcode", "PREV", "SC4"],  # second call: fallback count
        ]
        client = _make_client(ws)

        idx = client.append("SC4", "url", "cap", "user", "VIDEO")
        assert idx == 3   # len(["shortcode", "PREV", "SC4"])

    def test_gspread_error_retries_then_raises(self):
        ws = _ws()
        ws.col_values.side_effect = gspread.exceptions.GSpreadException("quota exceeded")
        client = _make_client(ws)

        with patch("app.queue_client.time.sleep"):
            with pytest.raises(QueueError, match="failed after retry"):
                client.append("SC5", "", "", "", "IMAGE")


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  get_next_ready()
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetNextReady:

    def test_returns_none_when_sheet_is_empty(self):
        ws = _ws()
        ws.col_values.return_value = []
        client = _make_client(ws)
        assert client.get_next_ready() is None

    def test_returns_none_when_only_header(self):
        ws = _ws()
        ws.col_values.return_value = ["status"]
        client = _make_client(ws)
        assert client.get_next_ready() is None

    def test_returns_none_when_no_ready_rows(self):
        ws = _ws()
        ws.col_values.return_value = ["status", STATUS_PENDING, STATUS_POSTED]
        client = _make_client(ws)
        assert client.get_next_ready() is None

    def test_returns_oldest_ready_row(self):
        ws = _ws()
        ws.col_values.return_value = [
            "status",        # row 1 (header)
            STATUS_PENDING,  # row 2
            STATUS_READY,    # row 3  ← first ready
            STATUS_READY,    # row 4
        ]
        ready_cells = _make_row(shortcode="READY1", status=STATUS_READY)
        ws.batch_get.return_value = [[ready_cells]]

        client = _make_client(ws)
        result = client.get_next_ready()

        assert result is not None
        assert result.row_index == 3
        assert result.shortcode == "READY1"
        # Verify batch_get was called with the correct range
        ws.batch_get.assert_called_once_with(["A3:J3"])

    def test_skips_pending_and_posted_rows(self):
        ws = _ws()
        ws.col_values.return_value = [
            "status",
            STATUS_PENDING,
            STATUS_POSTED,
            STATUS_ERROR,
            STATUS_READY,    # row 5
        ]
        ws.batch_get.return_value = [[_make_row(shortcode="DEEP", status=STATUS_READY)]]
        client = _make_client(ws)

        result = client.get_next_ready()
        assert result.row_index == 5
        ws.batch_get.assert_called_once_with(["A5:J5"])

    def test_handles_empty_batch_get_response(self):
        ws = _ws()
        ws.col_values.return_value = ["status", STATUS_READY]
        ws.batch_get.return_value = [[]]   # empty inner list
        client = _make_client(ws)

        result = client.get_next_ready()
        assert result is not None
        assert result.shortcode == ""   # padded empty row


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  get_all()
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetAll:

    def test_returns_empty_list_for_empty_sheet(self):
        ws = _ws()
        ws.get_all_values.return_value = []
        client = _make_client(ws)
        assert client.get_all() == []

    def test_returns_all_data_rows_skipping_header(self):
        ws = _ws()
        ws.get_all_values.return_value = [
            ["shortcode"] + [""] * 9,        # header
            _make_row(shortcode="R1"),
            _make_row(shortcode="R2"),
        ]
        client = _make_client(ws)
        rows = client.get_all()

        assert len(rows) == 2
        assert rows[0].shortcode == "R1"
        assert rows[0].row_index == 2
        assert rows[1].shortcode == "R2"
        assert rows[1].row_index == 3

    def test_filters_by_status(self):
        ws = _ws()
        ws.get_all_values.return_value = [
            ["shortcode"] + [""] * 9,
            _make_row(shortcode="PEND", status=STATUS_PENDING),
            _make_row(shortcode="DONE", status=STATUS_POSTED),
            _make_row(shortcode="PEND2", status=STATUS_PENDING),
        ]
        client = _make_client(ws)

        pending = client.get_all(status=STATUS_PENDING)
        assert len(pending) == 2
        assert all(r.status == STATUS_PENDING for r in pending)

        posted = client.get_all(status=STATUS_POSTED)
        assert len(posted) == 1
        assert posted[0].shortcode == "DONE"

    def test_returns_all_when_status_none(self):
        ws = _ws()
        ws.get_all_values.return_value = [
            ["shortcode"] + [""] * 9,
            _make_row(shortcode="A", status=STATUS_PENDING),
            _make_row(shortcode="B", status=STATUS_POSTED),
            _make_row(shortcode="C", status=STATUS_ERROR),
        ]
        client = _make_client(ws)
        rows = client.get_all(status=None)
        assert len(rows) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# 5.  mark_posted()
# ═══════════════════════════════════════════════════════════════════════════════

class TestMarkPosted:

    def test_updates_status_post_id_and_posted_at(self):
        ws = _ws()
        client = _make_client(ws)

        client.mark_posted(row_index=5, post_id="17855649574791001")

        ws.batch_update.assert_called_once()
        updates = ws.batch_update.call_args[0][0]

        ranges = {u["range"]: u["values"][0][0] for u in updates}
        assert ranges["F5"] == STATUS_POSTED
        assert ranges["G5"] == "17855649574791001"
        # posted_at should be a UTC ISO timestamp
        assert "T" in ranges["I5"]
        assert ranges["I5"].endswith("Z")

    def test_correct_row_addressed(self):
        ws = _ws()
        client = _make_client(ws)
        client.mark_posted(row_index=12, post_id="PID")

        updates = ws.batch_update.call_args[0][0]
        ranges_updated = [u["range"] for u in updates]
        assert "F12" in ranges_updated
        assert "G12" in ranges_updated
        assert "I12" in ranges_updated

    def test_gspread_error_retries_once(self):
        ws = _ws()
        ws.batch_update.side_effect = [
            gspread.exceptions.GSpreadException("rate limit"),
            None,
        ]
        client = _make_client(ws)

        with patch("app.queue_client.time.sleep") as mock_sleep:
            client.mark_posted(3, "PID")
            mock_sleep.assert_called_once_with(2)

        assert ws.batch_update.call_count == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 6.  mark_error()
# ═══════════════════════════════════════════════════════════════════════════════

class TestMarkError:

    def test_updates_status_and_error_columns(self):
        ws = _ws()
        client = _make_client(ws)
        client.mark_error(row_index=7, error_msg="instaloader failed")

        updates = ws.batch_update.call_args[0][0]
        ranges = {u["range"]: u["values"][0][0] for u in updates}
        assert ranges["F7"] == STATUS_ERROR
        assert ranges["J7"] == "instaloader failed"

    def test_truncates_long_error_messages(self):
        ws = _ws()
        client = _make_client(ws)
        long_error = "x" * 1000
        client.mark_error(2, long_error)

        updates = ws.batch_update.call_args[0][0]
        error_cell = next(u for u in updates if u["range"] == "J2")
        assert len(error_cell["values"][0][0]) == 500

    def test_correct_row_addressed(self):
        ws = _ws()
        client = _make_client(ws)
        client.mark_error(row_index=99, error_msg="err")

        updates = ws.batch_update.call_args[0][0]
        ranges_updated = [u["range"] for u in updates]
        assert "F99" in ranges_updated
        assert "J99" in ranges_updated


# ═══════════════════════════════════════════════════════════════════════════════
# 7.  update_status()
# ═══════════════════════════════════════════════════════════════════════════════

class TestUpdateStatus:

    def test_writes_correct_cell(self):
        ws = _ws()
        client = _make_client(ws)
        client.update_status(row_index=4, status=STATUS_READY)

        ws.update.assert_called_once_with("F4", [[STATUS_READY]])

    def test_update_status_pending(self):
        ws = _ws()
        client = _make_client(ws)
        client.update_status(8, STATUS_PENDING)
        ws.update.assert_called_once_with("F8", [[STATUS_PENDING]])


# ═══════════════════════════════════════════════════════════════════════════════
# 8.  count_today()
# ═══════════════════════════════════════════════════════════════════════════════

class TestCountToday:

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def test_counts_posted_today(self):
        ws = _ws()
        today = self._today()
        ws.col_values.side_effect = [
            # status column (col F)
            ["status", STATUS_POSTED, STATUS_POSTED, STATUS_PENDING, STATUS_POSTED],
            # posted_at column (col I)
            ["posted_at", f"{today}T10:00:00Z", f"{today}T11:00:00Z", "", "2020-01-01T00:00:00Z"],
        ]
        client = _make_client(ws)
        assert client.count_today(STATUS_POSTED) == 2

    def test_excludes_other_days(self):
        ws = _ws()
        today = self._today()
        ws.col_values.side_effect = [
            ["status", STATUS_POSTED, STATUS_POSTED],
            ["posted_at", f"{today}T09:00:00Z", "2020-06-01T09:00:00Z"],
        ]
        client = _make_client(ws)
        assert client.count_today() == 1

    def test_returns_zero_for_empty_sheet(self):
        ws = _ws()
        ws.col_values.side_effect = [["status"], ["posted_at"]]
        client = _make_client(ws)
        assert client.count_today() == 0

    def test_counts_other_status(self):
        ws = _ws()
        today = self._today()
        ws.col_values.side_effect = [
            ["status", STATUS_ERROR, STATUS_POSTED],
            ["posted_at", f"{today}T08:00:00Z", f"{today}T09:00:00Z"],
        ]
        client = _make_client(ws)
        assert client.count_today(STATUS_ERROR) == 1

    def test_handles_mismatched_column_lengths(self):
        """status col longer than posted_at col — should not raise."""
        ws = _ws()
        today = self._today()
        ws.col_values.side_effect = [
            ["status", STATUS_POSTED, STATUS_POSTED, STATUS_POSTED],
            ["posted_at", f"{today}T10:00:00Z"],   # shorter
        ]
        client = _make_client(ws)
        # Only first row has posted_at matching today
        assert client.count_today() == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 9.  Retry decorator
# ═══════════════════════════════════════════════════════════════════════════════

class TestRetryDecorator:

    def test_second_attempt_succeeds_after_gspread_error(self):
        ws = _ws()
        ws.col_values.side_effect = [
            gspread.exceptions.GSpreadException("transient error"),
            ["status"],   # second call succeeds (no ready rows)
        ]
        client = _make_client(ws)

        with patch("app.queue_client.time.sleep") as mock_sleep:
            result = client.get_next_ready()
            mock_sleep.assert_called_once_with(2)

        assert result is None

    def test_raises_queue_error_if_both_attempts_fail(self):
        ws = _ws()
        ws.col_values.side_effect = gspread.exceptions.GSpreadException("always fails")
        client = _make_client(ws)

        with patch("app.queue_client.time.sleep"):
            with pytest.raises(QueueError, match="failed after retry"):
                client.get_next_ready()


# ═══════════════════════════════════════════════════════════════════════════════
# 10.  QueueRow dataclass
# ═══════════════════════════════════════════════════════════════════════════════

class TestQueueRow:

    def test_all_fields_accessible(self):
        row = QueueRow(
            row_index=3,
            shortcode="ABC",
            media_url="https://example.com/a.jpg",
            caption="cap",
            source_user="user",
            media_type="IMAGE",
            status=STATUS_PENDING,
            post_id=None,
            created_at="2026-04-27T10:00:00Z",
            posted_at=None,
            error=None,
        )
        assert row.row_index == 3
        assert row.post_id   is None
        assert row.error     is None

    def test_optional_fields_can_be_set(self):
        row = QueueRow(
            row_index=5,
            shortcode="XYZ",
            media_url="",
            caption="",
            source_user="",
            media_type="VIDEO",
            status=STATUS_POSTED,
            post_id="17855649574791001",
            created_at="2026-04-27T10:00:00Z",
            posted_at="2026-04-27T12:00:00Z",
            error=None,
        )
        assert row.post_id   == "17855649574791001"
        assert row.posted_at == "2026-04-27T12:00:00Z"
