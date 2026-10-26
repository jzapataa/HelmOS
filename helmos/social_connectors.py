"""Read-only official API adapters; transport and credentials stay outside records."""

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
class MetricMetadata:
    """Persisted classification or explicit unknowns; never an inferred piece link."""

    topic: str | None
    format: str | None
    editorial_piece_id: str | None = None


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


def response_payload(response: Response, platform: str) -> dict:
    if not isinstance(response, Response) or type(response.status) is not int:
        raise ConnectorError("invalid_response")
    if not isinstance(response.headers, Mapping) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in response.headers.items()):
        raise ConnectorError("invalid_response")
    retry = next((v for k, v in response.headers.items() if k.lower() == "retry-after"), "")
    retry = int(retry) if isinstance(retry, str) and retry.isascii() and retry.isdecimal() and len(retry) < 10 else None
    if platform == "Instagram" and isinstance(response.body, dict):
        remote_error = response.body.get("error")
        if isinstance(remote_error, dict):
            if remote_error.get("code") == 190:
                raise ConnectorError("authentication_required", status=response.status)
            if remote_error.get("code") in (10, 200):
                raise ConnectorError("instagram_permissions_required", status=response.status)
            if remote_error.get("code") in (4, 17, 32, 613):
                raise ConnectorError("rate_limited", status=response.status, retry_after=retry)
            if remote_error.get("code") == 100 and response.status in (200, 400):
                raise ConnectorError("metric_or_parameter_rejected", status=response.status)
    if response.status != 200:
        code = {401: "authentication_required", 403: "access_denied",
                429: "rate_limited"}.get(response.status, "http_error")
        raise ConnectorError(code, status=response.status, retry_after=retry)
    payload = _object(response.body)
    if platform == "Instagram" and "error" in payload:
        raise ConnectorError("api_error")
    if platform == "TikTok" and _object(payload.get("error")).get("code") != "ok":
        raise ConnectorError("api_error")
    return payload


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
        except ConnectorError as error:
            safe_codes = {"transport_failure", "deadline_exceeded", "redirect_rejected",
                          "api_version_mismatch", "request_budget_exceeded",
                          "response_too_large", "invalid_json", "instagram_request_not_allowed"}
            if error.code in safe_codes:
                raise ConnectorError(error.code) from None
            raise ConnectorError("provider_failure") from None
        except Exception:
            # This boundary deliberately discards provider exceptions (may contain tokens).
            raise ConnectorError("provider_failure") from None
        return response_payload(response, self.platform)

    def collect(self, metadata: Mapping[str, EditorialMetadata | MetricMetadata], *,
                captured_at: datetime) -> list[dict]:
        """Fetch a complete bounded batch, without writing. Dates are UTC capture days.

        Strict adapters require explicit metadata. The bounded Instagram sync may
        capture pending classification without inferring topics or piece links.
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
            if not isinstance(published, datetime) or published.utcoffset() is None:
                raise ConnectorError("invalid_publication_date")
            published = published.astimezone(timezone.utc)
            if published > captured_at:
                raise ConnectorError("capture_precedes_publication")
            editorial = metadata.get(post_id)
            if not isinstance(editorial, EditorialMetadata) and not (
                    getattr(self, "allow_unclassified", False) and isinstance(editorial, MetricMetadata)):
                if getattr(self, "allow_unclassified", False):
                    editorial = MetricMetadata(None, None)
                else:
                    raise ConnectorError("editorial_metadata_required")
            pending = not editorial.topic or not editorial.format
            if pending:
                self.unclassified_posts.append({"post_id": post_id,
                                                "timestamp": published.isoformat(),
                                                "media_type": post.get("media_type"),
                                                "metrics": metrics})
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
                **({"classification_status": "pending"} if pending else {}),
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
                 max_requests: int = 100, partial_insights: bool = False,
                 page_size: int = 25, max_pages: int = 10, max_posts: int = 100,
                 allow_unclassified: bool = False):
        super().__init__(provider, max_requests=max_requests)
        # Choose a supported Graph version at app registration; never silently use latest.
        if not isinstance(api_version, str) or not re.fullmatch(r"v[0-9]{2}\.0", api_version):
            raise ConnectorError("invalid_api_version")
        if not provider.account.account_id.isdecimal():
            raise ConnectorError("invalid_instagram_account")
        self.api_version = api_version
        for value, maximum in ((page_size, 100), (max_pages, 1000), (max_posts, 10000)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ConnectorError("invalid_collection_limit")
        self.partial_insights = partial_insights
        self.allow_unclassified = allow_unclassified
        self.page_size, self.max_pages, self.max_posts = page_size, max_pages, max_posts
        self.partial_errors = []
        self.unclassified_posts = []
        self.truncated = False
        self.consulted = 0
        self.duplicates = 0

    def collect(self, metadata, *, captured_at):
        self.partial_errors = []
        self.unclassified_posts = []
        self.truncated = False
        self.consulted = self.duplicates = 0
        return super().collect(metadata, captured_at=captured_at)

    def _individual_insights(self, post_id, product):
        # Common scalar media metrics for supported FEED and REELS containers.
        # No Stories/children, deprecated plays/impressions or Reel-only watch time.
        allowed = {"FEED": tuple(self.metric_mapping), "REELS": tuple(self.metric_mapping)}
        metrics = {}
        for name in allowed[product]:
            try:
                payload = self._request("GET", f"/{self.api_version}/{post_id}/insights",
                                        params={"metric": name})
                items = _items(payload.get("data"))
                if len(items) > 1:
                    raise ConnectorError("invalid_insight")
                for item in items:
                    if item.get("name") != name or item.get("period") != "lifetime":
                        raise ConnectorError("invalid_insight")
                    values = _items(item.get("values"))
                    if not values:
                        continue
                    if len(values) != 1 or "value" not in values[0]:
                        raise ConnectorError("invalid_insight")
                    if values[0]["value"] is not None:
                        metrics[self.metric_mapping[name]] = _count(values[0]["value"])
            except ConnectorError as error:
                if error.code in ("authentication_required", "instagram_permissions_required", "rate_limited", "request_budget_exceeded",
                                  "provider_failure", "transport_failure", "deadline_exceeded",
                                  "response_too_large", "redirect_rejected", "api_version_mismatch",
                                  "instagram_request_not_allowed"):
                    raise
                self.partial_errors.append({"post_id": post_id, "metric": name,
                                            "code": error.code, "status": error.status})
        return metrics

    def _fetch(self):
        cursor, seen = None, set()
        pages, media_seen = 0, {}
        while True:
            params = {"fields": "id,timestamp,media_type,media_product_type", "limit": self.page_size}
            if cursor is not None:
                params["after"] = cursor
            owner = "me" if self.partial_insights else self.account.account_id
            payload = self._request("GET", f"/{self.api_version}/{owner}/media",
                                    params=params)
            pages += 1
            media_items = _items(payload.get("data"))
            for index, media in enumerate(media_items):
                post_id = _id(media.get("id"))
                if self.partial_insights and post_id in media_seen:
                    if any(media.get(key) != media_seen[post_id].get(key) for key in
                           ("timestamp", "media_type", "media_product_type")):
                        raise ConnectorError("duplicate_publication_conflict")
                    self.duplicates += 1
                    continue
                media_seen[post_id] = media
                self.consulted += 1
                # Stories, live and carousel children require different contracts.
                supported = {"FEED": ("IMAGE", "VIDEO", "CAROUSEL_ALBUM"), "REELS": ("VIDEO",)}
                product = media.get("media_product_type")
                if not isinstance(product, str) or media.get("media_type") not in supported.get(product, ()):
                    raise ConnectorError("unsupported_media_type")
                try:
                    timestamp = media["timestamp"]
                    if not isinstance(timestamp, str):
                        raise ValueError
                    published = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                except (KeyError, ValueError):
                    raise ConnectorError("invalid_publication_date") from None
                if self.partial_insights:
                    metrics = self._individual_insights(post_id, media["media_product_type"])
                    yield media, metrics, published
                    if len(media_seen) >= self.max_posts:
                        self.truncated = index + 1 < len(media_items) or bool(
                            _object(payload.get("paging", {})).get("next"))
                        return
                    continue
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
            if pages >= self.max_pages:
                if not self.partial_insights:
                    raise ConnectorError("page_limit_exceeded")
                self.truncated = True
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
