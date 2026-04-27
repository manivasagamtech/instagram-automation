"""
app/web.py
──────────
Flask application factory for the Instagram repost bot web interface.

Routes
──────
    GET  /                       → redirect to /submit (authed) or /login
    POST /login                  → authenticate with APP_PASSWORD
    GET  /logout                 → clear session, redirect to /login
    GET  /submit                 → URL submission form
    POST /submit                 → download media, redirect to preview
    GET  /submit/preview         → caption-edit preview page
    POST /submit/confirm         → upload to Catbox, append to queue
    GET  /preview/<shortcode>    → serve downloaded media file
    GET  /queue                  → dashboard: all queue rows
    POST /queue/<int:row>/approve → set status=ready
    POST /queue/<int:row>/reject  → set status=rejected
    GET  /healthz                → Railway health check (always 200)
"""

from __future__ import annotations

import os
import secrets
import shutil
import time
import traceback
from datetime import timedelta
from pathlib import Path
from typing import Optional

from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from app.downloader import (
    DownloaderError,
    PostNotFoundError,
    RateLimitedError,
    download_from_url,
)
from app.logger import get_logger
from app.queue_client import (
    DuplicateError,
    QueueClient,
    QueueError,
    STATUS_READY,
)
from app.uploader import FileTooLargeError, UploadError, upload_with_fallback

log = get_logger(__name__)

# ── Module-level state ─────────────────────────────────────────────────────────

# Sliding-window rate limiter: session_id → list of request timestamps
_rate_limits: dict[str, list[float]] = {}

# Absolute path to the downloads scratch folder
_DOWNLOADS_ROOT = Path(__file__).parent.parent / "downloads"


# ── App factory ────────────────────────────────────────────────────────────────

