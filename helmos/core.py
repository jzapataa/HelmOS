"""Small, local execution path for the first Core increment."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from .social_sample import SampleValidationError, analyze_csv


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SUPPORTED_OPERATION = "core.status"
SAMPLE_OPERATION = "social.sample"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def route(operation: str) -> dict[str, str]:
    if operation == SUPPORTED_OPERATION:
        return {
            "domain": "core",
            "handler": "local_status",
            "reason": "Explicit rule for core.status",
        }
    if operation == SAMPLE_OPERATION:
        return {
            "domain": "social_sample",
            "handler": "analyze_csv",
            "reason": "Explicit rule for the bounded social sample analysis",
        }
    raise ValueError(f"Unsupported operation: {operation}")


def local_status() -> dict[str, object]:
    """Report which local inputs for the Core are present, without reading them."""
    required = {
        "v1_spec": REPOSITORY_ROOT / "docs" / "SYSTEM_SPEC_V1.0.md",
        "developer_profile": REPOSITORY_ROOT / ".codex" / "agents" / "developer.toml",
        "qa_profile": REPOSITORY_ROOT / ".codex" / "agents" / "qa.toml",
        "reviewer_profile": REPOSITORY_ROOT / ".codex" / "agents" / "reviewer.toml",
    }
    available = {name: path.is_file() for name, path in required.items()}
    return {"ready": all(available.values()), "local_inputs": available}


def _write_atomic(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".run-", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise FileExistsError(f"Execution already exists: {path.name}")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def execute(operation: str, state_dir: Path, csv_path: Path | None = None) -> dict[str, object]:
    selected_route = route(operation)
    if operation == SAMPLE_OPERATION and csv_path is None:
        raise ValueError("social.sample requires --csv PATH")
    if operation == SUPPORTED_OPERATION and csv_path is not None:
        raise ValueError("core.status does not accept --csv")
    request: dict[str, str] = {"operation": operation}
    if csv_path is not None:
        request["csv_path"] = str(csv_path)
    record: dict[str, object] = {
        "id": str(uuid4()),
        "input": request,
        "route": selected_route,
        "context": (
            ["presence of local specification and agent profiles"]
            if operation == SUPPORTED_OPERATION else ["user-selected CSV"]
        ),
        "capabilities": (
            ["read local file metadata", "write execution record"]
            if operation == SUPPORTED_OPERATION else ["read selected CSV", "write execution record"]
        ),
        "status": "running",
        "started_at": utc_now(),
        "finished_at": None,
        "result": None,
        "error": None,
        "consumption": {"tokens": None, "cost": None, "measurement": "unknown"},
    }
    try:
        record["result"] = (
            local_status() if operation == SUPPORTED_OPERATION else analyze_csv(csv_path)
        )
        record["status"] = "succeeded"
    except (OSError, SampleValidationError) as error:
        record["status"] = "failed"
        record["error"] = (
            f"Local status check failed: {error}"
            if operation == SUPPORTED_OPERATION else f"Social sample analysis failed: {error}"
        )
    record["finished_at"] = utc_now()
    _write_atomic(state_dir / "runs" / f"{record['id']}.json", record)
    return record


def read_execution(identifier: str, state_dir: Path) -> dict[str, object]:
    try:
        normalized = str(UUID(identifier))
    except ValueError as error:
        raise ValueError("Invalid execution ID") from error
    path = state_dir / "runs" / f"{normalized}.json"
    with path.open("r", encoding="utf-8") as stream:
        record = json.load(stream)
    if not _valid_record(record, normalized):
        raise ValueError("Execution record is invalid")
    return record


def _valid_record(record: object, identifier: str) -> bool:
    if not isinstance(record, dict) or record.get("id") != identifier:
        return False
    request = record.get("input")
    selected_route = record.get("route")
    consumption = record.get("consumption")
    if not isinstance(request, dict) or not isinstance(selected_route, dict):
        return False
    if not isinstance(consumption, dict):
        return False
    if not isinstance(request.get("operation"), str) or not request["operation"]:
        return False
    if any(
        not isinstance(selected_route.get(key), str) or not selected_route[key]
        for key in ("domain", "handler", "reason")
    ):
        return False
    if any(
        not isinstance(record.get(key), str) or not record[key]
        for key in ("started_at", "finished_at")
    ):
        return False
    if not all(key in consumption for key in ("tokens", "cost", "measurement")):
        return False
    tokens = consumption["tokens"]
    cost = consumption["cost"]
    if tokens is not None and (type(tokens) is not int or tokens < 0):
        return False
    if cost is not None and (type(cost) not in (int, float) or cost < 0):
        return False
    if not isinstance(consumption["measurement"], str) or not consumption["measurement"]:
        return False
    if record.get("status") == "succeeded":
        return isinstance(record.get("result"), dict) and record.get("error") is None
    if record.get("status") == "failed":
        return (
            record.get("result") is None
            and isinstance(record.get("error"), str)
            and bool(record["error"])
        )
    return False
