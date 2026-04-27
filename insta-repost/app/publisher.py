"""
app/publisher.py
────────────────
Publishes media to Instagram via the official Graph API.

Supports:
  • Single image  (IMAGE)
  • Single video  (VIDEO)
  • Carousel      (CAROUSEL_ALBUM — up to 10 slides)

Two-step Graph API flow:
  1. POST /{ig_user_id}/media          → container_id
  2. POST /{ig_user_id}/media_publish  → post_id

Public API
──────────
    publish_post(entry: QueueEntry, cfg: Config) -> str   # returns post_id
"""

from __future__ import annotations

from typing import List

from app.config import Config
from app.queue_client import QueueEntry

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"


def publish_post(entry: QueueEntry, cfg: Config) -> str:
    """
    Publish a post to Instagram using the Graph API.

    Automatically selects the correct flow (image / video / carousel)
    based on ``entry.media_type``.

    Args:
        entry: A fully populated :class:`QueueEntry` with a live *media_url*
               (Catbox CDN link) and caption.
        cfg:   Application config holding IG credentials.

    Returns:
        The published Instagram post ID string.

    Raises:
        PublishError: If the Graph API returns an error at any step.
        NotImplementedError: Until Phase 4 is implemented.
    """
    raise NotImplementedError("Phase 4")


def _create_image_container(
    image_url: str,
    caption: str,
    cfg: Config,
) -> str:
    """
    Step 1 for images: create a media container and return its ID.

    Args:
        image_url: Public HTTPS URL of the image (Catbox CDN).
        caption:   Post caption text.
        cfg:       Application config.

    Returns:
        Container ID string from the Graph API.

    Raises:
        PublishError: On Graph API error.
        NotImplementedError: Until Phase 4 is implemented.
    """
    raise NotImplementedError("Phase 4")


def _create_video_container(
    video_url: str,
    caption: str,
    cfg: Config,
) -> str:
    """
    Step 1 for videos: create a media container and return its ID.

    Args:
        video_url: Public HTTPS URL of the video (Catbox CDN).
        caption:   Post caption text.
        cfg:       Application config.

    Returns:
        Container ID string from the Graph API.

    Raises:
        PublishError: On Graph API error.
        NotImplementedError: Until Phase 4 is implemented.
    """
    raise NotImplementedError("Phase 4")


def _create_carousel_container(
    media_urls: List[str],
    caption: str,
    cfg: Config,
) -> str:
    """
    Step 1 for carousels: create child containers then the parent container.

    Args:
        media_urls: List of public HTTPS URLs (up to 10).
        caption:    Post caption text.
        cfg:        Application config.

    Returns:
        Parent carousel container ID string.

    Raises:
        PublishError: On Graph API error.
        NotImplementedError: Until Phase 4 is implemented.
    """
    raise NotImplementedError("Phase 4")


def _publish_container(container_id: str, cfg: Config) -> str:
    """
    Step 2 (all types): publish a ready container and return the post ID.

    Args:
        container_id: ID returned by a ``_create_*_container`` call.
        cfg:          Application config.

    Returns:
        Published Instagram post ID string.

    Raises:
        PublishError: On Graph API error.
        NotImplementedError: Until Phase 4 is implemented.
    """
    raise NotImplementedError("Phase 4")


class PublishError(Exception):
    """Raised when the Instagram Graph API returns an error response."""
