"""
app/publisher.py
────────────────
Publishes media to Instagram via the official Graph API (v21.0).

Supports IMAGE and VIDEO (published as REELS).

Publishing flow (two-step Graph API):
  1. POST /{ig_user_id}/media          → container_id
  2. Poll  /{container_id}?fields=status_code  (FINISHED / IN_PROGRESS / ERROR)
  3. POST /{ig_user_id}/media_publish  → post_id

Note on timezones
─────────────────
All posting-window comparisons are done in UTC.  If your audience is in a
different timezone, adjust POSTING_HOURS_START / POSTING_HOURS_END accordingly
in your .env (e.g. if your audience is UTC+5:30, set START=2 END=16 to match
8 AM – 10 PM IST).

Public API
──────────
    publish_next(cfg, queue_client) -> Optional[str]
    refresh_access_token(cfg) -> str
"""

from __future__ import annotations

import time
import traceback
from datetime import datetime, timezone
from typing import Optional

import requests

from app.logger import get_logger

log = get_logger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

GRAPH_API_BASE    = "https://graph.facebook.com/v21.0"
GRAPH_API_TIMEOUT = 30                  # seconds for all Graph API HTTP calls
POLL_INTERVAL     = 10                  # seconds between container status polls
POLL_MAX_WAIT     = 300                 # 5 minutes total polling budget
DAILY_CAP_HARD    = 20                  # absolute ceiling regardless of config


# ── Exceptions ─────────────────────────────────────────────────────────────────

class PublisherError(Exception):
    """Raised for unrecoverable Graph API or publisher logic errors."""


# ── Public API ─────────────────────────────────────────────────────────────────

def publish_next(cfg, queue_client) -> Optional[str]:
    """
    Fetch the next ready queue row, publish it to Instagram, and mark it posted.

    Guards:
      • Posting window  — current UTC hour must be within
                          [POSTING_HOURS_START, POSTING_HOURS_END).
      • Daily soft cap  — ``queue_client.count_today('posted')`` <
                          ``cfg.max_posts_per_day``.
      • Daily hard cap  — never exceed :data:`DAILY_CAP_HARD` (20) posts/day.
      • Queue empty     — returns ``None`` if no row has ``status='ready'``.

    On any publishing failure the row is marked as ``error`` and ``None``
    is returned so the calling scheduler can continue to the next tick.

    Args:
        cfg:          Loaded :class:`app.config.Config` instance.
        queue_client: Connected :class:`app.queue_client.QueueClient`.

    Returns:
        The Instagram post ID string on success, or ``None``.
    """
    # ── 1. Posting window check ────────────────────────────────────────────────
    current_hour = datetime.now(timezone.utc).hour
    if not (cfg.posting_hours_start <= current_hour < cfg.posting_hours_end):
        log.info(
            "Outside posting window (current UTC hour=%d, window=%d–%d). Skipping.",
            current_hour, cfg.posting_hours_start, cfg.posting_hours_end,
        )
        return None

    # ── 2 & 3. Daily cap checks ────────────────────────────────────────────────
    posts_today = queue_client.count_today("posted")
    effective_cap = min(cfg.max_posts_per_day, DAILY_CAP_HARD)

    if posts_today >= effective_cap:
        log.info(
            "Daily cap reached: %d/%d posts today. Skipping.",
            posts_today, effective_cap,
        )
        return None

    # ── 4. Get next ready row ──────────────────────────────────────────────────
    row = queue_client.get_next_ready()
    if row is None:
        log.info("No ready rows in queue. Nothing to publish.")
        return None

    log.info(
        "Publishing row %d: shortcode=%s media_type=%s",
        row.row_index, row.shortcode, row.media_type,
    )

    # ── 5–8. Full publish pipeline ─────────────────────────────────────────────
    try:
        post_id = _publish_row(row, cfg)
        queue_client.mark_posted(row.row_index, post_id)
        log.info(
            "SUCCESS: row %d posted as IG post %s (shortcode=%s)",
            row.row_index, post_id, row.shortcode,
        )
        return post_id

    except Exception as exc:
        # ── 9. On any failure: mark error, do not raise ────────────────────────
        error_msg = f"{type(exc).__name__}: {exc}"
        log.error(
            "FAILED to publish row %d (shortcode=%s):\n%s",
            row.row_index, row.shortcode, traceback.format_exc(),
        )
        try:
            queue_client.mark_error(row.row_index, error_msg)
        except Exception as mark_exc:
            log.error("Could not mark row %d as error: %s", row.row_index, mark_exc)
        return None


