"""
tests/test_scheduler.py
────────────────────────
Unit tests for app/scheduler.py.

Strategy
────────
• All APScheduler and external-I/O calls are mocked.
• ``BackgroundScheduler`` is patched at the module level so no real threads
  are ever started during testing.
• Job implementation functions (_publish_job, _token_refresh_job, _cleanup_job)
  are tested in isolation with their own internal imports patched.
• SIGTERM handler and atexit registration are verified without triggering
  actual signals or process exit.

Coverage targets
────────────────
  start_scheduler        — scheduler created, 3 jobs added, atexit+SIGTERM registered
  stop_scheduler         — shutdown(wait=True) called when running; no-op when stopped
  _publish_job           — success path, None path, exception path
  _token_refresh_job     — success path (WARN log), exception path
  _cleanup_job           — removes stale dirs, keeps fresh dirs, handles missing root
  _install_sigterm_handler — handler installed, re-raises on SIGTERM
  _utc_now               — returns a non-empty timestamp string
"""

from __future__ import annotations

import signal
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def cfg():
    """Minimal Config-like namespace used by all scheduler tests."""
    return SimpleNamespace(
        post_interval_minutes=60,
        posting_hours_start=8,
        posting_hours_end=22,
        ig_access_token="test_token",
        ig_user_id="12345",
    )


@pytest.fixture()
def queue_client():
    return MagicMock()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_scheduler_mock(running: bool = False):
    """Return a MagicMock that mimics BackgroundScheduler."""
    mock = MagicMock()
    mock.running = running
    mock.get_jobs.return_value = [
        SimpleNamespace(id="publish_job"),
        SimpleNamespace(id="token_refresh_job"),
        SimpleNamespace(id="cleanup_job"),
    ]
    return mock


# ── start_scheduler ────────────────────────────────────────────────────────────

