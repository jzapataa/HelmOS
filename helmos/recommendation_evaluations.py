"""On-demand, deterministic observation of accepted editorial recommendations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path

from .recommendations import read_recommendation, utc_now
from .social_history import observations, publication


METHOD = "observed-cumulative-v1"
CAUTION = ("Observed performance does not establish causality or validate the editorial "
           "hypothesis. Association does not verify implementation; no control group, "
           "baseline or success criterion is defined by this evaluation.")


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _directory(state_dir: Path) -> Path:
    directory = state_dir / "social" / "evaluations"
    if directory.parent.is_symlink() or directory.is_symlink():
        raise ValueError("Evaluation directory cannot be a symbolic link")
    return directory


def _path(identifier: str, state_dir: Path) -> Path:
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{64}", identifier):
        raise ValueError("Invalid evaluation ID")
    path = _directory(state_dir) / f"{identifier}.json"
    if path.is_symlink():
        raise ValueError("Evaluation path cannot be a symbolic link")
    return path


def read_evaluation(identifier: str, state_dir: Path) -> dict[str, object]:
    record = json.loads(_path(identifier, state_dir).read_text(encoding="utf-8"))
    if (not isinstance(record, dict) or record.get("id") != identifier
            or record.get("method") != METHOD
            or record.get("status") not in ("observed", "insufficient_data")
            or not isinstance(record.get("recommendation_id"), str)
            or not isinstance(record.get("evaluated_at"), str)
            or not isinstance(record.get("publications"), list)
            or not record["publications"]
            or not isinstance(record.get("limitations"), list)
            or record.get("hypothesis_assessment") != "insufficient_data"
            or not isinstance(record.get("explanation"), str)):
        raise ValueError("Invalid evaluation record")
    return record


def list_evaluations(identifier: str, state_dir: Path) -> list[dict[str, object]]:
    read_recommendation(identifier, state_dir)
    records = [read_evaluation(path.stem, state_dir)
               for path in _directory(state_dir).glob("*.json")]
    return sorted((item for item in records if item["recommendation_id"] == identifier),
                  key=lambda item: (item["evaluated_at"], item["id"]))


def _performance(post: dict[str, object], items: list[dict[str, object]]) -> dict[str, object]:
    snapshots = []
    series: dict[str, list[dict[str, object]]] = {}
    limitations = []
    for item in items:
        # Content references also cover the initial observation and legacy records,
        # which have no standalone observation file. Provenance is retained separately.
        measurement = {key: item[key] for key in
                       ("observed_at", "measurement_type", "period_start", "period_end", "metrics")}
        ref = _digest({"platform": post["platform"], "post_id": post["post_id"], **measurement})
        snapshot = {"ref": ref, **measurement, "source_csv": item["source_csv"],
                    "source_row": item["source_row"]}
        snapshots.append(snapshot)
        if item["measurement_type"] != "cumulative" or item["observed_at"] is None:
            limitations.append(f"Observation {ref}: no dated cumulative comparison; "
                                "period or unknown coverage retained without subtraction.")
            continue
        for metric, value in item["metrics"].items():
            series.setdefault(metric, []).append({"ref": ref, "value": value,
                                                  "observed_at": item["observed_at"]})
    changes = []
    for metric, points in sorted(series.items()):
        for before, after in zip(points, points[1:]):
            if before["observed_at"] >= after["observed_at"] or after["value"] < before["value"]:
                limitations.append(f"{metric}: ambiguous dates or decreasing cumulative count; "
                                    f"comparison {before['ref']} to {after['ref']} excluded.")
                continue
            changes.append({"metric": metric, "from_observation": before["ref"],
                            "to_observation": after["ref"], "from_date": before["observed_at"],
                            "to_date": after["observed_at"], "from_value": before["value"],
                            "to_value": after["value"], "difference": after["value"] - before["value"],
                            "elapsed_days": (date.fromisoformat(after["observed_at"]) -
                                             date.fromisoformat(before["observed_at"])).days})
    if not changes:
        limitations.append("No metric has two adequate dated cumulative observations.")
    limitations.append("Missing metrics are unknown, never zero. Each metric uses its own capture "
                        "dates; cumulative coverage start is unknown. No cross-publication or "
                        "cross-platform subtraction is performed.")
    return {"platform": post["platform"], "post_id": post["post_id"],
            "published_at": post["published_at"], "observations": snapshots,
            "available_metrics": sorted({key for item in items for key in item["metrics"]}),
            "changes": changes, "status": "observed" if changes else "insufficient_data",
            "limitations": limitations}


def evaluate_recommendation(identifier: str, state_dir: Path) -> dict[str, object]:
    recommendation = read_recommendation(identifier, state_dir)
    if recommendation["status"] != "accepted":
        raise ValueError("Only accepted recommendations can be evaluated")
    if not recommendation["associated_posts"]:
        raise ValueError("Recommendation has no associated publications")
    results = []
    date_revisions = []
    for reference in sorted(recommendation["associated_posts"],
                            key=lambda item: (item["platform"], item["post_id"])):
        post = publication(state_dir, reference["platform"], reference["post_id"])
        results.append(_performance(post, observations(state_dir, post)))
        revision = post.get("date_reconciliation")
        if revision:
            date_revisions.append({"platform": post["platform"], "post_id": post["post_id"],
                                   "published_at": post["published_at"],
                                   "operation": revision["operation"]})
    identity = {"method": METHOD, "recommendation_id": identifier,
                "publications": [{"platform": item["platform"], "post_id": item["post_id"],
                                  "observations": sorted(obs["ref"] for obs in item["observations"])}
                                 for item in results]}
    # Preserve every existing ID until an explicitly reconciled publication is used.
    if date_revisions:
        identity["date_revisions"] = date_revisions
    evaluation_id = _digest(identity)
    path = _path(evaluation_id, state_dir)
    if path.exists():
        return read_evaluation(evaluation_id, state_dir)
    observed = sum(item["status"] == "observed" for item in results)
    changes = sum(len(item["changes"]) for item in results)
    record = {"id": evaluation_id, "method": METHOD, "recommendation_id": identifier,
              "evaluated_at": utc_now(), "publications": results,
              "status": "observed" if observed else "insufficient_data",
              "hypothesis_assessment": "insufficient_data", "limitations": [CAUTION],
              "explanation": f"{changes} comparable cumulative metric intervals across "
                             f"{observed}/{len(results)} associated publications. {CAUTION}"}
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".evaluation-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return record
