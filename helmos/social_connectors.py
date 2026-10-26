"""Read-only official API adapters. No live transport or credential storage in I4-A1."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Protocol

from .social_import import _persist_posts


class ConnectorError(ValueError):
    """Safe diagnostic: never includes remote bodies, URLs or exception messages."""

    def __init__(self, code: str, *, status: int | None = None,
                 retry_after: int | None = None):
        self.code = code
        self.status = status
        self.retry_after = retry_after
        super().__init__(code)


@dataclass(frozen=True)
class Account:
    platform: str
    account_id: str

    def __post_init__(self):
        if self.platform not in ("Instagram", "TikTok", "LinkedIn"):
            raise ConnectorError("unsupported_platform")
        if not isinstance(self.account_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", self.account_id):
            raise ConnectorError("invalid_account")


@dataclass(frozen=True)
class EditorialMetadata:
    """Human supplied identity; the same video on IG/TT uses the same piece ID."""

    topic: str
    format: str
    editorial_piece_id: str

    def __post_init__(self):
        for value in (self.topic, self.format, self.editorial_piece_id):
            if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 200:
                raise ConnectorError("invalid_editorial_metadata")


@dataclass(frozen=True)
class Response:
    status: int
    body: object = field(repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)


class AuthorizedProvider(Protocol):
    """Bound to the authenticated account, scopes and host, outside run context.

    Future providers must enforce TLS, host allowlist, no redirects, finite timeout
    and response size, and must not log requests, credentials or remote bodies.
    POST is used only for TikTok's read-only video/list endpoint.
    """

    account: Account

    def request(self, method: str, path: str, *, params: dict,
                body: dict | None = None) -> Response: ...


def _object(value: object) -> dict:
    if not isinstance(value, dict):
        raise ConnectorError("invalid_response")
    return value


def _items(value: object) -> list[dict]:
    if not isinstance(value, list):
        raise ConnectorError("invalid_response")
    return [_object(item) for item in value]


def _count(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ConnectorError("invalid_metric")
    return value


def _id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,200}", value):
        raise ConnectorError("invalid_publication_id")
    return value


class SocialConnector:
    platform: str

    def __init__(self, provider: AuthorizedProvider, *, max_requests: int = 100):
        if provider.account.platform != self.platform:
            raise ConnectorError("account_platform_mismatch")
        if type(max_requests) is not int or not 1 <= max_requests <= 1000:
            raise ConnectorError("invalid_request_budget")
        self.provider = provider
        self.account = provider.account
        self.max_requests = max_requests

    def _request(self, method: str, path: str, *, params: dict,
                 body: dict | None = None) -> dict:
        if self._remaining == 0:
            raise ConnectorError("request_budget_exceeded")
        self._remaining -= 1
        try:
            response = self.provider.request(method, path, params=params, body=body)
        except Exception:
            # This boundary deliberately discards provider exceptions (may contain tokens).
            raise ConnectorError("provider_failure") from None
        if not isinstance(response, Response) or type(response.status) is not int:
            raise ConnectorError("invalid_response")
        if not isinstance(response.headers, Mapping) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in response.headers.items()):
            raise ConnectorError("invalid_response")
        if response.status != 200:
            retry = next((v for k, v in response.headers.items() if k.lower() == "retry-after"), "")
            retry = int(retry) if isinstance(retry, str) and retry.isascii() and retry.isdecimal() and len(retry) < 10 else None
            code = {401: "authentication_required", 403: "access_denied",
                    429: "rate_limited"}.get(response.status, "http_error")
            raise ConnectorError(code, status=response.status, retry_after=retry)
        payload = _object(response.body)
        if self.platform == "Instagram" and "error" in payload:
            raise ConnectorError("api_error")
        if self.platform == "TikTok" and _object(payload.get("error")).get("code") != "ok":
            raise ConnectorError("api_error")
        return payload

    def collect(self, metadata: Mapping[str, EditorialMetadata], *,
                captured_at: datetime) -> list[dict]:
        """Fetch a complete bounded batch, without writing. Dates are UTC capture days.

        Every discovered post needs explicit metadata. No inference from captions or
        cross-platform timestamps; no partial persistence on a remote/validation error.
        """
        if not isinstance(captured_at, datetime) or captured_at.utcoffset() is None:
            raise ConnectorError("timezone_required")
        captured_at = captured_at.astimezone(timezone.utc)
        self._remaining = self.max_requests
        records, seen = [], set()
        for post, metrics, published in self._fetch():
            post_id = _id(post.get("id"))
            if post_id in seen:
                raise ConnectorError("duplicate_publication")
            seen.add(post_id)
            editorial = metadata.get(post_id)
            if not isinstance(editorial, EditorialMetadata):
                raise ConnectorError("editorial_metadata_required")
            if not isinstance(published, datetime) or published.utcoffset() is None:
                raise ConnectorError("invalid_publication_date")
            published = published.astimezone(timezone.utc)
            if published > captured_at:
                raise ConnectorError("capture_precedes_publication")
            provenance = {
                "source": "official_api", "platform": self.platform,
                "account_id": self.account.account_id, "api_version": self.api_version,
                "captured_at": captured_at.isoformat(),
                "editorial_piece_id": editorial.editorial_piece_id,
                "metric_mapping": self.metric_mapping,
            }
            observation = {
                "observed_at": captured_at.date().isoformat(),
                "measurement_type": "cumulative", "period_start": None,
                "period_end": None, "metrics": metrics, "source_csv": None,
                "source_row": {}, "provenance": provenance,
            }
            records.append({
                "platform": self.platform, "post_id": post_id,
                "published_at": published.date().isoformat(),
                "format": editorial.format, "topic": editorial.topic,
                "metrics": metrics, "source_row": {}, "initial_observation": observation,
            })
        return records

    def import_snapshot(self, state_dir: Path, metadata: Mapping[str, EditorialMetadata], *,
                        captured_at: datetime) -> dict:
        """Single writer only; reuse Social's preflight, deduplication and history."""
        records = self.collect(metadata, captured_at=captured_at)
        return _persist_posts(records, state_dir)