def create_app(cfg=None) -> Flask:
    """
    Flask application factory.

    Args:
        cfg: Optional pre-built :class:`app.config.Config`.  When ``None``
             (the default used by the Flask CLI), config is loaded from env.

    Returns:
        A configured :class:`flask.Flask` application.
    """
    from app.config import Config, ConfigError  # local import avoids circular

    if cfg is None:
        cfg = Config.from_env()

    app = Flask(__name__, template_folder="templates")
    app.secret_key = cfg.flask_secret_key
    app.permanent_session_lifetime = timedelta(days=7)

    # Store config on the app for access inside request handlers
    app.config["BOT_CONFIG"] = cfg
    app.config["APP_PASSWORD"] = cfg.app_password

    # Make CSRF token generator available in every template
    @app.context_processor
    def _inject_globals():
        return {
            "csrf_token": _get_csrf_token,
            "status_badge": _status_badge,
        }

    # ── Routes ─────────────────────────────────────────────────────────────────

    @app.route("/")
    def index():
        if _is_authenticated():
            return redirect(url_for("submit"))
        return redirect(url_for("login"))

    # ── Auth ───────────────────────────────────────────────────────────────────

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if _is_authenticated():
            return redirect(url_for("submit"))

        error: Optional[str] = None
        if request.method == "POST":
            password   = request.form.get("password", "")
            expected   = app.config["APP_PASSWORD"]
            csrf_ok    = request.form.get("_csrf_token") == session.get("_csrf_token")

            if not csrf_ok:
                abort(403)

            if secrets.compare_digest(password, expected):
                session.permanent = True
                session["authenticated"] = True
                log.info("Login successful from %s", request.remote_addr)
                return redirect(url_for("submit"))
            else:
                error = "Incorrect password."
                log.warning("Failed login attempt from %s", request.remote_addr)

        return render_template("login.html", error=error)

    @app.route("/logout")
    def logout():
        session.clear()
        flash("Logged out.", "info")
        return redirect(url_for("login"))

    # ── Submit ─────────────────────────────────────────────────────────────────

    @app.route("/submit", methods=["GET"])
    @_require_auth
    def submit():
        return render_template("submit.html")

    @app.route("/submit", methods=["POST"])
    @_require_auth
    def submit_post():
        _validate_csrf()

        # Rate limiting: 10 req/min per session
        sid = session.get("_csrf_token", "anon")
        if not _rate_limit_ok(sid):
            flash("Too many requests — please wait a minute.", "error")
            return redirect(url_for("submit"))

        url = request.form.get("url", "").strip()
        if not url:
            flash("Please enter an Instagram URL.", "error")
            return redirect(url_for("submit"))

        _DOWNLOADS_ROOT.mkdir(parents=True, exist_ok=True)

        try:
            result = download_from_url(url, _DOWNLOADS_ROOT)
        except ValueError as exc:
            flash(f"Invalid URL: {exc}", "error")
            return redirect(url_for("submit"))
        except PostNotFoundError as exc:
            flash(f"Post not found (private or deleted): {exc}", "error")
            return redirect(url_for("submit"))
        except RateLimitedError:
            flash("Instagram is rate-limiting downloads. Try again in a few minutes.", "error")
            return redirect(url_for("submit"))
        except DownloaderError as exc:
            log.error("Download failed:\n%s", traceback.format_exc())
            flash(f"Download failed: {exc}", "error")
            return redirect(url_for("submit"))

        credit = f"\n\n🎥 Credits: @{result.source_user}" if result.source_user else ""
        default_caption = result.caption + credit

        # Store pending download in session (JSON-safe strings)
        session["pending"] = {
            "shortcode":   result.shortcode,
            "media_path":  str(result.media_path),
            "caption":     result.caption,
            "source_user": result.source_user,
            "media_type":  result.media_type,
            "is_carousel": result.is_carousel,
            "default_caption": default_caption,
        }

        log.info(
            "Download complete for %s (%s) — showing preview",
            result.shortcode, result.media_type,
        )
        return redirect(url_for("submit_preview"))

    @app.route("/submit/preview", methods=["GET"])
    @_require_auth
    def submit_preview():
        pending = session.get("pending")
        if not pending:
            flash("No pending download. Please submit a URL first.", "error")
            return redirect(url_for("submit"))
        return render_template("submit_preview.html", pending=pending)

    @app.route("/submit/confirm", methods=["POST"])
    @_require_auth
    def submit_confirm():
        _validate_csrf()

        pending = session.get("pending")
        if not pending:
            flash("Session expired. Please submit the URL again.", "error")
            return redirect(url_for("submit"))

        final_caption = request.form.get("caption", "").strip()
        media_path    = Path(pending["media_path"])

        if not media_path.exists():
            flash("Downloaded file is missing. Please re-submit the URL.", "error")
            session.pop("pending", None)
            return redirect(url_for("submit"))

        try:
            media_url = upload_with_fallback(media_path)
        except FileTooLargeError as exc:
            flash(f"File too large to upload: {exc}", "error")
            return redirect(url_for("submit_preview"))
        except UploadError as exc:
            log.error("Upload failed:\n%s", traceback.format_exc())
            flash(f"Upload failed: {exc}", "error")
            return redirect(url_for("submit_preview"))

        # Clean up local file after successful upload
        try:
            shutil.rmtree(media_path.parent, ignore_errors=True)
            log.info("Deleted local download folder: %s", media_path.parent)
        except Exception as exc:
            log.warning("Could not delete download folder: %s", exc)

        cfg = app.config["BOT_CONFIG"]
        queue = _get_queue_client(cfg)

        try:
            row_idx = queue.append(
                shortcode   = pending["shortcode"],
                media_url   = media_url,
                caption     = final_caption,
                source_user = pending["source_user"],
                media_type  = pending["media_type"],
            )
        except DuplicateError:
            flash(
                f"Shortcode '{pending['shortcode']}' is already in the queue.",
                "error",
            )
            return redirect(url_for("queue"))
        except QueueError as exc:
            log.error("Queue append failed:\n%s", traceback.format_exc())
            flash(f"Failed to add to queue: {exc}", "error")
            return redirect(url_for("submit_preview"))

        session.pop("pending", None)
        flash(
            f"✅ Queued '{pending['shortcode']}' at row {row_idx} — status: pending.",
            "success",
        )
        log.info(
            "Queued shortcode '%s' at row %d (url=%s)",
            pending["shortcode"], row_idx, media_url,
        )
        return redirect(url_for("queue"))

    # ── Media preview serving ──────────────────────────────────────────────────

    @app.route("/preview/<shortcode>")
    @_require_auth
    def preview_media(shortcode: str):
        # Safety: only allow alphanumeric + _ -
        if not _safe_shortcode(shortcode):
            abort(400)

        folder = _DOWNLOADS_ROOT / shortcode
        if not folder.exists():
            abort(404)

        for ext in [".mp4", ".jpg", ".jpeg", ".png", ".webp"]:
            candidates = sorted(folder.glob(f"*{ext}"))
            if candidates:
                return send_file(candidates[0])

        abort(404)

    # ── Queue dashboard ────────────────────────────────────────────────────────

    @app.route("/queue", methods=["GET"])
    @_require_auth
    def queue():
        cfg    = app.config["BOT_CONFIG"]
        client = _get_queue_client(cfg)

        try:
            rows = client.get_all()
        except QueueError as exc:
            log.error("Failed to fetch queue: %s", exc)
            flash(f"Could not load queue: {exc}", "error")
            rows = []

        return render_template("queue.html", rows=rows)

    @app.route("/queue/<int:row>/approve", methods=["POST"])
    @_require_auth
    def queue_approve(row: int):
        _validate_csrf()
        cfg    = app.config["BOT_CONFIG"]
        client = _get_queue_client(cfg)

        try:
            client.update_status(row, STATUS_READY)
            flash(f"Row {row} approved — status set to ready.", "success")
        except QueueError as exc:
            flash(f"Failed to approve row {row}: {exc}", "error")

        return redirect(url_for("queue"))

    @app.route("/queue/<int:row>/reject", methods=["POST"])
    @_require_auth
    def queue_reject(row: int):
        _validate_csrf()
        cfg    = app.config["BOT_CONFIG"]
        client = _get_queue_client(cfg)

        try:
            client.update_status(row, "rejected")
            flash(f"Row {row} rejected.", "info")
        except QueueError as exc:
            flash(f"Failed to reject row {row}: {exc}", "error")

        return redirect(url_for("queue"))

    # ── Health check ───────────────────────────────────────────────────────────

    @app.route("/healthz")
    def healthz():
        return "ok", 200

    return app