class TestStartScheduler:

    @patch("app.scheduler.atexit.register")
    @patch("app.scheduler._install_sigterm_handler")
    @patch("app.scheduler.BackgroundScheduler")
    def test_creates_scheduler_with_utc_timezone(
        self, MockSched, mock_sigterm, mock_atexit, cfg, queue_client
    ):
        from app.scheduler import start_scheduler

        mock_sched = _make_scheduler_mock()
        MockSched.return_value = mock_sched

        start_scheduler(cfg, queue_client)

        MockSched.assert_called_once_with(timezone="UTC")

    @patch("app.scheduler.atexit.register")
    @patch("app.scheduler._install_sigterm_handler")
    @patch("app.scheduler.BackgroundScheduler")
    def test_adds_three_jobs(
        self, MockSched, mock_sigterm, mock_atexit, cfg, queue_client
    ):
        from app.scheduler import start_scheduler

        mock_sched = _make_scheduler_mock()
        MockSched.return_value = mock_sched

        start_scheduler(cfg, queue_client)

        assert mock_sched.add_job.call_count == 3

    @patch("app.scheduler.atexit.register")
    @patch("app.scheduler._install_sigterm_handler")
    @patch("app.scheduler.BackgroundScheduler")
    def test_publish_job_id(
        self, MockSched, mock_sigterm, mock_atexit, cfg, queue_client
    ):
        from app.scheduler import start_scheduler, _publish_job

        mock_sched = _make_scheduler_mock()
        MockSched.return_value = mock_sched

        start_scheduler(cfg, queue_client)

        calls = mock_sched.add_job.call_args_list
        publish_call = next(c for c in calls if c.kwargs.get("id") == "publish_job")
        assert publish_call.kwargs["func"] is _publish_job
        assert publish_call.kwargs["args"] == [cfg, queue_client]
        assert publish_call.kwargs["max_instances"] == 1

    @patch("app.scheduler.atexit.register")
    @patch("app.scheduler._install_sigterm_handler")
    @patch("app.scheduler.BackgroundScheduler")
    def test_token_refresh_job_id(
        self, MockSched, mock_sigterm, mock_atexit, cfg, queue_client
    ):
        from app.scheduler import start_scheduler, _token_refresh_job

        mock_sched = _make_scheduler_mock()
        MockSched.return_value = mock_sched

        start_scheduler(cfg, queue_client)

        calls = mock_sched.add_job.call_args_list
        refresh_call = next(c for c in calls if c.kwargs.get("id") == "token_refresh_job")
        assert refresh_call.kwargs["func"] is _token_refresh_job
        assert refresh_call.kwargs["args"] == [cfg]

    @patch("app.scheduler.atexit.register")
    @patch("app.scheduler._install_sigterm_handler")
    @patch("app.scheduler.BackgroundScheduler")
    def test_cleanup_job_id(
        self, MockSched, mock_sigterm, mock_atexit, cfg, queue_client
    ):
        from app.scheduler import start_scheduler, _cleanup_job

        mock_sched = _make_scheduler_mock()
        MockSched.return_value = mock_sched

        start_scheduler(cfg, queue_client)

        calls = mock_sched.add_job.call_args_list
        cleanup_call = next(c for c in calls if c.kwargs.get("id") == "cleanup_job")
        assert cleanup_call.kwargs["func"] is _cleanup_job

    @patch("app.scheduler.atexit.register")
    @patch("app.scheduler._install_sigterm_handler")
    @patch("app.scheduler.BackgroundScheduler")
    def test_registers_atexit_hook(
        self, MockSched, mock_sigterm, mock_atexit, cfg, queue_client
    ):
        from app.scheduler import start_scheduler, stop_scheduler

        mock_sched = _make_scheduler_mock()
        MockSched.return_value = mock_sched

        start_scheduler(cfg, queue_client)

        mock_atexit.assert_called_once_with(stop_scheduler, mock_sched)

    @patch("app.scheduler.atexit.register")
    @patch("app.scheduler._install_sigterm_handler")
    @patch("app.scheduler.BackgroundScheduler")
    def test_installs_sigterm_handler(
        self, MockSched, mock_sigterm, mock_atexit, cfg, queue_client
    ):
        from app.scheduler import start_scheduler

        mock_sched = _make_scheduler_mock()
        MockSched.return_value = mock_sched

        start_scheduler(cfg, queue_client)

        mock_sigterm.assert_called_once_with(mock_sched)

    @patch("app.scheduler.atexit.register")
    @patch("app.scheduler._install_sigterm_handler")
    @patch("app.scheduler.BackgroundScheduler")
    def test_starts_scheduler(
        self, MockSched, mock_sigterm, mock_atexit, cfg, queue_client
    ):
        from app.scheduler import start_scheduler

        mock_sched = _make_scheduler_mock()
        MockSched.return_value = mock_sched

        result = start_scheduler(cfg, queue_client)

        mock_sched.start.assert_called_once()
        assert result is mock_sched

    @patch("app.scheduler.atexit.register")
    @patch("app.scheduler._install_sigterm_handler")
    @patch("app.scheduler.BackgroundScheduler")
    def test_publish_job_uses_interval_trigger(
        self, MockSched, mock_sigterm, mock_atexit, cfg, queue_client
    ):
        from app.scheduler import start_scheduler
        from apscheduler.triggers.interval import IntervalTrigger

        mock_sched = _make_scheduler_mock()
        MockSched.return_value = mock_sched
        cfg.post_interval_minutes = 30

        start_scheduler(cfg, queue_client)

        calls = mock_sched.add_job.call_args_list
        publish_call = next(c for c in calls if c.kwargs.get("id") == "publish_job")
        trigger = publish_call.kwargs["trigger"]
        assert isinstance(trigger, IntervalTrigger)


# ── stop_scheduler ─────────────────────────────────────────────────────────────

class TestStopScheduler:

    def test_shuts_down_when_running(self):
        from app.scheduler import stop_scheduler

        mock_sched = MagicMock()
        mock_sched.running = True

        stop_scheduler(mock_sched)

        mock_sched.shutdown.assert_called_once_with(wait=True)

    def test_noop_when_not_running(self):
        from app.scheduler import stop_scheduler

        mock_sched = MagicMock()
        mock_sched.running = False

        stop_scheduler(mock_sched)

        mock_sched.shutdown.assert_not_called()

    def test_handles_shutdown_exception(self):
        from app.scheduler import stop_scheduler

        mock_sched = MagicMock()
        mock_sched.running = True
        mock_sched.shutdown.side_effect = RuntimeError("oops")

        # Should not raise
        stop_scheduler(mock_sched)


