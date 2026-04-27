"""
main.py
───────
Module-level WSGI entrypoint for the Instagram repost bot.

Gunicorn (production)
─────────────────────
    gunicorn main:app --bind 0.0.0.0:$PORT --workers 1 --threads 4

Flask dev server (local)
────────────────────────
    python main.py

Boot sequence (shared by both)
───────────────────────────────
  1. Load and validate Config from environment variables.
  2. Create the Flask WSGI application via app.web.create_app().
  3. Unless TESTING=1 is set, connect QueueClient and start the
     APScheduler BackgroundScheduler (publish / token-refresh / cleanup).

TESTING guard
─────────────
Set ``TESTING=1`` in the environment (or in pytest's monkeypatch) to
suppress the scheduler during test runs.  The web tests call
``create_app()`` directly and never import this module, so in practice
the guard is a safety net for any future integration test that does
``import main``.

Single-worker requirement
─────────────────────────
Always deploy with ``--workers 1``.  The BackgroundScheduler lives
inside the Gunicorn worker process; multiple workers create duplicate
scheduler instances and cause double-posting.
"""

from __future__ import annotations

import os
import sys

from app.config import Config, ConfigError
from app.logger import get_logger

log = get_logger("main")

# ── Testing guard ──────────────────────────────────────────────────────────────
# Set TESTING=1 to skip the scheduler (useful for integration tests that
# import this module without real Google / Instagram credentials).
_TESTING: bool = os.environ.get("TESTING", "").strip().lower() in ("1", "true", "yes")


# ── Boot sequence ──────────────────────────────────────────────────────────────

def _boot():
    """
    Build the Flask app and (unless TESTING) start the background scheduler.

    Calls ``sys.exit(1)`` on fatal configuration errors so Railway surfaces
    a clean startup failure rather than a cryptic import traceback.

    Returns:
        The configured :class:`flask.Flask` WSGI application.
    """
    # 1. Config ─────────────────────────────────────────────────────────────────
    try:
        cfg = Config.from_env()
    except ConfigError as exc:
        log.error("Configuration error — cannot start: %s", exc)
        sys.exit(1)

    log.info(
        "Config loaded — window %02d:00–%02d:00 UTC | interval %d min | cap %d/day",
        cfg.posting_hours_start,
        cfg.posting_hours_end,
        cfg.post_interval_minutes,
        cfg.max_posts_per_day,
    )

    # 2. Flask app ───────────────────────────────────────────────────────────────
    from app.web import create_app

    flask_app = create_app(cfg)

    # 3. Scheduler (skipped in test runs) ────────────────────────────────────────
    if not _TESTING:
        from app.queue_client import QueueClient
        from app.scheduler import start_scheduler

        try:
            queue_client = QueueClient(cfg.google_credentials, cfg.google_sheet_name)
            start_scheduler(cfg, queue_client)
        except Exception as exc:
            log.error("Failed to start scheduler: %s", exc, exc_info=True)
            raise

        log.info("Scheduler started. Bot is live.")
    else:
        log.info("TESTING=1 — scheduler suppressed.")

    return flask_app


# Module-level WSGI object — imported by Gunicorn as ``main:app``
app = _boot()


# ── Dev-server entrypoint ──────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    log.info("Starting Flask development server on port %d …", port)
    # use_reloader=False prevents Werkzeug from spawning a second child process
    # that would launch a second scheduler instance.
    app.run(host="0.0.0.0", port=port, use_reloader=False)
