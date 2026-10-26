"""Persistent editorial recommendations and explicit human follow-up."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from .social_history import publication


STATES = frozenset({"pending", "accepted", "rejected"})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(value: str) -> tuple[str, int]:
    match = re.fullmatch(r"([0-9a-f-]{36}):([1-9][0-9]*)", value) if isinstance(value, str) else None
    if not match:
        raise ValueError("Invalid recommendation ID")
    try:
        run_id = str(UUID(match[1]))
    except ValueError as error:
        raise ValueError("Invalid recommendation ID") from error
    if run_id != match[1]:
        raise ValueError("Invalid recommendation ID")
    return run_id, int(match[2])


def _directory(state_dir: Path) -> Path:
    directory = state_dir / "social" / "recommendations"
    if directory.parent.is_symlink() or directory.is_symlink():
        raise ValueError("Recommendation directory cannot be a symbolic link")
    return directory


def _path(identifier: str, state_dir: Path) -> Path:
    run_id, index = _id(identifier)
    return _directory(state_dir) / f"{run_id}-{index}.json"


def _write(path: Path, record: dict[str, object], *, create: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.parent.parent.is_symlink() or path.is_symlink():
        raise ValueError("Recommendation path cannot be a symbolic link")
    descriptor, name = tempfile.mkstemp(prefix=".recommendation-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if create and path.exists():
            raise FileExistsError(f"Recommendation already exists: {record['id']}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def register_run(run: dict[str, object], state_dir: Path) -> list[dict[str, object]]:
    """Record only a newly completed analysis; repeated calls preserve human decisions."""
    if run.get("status") != "succeeded" or run.get("input", {}).get("operation") != "social.analyze":
        raise ValueError("A successful social.analyze execution is required")
    run_id = str(UUID(run["id"]))
    dataset = run["result"]["dataset"]
    editorial = run["result"]["editorial"]
    posts = {post["ref"]: post for post in dataset["posts"]}
    saved = []
    for index, item in enumerate(editorial["recommendations"], 1):
        identifier = f"{run_id}:{index}"
        path = _path(identifier, state_dir)
        if path.exists() or path.is_symlink():
            saved.append(read_recommendation(identifier, state_dir))
            continue
        references = [{"ref": ref, "platform": posts[ref]["platform"],
                       "post_id": posts[ref]["post_id"]} for ref in item["post_refs"]]
        created_at = utc_now()
        record = {
            "id": identifier, "origin_run_id": run_id, "text": item["action"],
            "rationale": item["rationale"], "evidence": references,
            "platform": item.get("platform"), "topic": item.get("topic"),
            "created_at": created_at, "status": "pending", "resolved_at": None,
            "history": [{"kind": "created", "at": created_at, "from": None, "to": "pending"}],
            "associated_posts": [],
        }
        _write(path, record, create=True)
        saved.append(record)
    return saved


def read_recommendation(identifier: str, state_dir: Path) -> dict[str, object]:
    path = _path(identifier, state_dir)
    if path.is_symlink():
        raise ValueError("Recommendation path cannot be a symbolic link")
    with path.open("r", encoding="utf-8") as stream:
        record = json.load(stream)
    if (not isinstance(record, dict) or record.get("id") != identifier
            or record.get("origin_run_id") != _id(identifier)[0]
            or record.get("status") not in STATES
            or not isinstance(record.get("history"), list)
            or not isinstance(record.get("associated_posts"), list)
            or not all(isinstance(record.get(key), str) and record[key]
                       for key in ("text", "rationale", "created_at"))
            or not isinstance(record.get("evidence"), list)
            or not record["evidence"]):
        raise ValueError("Recommendation record is invalid")
    history = record["history"]
    if not history or history[0] != {"kind": "created", "at": record["created_at"],
                                      "from": None, "to": "pending"}:
        raise ValueError("Recommendation record is invalid")
    if not all(isinstance(event, dict) for event in history):
        raise ValueError("Recommendation record is invalid")
    decisions = [event for event in history if event.get("kind") == "decision"]
    if record["status"] == "pending":
        valid_decision = not decisions and record.get("resolved_at") is None
    else:
        valid_decision = (len(decisions) == 1 and decisions[0] == {
            "kind": "decision", "at": record.get("resolved_at"), "from": "pending",
            "to": record["status"]} and isinstance(record.get("resolved_at"), str))
    if not valid_decision:
        raise ValueError("Recommendation record is invalid")
    posts = record["associated_posts"]
    if (not all(isinstance(post, dict) and isinstance(post.get("platform"), str)
                and isinstance(post.get("post_id"), str) for post in posts)
            or len({(post["platform"], post["post_id"]) for post in posts}) != len(posts)
            or (posts and record["status"] != "accepted")):
        raise ValueError("Recommendation record is invalid")
    return record


def list_recommendations(state_dir: Path, status: str | None = None) -> list[dict[str, object]]:
    if status is not None and status not in STATES:
        raise ValueError("Invalid recommendation status")
    directory = _directory(state_dir)
    if not directory.exists():
        return []
    records = (read_recommendation(f"{path.stem.rsplit('-', 1)[0]}:{path.stem.rsplit('-', 1)[1]}", state_dir)
               for path in directory.glob("*.json"))
    return sorted((record for record in records if status is None or record["status"] == status),
                  key=lambda record: (record["created_at"], record["id"]))


def decide_recommendation(identifier: str, decision: str, state_dir: Path) -> dict[str, object]:
    if decision not in ("accepted", "rejected"):
        raise ValueError("Invalid recommendation decision")
    record = read_recommendation(identifier, state_dir)
    if record["status"] != "pending":
        raise ValueError(f"Recommendation is already {record['status']}")
    now = utc_now()
    record["status"] = decision
    record["resolved_at"] = now
    record["history"].append({"kind": "decision", "at": now, "from": "pending", "to": decision})
    _write(_path(identifier, state_dir), record, create=False)
    return record


def associate_post(identifier: str, platform: str, post_id: str, state_dir: Path) -> dict[str, object]:
    record = read_recommendation(identifier, state_dir)
    if record["status"] != "accepted":
        raise ValueError("Only accepted recommendations can be associated with publications")
    post = publication(state_dir, platform, post_id)
    reference = {"platform": post["platform"], "post_id": post["post_id"]}
    if reference in record["associated_posts"]:
        raise ValueError("Publication is already associated with this recommendation")
    now = utc_now()
    record["associated_posts"].append(reference)
    record["history"].append({"kind": "association", "at": now, "publication": reference})
    _write(_path(identifier, state_dir), record, create=False)
    return record


def associated_posts(identifier: str, state_dir: Path) -> list[dict[str, str]]:
    return read_recommendation(identifier, state_dir)["associated_posts"]