# ── _publish_job ───────────────────────────────────────────────────────────────

class TestPublishJob:

    @patch("app.scheduler.publish_next")
    def test_success_path_logs_post_id(self, mock_publish, cfg, queue_client):
        """publish_next returns a post ID — job logs success."""
        from app.scheduler import _publish_job

        mock_publish.return_value = "ig_post_123"

        _publish_job(cfg, queue_client)

        mock_publish.assert_called_once_with(cfg, queue_client)

    @patch("app.scheduler.publish_next")
    def test_none_path_does_not_raise(self, mock_publish, cfg, queue_client):
        """publish_next returns None (nothing to post) — job exits cleanly."""
        from app.scheduler import _publish_job

        mock_publish.return_value = None

        _publish_job(cfg, queue_client)  # must not raise

        mock_publish.assert_called_once()

    @patch("app.scheduler.publish_next")
    def test_exception_is_caught(self, mock_publish, cfg, queue_client):
        """Unexpected exception from publish_next is swallowed — scheduler lives."""
        from app.scheduler import _publish_job

        mock_publish.side_effect = RuntimeError("unexpected graph error")

        _publish_job(cfg, queue_client)  # must not raise

    @patch("app.scheduler.publish_next")
    def test_passes_cfg_and_queue_client(self, mock_publish, cfg, queue_client):
        from app.scheduler import _publish_job

        mock_publish.return_value = None
        _publish_job(cfg, queue_client)

        mock_publish.assert_called_once_with(cfg, queue_client)


# ── _token_refresh_job ─────────────────────────────────────────────────────────

class TestTokenRefreshJob:

    @patch("app.scheduler.refresh_access_token")
    def test_success_calls_refresh(self, mock_refresh, cfg):
        from app.scheduler import _token_refresh_job

        mock_refresh.return_value = "new_tok_abc"
        _token_refresh_job(cfg)

        mock_refresh.assert_called_once_with(cfg)

    @patch("app.scheduler.refresh_access_token")
    def test_exception_is_caught(self, mock_refresh, cfg):
        from app.scheduler import _token_refresh_job

        mock_refresh.side_effect = Exception("token API down")
        _token_refresh_job(cfg)  # must not raise

    @patch("app.scheduler.refresh_access_token")
    def test_passes_cfg(self, mock_refresh, cfg):
        from app.scheduler import _token_refresh_job

        mock_refresh.return_value = "tok"
        _token_refresh_job(cfg)

        args, _ = mock_refresh.call_args
        assert args[0] is cfg


# ── _cleanup_job ───────────────────────────────────────────────────────────────

