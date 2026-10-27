"""Instagram credential lifecycle. Remote eligibility is never inferred from /me."""

from __future__ import annotations

import getpass
import os
import sys
import warnings
from datetime import datetime, timedelta, timezone

from .credential_store import CredentialStore, WindowsCredentialStore
from .instagram_http import InstagramHTTPProvider
from .social_connectors import ConnectorError


KINDS = ("unknown", "short-lived", "long-lived", "dashboard")
REFRESH_WINDOW = timedelta(days=7)
MINIMUM_AGE = timedelta(hours=24)


def utc_now():
    return datetime.now(timezone.utc)


def timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise ConnectorError("invalid_credential_date") from None


def validate_token(token):
    if (not isinstance(token, str) or not token or len(token) > 16000
            or any(char.isspace() or ord(char) < 33 or ord(char) > 126 for char in token)):
        raise ConnectorError("instagram_token_required")


def secure_input() -> str:
    if not sys.stdin.isatty():
        raise ConnectorError("interactive_terminal_required")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass("Token Instagram (entrada oculta): ")
    except (getpass.GetPassWarning, EOFError):
        raise ConnectorError("secure_input_unavailable") from None


class InstagramCredentials:
    def __init__(self, *, api_version: str, expected_username: str,
                 store: CredentialStore | None = None, clock=utc_now,
                 dpapi_only: bool = False, **http_options):
        self.api_version = api_version
        self.expected_username = expected_username
        self._store = store
        self.clock = clock
        self.http_options = http_options
        self.dpapi_only = dpapi_only

    @property
    def store(self):
        if self._store is None:
            self._store = WindowsCredentialStore()
        return self._store

    def _load(self):
        # Explicit process environment preserves I4-A2 compatibility; never update
        # a store that would be shadowed by this environment on the next execution.
        token = None if self.dpapi_only else os.environ.get("HELMOS_INSTAGRAM_ACCESS_TOKEN")
        if token:
            validate_token(token)
            return {"token": token, "kind": "unknown"}, "environment"
        record = self.store.load()
        if record is None:
            raise ConnectorError("instagram_token_required")
        validate_token(record.get("token"))
        if record.get("kind") not in KINDS:
            raise ConnectorError("invalid_credential_store")
        for key in ("issued_at", "expires_at", "observed_at", "checked_at", "last_refreshed_at"):
            timestamp(record.get(key))
        username = record.get("username")
        if not isinstance(username, str) or username.casefold() != self.expected_username.casefold():
            raise ConnectorError("instagram_account_mismatch")
        return record, "windows_dpapi"

    def _provider(self, record):
        expiry = timestamp(record.get("expires_at"))
        if expiry and expiry <= self.clock():
            raise ConnectorError("instagram_token_expired_reauthorize")
        provider = InstagramHTTPProvider(
            api_version=self.api_version, expected_username=self.expected_username,
            token=record["token"], **self.http_options)
        if record.get("account_id") and record["account_id"] != provider.account.account_id:
            raise ConnectorError("instagram_account_mismatch")
        return provider

    def _reason(self, record, source, *, requested=False):
        now = self.clock()
        expiry = timestamp(record.get("expires_at"))
        if expiry and expiry <= now:
            return "expired_reauthorize"
        if source == "environment":
            return "import_required_for_refresh"
        if record.get("kind") not in ("long-lived", "dashboard"):
            return "long_lived_token_not_established"
        # Observation is a conservative lower bound, not an invented issuance date.
        age_from = timestamp(record.get("issued_at")) or timestamp(record.get("observed_at"))
        if age_from is None or now - age_from < MINIMUM_AGE:
            return "minimum_age_not_reached"
        if expiry and expiry - now > REFRESH_WINDOW:
            return "not_due"
        if expiry is None and not requested:
            return "expiry_unknown_controlled_refresh_required"
        return "due"

    def _summary(self, record, source, *, action="status", live=False):
        reason = self._reason(record, source)
        return {"source": source, "account_id": record.get("account_id"),
                "username": record.get("username"), "kind": record["kind"],
                "checked_at": record.get("checked_at"),
                "expires_at": record.get("expires_at"),
                "expiry_source": record.get("expiry_source", "unknown"),
                "issued_at": record.get("issued_at"),
                "observed_at": record.get("observed_at"),
                "last_refreshed_at": record.get("last_refreshed_at"),
                "refresh_reason": reason, "action": action,
                "authentication": "valid_now" if live else "not_checked",
                "reauthorization_required": reason == "expired_reauthorize"}

    def status(self):
        try:
            record, source = self._load()
        except ConnectorError as error:
            if error.code == "instagram_token_required":
                return {"source": "absent", "authentication": "not_checked",
                        "reauthorization_required": True, "expires_at": None}
            raise
        return self._summary(record, source)

    def import_token(self, token: str, *, kind="unknown", issued_at=None, expires_at=None):
        validate_token(token)
        if kind not in KINDS:
            raise ConnectorError("invalid_credential_kind")
        now = self.clock()
        issued, expiry = timestamp(issued_at), timestamp(expires_at)
        if (issued and issued > now) or (expiry and (expiry <= now or (issued and expiry <= issued))):
            raise ConnectorError("invalid_credential_date")
        record = {"token": token, "kind": kind,
                  "issued_at": issued.isoformat() if issued else None,
                  "expires_at": expiry.isoformat() if expiry else None,
                  "expiry_source": "operator" if expiry else "unknown",
                  "observed_at": now.isoformat()}
        with self.store.transaction():
            provider = self._provider(record)
            record.update(account_id=provider.account.account_id, username=provider.username,
                          checked_at=self.clock().isoformat())
            self.store.save(record)
        result = self._summary(record, "windows_dpapi", action="imported", live=True)
        result["environment_override"] = bool(os.environ.get("HELMOS_INSTAGRAM_ACCESS_TOKEN"))
        return result

    def prepare(self, *, requested=False, refresh=True):
        # Read environment without creating a store or lock (legacy read-only path).
        if not self.dpapi_only and os.environ.get("HELMOS_INSTAGRAM_ACCESS_TOKEN"):
            record, source = self._load()
            provider = self._provider(record)
            record.update(account_id=provider.account.account_id, username=provider.username,
                          checked_at=self.clock().isoformat())
            return provider, self._summary(record, source, action="checked", live=True)
        with self.store.transaction():
            record, source = self._load()
            provider = self._provider(record)
            updated = dict(record, checked_at=self.clock().isoformat())
            action = "checked"
            if refresh and self._reason(record, source, requested=requested) == "due":
                started = self.clock()
                payload = provider.refresh_token()
                new_token, lifetime = payload.get("access_token"), payload.get("expires_in")
                validate_token(new_token)
                if (type(lifetime) is not int or not 0 < lifetime <= 60 * 86400
                        or str(payload.get("token_type", "")).lower() != "bearer"):
                    raise ConnectorError("invalid_refresh_response")
                # Same transport, request budget and deadline; validate returned identity
                # before the encrypted replacement. A failed check never saves anything.
                provider.use_refreshed_token(new_token)
                completed = self.clock().isoformat()
                updated.update(token=new_token, kind="long-lived", issued_at=None,
                               observed_at=completed, last_refreshed_at=completed,
                               expires_at=(started + timedelta(seconds=lifetime)).isoformat(),
                               expiry_source="meta_refresh", checked_at=completed)
                action = "refreshed"
            self.store.save(updated)
        return provider, self._summary(updated, source, action=action, live=True)

    def check(self):
        return self.prepare(refresh=False)[1]

    def refresh(self):
        return self.prepare(requested=True)[1]
