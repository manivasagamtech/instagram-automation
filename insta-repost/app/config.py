"""
app/config.py
─────────────
Central configuration loaded from environment variables.
Uses python-dotenv to populate os.environ from a .env file (if present).

Usage:
    from app.config import Config, ConfigError
    cfg = Config.from_env()
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict

from dotenv import load_dotenv

# Load .env into os.environ (no-op if file doesn't exist)
load_dotenv()


class ConfigError(Exception):
    """Raised when a required environment variable is missing or invalid."""


def _require(name: str) -> str:
    """Return the value of *name* from env or raise ConfigError."""
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(
            f"Required environment variable '{name}' is missing or empty. "
            f"Check your .env file or Railway variable settings."
        )
    return value


def _optional(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass
class Config:
    # Flask / App
    flask_secret_key: str
    app_password: str

    # Instagram / Meta
    ig_user_id: str
    ig_access_token: str
    fb_app_id: str
    fb_app_secret: str

    # Google Sheets
    google_credentials: Dict[str, Any]   # parsed from JSON string
    google_sheet_name: str

    # Downloader burner account
    ig_login_user: str
    ig_login_pass: str

    # Scheduler
    post_interval_minutes: int
    max_posts_per_day: int
    posting_hours_start: int
    posting_hours_end: int

    # Misc
    log_level: str
    port: int

    @classmethod
    def from_env(cls) -> "Config":
        """
        Load and validate all configuration from environment variables.

        Returns a fully populated Config instance.
        Raises ConfigError if any required variable is absent or unparseable.
        """
        # ── Required vars ──────────────────────────────────────────────
        flask_secret_key = _require("FLASK_SECRET_KEY")
        app_password = _require("APP_PASSWORD")
        ig_user_id = _require("IG_USER_ID")
        ig_access_token = _require("IG_ACCESS_TOKEN")
        fb_app_id = _require("FB_APP_ID")
        fb_app_secret = _require("FB_APP_SECRET")
        ig_login_user = _require("IG_LOGIN_USER")
        ig_login_pass = _require("IG_LOGIN_PASS")
        google_sheet_name = _require("GOOGLE_SHEET_NAME")

        # ── Google credentials JSON ───────────────────────────────────
        raw_creds = _require("GOOGLE_CREDENTIALS_JSON")
        try:
            google_credentials: Dict[str, Any] = json.loads(raw_creds)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                "GOOGLE_CREDENTIALS_JSON is not valid JSON. "
                "Make sure the entire service-account key file content "
                "is stored as a single JSON string."
            ) from exc

        # ── Optional / defaulted vars ─────────────────────────────────
        try:
            post_interval_minutes = int(_optional("POST_INTERVAL_MINUTES", "60"))
            max_posts_per_day = int(_optional("MAX_POSTS_PER_DAY", "5"))
            posting_hours_start = int(_optional("POSTING_HOURS_START", "8"))
            posting_hours_end = int(_optional("POSTING_HOURS_END", "22"))
            port = int(_optional("PORT", "8080"))
        except ValueError as exc:
            raise ConfigError(f"Numeric environment variable has non-integer value: {exc}") from exc

        log_level = _optional("LOG_LEVEL", "INFO").upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigError(
                f"LOG_LEVEL must be one of DEBUG/INFO/WARNING/ERROR/CRITICAL, got '{log_level}'."
            )

        return cls(
            flask_secret_key=flask_secret_key,
            app_password=app_password,
            ig_user_id=ig_user_id,
            ig_access_token=ig_access_token,
            fb_app_id=fb_app_id,
            fb_app_secret=fb_app_secret,
            google_credentials=google_credentials,
            google_sheet_name=google_sheet_name,
            ig_login_user=ig_login_user,
            ig_login_pass=ig_login_pass,
            post_interval_minutes=post_interval_minutes,
            max_posts_per_day=max_posts_per_day,
            posting_hours_start=posting_hours_start,
            posting_hours_end=posting_hours_end,
            log_level=log_level,
            port=port,
        )

    def __repr__(self) -> str:
        # Never print secrets
        return (
            f"Config(ig_user_id={self.ig_user_id!r}, "
            f"sheet={self.google_sheet_name!r}, "
            f"interval={self.post_interval_minutes}m, "
            f"max_per_day={self.max_posts_per_day}, "
            f"window={self.posting_hours_start}–{self.posting_hours_end}h)"
        )
