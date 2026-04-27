"""
app/scheduler.py
────────────────
APScheduler background worker — runs inside the single Gunicorn worker
process alongside the Flask app.

Jobs
────
  publish_job        — every POST_INTERVAL_MINUTES
                       Calls publisher.publish_next(). One post per tick.
  token_refresh_job  — every Sunday 03:00 UTC
                       Calls publisher.refresh_access_token(). Logs new
                       token at WARN so it shows up in Railway log stream.
  cleanup_job        — every day 04:00 UTC
                       Deletes files in downloads/ older than 24 h so the
                       container disk never fills up.

Design notes
────────────
• Single-worker deployment (--workers 1): the scheduler runs in the
  main process, no coordination needed.
• All jobs are wrapped in broad try/except — one failure never kills
  the scheduler or the web process.
• SIGTERM (sent by Railway on redeploy) triggers a clean shutdown via
  an atexit hook registered in start_scheduler().

Public API
──────────
    start_scheduler(cfg, queue_client) -> BackgroundScheduler
    stop_scheduler(scheduler) -> None
"""

from __future__ import annotations

import atexit
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.logger import get_logger
from app.publisher import PublisherError, publish_next, refresh_access_token

log = get_logger(__name__)

# Absolute path to the downloads scratch directory (same as web.py)
_DOWNLOADS_ROOT = Path(__file__).parent.parent / "downloads"
_DOWNLOAD_MAX_AGE_SECONDS = 24 * 60 * 60   # 24 hours


# ── Public API ─────────────────────────────────────────────────────────────────

def start_scheduler(cfg, queue_client) -> BackgroundScheduler:
    """
    Create, configure, and start the APScheduler :class:`BackgroundScheduler`.

    Registers three jobs:
      1. ``publish_job``       — interval, every ``cfg.post_interval_minutes`` min
      2. ``token_refresh_job`` — cron, every Sunday 03:00 UTC
      3. ``cleanup_job``       — cron, every day 04:00 UTC

    Also registers:
      • ``atexit`` hook for graceful shutdown on normal process exit
      • ``SIGTERM`` handler so Railway redeploys drain cleanly

    Args:
        cfg:          Loaded :class:`app.config.Config`.
        queue_client: Connected :class:`app.queue_client.QueueClient`.

    Returns:
        The running :class:`BackgroundScheduler` instance.
    """
    scheduler = BackgroundScheduler(timezone="UTC")

    # ── Job 1: publish_job ─────────────────────────────────────────────────────
    scheduler.add_job(
        func=_publish_job,
        trigger=IntervalTrigger(minutes=cfg.post_interval_minutes),
        id="publish_job",
        name="Publish next ready post",
        args=[cfg, queue_client],
        replace_existing=True,
        max_instances=1,        # never overlap; skip if previous tick is still running
        misfire_grace_time=60,  # allow up to 60 s late start before skipping
    )
    log.info(
        "Scheduled publish_job every %d minutes.", cfg.post_interval_minutes
    )

    # ── Job 2: token_refresh_job ───────────────────────────────────────────────
    scheduler.add_job(
        func=_token_refresh_job,
        trigger=CronTrigger(day_of_week="sun", hour=3, minute=0, timezone="UTC"),
        id="token_refresh_job",
        name="Weekly IG access-token refresh",
        args=[cfg],
        replace_existing=True,
        max_instances=1,
    )
    log.info("Scheduled token_refresh_job every Sunday 03:00 UTC.")

    # ── Job 3: cleanup_job ─────────────────────────────────────────────────────
    scheduler.add_job(
        func=_cleanup_job,
        trigger=CronTrigger(hour=4, minute=0, timezone="UTC"),
        id="cleanup_job",
        name="Cleanup stale download files",
        replace_existing=True,
        max_instances=1,
    )
    log.info("Scheduled cleanup_job every day 04:00 UTC.")

    # ── Graceful shutdown hooks ────────────────────────────────────────────────
    atexit.register(stop_scheduler, scheduler)
    _install_sigterm_handler(scheduler)

    scheduler.start()
    log.info(
        "BackgroundScheduler started. Jobs: %s",
        [j.id for j in scheduler.get_jobs()],
    )
    return scheduler


