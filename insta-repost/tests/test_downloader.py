"""
tests/test_downloader.py
────────────────────────
Unit tests for app/downloader.py.

All network calls are mocked — no real Instagram traffic is made.
Run with:  pytest tests/test_downloader.py -v
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

from app.downloader import (
    DownloadResult,
    DownloaderError,
    PostNotFoundError,
    RateLimitedError,
    _extract_shortcode,
    _normalize_caption,
    _pick_media_file,
    download_from_url,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def tmp_dest(tmp_path: Path) -> Path:
    """A fresh temporary directory for each test."""
    return tmp_path


def _make_file(folder: Path, name: str) -> Path:
    """Helper: create a dummy file inside *folder*."""
    p = folder / name
    p.write_bytes(b"fake media content")
    return p


# ═══════════════════════════════════════════════════════════════════════════════
# 1.  Shortcode extraction
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtractShortcode:

    def test_standard_post_url(self):
        assert _extract_shortcode("https://www.instagram.com/p/CxYz123abcd/") == "CxYz123abcd"

    def test_reel_url(self):
        assert _extract_shortcode("https://www.instagram.com/reel/ABC123xyz/") == "ABC123xyz"

    def test_reels_url(self):
        assert _extract_shortcode("https://www.instagram.com/reels/ABC123xyz/") == "ABC123xyz"

    def test_tv_url(self):
        assert _extract_shortcode("https://www.instagram.com/tv/DEF456uvw/") == "DEF456uvw"

    def test_url_without_trailing_slash(self):
        assert _extract_shortcode("https://www.instagram.com/p/CxYz123abcd") == "CxYz123abcd"

    def test_url_with_query_string(self):
        assert _extract_shortcode(
            "https://www.instagram.com/p/CxYz123abcd/?igshid=abc"
        ) == "CxYz123abcd"

    def test_bare_shortcode_passthrough(self):
        assert _extract_shortcode("CxYz123abcd") == "CxYz123abcd"

    def test_bare_shortcode_with_hyphens_underscores(self):
        assert _extract_shortcode("CxYz_123-abcd") == "CxYz_123-abcd"

    def test_profile_url_raises(self):
        with pytest.raises(ValueError, match="Cannot extract"):
            _extract_shortcode("https://www.instagram.com/some_profile/")

    def test_home_url_raises(self):
        with pytest.raises(ValueError, match="Cannot extract"):
            _extract_shortcode("https://www.instagram.com/")

    def test_non_instagram_url_raises(self):
        with pytest.raises(ValueError, match="Cannot extract"):
            _extract_shortcode("https://twitter.com/p/CxYz123abcd/")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            _extract_shortcode("")

    def test_mobile_url(self):
        assert _extract_shortcode("https://m.instagram.com/p/CxYz123abcd/") == "CxYz123abcd"


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  Caption normalisation
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormalizeCaption:

    def test_strips_whitespace(self):
        assert _normalize_caption("  hello world  ") == "hello world"

    def test_strips_null_bytes(self):
        assert _normalize_caption("hello\x00world") == "helloworld"

    def test_truncates_at_2000(self):
        long = "a" * 2500
        result = _normalize_caption(long)
        assert len(result) == 2000

    def test_empty_string(self):
        assert _normalize_caption("") == ""

    def test_newlines_preserved(self):
        s = "line1\nline2"
        assert _normalize_caption(s) == "line1\nline2"

    def test_exactly_2000_chars_unchanged(self):
        s = "b" * 2000
        assert _normalize_caption(s) == s


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  Media file selection
# ═══════════════════════════════════════════════════════════════════════════════

class TestPickMediaFile:

    def test_prefers_mp4_over_jpg(self, tmp_dest):
        _make_file(tmp_dest, "post.jpg")
        _make_file(tmp_dest, "post.mp4")
        path, is_carousel = _pick_media_file(tmp_dest)
        assert path.suffix == ".mp4"
        assert not is_carousel

    def test_falls_back_to_jpg(self, tmp_dest):
        _make_file(tmp_dest, "post.jpg")
        path, is_carousel = _pick_media_file(tmp_dest)
        assert path.suffix == ".jpg"
        assert not is_carousel

    def test_falls_back_to_png(self, tmp_dest):
        _make_file(tmp_dest, "post.png")
        path, is_carousel = _pick_media_file(tmp_dest)
        assert path.suffix == ".png"
        assert not is_carousel

    def test_carousel_detected(self, tmp_dest):
        _make_file(tmp_dest, "slide1.jpg")
        _make_file(tmp_dest, "slide2.jpg")
        path, is_carousel = _pick_media_file(tmp_dest)
        assert is_carousel
        assert path.name == "slide1.jpg"

    def test_empty_folder_raises(self, tmp_dest):
        with pytest.raises(DownloaderError, match="No usable media file"):
            _pick_media_file(tmp_dest)

    def test_ignores_non_media_files(self, tmp_dest):
        _make_file(tmp_dest, "meta.json")
        _make_file(tmp_dest, "post.jpg")
        path, _ = _pick_media_file(tmp_dest)
        assert path.suffix == ".jpg"

    def test_mp4_carousel_returns_first_mp4(self, tmp_dest):
        _make_file(tmp_dest, "a_clip1.mp4")
        _make_file(tmp_dest, "b_clip2.mp4")
        path, is_carousel = _pick_media_file(tmp_dest)
        assert path.name == "a_clip1.mp4"
        assert is_carousel


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  download_from_url — instaloader happy path
# ═══════════════════════════════════════════════════════════════════════════════

class TestDownloadFromUrlInstaloader:

    @patch("app.downloader._download_with_instaloader")
    def test_returns_result_on_success(self, mock_il, tmp_dest):
        expected = DownloadResult(
            shortcode="CxYz123abcd",
            media_path=tmp_dest / "CxYz123abcd" / "post.jpg",
            caption="funny meme",
            source_user="memepage",
            media_type="IMAGE",
            is_carousel=False,
        )
        mock_il.return_value = expected

        result = download_from_url(
            "https://www.instagram.com/p/CxYz123abcd/", tmp_dest
        )

        mock_il.assert_called_once()
        assert result.shortcode == "CxYz123abcd"
        assert result.caption == "funny meme"
        assert result.source_user == "memepage"
        assert result.media_type == "IMAGE"

    @patch("app.downloader._download_with_ytdlp")
    @patch("app.downloader._download_with_instaloader")
    def test_falls_back_to_ytdlp_on_generic_error(self, mock_il, mock_yt, tmp_dest):
        mock_il.side_effect = RuntimeError("connection reset")
        expected = DownloadResult(
            shortcode="CxYz123abcd",
            media_path=tmp_dest / "CxYz123abcd" / "post.mp4",
            caption="",
            source_user="memepage",
            media_type="VIDEO",
            is_carousel=False,
        )
        mock_yt.return_value = expected

        result = download_from_url(
            "https://www.instagram.com/reel/CxYz123abcd/", tmp_dest
        )

        mock_il.assert_called_once()
        mock_yt.assert_called_once()
        assert result.media_type == "VIDEO"

    @patch("app.downloader._download_with_instaloader")
    def test_post_not_found_propagates_without_ytdlp(self, mock_il, tmp_dest):
        mock_il.side_effect = PostNotFoundError("private post")

        with pytest.raises(PostNotFoundError, match="private post"):
            download_from_url("https://www.instagram.com/p/PRIVATE123/", tmp_dest)

    @patch("app.downloader._download_with_instaloader")
    def test_rate_limited_propagates_without_ytdlp(self, mock_il, tmp_dest):
        mock_il.side_effect = RateLimitedError("429 too many requests")

        with pytest.raises(RateLimitedError):
            download_from_url("https://www.instagram.com/p/RATELIM123/", tmp_dest)

    @patch("app.downloader._download_with_ytdlp")
    @patch("app.downloader._download_with_instaloader")
    def test_both_fail_raises_downloader_error(self, mock_il, mock_yt, tmp_dest):
        mock_il.side_effect = RuntimeError("network error")
        mock_yt.side_effect = RuntimeError("yt-dlp network error")

        with pytest.raises(DownloaderError, match="All download strategies failed"):
            download_from_url("https://www.instagram.com/p/FAIL1234/", tmp_dest)

    def test_invalid_url_raises_value_error(self, tmp_dest):
        with pytest.raises(ValueError, match="Cannot extract"):
            download_from_url("https://www.instagram.com/some_profile/", tmp_dest)

    def test_reel_url_parses_correctly(self, tmp_dest):
        with patch("app.downloader._download_with_instaloader") as mock_il:
            mock_il.return_value = DownloadResult(
                shortcode="ReelABC123",
                media_path=tmp_dest / "ReelABC123" / "reel.mp4",
                caption="",
                source_user="creator",
                media_type="VIDEO",
                is_carousel=False,
            )
            result = download_from_url(
                "https://www.instagram.com/reels/ReelABC123/", tmp_dest
            )
        assert result.shortcode == "ReelABC123"


# ═══════════════════════════════════════════════════════════════════════════════
# 5.  instaloader integration (mocked instaloader library)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDownloadWithInstaloader:
    """Tests for _download_with_instaloader using a fully mocked instaloader."""

    def _make_mock_post(
        self,
        caption: str = "test caption",
        owner_username: str = "testuser",
        is_video: bool = False,
    ) -> MagicMock:
        post = MagicMock()
        post.caption = caption
        post.owner_username = owner_username
        post.is_video = is_video
        post.shortcode = "ABC123"
        return post

    @patch.dict(os.environ, {"IG_LOGIN_USER": "", "IG_LOGIN_PASS": ""})
    @patch("app.downloader._pick_media_file")
    @patch("instaloader.Post")
    @patch("instaloader.Instaloader")
    def test_image_post_success(self, MockIL, MockPost, mock_pick, tmp_dest):
        mock_post = self._make_mock_post(caption="funny meme", owner_username="memegod")
        MockPost.from_shortcode.return_value = mock_post

        media_file = tmp_dest / "ABC123" / "post.jpg"
        media_file.parent.mkdir(parents=True, exist_ok=True)
        media_file.write_bytes(b"fake")
        mock_pick.return_value = (media_file, False)

        il_instance = MockIL.return_value
        il_instance.context = MagicMock()

        from app.downloader import _download_with_instaloader
        with patch("app.downloader.instaloader", create=True) as mock_il_mod:
            mock_il_mod.Instaloader.return_value = il_instance
            mock_il_mod.Post.from_shortcode.return_value = mock_post
            mock_il_mod.exceptions.InstaloaderException = Exception
            mock_il_mod.exceptions.BadCredentialsException = ValueError

            result = _download_with_instaloader("ABC123", tmp_dest / "ABC123")

        assert result.source_user == "memegod"
        assert result.caption == "funny meme"
        assert result.media_type == "IMAGE"
        assert not result.is_carousel


# ═══════════════════════════════════════════════════════════════════════════════
# 6.  yt-dlp fallback integration (mocked yt_dlp library)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDownloadWithYtdlp:

    @patch("app.downloader._pick_media_file")
    @patch("app.downloader.yt_dlp")
    def test_success_returns_empty_caption(self, mock_ydl_mod, mock_pick, tmp_dest):
        media_file = tmp_dest / "ABC123" / "video.mp4"
        media_file.parent.mkdir(parents=True, exist_ok=True)
        media_file.write_bytes(b"fake video")
        mock_pick.return_value = (media_file, False)

        fake_info = {"uploader_id": "memecreator", "uploader": "Meme Creator"}

        mock_ydl_instance = MagicMock()
        mock_ydl_instance.__enter__ = MagicMock(return_value=mock_ydl_instance)
        mock_ydl_instance.__exit__ = MagicMock(return_value=False)
        mock_ydl_instance.extract_info.return_value = fake_info
        mock_ydl_mod.YoutubeDL.return_value = mock_ydl_instance
        # utils.DownloadError must be a real exception class for except clause
        mock_ydl_mod.utils.DownloadError = type("DownloadError", (Exception,), {})

        from app.downloader import _download_with_ytdlp
        result = _download_with_ytdlp("ABC123", tmp_dest / "ABC123")

        assert result.caption == ""
        assert result.source_user == "memecreator"
        assert result.media_type == "VIDEO"

    @patch("app.downloader._pick_media_file")
    @patch("app.downloader.yt_dlp")
    def test_download_error_private_raises_post_not_found(self, mock_ydl_mod, mock_pick, tmp_dest):
        FakeDownloadError = type("DownloadError", (Exception,), {})
        mock_ydl_mod.utils.DownloadError = FakeDownloadError

        mock_ydl_instance = MagicMock()
        mock_ydl_instance.__enter__ = MagicMock(return_value=mock_ydl_instance)
        mock_ydl_instance.__exit__ = MagicMock(return_value=False)
        mock_ydl_instance.extract_info.side_effect = FakeDownloadError(
            "private video login required"
        )
        mock_ydl_mod.YoutubeDL.return_value = mock_ydl_instance

        from app.downloader import _download_with_ytdlp
        with pytest.raises(PostNotFoundError):
            _download_with_ytdlp("PRIV123", tmp_dest / "PRIV123")

    @patch("app.downloader._pick_media_file")
    @patch("app.downloader.yt_dlp")
    def test_rate_limit_in_ytdlp_raises_rate_limited(self, mock_ydl_mod, mock_pick, tmp_dest):
        FakeDownloadError = type("DownloadError", (Exception,), {})
        mock_ydl_mod.utils.DownloadError = FakeDownloadError

        mock_ydl_instance = MagicMock()
        mock_ydl_instance.__enter__ = MagicMock(return_value=mock_ydl_instance)
        mock_ydl_instance.__exit__ = MagicMock(return_value=False)
        mock_ydl_instance.extract_info.side_effect = FakeDownloadError(
            "HTTP Error 429: too many requests"
        )
        mock_ydl_mod.YoutubeDL.return_value = mock_ydl_instance

        from app.downloader import _download_with_ytdlp
        with pytest.raises(RateLimitedError):
            _download_with_ytdlp("RATEOK1", tmp_dest / "RATEOK1")


# ═══════════════════════════════════════════════════════════════════════════════
# 7.  DownloadResult dataclass sanity
# ═══════════════════════════════════════════════════════════════════════════════

class TestDownloadResult:

    def test_fields(self, tmp_dest):
        p = tmp_dest / "media.jpg"
        p.write_bytes(b"x")
        r = DownloadResult(
            shortcode="ABC",
            media_path=p,
            caption="hello",
            source_user="bob",
            media_type="IMAGE",
            is_carousel=False,
        )
        assert r.shortcode == "ABC"
        assert r.media_path == p
        assert r.caption == "hello"
        assert r.source_user == "bob"
        assert r.media_type == "IMAGE"
        assert not r.is_carousel

    def test_is_carousel_true(self, tmp_dest):
        p = tmp_dest / "slide1.jpg"
        p.write_bytes(b"x")
        r = DownloadResult(
            shortcode="XYZ",
            media_path=p,
            caption="",
            source_user="alice",
            media_type="IMAGE",
            is_carousel=True,
        )
        assert r.is_carousel