class TikTokConnector(SocialConnector):
    platform = "TikTok"
    api_version = "v2"
    metric_mapping = {"view_count": "views", "like_count": "likes",
                      "comment_count": "comments", "share_count": "shares"}

    def _fetch(self):
        cursor, seen = None, set()
        while True:
            body = {"max_count": 20}
            if cursor is not None:
                body["cursor"] = cursor
            payload = self._request("POST", "/v2/video/list/", params={
                "fields": ",".join(("id", "create_time", *self.metric_mapping)),
            }, body=body)
            data = _object(payload.get("data"))
            for video in _items(data.get("videos")):
                _id(video.get("id"))
                try:
                    published = datetime.fromtimestamp(_count(video.get("create_time")), timezone.utc)
                except (OverflowError, OSError, ValueError):
                    raise ConnectorError("invalid_publication_date") from None
                metrics = {local: _count(video[remote]) for remote, local in self.metric_mapping.items()
                           if remote in video and video[remote] is not None}
                yield video, metrics, published
            if type(data.get("has_more")) is not bool:
                raise ConnectorError("invalid_pagination")
            if not data["has_more"]:
                return
            cursor = data.get("cursor")
            if type(cursor) is not int or cursor < 0 or cursor in seen:
                raise ConnectorError("invalid_pagination")
            seen.add(cursor)


class InstagramConnector(SocialConnector):
    platform = "Instagram"
    metric_mapping = {"views": "views", "reach": "reach", "likes": "likes",
                      "comments": "comments", "shares": "shares", "saved": "saves"}

    def __init__(self, provider: AuthorizedProvider, *, api_version: str,
                 max_requests: int = 100):
        super().__init__(provider, max_requests=max_requests)
        # Choose a supported Graph version at app registration; never silently use latest.
        if not isinstance(api_version, str) or not re.fullmatch(r"v[0-9]{2}\.0", api_version):
            raise ConnectorError("invalid_api_version")
        if not provider.account.account_id.isdecimal():
            raise ConnectorError("invalid_instagram_account")
        self.api_version = api_version

    def _fetch(self):
        cursor, seen = None, set()
        while True:
            params = {"fields": "id,timestamp,media_type,media_product_type", "limit": 25}
            if cursor is not None:
                params["after"] = cursor
            payload = self._request("GET", f"/{self.api_version}/{self.account.account_id}/media",
                                    params=params)
            for media in _items(payload.get("data")):
                post_id = _id(media.get("id"))
                # Stories, live and carousel children require different contracts.
                if media.get("media_product_type") not in ("FEED", "REELS") or media.get("media_type") not in (
                        "IMAGE", "VIDEO", "CAROUSEL_ALBUM"):
                    raise ConnectorError("unsupported_media_type")
                try:
                    timestamp = media["timestamp"]
                    if not isinstance(timestamp, str):
                        raise ValueError
                    published = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                except (KeyError, ValueError):
                    raise ConnectorError("invalid_publication_date") from None
                insight = self._request("GET", f"/{self.api_version}/{post_id}/insights",
                                        params={"metric": ",".join(self.metric_mapping)})
                metrics = {}
                for item in _items(insight.get("data")):
                    name = item.get("name")
                    if not isinstance(name, str) or name not in self.metric_mapping:
                        continue
                    local = self.metric_mapping[name]
                    if local in metrics or item.get("period") != "lifetime":
                        raise ConnectorError("invalid_insight")
                    values = _items(item.get("values"))
                    if not values:
                        continue
                    if len(values) != 1 or "value" not in values[0]:
                        raise ConnectorError("invalid_insight")
                    if values[0]["value"] is not None:
                        metrics[local] = _count(values[0]["value"])
                yield media, metrics, published
            paging = _object(payload.get("paging", {}))
            if not paging.get("next"):
                return
            # Never follow next URLs: they may contain tokens or untrusted hosts.
            cursor = _object(paging.get("cursors")).get("after")
            if not isinstance(cursor, str) or not cursor or len(cursor) > 2048 or cursor in seen:
                raise ConnectorError("invalid_pagination")
            seen.add(cursor)


class LinkedInConnector:
    """Explicit capability gate: no member post discovery without approved access."""

    platform = "LinkedIn"

    def collect(self, metadata, *, captured_at):
        raise ConnectorError("linkedin_member_access_unavailable")

    def import_snapshot(self, state_dir, metadata, *, captured_at):
        raise ConnectorError("linkedin_member_access_unavailable")
