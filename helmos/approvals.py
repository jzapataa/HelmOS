"""Local proposals for human decisions; no proposal dispatches work."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

from .core import read_execution, utc_now


ALLOWED_ACTIONS = frozenset({"review_output", "manual_follow_up"})
MAX_TEXT_LENGTH = 1000
RESOLVED_STATES = frozenset({"approved", "rejected"})


def _identifier(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Invalid proposal ID") from error


def _directory(state_dir: Path) -> Path:
    directory = state_dir / "approvals"
    if directory.is_symlink():
        raise ValueError("Approval directory cannot be a symbolic link")
    return directory


def _path(identifier: str, state_dir: Path) -> Path:
    return _directory(state_dir) / f"{_identifier(identifier)}.json"


def _validate_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT_LENGTH:
        raise ValueError(f"{label} must contain 1 to {MAX_TEXT_LENGTH} characters")
    return value.strip()


def _write(path: Path, record: dict[str, object], *, replace: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("Approval path cannot be a symbolic link")
    descriptor, name = tempfile.mkstemp(prefix=".approval-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if not replace and path.exists():
            raise FileExistsError(f"Proposal already exists: {path.name}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def create_proposal(
    origin_run_id: str, action: str, description: str, scope: str, state_dir: Path,
) -> dict[str, object]:
    if not isinstance(action, str) or action not in ALLOWED_ACTIONS:
        raise ValueError(f"Unsupported proposal action: {action}")
    description = _validate_text(description, "Description")
    scope = _validate_text(scope, "Scope")
    origin = read_execution(origin_run_id, state_dir)
    identifier = str(uuid4())
    created_at = utc_now()
    record: dict[str, object] = {
        "id": identifier,
        "action": action,
        "description": description,
        "scope": scope,
        "status": "pending",
        "origin_run_id": origin["id"],
        "created_at": created_at,
        "resolved_at": None,
        "transitions": [{"from": None, "to": "pending", "at": created_at}],
    }
    _write(_path(identifier, state_dir), record, replace=False)
    return record


def _valid_record(record: object, identifier: str) -> bool:
    if not isinstance(record, dict) or record.get("id") != identifier:
        return False
    if not isinstance(record.get("action"), str) or record["action"] not in ALLOWED_ACTIONS:
        return False
    if not all(isinstance(record.get(key), str) and record[key].strip()
               and len(record[key]) <= MAX_TEXT_LENGTH for key in ("description", "scope")):
        return False
    try:
        if str(UUID(record.get("origin_run_id"))) != record["origin_run_id"]:
            return False
    except (ValueError, TypeError, AttributeError):
        return False
    created_at = record.get("created_at")
    transitions = record.get("transitions")
    status = record.get("status")
    if not isinstance(created_at, str) or not created_at:
        return False
    if not isinstance(transitions, list) or not transitions:
        return False
    if transitions[0] != {"from": None, "to": "pending", "at": created_at}:
        return False
    if status == "pending":
        return len(transitions) == 1 and record.get("resolved_at") is None
    if isinstance(status, str) and status in RESOLVED_STATES:
        resolved_at = record.get("resolved_at")
        return (
            isinstance(resolved_at, str) and bool(resolved_at)
            and len(transitions) == 2
            and transitions[1] == {"from": "pending", "to": status, "at": resolved_at}
        )
    return False


def read_proposal(identifier: str, state_dir: Path) -> dict[str, object]:
    path = _path(identifier, state_dir)
    if path.is_symlink():
        raise ValueError("Proposal path cannot be a symbolic link")
    with path.open("r", encoding="utf-8") as stream:
        record = json.load(stream)
    if not _valid_record(record, path.stem):
        raise ValueError("Proposal record is invalid")
    return record


def list_proposals(state_dir: Path) -> list[dict[str, object]]:
    directory = _directory(state_dir)
    if not directory.exists():
        return []
    return sorted(
        (read_proposal(path.stem, state_dir) for path in directory.glob("*.json")),
        key=lambda record: (record["created_at"], record["id"]),
    )


def decide_proposal(identifier: str, decision: str, state_dir: Path) -> dict[str, object]:
    if not isinstance(decision, str) or decision not in RESOLVED_STATES:
        raise ValueError(f"Unsupported proposal decision: {decision}")
    record = read_proposal(identifier, state_dir)
    if record["status"] != "pending":
        raise ValueError(f"Proposal is already {record['status']}")
    resolved_at = utc_now()
    record["status"] = decision
    record["resolved_at"] = resolved_at
    record["transitions"].append({"from": "pending", "to": decision, "at": resolved_at})
    _write(_path(identifier, state_dir), record, replace=True)
    return record
