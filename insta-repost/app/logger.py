"""
app/logger.py
─────────────
Centralised logging setup for the Instagram repost bot.

All modules should obtain their logger via:
    from app.logger import get_logger
    log = get_logger(__name__)

The root log level is controlled by the LOG_LEVEL environment variable
(defaults to INFO).  Output is always to stdout so Railway / Docker
can capture it without extra configuration.
"""

from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False


def _configure() -> None:
    """Configure the root logger once per process."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)

    formatter = logging.Formatter(
        fmt="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    # Avoid adding duplicate handlers if this is called multiple times
    if not root.handlers:
        root.addHandler(handler)
    else:
        root.handlers.clear()
        root.addHandler(handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger, ensuring the root logger is configured first.

    Args:
        name: Typically ``__name__`` of the calling module.

    Returns:
        A :class:`logging.Logger` instance.
    """
    _configure()
    return logging.getLogger(name)