def refresh_access_token(cfg) -> str:
    """
    Refresh the long-lived Instagram access token and return the new value.

    The Graph API extends the token's 60-day lifetime by up to another 60 days.
    Call this weekly (e.g. from a cron job or the scheduler's weekly tick).

    ⚠️  The new token is logged at INFO level so the operator can copy it into
    their environment variables.  Tokens are NOT automatically persisted.

    Args:
        cfg: Loaded :class:`app.config.Config` with a valid ``ig_access_token``.

    Returns:
        The new access token string.

    Raises:
        PublisherError: If the Graph API returns an error.
    """
    url = f"{GRAPH_API_BASE}/refresh_access_token"
    params = {
        "grant_type":   "ig_refresh_token",
        "access_token": cfg.ig_access_token,
    }

    log.info("Requesting access-token refresh …")
    resp = requests.get(url, params=params, timeout=GRAPH_API_TIMEOUT)
    data = _check_response(resp, "token refresh")

    new_token: str = data.get("access_token", "")
    expires_in: int = data.get("expires_in", 0)

    if not new_token:
        raise PublisherError(
            f"Token refresh response missing 'access_token': {data}"
        )

    log.info(
        "━━━ ACCESS TOKEN REFRESHED ━━━\n"
        "New token  : %s\n"
        "Expires in : %d seconds (~%d days)\n"
        "ACTION     : Update IG_ACCESS_TOKEN in your .env / Railway variables.",
        new_token, expires_in, expires_in // 86_400,
    )
    return new_token


# ── Internal pipeline ──────────────────────────────────────────────────────────

def _publish_row(row, cfg) -> str:
    """
    Run the full two-step Graph API flow for one queue row.

    Args:
        row: :class:`app.queue_client.QueueRow` with media_url and caption.
        cfg: Loaded config with Graph API credentials.

    Returns:
        Published Instagram post ID.

    Raises:
        PublisherError: On any Graph API error.
    """
    media_type = (row.media_type or "IMAGE").upper()

    # ── Step 5: Create media container ────────────────────────────────────────
    if media_type == "VIDEO":
        container_id = _create_video_container(
            video_url=row.media_url,
            caption=row.caption,
            cfg=cfg,
        )
    else:
        container_id = _create_image_container(
            image_url=row.media_url,
            caption=row.caption,
            cfg=cfg,
        )

    # ── Step 6: Poll until FINISHED ───────────────────────────────────────────
    _wait_for_container(container_id, cfg)

    # ── Step 7: Publish ───────────────────────────────────────────────────────
    return _publish_container(container_id, cfg)


def _create_image_container(image_url: str, caption: str, cfg) -> str:
    """
    Create a media container for an image post.

    Args:
        image_url: Public HTTPS URL of the image (Catbox CDN).
        caption:   Post caption (≤2 200 chars).
        cfg:       Config with IG credentials.

    Returns:
        Container ID string.

    Raises:
        PublisherError: On Graph API error.
    """
    url = f"{GRAPH_API_BASE}/{cfg.ig_user_id}/media"
    params = {
        "image_url":    image_url,
        "caption":      caption,
        "access_token": cfg.ig_access_token,
    }
    log.info("Creating IMAGE container for user %s …", cfg.ig_user_id)
    resp = requests.post(url, params=params, timeout=GRAPH_API_TIMEOUT)
    data = _check_response(resp, "create image container")
    container_id: str = data["id"]
    log.info("Container created: %s", container_id)
    return container_id


def _create_video_container(video_url: str, caption: str, cfg) -> str:
    """
    Create a media container for a video post (published as a Reel).

    Args:
        video_url: Public HTTPS URL of the video (Catbox CDN).
        caption:   Post caption.
        cfg:       Config with IG credentials.

    Returns:
        Container ID string.

    Raises:
        PublisherError: On Graph API error.
    """
    url = f"{GRAPH_API_BASE}/{cfg.ig_user_id}/media"
    params = {
        "media_type":   "REELS",
        "video_url":    video_url,
        "caption":      caption,
        "access_token": cfg.ig_access_token,
    }
    log.info("Creating VIDEO/REELS container for user %s …", cfg.ig_user_id)
    resp = requests.post(url, params=params, timeout=GRAPH_API_TIMEOUT)
    data = _check_response(resp, "create video container")
    container_id: str = data["id"]
    log.info("Container created: %s", container_id)
    return container_id


