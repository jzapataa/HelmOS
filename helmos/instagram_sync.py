"""Explicit Instagram synchronization through existing canonical Social storage."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .instagram_credentials import InstagramCredentials
from .social_connectors import ConnectorError, EditorialMetadata, InstagramConnector, MetricMetadata
from .social_import import (SocialImportError, _persist_posts, _publication_date_evidence,
                            list_observations, list_posts)
from .sync_lock import serialize_instagram


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


def _metadata_report(records, saved, supplied, piece_ids, connector, capture, provider):
    conflicts = []
    for received in records:
        post_id = received["post_id"]
        historical = saved.get(post_id)
        if historical is None:
            continue
        before = _publication_date_evidence(historical)
        after = received["published_at"]
        if historical["published_at"] != after:
            instant = connector.publication_times[post_id]
            conflicts.append({
                "platform": "Instagram", "post_id": post_id, "field": "published_at",
                "historical": {**before,
                    "source_csv": historical.get("initial_observation", {}).get("source_csv"),
                    "source_row_value": historical.get("source_row", {}).get("published_at"),
                    "timezone": "unknown" if before["source"] != "official_api" else "UTC"},
                "received": {"value": after, "source": "official_api",
                    "timestamp": instant.isoformat(), "canonical_timezone": "UTC",
                    "account_id": provider.account.account_id,
                    "api_version": connector.api_version},
                "difference": {
                    "type": "different_civil_dates",
                    "timezone_cause": "undetermined",
                    "offset_changes_received_day": instant.date().isoformat() != after,
                    "explanation": "A historical day alone cannot establish timezone equivalence."},
            })
        proposed = supplied.get(post_id)
        if proposed:
            for field in ("topic", "format"):
                if getattr(proposed, field) != historical.get(field):
                    conflicts.append({"platform": "Instagram", "post_id": post_id,
                        "field": field,
                        "historical": {"value": historical.get(field), "source": "canonical_local"},
                        "received": {"value": getattr(proposed, field), "source": "metadata_file"},
                        "difference": {"type": "different_metadata", "timezone_cause": "not_applicable"}})
            if piece_ids.get(post_id) and piece_ids[post_id] != proposed.editorial_piece_id:
                conflicts.append({"platform": "Instagram", "post_id": post_id,
                    "field": "editorial_piece_id",
                    "historical": {"value": piece_ids[post_id], "source": "canonical_observations"},
                    "received": {"value": proposed.editorial_piece_id, "source": "metadata_file"},
                    "difference": {"type": "different_metadata", "timezone_cause": "not_applicable"}})
    consulted_ids = {post["post_id"] for post in records}
    return {"mode": "metadata_prevalidation", "platform": "Instagram",
            "account_id": provider.account.account_id, "api_version": connector.api_version,
            "captured_at": capture.astimezone(timezone.utc).isoformat(),
            "status": "conflicts" if conflicts else ("partial" if connector.truncated else "clear"),
            "posts_consulted": connector.consulted,
            "canonical_posts_checked": len(consulted_ids & saved.keys()),
            "canonical_posts_unchecked": len(saved.keys() - consulted_ids),
            "bounded_listing": connector.truncated, "conflicts": conflicts,
            "conflict_count": len(conflicts),
            "conflicting_posts": len({item["post_id"] for item in conflicts}),
            "requests_made": getattr(provider, "requests_made", None),
            "insights_requested": 0, "posts_imported": 0, "observations_saved": 0,
            "limitations": ["Only the consulted API window was checked; missing posts remain unchecked.",
                            "API media types do not define editorial topic or format."]}


@serialize_instagram
def sync_instagram(state_dir: Path, *, api_version: str, expected_username: str,
                   metadata_path: Path | None = None, preview: bool = False,
                   page_size: int = 5, max_pages: int = 1, max_posts: int = 5,
                   max_requests: int = 100, timeout: float = 10,
                   deadline_seconds: float = 120, provider=None,
                   captured_at: datetime | None = None,
                   preserve_partial: bool = False, diagnostics=None,
                   prevalidate_metadata: bool = False) -> dict:
    if diagnostics is not None:
        diagnostics.stage("history_read")
    for value, maximum in ((page_size, 100), (max_pages, 1000), (max_posts, 10000)):
        if type(value) is not int or not 1 <= value <= maximum:
            raise ConnectorError("invalid_collection_limit")
    supplied = load_metadata(metadata_path)
    saved = {post["post_id"]: post for post in list_posts(state_dir, "Instagram")}
    metadata: dict[str, EditorialMetadata | MetricMetadata] = dict(supplied)
    piece_ids = {}
    for post_id, post in saved.items():
        piece_id = None
        for observation in list_observations(state_dir, post):
            origin = observation.get("provenance", {})
            if origin.get("editorial_piece_id"):
                if piece_id and piece_id != origin["editorial_piece_id"]:
                    raise SocialImportError("Conflicting saved editorial identities")
                piece_id = origin["editorial_piece_id"]
        piece_ids[post_id] = piece_id
        proposed = supplied.get(post_id)
        if proposed:
            if not prevalidate_metadata and (proposed.topic != post.get("topic") or proposed.format != post.get("format")
                    or (piece_id and proposed.editorial_piece_id != piece_id)):
                raise SocialImportError("Existing publication metadata differs")
            piece_id = proposed.editorial_piece_id
        metadata[post_id] = MetricMetadata(post.get("topic"), post.get("format"), piece_id)
    credential_status = None
    if provider is None:
        provider, credential_status = InstagramCredentials(
            api_version=api_version, expected_username=expected_username, timeout=timeout,
            deadline_seconds=deadline_seconds, max_requests=max_requests).prepare(
                refresh=not prevalidate_metadata, read_only=prevalidate_metadata)
    connector = InstagramConnector(provider, api_version=api_version,
                                   max_requests=max_requests, partial_insights=True,
                                   page_size=page_size, max_pages=max_pages, max_posts=max_posts,
                                   allow_unclassified=True, metadata_only=prevalidate_metadata)
    capture = captured_at or datetime.now(timezone.utc)
    failure = None
    if diagnostics is not None:
        diagnostics.stage("collection")
    try:
        records = connector.collect(metadata, captured_at=capture)
    except ConnectorError as error:
        recoverable = error.code in {
                "transport_failure", "provider_failure", "deadline_exceeded",
                "request_budget_exceeded", "rate_limited", "authentication_required",
                "instagram_permissions_required"} or (
                    error.code == "http_error" and error.status is not None and error.status >= 500)
        if prevalidate_metadata or not preserve_partial or not recoverable:
            raise
        records = getattr(connector, "completed_records", [])
        failure = {"code": error.code, "status": error.status,
                   "retry_after": error.retry_after}
    if prevalidate_metadata:
        return _metadata_report(records, saved, supplied, piece_ids, connector, capture, provider)
    if diagnostics is not None:
        diagnostics.stage("observations_write")
    plan = _persist_posts(records, state_dir, precise_captures=True, dry_run=preview,
                          diagnostics=diagnostics)
    discovered_ids = {post["post_id"] for post in records} | {
        item["post_id"] for item in connector.unclassified_posts}
    existing = len(discovered_ids & saved.keys())
    planned = plan["imported"] + plan["observations_added"]
    return {
        "credential": credential_status,
        "platform": "Instagram", "account_id": provider.account.account_id,
        "api_version": api_version, "captured_at": capture.astimezone(timezone.utc).isoformat(),
        "preview": preview, "status": "failed" if failure else (
            "partial" if connector.partial_errors else "succeeded"),
        "failure": failure,
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
