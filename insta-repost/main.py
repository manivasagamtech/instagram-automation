"""
main.py
───────
Phase 1 sanity-check entrypoint.

Loads Config.from_env(), verifies all required variables are present,
logs a startup message, and exits 0.

In later phases this will hand off to the Flask app + scheduler.
"""

import sys

from app.config import Config, ConfigError
from app.logger import get_logger

log = get_logger("main")


def main() -> int:
    log.info("Bot starting up…")

    try:
        cfg = Config.from_env()
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return 1

    log.info("Configuration loaded successfully: %s", cfg)
    log.info(
        "Posting window: %02d:00 – %02d:00 | interval: %d min | max/day: %d",
        cfg.posting_hours_start,
        cfg.posting_hours_end,
        cfg.post_interval_minutes,
        cfg.max_posts_per_day,
    )
    log.info("Phase 1 complete — all env vars present. Ready for Phase 2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
