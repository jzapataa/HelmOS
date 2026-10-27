"""One reviewed I4-B2 batch. Offline only; never used by normal synchronization."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .social_import import _post_path, list_observations, list_posts
from .sync_lock import execution_lock


OPERATION = "i4-b2-instagram-dates-20261027"
# Private identities and API evidence are loaded explicitly, never versioned.
EXPECTED = ()
EVIDENCE = {}


def configure_batch(path: Path) -> None:
    global EXPECTED, EVIDENCE
    batch = json.loads(safe_path(path).read_bytes())
    expected = tuple(tuple(item) for item in batch["expected"])
    if (len(expected) != 4 or any(len(item) != 3 for item in expected)
            or len({item[0] for item in expected}) != 4
            or not isinstance(batch["evidence"], dict)):
        raise ValueError("Exactly four reviewed identities and evidence required")
    EXPECTED, EVIDENCE = expected, batch["evidence"]


JOURNAL = ".i4-b2-date-transaction.json"


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def safe_path(path: Path) -> Path:
    if any(part.is_symlink() or part.is_junction() for part in (path, *path.parents)):
        raise ValueError("Reconciliation paths cannot contain links or junctions")
    return path


def atomic_write(path: Path, content: bytes) -> None:
    safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".reconcile-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def inventory(state: Path) -> dict[str, str]:
    """Pin dependencies too, including saved analyses, recommendations and evaluations."""
    return {p.relative_to(state).as_posix(): digest(safe_path(p).read_bytes())
            for p in sorted(state.rglob("*.json"))
            if p.name != JOURNAL and "reconciliations" not in p.relative_to(state).parts}


def receipt_path(state: Path) -> Path:
    return state / "social" / "reconciliations" / f"{OPERATION}.json"


def prepare(state: Path) -> dict:
    safe_path(state)
    if len(EXPECTED) != 4 or not EVIDENCE:
        raise ValueError("Load the private reviewed batch first")
    if (state / "social" / JOURNAL).exists() or receipt_path(state).exists():
        raise ValueError("Batch already started; use its existing plan")
    before_inventory = inventory(state)
    posts = list_posts(state, "Instagram")
    records = []
    for post_id, old_day, timestamp in EXPECTED:
        path = safe_path(_post_path(state, "Instagram", post_id))
        raw = path.read_bytes()
        post = json.loads(raw)
        matches = [p for p in posts if p["post_id"] == post_id]
        if len(matches) != 1 or matches[0] != post or post["platform"] != "Instagram":
            raise ValueError("Canonical identity mismatch or duplicate")
        initial = post["initial_observation"]
        row = post["source_row"]
        if (post["published_at"] != old_day or row["published_at"] != old_day
                or initial["source_row"] != row or "date_reconciliation" in post):
            raise ValueError("Historical date/provenance mismatch")
        source = safe_path(Path(initial["source_csv"]))
        source_bytes = source.read_bytes()
        rows = list(csv.DictReader(io.StringIO(source_bytes.decode("utf-8-sig"))))
        matching = [r for r in rows if r.get("platform") == "Instagram" and r.get("post_id") == post_id]
        if matching != [row]:
            raise ValueError("Original CSV row mismatch or duplicate")
        new_day = datetime.fromisoformat(timestamp).astimezone(timezone.utc).date().isoformat()
        for obs in list_observations(state, post):
            if ((obs["observed_at"] and obs["observed_at"] < new_day)
                    or (obs["period_start"] and obs["period_start"] < new_day)):
                raise ValueError("Proposed date contradicts an observation window")
        records.append({"platform": "Instagram", "post_id": post_id,
                        "path": path.relative_to(state).as_posix(), "before_date": old_day,
                        "after_date": new_day, "timestamp": timestamp,
                        "before_sha256": digest(raw),
                        "before_base64": base64.b64encode(raw).decode("ascii"),
                        "source_csv": str(source), "source_csv_sha256": digest(source_bytes)})
    if inventory(state) != before_inventory:
        raise ValueError("State changed during preparation")
    return {"operation": OPERATION, "state_dir": str(state.resolve()), "evidence": EVIDENCE,
            "records": records, "inventory": before_inventory}


def validate_plan(plan: dict, state: Path) -> list[tuple[Path, bytes, bytes]]:
    if (len(EXPECTED) != 4 or not EVIDENCE or plan["operation"] != OPERATION or plan["evidence"] != EVIDENCE
            or plan["state_dir"] != str(state.resolve()) or len(plan["records"]) != 4):
        raise ValueError("Not the approved four-record operation")
    changes = []
    for item, (post_id, day, timestamp) in zip(plan["records"], EXPECTED):
        path = safe_path(_post_path(state, "Instagram", post_id))
        before = base64.b64decode(item["before_base64"], validate=True)
        post = json.loads(before)
        new_day = timestamp[:10]
        if (item["platform"] != "Instagram" or item["post_id"] != post_id
                or item["before_date"] != day or item["timestamp"] != timestamp
                or item["after_date"] != new_day or item["path"] != path.relative_to(state).as_posix()
                or digest(before) != item["before_sha256"]
                or post["platform"] != "Instagram" or post["post_id"] != post_id
                or post["published_at"] != day or post["source_row"]["published_at"] != day
                or post["initial_observation"]["source_row"] != post["source_row"]
                or item["source_csv"] != post["initial_observation"]["source_csv"]
                or "date_reconciliation" in post):
            raise ValueError("Plan identity/date/provenance mismatch")
        post["published_at"] = new_day
        post["date_reconciliation"] = {"operation": OPERATION, "original_date": day,
                                       "timestamp": timestamp, **EVIDENCE}
        changes.append((path, before, encoded(post)))
    return changes


def run(state: Path, plan: dict, *, apply: bool = False, rollback: bool = False,
        approval: str | None = None, actor: str | None = None) -> dict:
    state = safe_path(state.absolute()).resolve()
    plan_hash = digest(encoded(plan))
    with execution_lock(state.parent, f".{state.name}.instagram.lock"):
        changes = validate_plan(plan, state)
        journal = safe_path(state / "social" / JOURNAL)
        receipt = safe_path(receipt_path(state))
        if apply or rollback:
            if approval != plan_hash or not actor or not actor.strip():
                raise ValueError("Explicit plan SHA-256 approval and actor required")
        if rollback:
            if receipt.exists():
                raise ValueError("Committed reconciliation cannot be rolled back by recovery")
            if not journal.exists() or json.loads(journal.read_bytes())["plan_sha256"] != plan_hash:
                raise ValueError("No matching interrupted transaction")
            # Validate ALL four before restoring any: never overwrite an unknown version.
            if any(p.read_bytes() not in (before, after) for p, before, after in changes):
                raise ValueError("Recovery blocked by unexpected record contents")
            for path, before, _ in changes:
                atomic_write(path, before)
            journal.unlink()
            return {"status": "rolled_back", "plan_sha256": plan_hash}
        if receipt.exists():
            saved = json.loads(receipt.read_bytes())
            if (saved["plan_sha256"] != plan_hash or saved["plan"] != plan
                    or any(p.read_bytes() != after for p, _, after in changes)):
                raise ValueError("Committed receipt or records differ")
            if journal.exists():
                if not apply:
                    raise ValueError("Committed transaction needs authorized cleanup")
                journal.unlink()
            return {"status": "already_applied", "plan_sha256": plan_hash, "modified": 0}
        if journal.exists():
            raise ValueError("Interrupted reconciliation: explicit --rollback required")
        if any(p.read_bytes() != before for p, before, _ in changes):
            raise ValueError("Exact four-record precondition failed; no writes")
        if inventory(state) != plan["inventory"]:
            raise ValueError("Dependencies changed since dry-run; review a new plan")
        for item in plan["records"]:
            if digest(safe_path(Path(item["source_csv"])).read_bytes()) != item["source_csv_sha256"]:
                raise ValueError("Source CSV changed since dry-run")
        report = {"status": "ready" if not apply else "applied", "operation": OPERATION,
                  "plan_sha256": plan_hash, "modified": 0 if not apply else 4,
                  "records": [{k: v for k, v in r.items() if k != "before_base64"}
                              for r in plan["records"]]}
        if not apply:
            return report
        audit = {"plan_sha256": plan_hash, "plan": plan, "actor": actor,
                 "approved_at": datetime.now(timezone.utc).isoformat(),
                 "status": "committed"}
        atomic_write(journal, encoded({**audit, "status": "prepared"}))
        try:
            for path, _, after in changes:
                atomic_write(path, after)
            atomic_write(receipt, encoded(audit))
        except BaseException:
            # A killed process leaves the durable journal for explicit offline recovery.
            # Ordinary write failures restore exact original bytes before releasing lock.
            if not receipt.exists():
                for path, before, _ in changes:
                    atomic_write(path, before)
                journal.unlink()
            raise
        journal.unlink()
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True, help="Private plan outside state-dir")
    parser.add_argument("--batch", type=Path, required=True, help="Private reviewed four-record evidence")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--rollback", action="store_true", help="Recover an interrupted, uncommitted batch")
    parser.add_argument("--approve", help="Exact SHA-256 printed by reviewed dry-run")
    parser.add_argument("--actor")
    args = parser.parse_args(argv)
    try:
        configure_batch(args.batch)
        state = safe_path(args.state_dir.absolute()).resolve()
        plan_path = safe_path(args.plan.absolute()).resolve()
        if plan_path.is_relative_to(state):
            raise ValueError("Store the plan outside state-dir")
        if not plan_path.exists():
            if not args.dry_run:
                raise ValueError("Prepare and review a dry-run first")
            with execution_lock(state.parent, f".{state.name}.instagram.lock"):
                plan = prepare(state)
                atomic_write(plan_path, encoded(plan))
        else:
            plan = json.loads(plan_path.read_bytes())
        report = run(state, plan, apply=args.apply, rollback=args.rollback,
                     approval=args.approve, actor=args.actor)
        print(json.dumps(report, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"Reconciliation blocked: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