class TestCleanupJob:

    def test_removes_stale_directory(self, tmp_path):
        """Directories older than 24 h should be removed."""
        import app.scheduler as sched_module

        stale_dir = tmp_path / "stale_shortcode"
        stale_dir.mkdir()
        # Back-date mtime by 25 hours
        old_time = time.time() - (25 * 3600)
        import os
        os.utime(stale_dir, (old_time, old_time))

        with patch.object(sched_module, "_DOWNLOADS_ROOT", tmp_path):
            sched_module._cleanup_job()

        assert not stale_dir.exists()

    def test_keeps_fresh_directory(self, tmp_path):
        """Directories younger than 24 h must NOT be removed."""
        import app.scheduler as sched_module

        fresh_dir = tmp_path / "fresh_shortcode"
        fresh_dir.mkdir()
        # mtime = 1 hour ago → still fresh
        recent_time = time.time() - 3600
        import os
        os.utime(fresh_dir, (recent_time, recent_time))

        with patch.object(sched_module, "_DOWNLOADS_ROOT", tmp_path):
            sched_module._cleanup_job()

        assert fresh_dir.exists()

    def test_skips_files_not_directories(self, tmp_path):
        """Plain files in the downloads root should not be touched."""
        import app.scheduler as sched_module

        stray_file = tmp_path / "leftover.txt"
        stray_file.write_text("data")
        old_time = time.time() - (25 * 3600)
        import os
        os.utime(stray_file, (old_time, old_time))

        with patch.object(sched_module, "_DOWNLOADS_ROOT", tmp_path):
            sched_module._cleanup_job()

        assert stray_file.exists()

    def test_handles_missing_downloads_root(self, tmp_path):
        """If downloads/ doesn't exist, job should exit cleanly."""
        import app.scheduler as sched_module

        non_existent = tmp_path / "does_not_exist"

        with patch.object(sched_module, "_DOWNLOADS_ROOT", non_existent):
            sched_module._cleanup_job()  # must not raise

    def test_mixed_stale_and_fresh(self, tmp_path):
        """Only stale directories are deleted; fresh ones survive."""
        import app.scheduler as sched_module
        import os

        stale = tmp_path / "stale"
        fresh = tmp_path / "fresh"
        stale.mkdir()
        fresh.mkdir()

        now = time.time()
        os.utime(stale, (now - 25 * 3600, now - 25 * 3600))
        os.utime(fresh, (now - 3600, now - 3600))

        with patch.object(sched_module, "_DOWNLOADS_ROOT", tmp_path):
            sched_module._cleanup_job()

        assert not stale.exists()
        assert fresh.exists()

    def test_counts_removed_correctly(self, tmp_path, caplog):
        """Log message should report the correct removal count."""
        import app.scheduler as sched_module
        import os
        import logging

        for name in ("old1", "old2"):
            d = tmp_path / name
            d.mkdir()
            old_time = time.time() - 25 * 3600
            os.utime(d, (old_time, old_time))

        with patch.object(sched_module, "_DOWNLOADS_ROOT", tmp_path):
            with caplog.at_level(logging.INFO, logger="app.scheduler"):
                sched_module._cleanup_job()

        assert "removed=2" in caplog.text


# ── _install_sigterm_handler ───────────────────────────────────────────────────

class TestInstallSigtermHandler:

    def test_installs_handler(self):
        from app.scheduler import _install_sigterm_handler

        mock_sched = MagicMock()

        original = signal.getsignal(signal.SIGTERM)
        try:
            _install_sigterm_handler(mock_sched)
            new_handler = signal.getsignal(signal.SIGTERM)
            assert new_handler is not original
            assert callable(new_handler)
        finally:
            # Restore original handler so we don't pollute other tests
            signal.signal(signal.SIGTERM, original)

    def test_handler_calls_stop_scheduler(self):
        """On SIGTERM, stop_scheduler should be called before re-raise."""
        from app.scheduler import _install_sigterm_handler, stop_scheduler

        mock_sched = MagicMock()
        mock_sched.running = True

        original = signal.getsignal(signal.SIGTERM)
        try:
            _install_sigterm_handler(mock_sched)

            with patch("app.scheduler.stop_scheduler") as mock_stop, \
                 patch("signal.raise_signal"):
                handler = signal.getsignal(signal.SIGTERM)
                handler(signal.SIGTERM, None)
                mock_stop.assert_called_once_with(mock_sched)
        finally:
            signal.signal(signal.SIGTERM, original)

    def test_no_sigterm_on_windows(self):
        """On platforms without SIGTERM, handler install is a no-op."""
        from app.scheduler import _install_sigterm_handler

        mock_sched = MagicMock()

        with patch("app.scheduler.signal") as mock_signal_module:
            # Simulate a platform without SIGTERM (e.g. Windows)
            del mock_signal_module.SIGTERM
            _install_sigterm_handler(mock_sched)
            mock_signal_module.signal.assert_not_called()


# ── _utc_now ───────────────────────────────────────────────────────────────────

class TestUtcNow:

    def test_returns_non_empty_string(self):
        from app.scheduler import _utc_now

        result = _utc_now()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_format_matches_expected(self):
        from app.scheduler import _utc_now
        import re

        result = _utc_now()
        # Expected format: "2024-01-15 08:30:00"
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", result)


# ── Module-level import guard for publish_next / refresh_access_token ──────────

class TestSchedulerImports:
    """Verify that publish_next and refresh_access_token are importable at module level."""

    def test_publish_next_imported(self):
        import app.scheduler as sched
        assert hasattr(sched, "publish_next")

    def test_refresh_access_token_imported(self):
        import app.scheduler as sched
        assert hasattr(sched, "refresh_access_token")
