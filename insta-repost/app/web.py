"""
app/web.py
──────────
Password-protected Flask web interface.

Routes
──────
    GET  /              → login page (if not authenticated)
    POST /login         → authenticate with APP_PASSWORD
    GET  /logout        → clear session
    GET  /dashboard     → queue status table (auth required)
    POST /submit        → add a new Instagram URL/shortcode to the queue
    GET  /health        → unauthenticated health-check for Railway

The login uses a server-side session (Flask's signed cookie).
"""

from __future__ import annotations

from flask import Flask
from app.config import Config


def create_app(cfg: Config) -> Flask:
    """
    Application factory — creates and configures the Flask app.

    Args:
        cfg: Fully loaded :class:`Config` instance.

    Returns:
        A configured :class:`flask.Flask` application ready to serve.

    Raises:
        NotImplementedError: Until Phase 5 is implemented.
    """
    raise NotImplementedError("Phase 5")


def _login_required(f):
    """
    Decorator: redirect to /login if the user is not authenticated.

    Args:
        f: The view function to protect.

    Returns:
        Wrapped function that enforces authentication.

    Raises:
        NotImplementedError: Until Phase 5 is implemented.
    """
    raise NotImplementedError("Phase 5")


def _extract_shortcode(url_or_shortcode: str) -> str:
    """
    Parse an Instagram post URL or bare shortcode into just the shortcode.

    Accepts formats:
        - https://www.instagram.com/p/<shortcode>/
        - https://instagram.com/p/<shortcode>
        - <shortcode>  (bare)

    Args:
        url_or_shortcode: Raw user input from the submit form.

    Returns:
        The cleaned shortcode string.

    Raises:
        ValueError: If the input cannot be parsed as a valid shortcode.
        NotImplementedError: Until Phase 5 is implemented.
    """
    raise NotImplementedError("Phase 5")
