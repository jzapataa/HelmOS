"""Explicit Instagram synchronization through existing canonical Social storage."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .instagram_credentials import InstagramCredentials
from .social_connectors import ConnectorError, EditorialMetadata, InstagramConnector, MetricMetadata
from .social_import import SocialImportError, _persist_posts, list_observations, list_posts


def load_metadata(path: Path | None) -> dict[str, EditorialMetadata]:
    if path is None:
        return {}
    try:
        if path.stat().st_size > 1024 * 1024:
            raise ConnectorError("metadata_file_too_large")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        result = {}
        for post_id, item in data.items():
            if not re.fullmatch(r"[0-9]{1,200}", post_id) or not isinstance(item, dict) or set(item) != {
                    "topic", "format", "editorial_piece_id"}:
                raise ValueError
            result[post_id] = EditorialMetadata(**item)
        return result
    except (OSError, ValueError, TypeError):
        raise ConnectorError("invalid_metadata_file") from None


def sync_instagram(state_dir: Path, *, api_version: str, expected_username: str,
                   metadata_path: Path | None = None, preview: bool = False,
                   page_size: int = 5, max_pages: int = 1, max_posts: int = 5,
                   max_requests: int = 100, timeout: float = 10,
                   deadline_seconds: float = 120, provider=None,
                   captured_at: datetime | None = None) -> dict:
    for value, maximum in ((page_size, 100), (max_pages, 1000), (max_posts, 10000)):
        if type(value) is not int or not 1 <= value <= maximum:
            raise ConnectorError("invalid_collection_limit")
    supplied = load_metadata(metadata_path)
    saved = {post["post_id"]: post for post in list_posts(state_dir, "Instagram")}
    metadata: dict[str, EditorialMetadata | MetricMetadata] = dict(supplied)
    for post_id, post in saved.items():
        piece_id = None
        for observation in list_observations(state_dir, post):
            origin = observation.get("provenance", {})
            if origin.get("editorial_piece_id"):
                if piece_id and piece_id != origin["editorial_piece_id"]:
                    raise SocialImportError("Conflicting saved editorial identities")
                piece_id = origin["editorial_piece_id"]
        proposed = supplied.get(post_id)
        if proposed:
            if (proposed.topic != post.get("topic") or proposed.format != post.get("format")
                    or (piece_id and proposed.editorial_piece_id != piece_id)):
                raise SocialImportError("Existing publication metadata differs")
            piece_id = proposed.editorial_piece_id
        metadata[post_id] = MetricMetadata(post.get("topic"), post.get("format"), piece_id)
    credential_status = None
    if provider is None:
        provider, credential_status = InstagramCredentials(
            api_version=api_version, expected_username=expected_username, timeout=timeout,
            deadline_seconds=deadline_seconds, max_requests=max_requests).prepare()
    connector = InstagramConnector(provider, api_version=api_version,
                                   max_requests=max_requests, partial_insights=True,
                                   page_size=page_size, max_pages=max_pages, max_posts=max_posts,
                                   allow_unclassified=True)
    capture = captured_at or datetime.now(timezone.utc)
    records = connector.collect(metadata, captured_at=capture)
    plan = _persist_posts(records, state_dir, precise_captures=True, dry_run=preview)
    discovered_ids = {post["post_id"] for post in records} | {
        item["post_id"] for item in connector.unclassified_posts}
    existing = len(discovered_ids & saved.keys())
    planned = plan["imported"] + plan["observations_added"]
    return {
        "credential": credential_status,
        "platform": "Instagram", "account_id": provider.account.account_id,
        "api_version": api_version, "captured_at": capture.astimezone(timezone.utc).isoformat(),
        "preview": preview, "status": "partial" if connector.partial_errors else "succeeded",
        "posts_consulted": connector.consulted, "new_posts": len(discovered_ids) - existing,
        "existing_posts": existing, "posts_imported": 0 if preview else plan["imported"],
        "observations_saved": 0 if preview else planned,
        "observations_planned": planned, "unchanged_posts": plan["already_present"],
        "duplicates_skipped": connector.duplicates, "bounded_listing": connector.truncated,
        "requests_made": getattr(provider, "requests_made", None),
        "partial_errors": connector.partial_errors, "unclassified_posts": connector.unclassified_posts,
        "preview_posts": [{"post_id": post["post_id"], "published_at": post["published_at"],
                           "metrics": post["metrics"]} for post in records] if preview else [],
    }
