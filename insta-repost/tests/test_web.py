"""
tests/test_web.py
─────────────────
Unit / integration tests for app/web.py using Flask's test client.

All external I/O (downloader, uploader, queue_client) is mocked.
No real network calls or file system side-effects escape the tmp_path.

Run with:  pytest tests/test_web.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_cfg(tmp_path: Path, password: str = "secret") -> MagicMock:
    """Return a minimal mock Config suitable for create_app()."""
    cfg = MagicMock()
    cfg.flask_secret_key    = "test-secret-key-32-bytes-long-xx"
    cfg.app_password        = password
    cfg.google_credentials  = {"type": "service_account"}
    cfg.google_sheet_name   = "MemeQueue"
    cfg.ig_login_user       = "burner"
    cfg.ig_login_pass       = "pass"
    return cfg


def _make_download_result(tmp_path: Path, shortcode: str = "ABC123") -> MagicMock:
    """Return a mock DownloadResult with a real file on disk."""
    from app.downloader import DownloadResult

    media_file = tmp_path / shortcode / "post.jpg"
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_bytes(b"\xff\xd8\xff" + b"x" * 512)   # fake JPEG

    result = MagicMock(spec=DownloadResult)
    result.shortcode   = shortcode
    result.media_path  = media_file
    result.caption     = "Original caption"
    result.source_user = "memegod"
    result.media_type  = "IMAGE"
    result.is_carousel = False
    return result


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path):
    """Create a test Flask app with a real tmp downloads dir."""
    cfg = _make_cfg(tmp_path)

    # Patch the downloads root so files land in tmp_path
    with patch("app.web._DOWNLOADS_ROOT", tmp_path):
        from app.web import create_app
        application = create_app(cfg)

    application.config["TESTING"] = True
    application.config["WTF_CSRF_ENABLED"] = False
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def authed_client(client):
    """A test client already logged in."""
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["_csrf_token"] = "test-csrf-token"
    return client


def _csrf(client) -> str:
    """Retrieve (or create) the CSRF token from the current session."""
    with client.session_transaction() as sess:
        if "_csrf_token" not in sess:
            sess["_csrf_token"] = "test-csrf-token"
        return sess["_csrf_token"]


# ═══════════════════════════════════════════════════════════════════════════════
# 1.  Health check
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealthz:

    def test_healthz_returns_200_without_auth(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert b"ok" in resp.data

    def test_healthz_returns_200_when_authed(self, authed_client):
        resp = authed_client.get("/healthz")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  Root redirect
# ═══════════════════════════════════════════════════════════════════════════════

class TestIndex:

    def test_unauthenticated_redirects_to_login(self, client):
        resp = client.get("/")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_authenticated_redirects_to_submit(self, authed_client):
        resp = authed_client.get("/")
        assert resp.status_code == 302
        assert "/submit" in resp.headers["Location"]


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  Login
# ═══════════════════════════════════════════════════════════════════════════════

class TestLogin:

    def test_get_login_returns_200(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert b"Log in" in resp.data

    def test_wrong_password_shows_error(self, client):
        token = _csrf(client)
        resp = client.post("/login", data={
            "password": "wrong-password",
            "_csrf_token": token,
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Incorrect" in resp.data

    def test_correct_password_redirects_to_submit(self, client):
        token = _csrf(client)
        resp = client.post("/login", data={
            "password": "secret",
            "_csrf_token": token,
        })
        assert resp.status_code == 302
        assert "/submit" in resp.headers["Location"]

    def test_correct_password_sets_session(self, client):
        token = _csrf(client)
        client.post("/login", data={
            "password": "secret",
            "_csrf_token": token,
        })
        with client.session_transaction() as sess:
            assert sess.get("authenticated") is True

    def test_csrf_mismatch_aborts_403(self, client):
        resp = client.post("/login", data={
            "password": "secret",
            "_csrf_token": "wrong-csrf",
        })
        assert resp.status_code == 403

    def test_already_authed_redirects_to_submit(self, authed_client):
        resp = authed_client.get("/login")
        assert resp.status_code == 302
        assert "/submit" in resp.headers["Location"]

    def test_logout_clears_session(self, authed_client):
        authed_client.get("/logout")
        with authed_client.session_transaction() as sess:
            assert not sess.get("authenticated")


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  Auth guard
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuthGuard:

    def test_submit_get_requires_auth(self, client):
        resp = client.get("/submit")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_queue_get_requires_auth(self, client):
        resp = client.get("/queue")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_preview_requires_auth(self, client):
        resp = client.get("/preview/ABC123")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_submit_post_requires_auth(self, client):
        resp = client.post("/submit", data={"url": "https://instagram.com/p/X/"})
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


# ═══════════════════════════════════════════════════════════════════════════════
# 5.  GET /submit
# ═══════════════════════════════════════════════════════════════════════════════

class TestSubmitGet:

    def test_submit_page_renders(self, authed_client):
        resp = authed_client.get("/submit")
        assert resp.status_code == 200
        assert b"Queue a Post" in resp.data
        assert b"instagram.com" in resp.data

    def test_submit_page_has_url_input(self, authed_client):
        resp = authed_client.get("/submit")
        assert b'name="url"' in resp.data


# ═══════════════════════════════════════════════════════════════════════════════
# 6.  POST /submit — download path
# ═══════════════════════════════════════════════════════════════════════════════

class TestSubmitPost:

    def test_empty_url_flashes_error(self, authed_client):
        token = _csrf(authed_client)
        resp = authed_client.post("/submit", data={
            "url": "",
            "_csrf_token": token,
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"enter an Instagram URL" in resp.data

    def test_invalid_url_flashes_error(self, authed_client):
        token = _csrf(authed_client)
        with patch("app.web.download_from_url",
                   side_effect=ValueError("Cannot extract")):
            resp = authed_client.post("/submit", data={
                "url": "https://twitter.com/p/ABC",
                "_csrf_token": token,
            }, follow_redirects=True)
        assert b"Invalid URL" in resp.data

    def test_private_post_flashes_error(self, authed_client):
        token = _csrf(authed_client)
        from app.downloader import PostNotFoundError
        with patch("app.web.download_from_url",
                   side_effect=PostNotFoundError("private")):
            resp = authed_client.post("/submit", data={
                "url": "https://www.instagram.com/p/PRIV123/",
                "_csrf_token": token,
            }, follow_redirects=True)
        assert b"not found" in resp.data.lower()

    def test_rate_limited_flashes_error(self, authed_client):
        token = _csrf(authed_client)
        from app.downloader import RateLimitedError
        with patch("app.web.download_from_url",
                   side_effect=RateLimitedError("429")):
            resp = authed_client.post("/submit", data={
                "url": "https://www.instagram.com/p/RATE123/",
                "_csrf_token": token,
            }, follow_redirects=True)
        assert b"rate-limiting" in resp.data.lower()

    def test_successful_download_stores_pending_and_redirects(self, authed_client, tmp_path):
        token = _csrf(authed_client)
        mock_result = _make_download_result(tmp_path, "SC001")

        with patch("app.web.download_from_url", return_value=mock_result):
            resp = authed_client.post("/submit", data={
                "url": "https://www.instagram.com/p/SC001/",
                "_csrf_token": token,
            })

        assert resp.status_code == 302
        assert "/submit/preview" in resp.headers["Location"]

        with authed_client.session_transaction() as sess:
            pending = sess.get("pending")
            assert pending is not None
            assert pending["shortcode"] == "SC001"
            assert pending["source_user"] == "memegod"
            assert "🎥 Credits: @memegod" in pending["default_caption"]

    def test_csrf_mismatch_on_post_submit_aborts_403(self, authed_client):
        resp = authed_client.post("/submit", data={
            "url": "https://www.instagram.com/p/X/",
            "_csrf_token": "totally-wrong",
        })
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# 7.  GET /submit/preview
# ═══════════════════════════════════════════════════════════════════════════════

class TestSubmitPreview:

    def _seed_pending(self, client, tmp_path: Path) -> dict:
        mock = _make_download_result(tmp_path, "PREV01")
        pending = {
            "shortcode":       "PREV01",
            "media_path":      str(mock.media_path),
            "caption":         "Original caption",
            "source_user":     "testuser",
            "media_type":      "IMAGE",
            "is_carousel":     False,
            "default_caption": "Original caption\n\n🎥 Credits: @testuser",
        }
        with client.session_transaction() as sess:
            sess["pending"] = pending
        return pending

    def test_renders_preview_with_pending(self, authed_client, tmp_path):
        self._seed_pending(authed_client, tmp_path)
        resp = authed_client.get("/submit/preview")
        assert resp.status_code == 200
        assert b"PREV01" in resp.data
        assert b"testuser" in resp.data
        assert b"Queue it" in resp.data

    def test_no_pending_redirects_to_submit(self, authed_client):
        resp = authed_client.get("/submit/preview")
        assert resp.status_code == 302
        assert "/submit" in resp.headers["Location"]


# ═══════════════════════════════════════════════════════════════════════════════
# 8.  POST /submit/confirm — full pipeline (upload → queue)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSubmitConfirm:

    def _seed_pending(self, client, tmp_path: Path, shortcode: str = "CONF01") -> Path:
        media_file = tmp_path / shortcode / "post.jpg"
        media_file.parent.mkdir(parents=True, exist_ok=True)
        media_file.write_bytes(b"\xff\xd8\xff" + b"x" * 512)

        with client.session_transaction() as sess:
            sess["pending"] = {
                "shortcode":       shortcode,
                "media_path":      str(media_file),
                "caption":         "Original",
                "source_user":     "creator",
                "media_type":      "IMAGE",
                "is_carousel":     False,
                "default_caption": "Original\n\n🎥 Credits: @creator",
            }
        return media_file

    def test_full_success_path(self, authed_client, tmp_path):
        self._seed_pending(authed_client, tmp_path, "CONF01")
        token = _csrf(authed_client)

        mock_queue = MagicMock()
        mock_queue.append.return_value = 5

        with patch("app.web.upload_with_fallback",
                   return_value="https://files.catbox.moe/ok.jpg"), \
             patch("app.web._get_queue_client", return_value=mock_queue):

            resp = authed_client.post("/submit/confirm", data={
                "caption": "Edited caption ✨",
                "_csrf_token": token,
            }, follow_redirects=True)

        assert resp.status_code == 200
        assert b"Queued" in resp.data
        mock_queue.append.assert_called_once()
        call_kwargs = mock_queue.append.call_args[1]
        assert call_kwargs["shortcode"]   == "CONF01"
        assert call_kwargs["caption"]     == "Edited caption ✨"
        assert call_kwargs["media_url"]   == "https://files.catbox.moe/ok.jpg"

    def test_upload_failure_flashes_error(self, authed_client, tmp_path):
        self._seed_pending(authed_client, tmp_path, "CONF02")
        token = _csrf(authed_client)

        from app.uploader import UploadError
        with patch("app.web.upload_with_fallback",
                   side_effect=UploadError("both hosts down")):
            resp = authed_client.post("/submit/confirm", data={
                "caption": "cap",
                "_csrf_token": token,
            }, follow_redirects=True)

        assert b"Upload failed" in resp.data

    def test_no_pending_redirects_to_submit(self, authed_client):
        token = _csrf(authed_client)
        resp = authed_client.post("/submit/confirm", data={
            "caption": "cap",
            "_csrf_token": token,
        }, follow_redirects=True)
        assert b"Session expired" in resp.data

    def test_duplicate_shortcode_flashes_error(self, authed_client, tmp_path):
        self._seed_pending(authed_client, tmp_path, "DUP01")
        token = _csrf(authed_client)

        from app.queue_client import DuplicateError
        mock_queue = MagicMock()
        mock_queue.append.side_effect = DuplicateError("DUP01 already exists")

        with patch("app.web.upload_with_fallback",
                   return_value="https://files.catbox.moe/ok.jpg"), \
             patch("app.web._get_queue_client", return_value=mock_queue):

            resp = authed_client.post("/submit/confirm", data={
                "caption": "cap",
                "_csrf_token": token,
            }, follow_redirects=True)

        assert b"already in the queue" in resp.data

    def test_session_cleared_after_success(self, authed_client, tmp_path):
        self._seed_pending(authed_client, tmp_path, "CLEAR01")
        token = _csrf(authed_client)

        mock_queue = MagicMock()
        mock_queue.append.return_value = 3

        with patch("app.web.upload_with_fallback",
                   return_value="https://files.catbox.moe/ok.jpg"), \
             patch("app.web._get_queue_client", return_value=mock_queue):

            authed_client.post("/submit/confirm", data={
                "caption": "cap",
                "_csrf_token": token,
            })

        with authed_client.session_transaction() as sess:
            assert "pending" not in sess

    def test_missing_file_flashes_error(self, authed_client, tmp_path):
        token = _csrf(authed_client)
        with authed_client.session_transaction() as sess:
            sess["pending"] = {
                "shortcode":       "MISSING01",
                "media_path":      str(tmp_path / "MISSING01" / "ghost.jpg"),
                "caption":         "cap",
                "source_user":     "user",
                "media_type":      "IMAGE",
                "is_carousel":     False,
                "default_caption": "cap",
            }
        resp = authed_client.post("/submit/confirm", data={
            "caption": "cap",
            "_csrf_token": token,
        }, follow_redirects=True)
        assert b"missing" in resp.data.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 9.  GET /preview/<shortcode>
# ═══════════════════════════════════════════════════════════════════════════════

class TestPreviewMedia:

    def test_serves_jpg_file(self, authed_client, tmp_path):
        folder = tmp_path / "MEDIA01"
        folder.mkdir()
        img = folder / "post.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"x" * 100)

        with patch("app.web._DOWNLOADS_ROOT", tmp_path):
            resp = authed_client.get("/preview/MEDIA01")
        assert resp.status_code == 200
        assert resp.content_type.startswith("image/")

    def test_404_for_unknown_shortcode(self, authed_client, tmp_path):
        with patch("app.web._DOWNLOADS_ROOT", tmp_path):
            resp = authed_client.get("/preview/GHOST999")
        assert resp.status_code == 404

    def test_400_for_unsafe_shortcode(self, authed_client):
        resp = authed_client.get("/preview/../etc/passwd")
        # Flask normalises path traversal attempts — will 404 or 400
        assert resp.status_code in (400, 404)


# ═══════════════════════════════════════════════════════════════════════════════
# 10.  GET /queue
# ═══════════════════════════════════════════════════════════════════════════════

class TestQueueDashboard:

    def _make_row(self, row_index: int, shortcode: str, status: str):
        from app.queue_client import QueueRow
        return QueueRow(
            row_index=row_index,
            shortcode=shortcode,
            media_url="https://files.catbox.moe/a.jpg",
            caption="cap",
            source_user="user",
            media_type="IMAGE",
            status=status,
            post_id=None,
            created_at="2026-04-27T10:00:00Z",
            posted_at=None,
            error=None,
        )

    def test_renders_empty_queue(self, authed_client):
        mock_queue = MagicMock()
        mock_queue.get_all.return_value = []
        with patch("app.web._get_queue_client", return_value=mock_queue):
            resp = authed_client.get("/queue")
        assert resp.status_code == 200
        assert b"empty" in resp.data.lower()

    def test_renders_rows(self, authed_client):
        mock_queue = MagicMock()
        mock_queue.get_all.return_value = [
            self._make_row(2, "ABC123", "pending"),
            self._make_row(3, "DEF456", "ready"),
        ]
        with patch("app.web._get_queue_client", return_value=mock_queue):
            resp = authed_client.get("/queue")
        assert b"ABC123" in resp.data
        assert b"DEF456" in resp.data
        assert b"pending" in resp.data
        assert b"ready" in resp.data

    def test_queue_error_shows_flash(self, authed_client):
        from app.queue_client import QueueError
        mock_queue = MagicMock()
        mock_queue.get_all.side_effect = QueueError("Sheets down")
        with patch("app.web._get_queue_client", return_value=mock_queue):
            resp = authed_client.get("/queue", follow_redirects=True)
        assert b"Could not load queue" in resp.data


# ═══════════════════════════════════════════════════════════════════════════════
# 11.  Queue approve / reject
# ═══════════════════════════════════════════════════════════════════════════════

class TestQueueActions:

    def test_approve_calls_update_status_ready(self, authed_client):
        token = _csrf(authed_client)
        mock_queue = MagicMock()

        with patch("app.web._get_queue_client", return_value=mock_queue):
            resp = authed_client.post("/queue/5/approve", data={
                "_csrf_token": token,
            }, follow_redirects=True)

        from app.queue_client import STATUS_READY
        mock_queue.update_status.assert_called_once_with(5, STATUS_READY)
        assert resp.status_code == 200
        assert b"approved" in resp.data.lower()

    def test_reject_calls_update_status_rejected(self, authed_client):
        token = _csrf(authed_client)
        mock_queue = MagicMock()

        with patch("app.web._get_queue_client", return_value=mock_queue):
            resp = authed_client.post("/queue/7/reject", data={
                "_csrf_token": token,
            }, follow_redirects=True)

        mock_queue.update_status.assert_called_once_with(7, "rejected")

    def test_approve_csrf_mismatch_aborts_403(self, authed_client):
        resp = authed_client.post("/queue/5/approve", data={
            "_csrf_token": "wrong",
        })
        assert resp.status_code == 403

    def test_approve_queue_error_flashes(self, authed_client):
        token = _csrf(authed_client)
        from app.queue_client import QueueError
        mock_queue = MagicMock()
        mock_queue.update_status.side_effect = QueueError("timeout")

        with patch("app.web._get_queue_client", return_value=mock_queue):
            resp = authed_client.post("/queue/3/approve", data={
                "_csrf_token": token,
            }, follow_redirects=True)

        assert b"Failed to approve" in resp.data


# ═══════════════════════════════════════════════════════════════════════════════
# 12.  Rate limiting
# ═══════════════════════════════════════════════════════════════════════════════

class TestRateLimit:

    def test_excessive_submit_requests_blocked(self, authed_client, tmp_path):
        """11th request in the same window should be rate-limited."""
        from app.web import _rate_limits

        token = _csrf(authed_client)

        # Seed rate limiter to simulate 10 already-used slots
        import time
        _rate_limits[token] = [time.monotonic()] * 10

        with patch("app.web.download_from_url") as mock_dl:
            resp = authed_client.post("/submit", data={
                "url": "https://www.instagram.com/p/X123/",
                "_csrf_token": token,
            }, follow_redirects=True)

        mock_dl.assert_not_called()
        assert b"Too many requests" in resp.data
