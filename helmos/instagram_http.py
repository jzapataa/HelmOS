"""Bounded Instagram Login GET transport; never exposes credentials."""

from __future__ import annotations

import http.client
import json
import math
import os
import re
import time
from urllib.parse import urlencode

from .social_connectors import Account, ConnectorError, Response, response_payload


class InstagramHTTPProvider:
    HOST = "graph.instagram.com"
    MAX_RESPONSE_BYTES = 1024 * 1024

    def __init__(self, *, api_version: str, expected_username: str,
                 timeout: float = 10, deadline_seconds: float = 120,
                 max_requests: int = 100, token: str | None = None, diagnostics=None):
        self.diagnostics = diagnostics
        if not re.fullmatch(r"v[0-9]{2}\.0", api_version):
            raise ConnectorError("invalid_api_version")
        if not isinstance(expected_username, str) or not re.fullmatch(r"[A-Za-z0-9_.]{1,30}", expected_username):
            raise ConnectorError("invalid_expected_username")
        if not math.isfinite(timeout) or not math.isfinite(deadline_seconds) or not 0 < timeout <= 30 or not 0 < deadline_seconds <= 600:
            raise ConnectorError("invalid_timeout")
        if type(max_requests) is not int or not 2 <= max_requests <= 1000:
            raise ConnectorError("invalid_request_budget")
        if token is None:
            token = os.environ.get("HELMOS_INSTAGRAM_ACCESS_TOKEN", "")
            if not token:
                from .credential_store import WindowsCredentialStore
                stored = WindowsCredentialStore().load()
                token = stored.get("token", "") if stored else ""
        if (not isinstance(token, str) or not token or len(token) > 16000
                or any(char.isspace() or ord(char) < 33 or ord(char) > 126 for char in token)):
            raise ConnectorError("instagram_token_required")
        self._token = token
        self.api_version = api_version
        self.timeout = timeout
        self._deadline = time.monotonic() + deadline_seconds
        self._remaining = max_requests
        self.requests_made = 0
        self._media_ids = set()
        response = self.request("GET", f"/{api_version}/me",
                                params={"fields": "user_id,username,account_type"})
        identity = response_payload(response, "Instagram")
        if identity.get("account_type") not in ("BUSINESS", "MEDIA_CREATOR", "CREATOR"):
            raise ConnectorError("professional_account_required")
        username = identity.get("username")
        if not isinstance(username, str) or username.casefold() != expected_username.casefold():
            raise ConnectorError("instagram_account_mismatch")
        user_id = identity.get("user_id")
        if not isinstance(user_id, str) or not re.fullmatch(r"[0-9]{1,200}", user_id):
            raise ConnectorError("invalid_instagram_account")
        self.account = Account("Instagram", user_id)
        self.username = username

    def refresh_token(self) -> dict:
        """Refresh only; eligibility and atomic persistence belong to the manager."""
        return response_payload(self._get(
            "/refresh_access_token", {"grant_type": "ig_refresh_token"}), "Instagram")

    def use_refreshed_token(self, token: str) -> None:
        """Validate replacement with the same budget, reverting memory on failure."""
        previous = self._token
        self._token = token
        try:
            identity = response_payload(self.request(
                "GET", f"/{self.api_version}/me",
                params={"fields": "user_id,username,account_type"}), "Instagram")
            if (identity.get("user_id") != self.account.account_id
                    or not isinstance(identity.get("username"), str)
                    or identity["username"].casefold() != self.username.casefold()
                    or identity.get("account_type") not in ("BUSINESS", "MEDIA_CREATOR", "CREATOR")):
                raise ConnectorError("instagram_account_mismatch")
        except Exception:
            self._token = previous
            raise

    def request(self, method: str, path: str, *, params: dict, body=None) -> Response:
        prefix = re.escape(self.api_version)
        if (method != "GET" or body is not None or not re.fullmatch(
                rf"/{prefix}/(?:me(?:/media)?|[0-9]{{1,200}}/(?:media|insights))", path)
                or set(params) - {"fields", "limit", "after", "metric"}):
            raise ConnectorError("instagram_request_not_allowed")
        resource = path.removeprefix(f"/{self.api_version}/")
        if resource.endswith("/media") and resource != "me/media" and (
                not hasattr(self, "account") or resource != f"{self.account.account_id}/media"):
            raise ConnectorError("instagram_request_not_allowed")
        if resource.endswith("/insights") and resource.split("/")[0] not in self._media_ids:
            raise ConnectorError("instagram_request_not_allowed")
        response = self._get(path, params)
        if response.status == 200 and resource.endswith("/media") and isinstance(response.body, dict):
            data = response.body.get("data", [])
            if isinstance(data, list):
                self._media_ids.update(item["id"] for item in data if isinstance(item, dict)
                                       and isinstance(item.get("id"), str)
                                       and re.fullmatch(r"[0-9]{1,200}", item["id"]))
        return response

    def _get(self, path: str, params: dict) -> Response:
        refresh = path == "/refresh_access_token"
        if self.diagnostics is not None:
            # Only local operation labels; never paths, cursors, fields or tokens.
            operation = "refresh" if refresh else (
                "metrics" if path.endswith("/insights") else (
                    "listing" if path.endswith("/media") else "identity"))
            self.diagnostics.context.update(operation=operation, requests_made=self.requests_made)
            self.diagnostics.context.pop("transport", None)
        if refresh and params != {"grant_type": "ig_refresh_token"}:
            raise ConnectorError("instagram_request_not_allowed")
        if self._remaining <= 0:
            raise ConnectorError("request_budget_exceeded")
        remaining_time = self._deadline - time.monotonic()
        if remaining_time <= 0:
            raise ConnectorError("deadline_exceeded")
        self._remaining -= 1
        self.requests_made += 1
        if self.diagnostics is not None:
            self.diagnostics.context["requests_made"] = self.requests_made
        connection = None
        failure = None
        try:
            connection = http.client.HTTPSConnection(self.HOST, timeout=min(self.timeout, remaining_time))
            connection.set_debuglevel(0)
            # Meta requires query authentication ONLY for refresh. Build the
            # relative request target in memory; never retain/log a complete URL.
            headers = {"Accept": "application/json"}
            if refresh:
                params = dict(params, access_token=self._token)
            else:
                headers["Authorization"] = "Bearer " + self._token
            connection.request("GET", path + "?" + urlencode(params), headers=headers)
            response = connection.getresponse()
            reported_version = response.getheader("Instagram-API-Version") or response.getheader("Facebook-API-Version")
            if reported_version and reported_version != self.api_version:
                raise ConnectorError("api_version_mismatch")
            retry = response.getheader("Retry-After", "")
            headers = {"Retry-After": retry} if retry.isascii() and retry.isdecimal() and len(retry) < 10 else {}
            if 300 <= response.status < 400:
                raise ConnectorError("redirect_rejected", status=response.status)
            chunks, size = [], 0
            while True:
                remaining_time = self._deadline - time.monotonic()
                if remaining_time <= 0:
                    raise ConnectorError("deadline_exceeded")
                if connection.sock is not None:
                    connection.sock.settimeout(min(self.timeout, remaining_time))
                chunk = response.read1(min(65536, self.MAX_RESPONSE_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > self.MAX_RESPONSE_BYTES:
                    raise ConnectorError("response_too_large")
                chunks.append(chunk)
            try:
                payload = json.loads(b"".join(chunks))
            except (ValueError, UnicodeError):
                if response.status == 200:
                    raise ConnectorError("invalid_json") from None
                payload = {}  # Status still reports failure, without remote body.
            result = Response(response.status, payload, headers)
        except ConnectorError as error:
            failure = ConnectorError(error.code, status=error.status, retry_after=error.retry_after)
        except Exception as error:
            if self.diagnostics is not None:
                self.diagnostics.context["transport"] = self.diagnostics.failure(error)["category"]
            failure = ConnectorError("transport_failure")
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception as error:
                    if self.diagnostics is not None:
                        self.diagnostics.context["transport"] = self.diagnostics.failure(error)["category"]
                    failure = ConnectorError("transport_failure")
            # Do not retain a request target in the connection/buffer on failure.
            connection = None
        # Raise outside the handler: no secret-bearing transport exception context.
        if failure is not None:
            raise failure from None
        return result
