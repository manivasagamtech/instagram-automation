"""
main.py
───────
Production + development entrypoint for the Instagram repost bot.

Usage
─────
  Production (Gunicorn / Railway):
      gunicorn main:application --bind 0.0.0.0:$PORT --workers 1 --threads 4

  Development:
      python main.py

How it works
────────────
``_create_application()`` is called at module import time so that both
``gunicorn main:application`` and ``python main.py`` share the same
boot sequence:

  1. Load and validate :class:`app.config.Config` from env vars.
  2. Create the Flask application via :func:`app.web.create_app`.
  3. Connect a :class:`app.queue_client.QueueClient` to Google Sheets.
  4. Start the :class:`apscheduler.schedulers.background.BackgroundScheduler`
     (publish_job / token_refresh_job / cleanup_job).

The scheduler's atexit hook and SIGTERM handler (both registered inside
:func:`app.scheduler.start_scheduler`) ensure clean shutdown on Railway
redeployments.

Single-worker requirement
─────────────────────────
Always run with ``--workers 1`` in production.  The BackgroundScheduler
runs inside the Gunicorn worker process; multiple workers would create
duplicate scheduler instances, leading to double-posting.
"""

from __future__ import annotations

import os
import sys

from app.config import Config, ConfigError
from app.logger import get_logger

log = get_logger("main")


# ── Application factory ────────────────────────────────────────────────────────

def _create_application():
    """
    Build the Flask WSGI application and start the background scheduler.

    Exits the process (code 1) on configuration errors so Railway surfaces
    a clear startup failure rather than a cryptic import traceback.

    Returns:
        The configured :class:`flask.Flask` WSGI application.
    """
    # ── 1. Config ──────────────────────────────────────────────────────────────
    try:
        cfg = Config.from_env()
    except ConfigError as exc:
        log.error("Configuration error — cannot start: %s", exc)
        sys.exit(1)

    log.info(
        "Config loaded — window %02d:00–%02d:00 UTC | interval %d min | max %d/day",
        cfg.posting_hours_start,
        cfg.posting_hours_end,
        cfg.post_interval_minutes,
        cfg.max_posts_per_day,
    )

    # ── 2. Flask app ───────────────────────────────────────────────────────────
    from app.web import create_app  # local import keeps testability clean

    app = create_app(cfg)

    # ── 3. Queue client + Scheduler ────────────────────────────────────────────
    from app.queue_client import QueueClient
    from app.scheduler import start_scheduler

    try:
        queue_client = QueueClient(cfg.google_credentials, cfg.google_sheet_name)
        start_scheduler(cfg, queue_client)
    except Exception as exc:
        log.error("Failed to initialise scheduler: %s", exc, exc_info=True)
        raise

    return app


# Module-level WSGI callable — Gunicorn imports this directly:
#   gunicorn main:application
application = _create_application()


# ── Dev-server entrypoint ──────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    log.info("Starting Flask development server on port %d …", port)
    # use_reloader=False — prevents a second process from spawning a second
    # scheduler instance during development.
    application.run(host="0.0.0.0", port=port, use_reloader=False)