# ── Helpers ────────────────────────────────────────────────────────────────────

def _is_authenticated() -> bool:
    """Return True if the current session is authenticated."""
    return bool(session.get("authenticated"))


def _require_auth(f):
    """Decorator: redirect to /login if the user is not authenticated."""
    import functools

    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not _is_authenticated():
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def _get_csrf_token() -> str:
    """Return (and lazily create) the per-session CSRF token."""
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return session["_csrf_token"]


def _validate_csrf() -> None:
    """Abort 403 if the submitted CSRF token doesn't match the session."""
    submitted = request.form.get("_csrf_token", "")
    expected  = session.get("_csrf_token", "")
    if not submitted or not secrets.compare_digest(submitted, expected):
        log.warning("CSRF validation failed from %s", request.remote_addr)
        abort(403)


def _rate_limit_ok(session_id: str, limit: int = 10, window: float = 60.0) -> bool:
    """
    Sliding-window rate limiter.

    Args:
        session_id: Key for this client (CSRF token used as stable ID).
        limit:      Max requests allowed within *window* seconds.
        window:     Rolling time window in seconds.

    Returns:
        ``True`` if the request is within the limit, ``False`` otherwise.
    """
    now    = time.monotonic()
    cutoff = now - window
    recent = [t for t in _rate_limits.get(session_id, []) if t > cutoff]
    if len(recent) >= limit:
        return False
    recent.append(now)
    _rate_limits[session_id] = recent
    return True


def _safe_shortcode(code: str) -> bool:
    """Return True if *code* contains only safe filename characters."""
    import re
    return bool(re.match(r"^[A-Za-z0-9_\-]+$", code))


def _get_queue_client(cfg):
    """
    Return a :class:`QueueClient` cached on Flask's ``g`` object.

    Re-uses the same client within a request; creates a fresh one per request.
    """
    if "queue_client" not in g:
        g.queue_client = QueueClient(cfg.google_credentials, cfg.google_sheet_name)
    return g.queue_client


def _status_badge(status: str) -> str:
    """Return an HTML span with a colour-coded status badge."""
    colours = {
        "pending":  "#f59e0b",   # amber
        "ready":    "#3b82f6",   # blue
        "posted":   "#10b981",   # green
        "error":    "#ef4444",   # red
        "rejected": "#6b7280",   # grey
    }
    colour = colours.get(status, "#6b7280")
    return (
        f'<span style="background:{colour};color:#fff;padding:2px 8px;'
        f'border-radius:9999px;font-size:.75rem;font-weight:600">'
        f"{status}</span>"
    )