def _wait_for_container(container_id: str, cfg) -> None:
    """
    Poll the container's ``status_code`` field until it reaches ``FINISHED``.

    Polls every :data:`POLL_INTERVAL` seconds for up to :data:`POLL_MAX_WAIT`
    seconds total.

    Container status values:
      • ``FINISHED``    — ready to publish
      • ``IN_PROGRESS`` — still processing (video transcode, etc.)
      • ``ERROR``       — terminal failure; raises :class:`PublisherError`
      • ``PUBLISHED``   — already published (unexpected here, treated as done)

    Args:
        container_id: The container ID to poll.
        cfg:          Config with IG credentials.

    Raises:
        PublisherError: If status is ``ERROR`` or the timeout is exceeded.
    """
    url = f"{GRAPH_API_BASE}/{container_id}"
    params = {
        "fields":       "status_code,status",
        "access_token": cfg.ig_access_token,
    }
    deadline = time.monotonic() + POLL_MAX_WAIT
    attempt  = 0

    while True:
        attempt += 1
        resp = requests.get(url, params=params, timeout=GRAPH_API_TIMEOUT)
        data = _check_response(resp, f"poll container {container_id}")

        status_code: str = data.get("status_code", "UNKNOWN")
        log.info(
            "Container %s status: %s (attempt %d, %.0fs remaining)",
            container_id, status_code, attempt,
            max(0, deadline - time.monotonic()),
        )

        if status_code in ("FINISHED", "PUBLISHED"):
            return

        if status_code == "ERROR":
            detail = data.get("status", "no detail")
            raise PublisherError(
                f"Container {container_id} reported ERROR: {detail}"
            )

        # IN_PROGRESS or unknown — keep waiting if budget allows
        if time.monotonic() >= deadline:
            raise PublisherError(
                f"Container {container_id} not FINISHED after {POLL_MAX_WAIT}s "
                f"(last status: {status_code})"
            )

        time.sleep(POLL_INTERVAL)


def _publish_container(container_id: str, cfg) -> str:
    """
    Publish a FINISHED media container and return the live post ID.

    Args:
        container_id: The container ID returned by a ``_create_*`` function.
        cfg:          Config with IG credentials.

    Returns:
        The published Instagram post ID string.

    Raises:
        PublisherError: On Graph API error.
    """
    url = f"{GRAPH_API_BASE}/{cfg.ig_user_id}/media_publish"
    params = {
        "creation_id":  container_id,
        "access_token": cfg.ig_access_token,
    }
    log.info("Publishing container %s …", container_id)
    resp = requests.post(url, params=params, timeout=GRAPH_API_TIMEOUT)
    data = _check_response(resp, "publish container")
    post_id: str = data["id"]
    log.info("Published! IG post ID: %s", post_id)
    return post_id


# ── Graph API response helper ──────────────────────────────────────────────────

def _check_response(resp: requests.Response, context: str) -> dict:
    """
    Parse a Graph API JSON response and raise on errors.

    The Graph API signals errors in two ways:
      1. Non-2xx HTTP status code.
      2. 200 response with ``{"error": {...}}`` body.

    Args:
        resp:    The :class:`requests.Response` to inspect.
        context: Human-readable description of the call (for error messages).

    Returns:
        The parsed JSON body as a dict.

    Raises:
        PublisherError: On non-2xx status or an error body.
    """
    try:
        data: dict = resp.json()
    except Exception:
        raise PublisherError(
            f"[{context}] Non-JSON response (HTTP {resp.status_code}): "
            f"{resp.text[:300]}"
        )

    if "error" in data:
        err  = data["error"]
        code = err.get("code", "?")
        msg  = err.get("message", "unknown error")
        sub  = err.get("error_subcode", "")
        raise PublisherError(
            f"[{context}] Graph API error {code}"
            + (f"/{sub}" if sub else "")
            + f": {msg}"
        )

    if not resp.ok:
        raise PublisherError(
            f"[{context}] HTTP {resp.status_code}: {resp.text[:300]}"
        )

    return data
