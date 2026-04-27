"""
tests/test_publisher.py
───────────────────────
Unit tests for app/publisher.py.

All Graph API HTTP calls are mocked — no real network traffic.
time.sleep is always patched so tests run instantly.

Run with:  pytest tests/test_publisher.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest
import requests

from app.publisher import (
    DAILY_CAP_HARD,
    GRAPH_API_BASE,
    PublisherError,
    _check_response,
    _create_image_container,
    _create_video_container,
    _publish_container,
    _wait_for_container,
    publish_next,
    refresh_access_token,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures & factories
# ═══════════════════════════════════════════════════════════════════════════════

def _make_cfg(
    ig_user_id:          str = "111222333",
    ig_access_token:     str = "EAAtest",
    max_posts_per_day:   int = 5,
    posting_hours_start: int = 0,    # open all day for most tests
    posting_hours_end:   int = 24,
) -> MagicMock:
    cfg = MagicMock()
    cfg.ig_user_id          = ig_user_id
    cfg.ig_access_token     = ig_access_token
    cfg.max_posts_per_day   = max_posts_per_day
    cfg.posting_hours_start = posting_hours_start
    cfg.posting_hours_end   = posting_hours_end
    return cfg


def _make_queue_client(
    next_ready=None,
    posts_today: int = 0,
) -> MagicMock:
    qc = MagicMock()
    qc.get_next_ready.return_value = next_ready
    qc.count_today.return_value    = posts_today
    return qc


def _make_row(
    row_index:  int = 2,
    shortcode:  str = "ABC123",
    media_url:  str = "https://files.catbox.moe/a.jpg",
    caption:    str = "test caption",
    media_type: str = "IMAGE",
) -> MagicMock:
    from app.queue_client import QueueRow
    row = MagicMock(spec=QueueRow)
    row.row_index  = row_index
    row.shortcode  = shortcode
    row.media_url  = media_url
    row.caption    = caption
    row.media_type = media_type
    return row


def _ok(body: dict) -> MagicMock:
    """Build a mock 200 OK response with a JSON body."""
    r = MagicMock(spec=requests.Response)
    r.status_code = 200
    r.ok          = True
    r.json.return_value = body
    return r


def _error_body(code: int = 190, msg: str = "Invalid token") -> MagicMock:
    """Build a mock 400 response carrying a Graph API error body."""
    r = MagicMock(spec=requests.Response)
    r.status_code = 400
    r.ok          = False
    r.json.return_value = {"error": {"code": code, "message": msg}}
    return r


def _http_error(status: int = 500) -> MagicMock:
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.ok          = False
    r.text        = "Internal Server Error"
    r.json.side_effect = ValueError("not JSON")
    return r


# ═══════════════════════════════════════════════════════════════════════════════
# 1.  _check_response
# ═══════════════════════════════════════════════════════════════════════════════

class TestCheckResponse:

    def test_passes_on_200_with_valid_body(self):
        resp = _ok({"id": "12345"})
        data = _check_response(resp, "test call")
        assert data["id"] == "12345"

    def test_raises_on_graph_error_body(self):
        resp = _error_body(190, "Invalid OAuth access token")
        with pytest.raises(PublisherError, match="190"):
            _check_response(resp, "test call")

    def test_raises_on_non_json_response(self):
        resp = _http_error(503)
        with pytest.raises(PublisherError, match="Non-JSON"):
            _check_response(resp, "test call")

    def test_raises_on_non_ok_without_error_key(self):
        r = MagicMock(spec=requests.Response)
        r.status_code = 429
        r.ok          = False
        r.text        = "rate limited"
        r.json.return_value = {}     # no 'error' key
        with pytest.raises(PublisherError, match="429"):
            _check_response(r, "test call")

    def test_error_subcode_included_in_message(self):
        r = MagicMock(spec=requests.Response)
        r.status_code = 400
        r.ok          = False
        r.json.return_value = {
            "error": {"code": 100, "error_subcode": 33, "message": "bad param"}
        }
        with pytest.raises(PublisherError, match="100/33"):
            _check_response(r, "test call")


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  _create_image_container
# ═══════════════════════════════════════════════════════════════════════════════

class TestCreateImageContainer:

    @patch("app.publisher.requests.post")
    def test_returns_container_id(self, mock_post):
        mock_post.return_value = _ok({"id": "container_img_001"})
        cfg = _make_cfg()

        cid = _create_image_container("https://files.catbox.moe/a.jpg", "cap", cfg)

        assert cid == "container_img_001"

    @patch("app.publisher.requests.post")
    def test_posts_to_correct_url(self, mock_post):
        mock_post.return_value = _ok({"id": "cid"})
        cfg = _make_cfg(ig_user_id="999888")

        _create_image_container("https://img.url/a.jpg", "cap", cfg)

        url = mock_post.call_args[0][0]
        assert "999888/media" in url
        assert GRAPH_API_BASE in url

    @patch("app.publisher.requests.post")
    def test_sends_image_url_and_caption(self, mock_post):
        mock_post.return_value = _ok({"id": "cid"})
        cfg = _make_cfg(ig_access_token="TESTTOKEN")

        _create_image_container("https://img.url/b.jpg", "hello caption", cfg)

        params = mock_post.call_args[1]["params"]
        assert params["image_url"] == "https://img.url/b.jpg"
        assert params["caption"]   == "hello caption"
        assert params["access_token"] == "TESTTOKEN"

    @patch("app.publisher.requests.post")
    def test_raises_publisher_error_on_api_error(self, mock_post):
        mock_post.return_value = _error_body()
        with pytest.raises(PublisherError):
            _create_image_container("https://img.url/a.jpg", "cap", _make_cfg())


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  _create_video_container
# ═══════════════════════════════════════════════════════════════════════════════

class TestCreateVideoContainer:

    @patch("app.publisher.requests.post")
    def test_returns_container_id(self, mock_post):
        mock_post.return_value = _ok({"id": "container_vid_001"})
        cid = _create_video_container("https://files.catbox.moe/v.mp4", "cap", _make_cfg())
        assert cid == "container_vid_001"

    @patch("app.publisher.requests.post")
    def test_sends_media_type_reels(self, mock_post):
        mock_post.return_value = _ok({"id": "cid"})
        _create_video_container("https://files.catbox.moe/v.mp4", "cap", _make_cfg())

        params = mock_post.call_args[1]["params"]
        assert params["media_type"] == "REELS"
        assert params["video_url"]  == "https://files.catbox.moe/v.mp4"

    @patch("app.publisher.requests.post")
    def test_raises_on_api_error(self, mock_post):
        mock_post.return_value = _error_body()
        with pytest.raises(PublisherError):
            _create_video_container("https://v.url/clip.mp4", "cap", _make_cfg())


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  _wait_for_container
# ═══════════════════════════════════════════════════════════════════════════════

class TestWaitForContainer:

    @patch("app.publisher.time.sleep")
    @patch("app.publisher.requests.get")
    def test_returns_immediately_on_finished(self, mock_get, mock_sleep):
        mock_get.return_value = _ok({"status_code": "FINISHED"})
        _wait_for_container("cid_001", _make_cfg())
        mock_sleep.assert_not_called()

    @patch("app.publisher.time.sleep")
    @patch("app.publisher.requests.get")
    def test_returns_on_published_status(self, mock_get, mock_sleep):
        mock_get.return_value = _ok({"status_code": "PUBLISHED"})
        _wait_for_container("cid_002", _make_cfg())   # should not raise

    @patch("app.publisher.time.sleep")
    @patch("app.publisher.requests.get")
    def test_polls_until_finished(self, mock_get, mock_sleep):
        mock_get.side_effect = [
            _ok({"status_code": "IN_PROGRESS"}),
            _ok({"status_code": "IN_PROGRESS"}),
            _ok({"status_code": "FINISHED"}),
        ]
        _wait_for_container("cid_003", _make_cfg())
        assert mock_get.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("app.publisher.time.sleep")
    @patch("app.publisher.requests.get")
    def test_raises_on_error_status(self, mock_get, mock_sleep):
        mock_get.return_value = _ok({"status_code": "ERROR", "status": "upload failed"})
        with pytest.raises(PublisherError, match="ERROR"):
            _wait_for_container("cid_004", _make_cfg())
        mock_sleep.assert_not_called()

    @patch("app.publisher.time.monotonic")
    @patch("app.publisher.time.sleep")
    @patch("app.publisher.requests.get")
    def test_raises_on_timeout(self, mock_get, mock_sleep, mock_mono):
        """Simulate clock advancing past POLL_MAX_WAIT after one IN_PROGRESS poll."""
        from app.publisher import POLL_MAX_WAIT
        # First call: deadline set (monotonic() = 0)
        # Second call inside loop: time.monotonic() >= deadline → timeout
        mock_mono.side_effect = [0, 0, POLL_MAX_WAIT + 1]
        mock_get.return_value = _ok({"status_code": "IN_PROGRESS"})

        with pytest.raises(PublisherError, match="not FINISHED"):
            _wait_for_container("cid_005", _make_cfg())

    @patch("app.publisher.time.sleep")
    @patch("app.publisher.requests.get")
    def test_passes_correct_fields_param(self, mock_get, mock_sleep):
        mock_get.return_value = _ok({"status_code": "FINISHED"})
        _wait_for_container("cid_006", _make_cfg(ig_access_token="TOKEN"))

        params = mock_get.call_args[1]["params"]
        assert "status_code" in params["fields"]
        assert params["access_token"] == "TOKEN"


# ═══════════════════════════════════════════════════════════════════════════════
# 5.  _publish_container
# ═══════════════════════════════════════════════════════════════════════════════

class TestPublishContainer:

    @patch("app.publisher.requests.post")
    def test_returns_post_id(self, mock_post):
        mock_post.return_value = _ok({"id": "17855649574791001"})
        cfg = _make_cfg()
        post_id = _publish_container("container_xyz", cfg)
        assert post_id == "17855649574791001"

    @patch("app.publisher.requests.post")
    def test_posts_to_media_publish_endpoint(self, mock_post):
        mock_post.return_value = _ok({"id": "pid"})
        cfg = _make_cfg(ig_user_id="777888")
        _publish_container("cid", cfg)

        url = mock_post.call_args[0][0]
        assert "777888/media_publish" in url

    @patch("app.publisher.requests.post")
    def test_sends_creation_id(self, mock_post):
        mock_post.return_value = _ok({"id": "pid"})
        _publish_container("MYCONTAINER", _make_cfg())

        params = mock_post.call_args[1]["params"]
        assert params["creation_id"] == "MYCONTAINER"

    @patch("app.publisher.requests.post")
    def test_raises_on_api_error(self, mock_post):
        mock_post.return_value = _error_body(100, "container not ready")
        with pytest.raises(PublisherError, match="100"):
            _publish_container("bad_cid", _make_cfg())


# ═══════════════════════════════════════════════════════════════════════════════
# 6.  publish_next — guard conditions
# ═══════════════════════════════════════════════════════════════════════════════

class TestPublishNextGuards:

    def test_returns_none_outside_posting_window(self):
        cfg = _make_cfg(posting_hours_start=8, posting_hours_end=9)
        qc  = _make_queue_client()

        # Force current UTC hour to be outside the window (e.g. hour 15)
        fake_dt = MagicMock()
        fake_dt.hour = 15
        with patch("app.publisher.datetime") as mock_dt:
            mock_dt.now.return_value = fake_dt

            result = publish_next(cfg, qc)

        assert result is None
        qc.get_next_ready.assert_not_called()

    def test_returns_none_at_daily_soft_cap(self):
        cfg = _make_cfg(max_posts_per_day=5)
        qc  = _make_queue_client(posts_today=5)

        with patch("app.publisher.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 12
            result = publish_next(cfg, qc)

        assert result is None
        qc.get_next_ready.assert_not_called()

    def test_returns_none_at_daily_hard_cap(self):
        """Even if max_posts_per_day > 20, hard cap of 20 applies."""
        cfg = _make_cfg(max_posts_per_day=50)
        qc  = _make_queue_client(posts_today=DAILY_CAP_HARD)

        with patch("app.publisher.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 12
            result = publish_next(cfg, qc)

        assert result is None

    def test_hard_cap_is_20(self):
        assert DAILY_CAP_HARD == 20

    def test_returns_none_when_queue_empty(self):
        cfg = _make_cfg()
        qc  = _make_queue_client(next_ready=None, posts_today=0)

        with patch("app.publisher.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 12
            result = publish_next(cfg, qc)

        assert result is None
        qc.mark_posted.assert_not_called()

    def test_posting_window_boundary_inclusive_start(self):
        """Hour exactly at POSTING_HOURS_START should be inside window."""
        cfg = _make_cfg(posting_hours_start=8, posting_hours_end=22)
        qc  = _make_queue_client(next_ready=None, posts_today=0)

        with patch("app.publisher.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 8
            publish_next(cfg, qc)

        # get_next_ready called means we passed the window check
        qc.get_next_ready.assert_called_once()

    def test_posting_window_boundary_exclusive_end(self):
        """Hour exactly at POSTING_HOURS_END should be outside window."""
        cfg = _make_cfg(posting_hours_start=8, posting_hours_end=22)
        qc  = _make_queue_client()

        with patch("app.publisher.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 22
            result = publish_next(cfg, qc)

        assert result is None
        qc.get_next_ready.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# 7.  publish_next — image publish (happy path)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPublishNextImageSuccess:

    @patch("app.publisher.time.sleep")
    @patch("app.publisher.requests.get")
    @patch("app.publisher.requests.post")
    def test_image_publish_returns_post_id(self, mock_post, mock_get, mock_sleep):
        cfg = _make_cfg()
        row = _make_row(media_type="IMAGE")
        qc  = _make_queue_client(next_ready=row, posts_today=0)

        mock_post.side_effect = [
            _ok({"id": "container_img_001"}),    # create container
            _ok({"id": "17855649574791001"}),     # publish
        ]
        mock_get.return_value = _ok({"status_code": "FINISHED"})

        with patch("app.publisher.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 12
            post_id = publish_next(cfg, qc)

        assert post_id == "17855649574791001"
        qc.mark_posted.assert_called_once_with(row.row_index, "17855649574791001")
        qc.mark_error.assert_not_called()

    @patch("app.publisher.time.sleep")
    @patch("app.publisher.requests.get")
    @patch("app.publisher.requests.post")
    def test_image_container_params_sent(self, mock_post, mock_get, mock_sleep):
        cfg = _make_cfg(ig_access_token="MYTOKEN", ig_user_id="U123")
        row = _make_row(
            media_type="IMAGE",
            media_url="https://files.catbox.moe/img.jpg",
            caption="Nice meme",
        )
        qc = _make_queue_client(next_ready=row, posts_today=0)

        mock_post.side_effect = [
            _ok({"id": "cid"}),
            _ok({"id": "pid"}),
        ]
        mock_get.return_value = _ok({"status_code": "FINISHED"})

        with patch("app.publisher.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 12
            publish_next(cfg, qc)

        # First POST = create container
        create_params = mock_post.call_args_list[0][1]["params"]
        assert create_params["image_url"]    == "https://files.catbox.moe/img.jpg"
        assert create_params["caption"]      == "Nice meme"
        assert create_params["access_token"] == "MYTOKEN"
        assert "U123/media" in mock_post.call_args_list[0][0][0]


# ═══════════════════════════════════════════════════════════════════════════════
# 8.  publish_next — video publish
# ═══════════════════════════════════════════════════════════════════════════════

class TestPublishNextVideoSuccess:

    @patch("app.publisher.time.sleep")
    @patch("app.publisher.requests.get")
    @patch("app.publisher.requests.post")
    def test_video_publish_returns_post_id(self, mock_post, mock_get, mock_sleep):
        cfg = _make_cfg()
        row = _make_row(
            media_type="VIDEO",
            media_url="https://files.catbox.moe/clip.mp4",
        )
        qc = _make_queue_client(next_ready=row, posts_today=1)

        mock_post.side_effect = [
            _ok({"id": "container_vid_001"}),
            _ok({"id": "17900000001"}),
        ]
        mock_get.return_value = _ok({"status_code": "FINISHED"})

        with patch("app.publisher.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 14
            post_id = publish_next(cfg, qc)

        assert post_id == "17900000001"
        qc.mark_posted.assert_called_once_with(row.row_index, "17900000001")

    @patch("app.publisher.time.sleep")
    @patch("app.publisher.requests.get")
    @patch("app.publisher.requests.post")
    def test_video_uses_reels_media_type(self, mock_post, mock_get, mock_sleep):
        cfg = _make_cfg()
        row = _make_row(media_type="VIDEO")
        qc  = _make_queue_client(next_ready=row, posts_today=0)

        mock_post.side_effect = [_ok({"id": "cid"}), _ok({"id": "pid"})]
        mock_get.return_value = _ok({"status_code": "FINISHED"})

        with patch("app.publisher.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 10
            publish_next(cfg, qc)

        create_params = mock_post.call_args_list[0][1]["params"]
        assert create_params["media_type"] == "REELS"

    @patch("app.publisher.time.sleep")
    @patch("app.publisher.requests.get")
    @patch("app.publisher.requests.post")
    def test_video_polls_during_processing(self, mock_post, mock_get, mock_sleep):
        """Verify the poller handles IN_PROGRESS before FINISHED."""
        cfg = _make_cfg()
        row = _make_row(media_type="VIDEO")
        qc  = _make_queue_client(next_ready=row, posts_today=0)

        mock_post.side_effect = [_ok({"id": "cid"}), _ok({"id": "pid"})]
        mock_get.side_effect  = [
            _ok({"status_code": "IN_PROGRESS"}),
            _ok({"status_code": "IN_PROGRESS"}),
            _ok({"status_code": "FINISHED"}),
        ]

        with patch("app.publisher.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 10
            publish_next(cfg, qc)

        assert mock_get.call_count == 3
        assert mock_sleep.call_count == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 9.  publish_next — error handling
# ═══════════════════════════════════════════════════════════════════════════════

class TestPublishNextErrors:

    @patch("app.publisher.time.sleep")
    @patch("app.publisher.requests.get")
    @patch("app.publisher.requests.post")
    def test_container_create_error_marks_row_error(self, mock_post, mock_get, mock_sleep):
        cfg = _make_cfg()
        row = _make_row()
        qc  = _make_queue_client(next_ready=row, posts_today=0)

        mock_post.return_value = _error_body(190, "Invalid token")

        with patch("app.publisher.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 12
            result = publish_next(cfg, qc)

        assert result is None
        qc.mark_error.assert_called_once()
        error_msg = qc.mark_error.call_args[0][1]
        assert "190" in error_msg or "PublisherError" in error_msg
        qc.mark_posted.assert_not_called()

    @patch("app.publisher.time.sleep")
    @patch("app.publisher.requests.get")
    @patch("app.publisher.requests.post")
    def test_container_error_status_marks_row_error(self, mock_post, mock_get, mock_sleep):
        cfg = _make_cfg()
        row = _make_row()
        qc  = _make_queue_client(next_ready=row, posts_today=0)

        mock_post.return_value = _ok({"id": "cid"})
        mock_get.return_value  = _ok({"status_code": "ERROR", "status": "upload failed"})

        with patch("app.publisher.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 12
            result = publish_next(cfg, qc)

        assert result is None
        qc.mark_error.assert_called_once()

    @patch("app.publisher.time.sleep")
    @patch("app.publisher.requests.get")
    @patch("app.publisher.requests.post")
    def test_publish_api_error_marks_row_error(self, mock_post, mock_get, mock_sleep):
        cfg = _make_cfg()
        row = _make_row()
        qc  = _make_queue_client(next_ready=row, posts_today=0)

        mock_post.side_effect = [
            _ok({"id": "cid"}),
            _error_body(100, "container not ready"),
        ]
        mock_get.return_value = _ok({"status_code": "FINISHED"})

        with patch("app.publisher.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 12
            result = publish_next(cfg, qc)

        assert result is None
        qc.mark_error.assert_called_once()
        qc.mark_posted.assert_not_called()

    @patch("app.publisher.time.sleep")
    @patch("app.publisher.requests.get")
    @patch("app.publisher.requests.post")
    def test_network_error_marks_row_error(self, mock_post, mock_get, mock_sleep):
        cfg = _make_cfg()
        row = _make_row()
        qc  = _make_queue_client(next_ready=row, posts_today=0)

        mock_post.side_effect = requests.ConnectionError("network down")

        with patch("app.publisher.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 12
            result = publish_next(cfg, qc)

        assert result is None
        qc.mark_error.assert_called_once()

    @patch("app.publisher.time.sleep")
    @patch("app.publisher.requests.get")
    @patch("app.publisher.requests.post")
    def test_does_not_raise_on_any_error(self, mock_post, mock_get, mock_sleep):
        """publish_next must never propagate exceptions — scheduler must keep running."""
        cfg = _make_cfg()
        row = _make_row()
        qc  = _make_queue_client(next_ready=row, posts_today=0)

        mock_post.side_effect = RuntimeError("unexpected crash")

        with patch("app.publisher.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 12
            result = publish_next(cfg, qc)    # must not raise

        assert result is None

    @patch("app.publisher.time.sleep")
    @patch("app.publisher.requests.get")
    @patch("app.publisher.requests.post")
    def test_mark_error_failure_does_not_propagate(self, mock_post, mock_get, mock_sleep):
        """Even if mark_error itself fails, publish_next must not raise."""
        cfg = _make_cfg()
        row = _make_row()
        qc  = _make_queue_client(next_ready=row, posts_today=0)

        mock_post.side_effect = PublisherError("publish failed")
        qc.mark_error.side_effect = Exception("Sheets also down")

        with patch("app.publisher.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 12
            result = publish_next(cfg, qc)

        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# 10.  refresh_access_token
# ═══════════════════════════════════════════════════════════════════════════════

class TestRefreshAccessToken:

    @patch("app.publisher.requests.get")
    def test_returns_new_token(self, mock_get):
        mock_get.return_value = _ok({
            "access_token": "NEWTOKEN_ABC",
            "token_type":   "bearer",
            "expires_in":   5183944,
        })
        cfg = _make_cfg(ig_access_token="OLDTOKEN")
        new_token = refresh_access_token(cfg)
        assert new_token == "NEWTOKEN_ABC"

    @patch("app.publisher.requests.get")
    def test_calls_correct_endpoint(self, mock_get):
        mock_get.return_value = _ok({"access_token": "NEW", "expires_in": 86400})
        cfg = _make_cfg(ig_access_token="OLD")
        refresh_access_token(cfg)

        url = mock_get.call_args[0][0]
        assert "refresh_access_token" in url
        assert GRAPH_API_BASE in url

    @patch("app.publisher.requests.get")
    def test_sends_correct_params(self, mock_get):
        mock_get.return_value = _ok({"access_token": "NEW", "expires_in": 100})
        cfg = _make_cfg(ig_access_token="MY_OLD_TOKEN")
        refresh_access_token(cfg)

        params = mock_get.call_args[1]["params"]
        assert params["grant_type"]   == "ig_refresh_token"
        assert params["access_token"] == "MY_OLD_TOKEN"

    @patch("app.publisher.requests.get")
    def test_raises_on_api_error(self, mock_get):
        mock_get.return_value = _error_body(190, "Token expired")
        cfg = _make_cfg()
        with pytest.raises(PublisherError, match="190"):
            refresh_access_token(cfg)

    @patch("app.publisher.requests.get")
    def test_raises_when_token_missing_from_response(self, mock_get):
        mock_get.return_value = _ok({"expires_in": 1000})   # no access_token key
        with pytest.raises(PublisherError, match="missing 'access_token'"):
            refresh_access_token(_make_cfg())

    @patch("app.publisher.requests.get")
    def test_logs_new_token_prominently(self, mock_get, caplog):
        import logging
        mock_get.return_value = _ok({"access_token": "LOGME123", "expires_in": 5000})
        with caplog.at_level(logging.INFO, logger="app.publisher"):
            refresh_access_token(_make_cfg())
        assert "LOGME123" in caplog.text
