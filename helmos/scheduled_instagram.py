"""Bounded, recoverable daily Instagram synchronization; no scheduler side effects."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .instagram_credentials import InstagramCredentials
from .instagram_sync import sync_instagram
from .social_connectors import ConnectorError
from .sync_lock import execution_lock


@dataclass(frozen=True)
class SyncConfig:
    api_version: str = "v26.0"
    interval_days: int = 1
    page_size: int = 25
    max_pages: int = 4
    max_posts: int = 100
    max_requests: int = 700
    timeout: float = 10.0
    deadline_seconds: float = 120.0

    def __post_init__(self):
        if not re.fullmatch(r"v[0-9]{2}\.0", self.api_version):
            raise ConnectorError("invalid_api_version")
        for name, low, high in (("interval_days", 1, 365), ("page_size", 1, 100),
                                ("max_pages", 1, 1000), ("max_posts", 1, 10000),
                                ("max_requests", 2, 1000)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ConnectorError("invalid_scheduled_config")
        for name, high in (("timeout", 30), ("deadline_seconds", 600)):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 < value <= high:
                raise ConnectorError("invalid_timeout")


def now():
    return datetime.now(timezone.utc).isoformat()


def write_record(path: Path, record: dict):
    """Atomic replacement; an interrupted write leaves the previous record readable."""
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(record, ensure_ascii=True, indent=2).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def sync_status(state_dir: Path) -> dict:
    directory = state_dir.resolve() / "social" / "scheduled_instagram"
    runs = []
    for path in (directory / "runs").glob("*.json"):
        runs.append(json.loads(path.read_text(encoding="utf-8")))
    runs.sort(key=lambda run: (run["started_at"], run["id"]))
    successes = [run for run in runs if run["status"] == "succeeded"]
    return {"last_run": runs[-1] if runs else None,
            "last_success": successes[-1] if successes else None}


def run_scheduled(state_dir: Path, config: SyncConfig) -> tuple[dict, int]:
    state_dir = state_dir.resolve()
    directory = state_dir / "social" / "scheduled_instagram"
    try:
        with execution_lock(directory):
            # No other scheduled writer can still own a 'running' record.
            for path in (directory / "runs").glob("*.json"):
                previous = json.loads(path.read_text(encoding="utf-8"))
                if previous["status"] == "running":
                    previous.update(status="interrupted", finished_at=now(), exit_code=2,
                                    error="previous_process_interrupted")
                    write_record(path, previous)
            identifier = uuid.uuid4().hex
            path = directory / "runs" / f"{identifier}.json"
            record = {"id": identifier, "username": "jorgebytes", "started_at": now(),
                      "finished_at": None, "status": "running", "config": asdict(config),
                      "retry_policy": "next_scheduled_run", "result": None}
            write_record(path, record)
            try:
                options = asdict(config)
                options.pop("interval_days")
                provider, _ = InstagramCredentials(
                    expected_username="jorgebytes", dpapi_only=True,
                    **{key: options[key] for key in (
                        "api_version", "timeout", "deadline_seconds", "max_requests")}).prepare()
                result = sync_instagram(state_dir, expected_username="jorgebytes",
                                        provider=provider, preserve_partial=True, **options)
                # Only allowlisted counters and safe diagnostics enter the run journal.
                record["result"] = {key: result[key] for key in (
                    "captured_at", "posts_consulted", "new_posts", "existing_posts",
                    "posts_imported", "observations_saved", "unchanged_posts",
                    "duplicates_skipped", "bounded_listing", "requests_made",
                    "partial_errors", "failure")}
                failure = result["failure"]
                record["status"] = result["status"]
                if result["bounded_listing"] and record["status"] == "succeeded":
                    record["status"] = "partial"
                code = 2 if record["status"] == "failed" else (
                    1 if record["status"] == "partial" else 0)
                error = failure["code"] if failure else None
            except ConnectorError as exc:
                # Codes originate only from local safe boundaries; no remote messages.
                error = exc.code
                record.update(status="failed", error=error,
                              retry_after=exc.retry_after, http_status=exc.status)
                code = 3 if error == "scheduled_sync_busy" else 2
                if code == 3:
                    record["status"] = "busy"
            except Exception:
                error = "scheduled_sync_failed"
                record.update(status="failed", error=error)
                code = 2
            record.update(finished_at=now(), exit_code=code,
                          reauthorization_required=error in {
                              "authentication_required", "instagram_token_required",
                              "instagram_token_expired_reauthorize", "instagram_permissions_required"})
            write_record(path, record)
            return record, code
    except ConnectorError as exc:
        return {"status": "busy", "error": exc.code}, 3
    except Exception:
        # A persistence failure must never become a success or expose file contents.
        return {"status": "failed", "error": "scheduled_record_failed"}, 2
