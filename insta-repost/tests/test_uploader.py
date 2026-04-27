"""
tests/test_uploader.py
──────────────────────
Unit tests for app/uploader.py.

All HTTP calls are mocked with unittest.mock — no real network traffic.
Run with:  pytest tests/test_uploader.py -v
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import requests

from app.uploader import (
    MAX_FILE_SIZE_BYTES,
    FileTooLargeError,
    UploadError,
    _upload_to_0x0,
    _validate_file,
    _validate_url,
    upload_to_catbox,
    upload_with_fallback,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures & helpers
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def small_jpg(tmp_path: Path) -> Path:
    """A tiny valid JPEG-named file."""
    p = tmp_path / "meme.jpg"
    p.write_bytes(b"\xff\xd8\xff" + b"x" * 1024)   # fake JPEG header + 1 KB
    return p


@pytest.fixture
def huge_file(tmp_path: Path) -> Path:
    """A placeholder 'file' whose stat reports > 200 MB (without writing bytes)."""
    p = tmp_path / "huge.mp4"
    p.write_bytes(b"")          # create the file so stat() works
    return p


def _mock_response(status: int = 200, text: str = "https://files.catbox.moe/abc123.jpg") -> MagicMock:
    """Build a mock requests.Response."""
    r = MagicMock()
    r.status_code = status
    r.text = text
    return r


def _mock_head_ok() -> MagicMock:
    h = MagicMock()
    h.status_code = 200
    return h


def _mock_head_fail(status: int = 404) -> MagicMock:
    h = MagicMock()
    h.status_code = status
    return h


# ═══════════════════════════════════════════════════════════════════════════════
# 1.  _validate_file
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidateFile:

    def test_passes_for_valid_small_file(self, small_jpg):
        _validate_file(small_jpg)   # should not raise

    def test_raises_file_not_found(self, tmp_path):
        missing = tmp_path / "ghost.jpg"
        with pytest.raises(FileNotFoundError, match="ghost.jpg"):
            _validate_file(missing)

    def test_raises_file_too_large(self, huge_file):
        # Patch stat().st_size to report > 200 MB
        import os
        fake_stat = os.stat_result((0o644, 0, 0, 1, 0, 0, MAX_FILE_SIZE_BYTES + 1, 0, 0, 0))
        with patch.object(Path, "stat", return_value=fake_stat):
            with pytest.raises(FileTooLargeError, match="200 MB"):
                _validate_file(huge_file)

    def test_exactly_200mb_is_allowed(self, tmp_path):
        p = tmp_path / "exact.mp4"
        p.write_bytes(b"")
        import os
        exact_stat = os.stat_result((0o644, 0, 0, 1, 0, 0, MAX_FILE_SIZE_BYTES, 0, 0, 0))
        with patch.object(Path, "stat", return_value=exact_stat):
            _validate_file(p)   # should not raise


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  _validate_url
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidateUrl:

    @patch("app.uploader.requests.head")
    def test_valid_url_passes(self, mock_head):
        mock_head.return_value = _mock_head_ok()
        _validate_url("https://files.catbox.moe/abc.jpg")   # no exception

    @patch("app.uploader.requests.head")
    def test_non_https_raises(self, mock_head):
        with pytest.raises(UploadError, match="not an HTTPS URL"):
            _validate_url("http://files.catbox.moe/abc.jpg")
        mock_head.assert_not_called()

    @patch("app.uploader.requests.head")
    def test_empty_url_raises(self, mock_head):
        with pytest.raises(UploadError, match="not an HTTPS URL"):
            _validate_url("")
        mock_head.assert_not_called()

    @patch("app.uploader.requests.head")
    def test_head_404_raises(self, mock_head):
        mock_head.return_value = _mock_head_fail(404)
        with pytest.raises(UploadError, match="HTTP 404"):
            _validate_url("https://files.catbox.moe/missing.jpg")

    @patch("app.uploader.requests.head")
    def test_head_network_error_raises(self, mock_head):
        mock_head.side_effect = requests.ConnectionError("timeout")
        with pytest.raises(UploadError, match="unreachable"):
            _validate_url("https://files.catbox.moe/abc.jpg")

    @patch("app.uploader.requests.head")
    def test_host_label_appears_in_error(self, mock_head):
        mock_head.return_value = _mock_head_fail(500)
        with pytest.raises(UploadError):
            _validate_url("https://files.catbox.moe/abc.jpg", host_label="Catbox")


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  upload_to_catbox — happy path
# ═══════════════════════════════════════════════════════════════════════════════

class TestUploadToCatboxSuccess:

    @patch("app.uploader.requests.head")
    @patch("app.uploader.requests.post")
    def test_returns_url_on_success(self, mock_post, mock_head, small_jpg):
        mock_post.return_value = _mock_response(200, "https://files.catbox.moe/xyz789.jpg")
        mock_head.return_value = _mock_head_ok()

        url = upload_to_catbox(small_jpg)

        assert url == "https://files.catbox.moe/xyz789.jpg"
        mock_post.assert_called_once()

    @patch("app.uploader.requests.head")
    @patch("app.uploader.requests.post")
    def test_post_called_with_correct_form_data(self, mock_post, mock_head, small_jpg):
        mock_post.return_value = _mock_response(200, "https://files.catbox.moe/ok.jpg")
        mock_head.return_value = _mock_head_ok()

        upload_to_catbox(small_jpg)

        _, kwargs = mock_post.call_args
        assert kwargs["data"] == {"reqtype": "fileupload"}
        assert "fileToUpload" in kwargs["files"]

    @patch("app.uploader.requests.head")
    @patch("app.uploader.requests.post")
    def test_url_is_stripped_of_whitespace(self, mock_post, mock_head, small_jpg):
        mock_post.return_value = _mock_response(200, "  https://files.catbox.moe/ok.jpg  \n")
        mock_head.return_value = _mock_head_ok()

        url = upload_to_catbox(small_jpg)
        assert url == "https://files.catbox.moe/ok.jpg"

    def test_missing_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            upload_to_catbox(tmp_path / "nonexistent.jpg")

    def test_file_too_large_raises_immediately(self, huge_file):
        import os
        fake_stat = os.stat_result((0o644, 0, 0, 1, 0, 0, MAX_FILE_SIZE_BYTES + 1, 0, 0, 0))
        with patch.object(Path, "stat", return_value=fake_stat):
            with pytest.raises(FileTooLargeError):
                upload_to_catbox(huge_file)


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  upload_to_catbox — retry logic
# ═══════════════════════════════════════════════════════════════════════════════

class TestUploadToCatboxRetry:

    @patch("app.uploader.time.sleep")          # prevent real sleeping in tests
    @patch("app.uploader.requests.head")
    @patch("app.uploader.requests.post")
    def test_retries_on_500_then_succeeds(self, mock_post, mock_head, mock_sleep, small_jpg):
        """First call returns 500, second call succeeds."""
        mock_post.side_effect = [
            _mock_response(500, "Internal Server Error"),
            _mock_response(200, "https://files.catbox.moe/retry_ok.jpg"),
        ]
        mock_head.return_value = _mock_head_ok()

        url = upload_to_catbox(small_jpg, retries=3)

        assert url == "https://files.catbox.moe/retry_ok.jpg"
        assert mock_post.call_count == 2
        mock_sleep.assert_called_once_with(2)   # 2^1 = 2s first backoff

    @patch("app.uploader.time.sleep")
    @patch("app.uploader.requests.head")
    @patch("app.uploader.requests.post")
    def test_retries_on_network_error_then_succeeds(self, mock_post, mock_head, mock_sleep, small_jpg):
        mock_post.side_effect = [
            requests.ConnectionError("connection reset"),
            _mock_response(200, "https://files.catbox.moe/net_ok.jpg"),
        ]
        mock_head.return_value = _mock_head_ok()

        url = upload_to_catbox(small_jpg, retries=3)

        assert url == "https://files.catbox.moe/net_ok.jpg"
        assert mock_post.call_count == 2

    @patch("app.uploader.time.sleep")
    @patch("app.uploader.requests.head")
    @patch("app.uploader.requests.post")
    def test_raises_after_all_retries_exhausted(self, mock_post, mock_head, mock_sleep, small_jpg):
        mock_post.return_value = _mock_response(500, "always failing")
        mock_head.return_value = _mock_head_ok()

        with pytest.raises(UploadError, match="failed after 3 attempts"):
            upload_to_catbox(small_jpg, retries=3)

        assert mock_post.call_count == 3

    @patch("app.uploader.time.sleep")
    @patch("app.uploader.requests.head")
    @patch("app.uploader.requests.post")
    def test_exponential_backoff_delays(self, mock_post, mock_head, mock_sleep, small_jpg):
        """Verify sleep is called with 2s, 4s between 3 failing attempts."""
        mock_post.return_value = _mock_response(503, "service unavailable")

        with pytest.raises(UploadError):
            upload_to_catbox(small_jpg, retries=3)

        # 3 attempts → 2 sleeps: 2^1=2, 2^2=4
        assert mock_sleep.call_args_list == [call(2), call(4)]

    @patch("app.uploader.time.sleep")
    @patch("app.uploader.requests.head")
    @patch("app.uploader.requests.post")
    def test_single_retry_allowed(self, mock_post, mock_head, mock_sleep, small_jpg):
        mock_post.side_effect = [
            _mock_response(500, "bad"),
            _mock_response(200, "https://files.catbox.moe/ok.jpg"),
        ]
        mock_head.return_value = _mock_head_ok()

        url = upload_to_catbox(small_jpg, retries=2)
        assert url == "https://files.catbox.moe/ok.jpg"

    @patch("app.uploader.time.sleep")
    @patch("app.uploader.requests.head")
    @patch("app.uploader.requests.post")
    def test_upload_error_on_unreachable_url(self, mock_post, mock_head, mock_sleep, small_jpg):
        """If HEAD check fails even after upload, treat as UploadError and retry."""
        mock_post.return_value = _mock_response(200, "https://files.catbox.moe/bad.jpg")
        mock_head.return_value = _mock_head_fail(503)

        with pytest.raises(UploadError):
            upload_to_catbox(small_jpg, retries=1)


# ═══════════════════════════════════════════════════════════════════════════════
# 5.  upload_with_fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestUploadWithFallback:

    @patch("app.uploader.upload_to_catbox")
    def test_returns_catbox_url_when_catbox_succeeds(self, mock_catbox, small_jpg):
        mock_catbox.return_value = "https://files.catbox.moe/success.jpg"

        url = upload_with_fallback(small_jpg)

        assert url == "https://files.catbox.moe/success.jpg"
        mock_catbox.assert_called_once_with(small_jpg)

    @patch("app.uploader._upload_to_0x0")
    @patch("app.uploader.upload_to_catbox")
    def test_falls_back_to_0x0_when_catbox_fails(self, mock_catbox, mock_0x0, small_jpg):
        mock_catbox.side_effect = UploadError("catbox down")
        mock_0x0.return_value = "https://0x0.st/ABCD.jpg"

        url = upload_with_fallback(small_jpg)

        assert url == "https://0x0.st/ABCD.jpg"
        mock_catbox.assert_called_once()
        mock_0x0.assert_called_once_with(small_jpg)

    @patch("app.uploader._upload_to_0x0")
    @patch("app.uploader.upload_to_catbox")
    def test_raises_if_both_fail(self, mock_catbox, mock_0x0, small_jpg):
        mock_catbox.side_effect = UploadError("catbox down")
        mock_0x0.side_effect = UploadError("0x0 also down")

        with pytest.raises(UploadError):
            upload_with_fallback(small_jpg)

    def test_file_too_large_before_any_network_call(self, huge_file):
        import os
        fake_stat = os.stat_result((0o644, 0, 0, 1, 0, 0, MAX_FILE_SIZE_BYTES + 1, 0, 0, 0))
        with patch.object(Path, "stat", return_value=fake_stat):
            with pytest.raises(FileTooLargeError):
                upload_with_fallback(huge_file)

    def test_missing_file_raises_before_network(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            upload_with_fallback(tmp_path / "ghost.jpg")


# ═══════════════════════════════════════════════════════════════════════════════
# 6.  _upload_to_0x0
# ═══════════════════════════════════════════════════════════════════════════════

class TestUploadTo0x0:

    @patch("app.uploader.requests.head")
    @patch("app.uploader.requests.post")
    def test_returns_url_on_success(self, mock_post, mock_head, small_jpg):
        mock_post.return_value = _mock_response(200, "https://0x0.st/WXYZ.jpg")
        mock_head.return_value = _mock_head_ok()

        url = _upload_to_0x0(small_jpg)
        assert url == "https://0x0.st/WXYZ.jpg"

    @patch("app.uploader.requests.head")
    @patch("app.uploader.requests.post")
    def test_post_uses_file_key(self, mock_post, mock_head, small_jpg):
        mock_post.return_value = _mock_response(200, "https://0x0.st/ok.jpg")
        mock_head.return_value = _mock_head_ok()

        _upload_to_0x0(small_jpg)

        _, kwargs = mock_post.call_args
        assert "file" in kwargs["files"]

    @patch("app.uploader.time.sleep")
    @patch("app.uploader.requests.head")
    @patch("app.uploader.requests.post")
    def test_retries_on_failure(self, mock_post, mock_head, mock_sleep, small_jpg):
        mock_post.side_effect = [
            _mock_response(500, "fail"),
            _mock_response(200, "https://0x0.st/retry.jpg"),
        ]
        mock_head.return_value = _mock_head_ok()

        url = _upload_to_0x0(small_jpg, retries=3)
        assert url == "https://0x0.st/retry.jpg"
        assert mock_post.call_count == 2

    @patch("app.uploader.time.sleep")
    @patch("app.uploader.requests.head")
    @patch("app.uploader.requests.post")
    def test_raises_after_all_retries(self, mock_post, mock_head, mock_sleep, small_jpg):
        mock_post.return_value = _mock_response(502, "bad gateway")
        mock_head.return_value = _mock_head_ok()

        with pytest.raises(UploadError, match="0x0.st upload failed"):
            _upload_to_0x0(small_jpg, retries=2)

        assert mock_post.call_count == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 7.  Edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    @patch("app.uploader.requests.head")
    @patch("app.uploader.requests.post")
    def test_catbox_returns_error_text_not_url(self, mock_post, mock_head, small_jpg):
        """Catbox sometimes returns 'Error: ...' in a 200 body."""
        mock_post.return_value = _mock_response(200, "Error: file too large")
        mock_head.return_value = _mock_head_ok()  # won't be reached

        with pytest.raises(UploadError, match="not an HTTPS URL"):
            upload_to_catbox(small_jpg, retries=1)

    @patch("app.uploader.time.sleep")
    @patch("app.uploader.requests.head")
    @patch("app.uploader.requests.post")
    def test_retries_default_is_3(self, mock_post, mock_head, mock_sleep, small_jpg):
        mock_post.return_value = _mock_response(500, "fail")

        with pytest.raises(UploadError):
            upload_to_catbox(small_jpg)   # retries kwarg not passed

        assert mock_post.call_count == 3

    @patch("app.uploader.requests.head")
    @patch("app.uploader.requests.post")
    def test_mp4_file_uploads_correctly(self, mock_post, mock_head, tmp_path):
        vid = tmp_path / "clip.mp4"
        vid.write_bytes(b"\x00\x00\x00\x18ftyp" + b"\x00" * 512)   # fake mp4

        mock_post.return_value = _mock_response(200, "https://files.catbox.moe/clip123.mp4")
        mock_head.return_value = _mock_head_ok()

        url = upload_to_catbox(vid)
        assert url.endswith(".mp4")