def stop_scheduler(scheduler: BackgroundScheduler) -> None:
    """
    Gracefully shut down the scheduler, waiting for running jobs to finish.

    Safe to call multiple times — checks ``scheduler.running`` first.

    Args:
        scheduler: The :class:`BackgroundScheduler` returned by
                   :func:`start_scheduler`.
    """
    if scheduler.running:
        log.info("Shutting down BackgroundScheduler …")
        try:
            scheduler.shutdown(wait=True)
            log.info("BackgroundScheduler stopped cleanly.")
        except Exception as exc:
            log.warning("Scheduler shutdown raised an exception: %s", exc)


# ── Job implementations ────────────────────────────────────────────────────────

def _publish_job(cfg, queue_client) -> None:
    """
    Scheduler tick: publish the next ready post.

    Wrapped in broad try/except so a single failure never kills the
    scheduler.  publisher.publish_next() already handles its own errors
    internally; this outer catch is a final safety net.
    """
    log.info("publish_job tick at %s UTC", _utc_now())
    try:
        post_id = publish_next(cfg, queue_client)
        if post_id:
            log.info("publish_job: published post %s", post_id)
        else:
            log.info("publish_job: nothing published this tick (window / cap / empty queue).")
    except Exception as exc:
        log.exception("publish_job raised an unexpected error: %s", exc)


def _token_refresh_job(cfg) -> None:
    """
    Weekly job: refresh the Instagram long-lived access token.

    Logs the new token loudly at WARN so it surfaces in the Railway
    log stream.  The operator must then update IG_ACCESS_TOKEN in the
    Railway environment variables panel.
    """
    log.info("token_refresh_job running at %s UTC", _utc_now())
    try:
        new_token = refresh_access_token(cfg)
        log.warning(
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "  TOKEN REFRESHED — update IG_ACCESS_TOKEN in Railway!\n"
            "  New token: %s\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            new_token,
        )
    except Exception as exc:
        log.exception("token_refresh_job failed: %s", exc)


def _cleanup_job() -> None:
    """
    Daily job: remove download folders older than 24 hours.

    Iterates subdirectories of :data:`_DOWNLOADS_ROOT`. Each subdirectory
    corresponds to one shortcode's downloaded files.  Directories are
    deleted if their mtime is older than :data:`_DOWNLOAD_MAX_AGE_SECONDS`.
    """
    log.info("cleanup_job running at %s UTC", _utc_now())
    if not _DOWNLOADS_ROOT.exists():
        log.debug("downloads/ does not exist — nothing to clean.")
        return

    now = time.time()
    removed = 0
    errors  = 0

    for entry in _DOWNLOADS_ROOT.iterdir():
        if not entry.is_dir():
            continue
        age = now - entry.stat().st_mtime
        if age > _DOWNLOAD_MAX_AGE_SECONDS:
            try:
                import shutil
                shutil.rmtree(entry, ignore_errors=True)
                log.debug("Removed stale download folder: %s (age %.0f s)", entry.name, age)
                removed += 1
            except Exception as exc:
                log.warning("Could not remove %s: %s", entry, exc)
                errors += 1

    log.info(
        "cleanup_job done: removed=%d errors=%d", removed, errors
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _utc_now() -> str:
    """Return a compact UTC timestamp string for log messages."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _install_sigterm_handler(scheduler: BackgroundScheduler) -> None:
    """
    Override the SIGTERM handler so Railway redeploys shut down cleanly.

    On SIGTERM: stop the scheduler (drain running jobs), then re-raise
    the default handler so the process exits with the correct code.

    Args:
        scheduler: The scheduler instance to stop on SIGTERM.
    """
    # SIGTERM is not available on Windows in the same way — guard it.
    if not hasattr(signal, "SIGTERM"):  # pragma: no cover
        return

    _original = signal.getsignal(signal.SIGTERM)

    def _handler(signum, frame):
        log.info("SIGTERM received — stopping scheduler before exit …")
        stop_scheduler(scheduler)
        # Restore and re-raise so the process exits properly
        signal.signal(signal.SIGTERM, _original)
        signal.raise_signal(signal.SIGTERM)

    signal.signal(signal.SIGTERM, _handler)
    log.debug("SIGTERM handler installed.")
