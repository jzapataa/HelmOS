"""Import publication CSVs and preserve dated observations as local JSON."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path


REQUIRED = ("platform", "post_id", "published_at", "format", "topic")
COMMON_METRICS = ("impressions", "reach", "views", "likes", "comments", "shares", "saves", "clicks")
SPECIFIC_METRICS = {
    "Instagram": ("instagram_profile_visits",),
    "TikTok": ("tiktok_favorites",),
    "LinkedIn": ("linkedin_reactions", "linkedin_reposts"),
}
METRICS = COMMON_METRICS + tuple(
    metric for columns in SPECIFIC_METRICS.values() for metric in columns
)
PLATFORMS = frozenset(SPECIFIC_METRICS)
TEMPORAL = ("observed_at", "measurement_type", "period_start", "period_end")
MEASUREMENT_TYPES = frozenset(("unspecified", "cumulative", "period"))


class SocialImportError(ValueError):
    """Invalid CSV or conflict with an already saved publication."""


def _post_path(state_dir: Path, platform: str, post_id: str) -> Path:
    digest = hashlib.sha256(f"{platform}\0{post_id}".encode("utf-8")).hexdigest()
    return state_dir / "social" / "posts" / f"{digest}.json"


def _observation_dir(state_dir: Path, platform: str, post_id: str) -> Path:
    return state_dir / "social" / "observations" / _post_path(state_dir, platform, post_id).stem


def _day(value: str, field: str, line: int) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise SocialImportError(f"{field} must be YYYY-MM-DD at row {line}")
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise SocialImportError(f"Invalid {field} at row {line}") from error
    return value


def _observation(raw: dict[str, str], metrics: dict[str, int],
                 source_csv: str, line: int) -> dict[str, object]:
    observed_at = raw.get("observed_at", "")
    kind = raw.get("measurement_type", "") or "unspecified"
    start, end = raw.get("period_start", ""), raw.get("period_end", "")
    if kind not in MEASUREMENT_TYPES:
        raise SocialImportError(f"Invalid measurement_type at row {line}")
    if observed_at:
        _day(observed_at, "observed_at", line)
        if observed_at < raw["published_at"]:
            raise SocialImportError(f"observed_at precedes publication at row {line}")
    elif kind != "unspecified" or start or end:
        raise SocialImportError(f"observed_at is required for dated measurements at row {line}")
    if kind == "period":
        if not start or not end:
            raise SocialImportError(f"period_start and period_end are required at row {line}")
        _day(start, "period_start", line)
        _day(end, "period_end", line)
        if not raw["published_at"] <= start <= end <= observed_at:
            raise SocialImportError(f"Invalid measurement period at row {line}")
    elif start or end:
        raise SocialImportError(f"Period dates require measurement_type=period at row {line}")
    return {
        "observed_at": observed_at or None, "measurement_type": kind,
        "period_start": start or None, "period_end": end or None,
        "metrics": metrics, "source_csv": source_csv, "source_row": raw,
    }


def _same_measurement(left: dict[str, object], right: dict[str, object]) -> bool:
    return all(left[key] == right[key] for key in
               ("observed_at", "measurement_type", "period_start", "period_end", "metrics"))


def _api_capture(observation: dict) -> str | None:
    origin = observation.get("provenance", {})
    if origin.get("source") == "official_api":
        return origin.get("captured_at")
    return None


def observation_sort_key(item: dict) -> tuple:
    return (item["observed_at"] is not None, item["observed_at"] or "",
            _api_capture(item) or "")


def _legacy_observation(post: dict[str, object]) -> dict[str, object]:
    return {
        "observed_at": None, "measurement_type": "unspecified",
        "period_start": None, "period_end": None, "metrics": post["metrics"],
        "source_csv": None, "source_row": post["source_row"],
    }


def list_observations(state_dir: Path, post: dict[str, object]) -> list[dict[str, object]]:
    """Read observations without changing legacy publication files."""
    directory = _observation_dir(state_dir, post["platform"], post["post_id"])
    if directory.is_symlink() or directory.parent.is_symlink():
        raise SocialImportError("Observation directory cannot be a symbolic link")
    initial = post.get("initial_observation") or _legacy_observation(post)
    if not directory.exists():
        return [initial]
    observations = [initial]
    for path in sorted(directory.glob("*.json")):
        if path.is_symlink():
            raise SocialImportError("Observation path cannot be a symbolic link")
        try:
            observation = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise SocialImportError(f"Invalid saved observation: {path.name}") from error
        if not isinstance(observation, dict) or not all(key in observation for key in
                ("observed_at", "measurement_type", "period_start", "period_end",
                 "metrics", "source_csv", "source_row")):
            raise SocialImportError(f"Invalid saved observation: {path.name}")
        observations.append(observation)
    return observations


def _read_post(path: Path) -> dict[str, object]:
    if path.is_symlink():
        raise SocialImportError(f"Publication path is a symbolic link: {path.name}")
    try:
        with path.open("r", encoding="utf-8") as stream:
            record = json.load(stream)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise SocialImportError(f"Invalid saved publication: {path.name}") from error
    if not isinstance(record, dict) or not all(key in record for key in ("platform", "post_id", "metrics", "source_row")):
        raise SocialImportError(f"Invalid saved publication: {path.name}")
    return record


def _save_post(path: Path, record: dict[str, object]) -> None:
    if path.parent.is_symlink() or path.parent.parent.is_symlink():
        raise SocialImportError("Publication directory cannot be a symbolic link")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.parent.parent.is_symlink() or path.is_symlink():
        raise SocialImportError("Publication path cannot be a symbolic link")
    if path.exists():
        raise SocialImportError(f"Saved JSON already exists: {path.name}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".post-", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _parse_csv(path: Path) -> list[dict[str, object]]:
    posts: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            headers = reader.fieldnames
            if (
                headers is None or len(headers) != len(set(headers))
                or headers[:len(REQUIRED)] != list(REQUIRED)
                or any(header not in REQUIRED + TEMPORAL + METRICS for header in headers)
                or not any(header in METRICS for header in headers)
            ):
                raise SocialImportError(
                    "CSV requires platform,post_id,published_at,format,topic followed by known metric columns"
                )
            for line, raw in enumerate(reader, start=2):
                if None in raw or any(value is None for value in raw.values()):
                    raise SocialImportError(f"Malformed CSV row {line}")
                platform = raw["platform"]
                post_id = raw["post_id"]
                if platform not in PLATFORMS:
                    raise SocialImportError(f"Unsupported platform at row {line}: {platform}")
                for field in REQUIRED[1:]:
                    value = raw[field]
                    if not value or value != value.strip() or len(value) > 200:
                        raise SocialImportError(f"Invalid {field} at row {line}")
                if (platform, post_id) in seen:
                    raise SocialImportError(f"Duplicate publication at row {line}: {platform}/{post_id}")
                seen.add((platform, post_id))
                published_at = raw["published_at"]
                _day(published_at, "published_at", line)
                metrics: dict[str, int] = {}
                for name in headers[len(REQUIRED):]:
                    if name in TEMPORAL:
                        continue
                    value = raw[name]
                    if value == "":
                        continue
                    if name in SPECIFIC_METRICS.get(platform, ()) or name in COMMON_METRICS:
                        if not value.isascii() or not value.isdecimal():
                            raise SocialImportError(f"{name} must be a non-negative integer at row {line}")
                        try:
                            metrics[name] = int(value)
                        except ValueError as error:
                            raise SocialImportError(f"{name} is too large at row {line}") from error
                    else:
                        raise SocialImportError(f"{name} does not apply to {platform} at row {line}")
                post = {
                    "platform": platform, "post_id": post_id, "published_at": published_at,
                    "format": raw["format"], "topic": raw["topic"],
                    "metrics": metrics, "source_row": raw,
                }
                post["initial_observation"] = _observation(raw, metrics, str(path), line)
                posts.append(post)
    except (UnicodeError, csv.Error) as error:
        raise SocialImportError(f"Invalid CSV: {error}") from error
    if not posts:
        raise SocialImportError("CSV must contain at least one publication")
    return posts


def list_posts(state_dir: Path, platform: str | None = None) -> list[dict[str, object]]:
    if platform is not None and platform not in PLATFORMS:
        raise SocialImportError(f"Unsupported platform: {platform}")
    directory = state_dir / "social" / "posts"
    if directory.is_symlink() or directory.parent.is_symlink():
        raise SocialImportError("Publication directory cannot be a symbolic link")
    if not directory.exists():
        return []
    posts = [_read_post(path) for path in directory.glob("*.json")]
    return sorted(
        (post for post in posts if platform is None or post["platform"] == platform),
        key=lambda post: (post["platform"], post["published_at"], post["post_id"]),
    )


def summarize(posts: list[dict[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for platform in sorted(PLATFORMS):
        selected = [post for post in posts if post["platform"] == platform]
        if not selected:
            continue
        metrics: dict[str, dict[str, int]] = {}
        for name in COMMON_METRICS + SPECIFIC_METRICS[platform]:
            values = [post["metrics"][name] for post in selected if name in post["metrics"]]
            if values:
                metrics[name] = {"sum": sum(values), "observed_posts": len(values)}
        result[platform] = {"post_count": len(selected), "metrics": metrics}
    return result


def import_csv(path: Path, state_dir: Path) -> dict[str, object]:
    posts = _parse_csv(path)
    return _persist_posts(posts, state_dir, str(path))


def _persist_posts(posts: list[dict[str, object]], state_dir: Path,
                   source_csv: str | None = None, *, precise_captures: bool = False,
                   dry_run: bool = False) -> dict[str, object]:
    """Shared single-writer persistence for validated CSV and connector records.

    Validate the complete batch before calling. Preflight conflicts before writes;
    filesystem failures can leave a partial batch, recoverable by idempotent replay.
    """
    directory = state_dir / "social" / "posts"
    if directory.is_symlink() or directory.parent.is_symlink():
        raise SocialImportError("Publication directory cannot be a symbolic link")
    already_present = 0
    new_posts: list[dict[str, object]] = []
    additions: list[tuple[Path, dict[str, object]]] = []
    for post in posts:
        saved_path = _post_path(state_dir, post["platform"], post["post_id"])
        if saved_path.exists() or saved_path.is_symlink():
            saved = _read_post(saved_path)
            if any(saved[key] != post[key] for key in ("platform", "post_id", "published_at", "format", "topic")):
                raise SocialImportError(
                    f"Existing publication metadata differs: {post['platform']}/{post['post_id']}"
                )
            observation = post["initial_observation"]
            current = list_observations(state_dir, saved)
            incoming = observation.get("provenance")
            if incoming:
                for item in current:
                    origin = item.get("provenance")
                    if origin and (origin.get("account_id") != incoming.get("account_id") or (
                            origin.get("editorial_piece_id")
                            and origin["editorial_piece_id"] != incoming["editorial_piece_id"])):
                        raise SocialImportError("Connector account or editorial identity differs")
            comparable = current
            if precise_captures:
                same_capture = [item for item in current if _api_capture(item) and
                                _api_capture(item) == _api_capture(observation)]
                if same_capture and not all(_same_measurement(item, observation) for item in same_capture):
                    raise SocialImportError("Conflicting metrics for the same API capture")
                comparable = same_capture or [max(current, key=observation_sort_key)]
            if any(_same_measurement(item, observation) for item in comparable):
                already_present += 1
                continue
            same_day = [item for item in current if item["observed_at"] == observation["observed_at"]]
            precise = precise_captures and _api_capture(observation) and all(
                _api_capture(item) and _api_capture(item) != _api_capture(observation)
                for item in same_day)
            if observation["observed_at"] is None or (same_day and not precise):
                raise SocialImportError(
                    f"Ambiguous observation date or period: {post['platform']}/{post['post_id']}"
                )
            directory = _observation_dir(state_dir, post["platform"], post["post_id"])
            digest = hashlib.sha256(json.dumps(
                {**{key: observation[key] for key in ("observed_at", "measurement_type", "period_start", "period_end", "metrics")},
                 **({"captured_at": _api_capture(observation)} if precise_captures else {})},
                sort_keys=True, ensure_ascii=False,
            ).encode("utf-8")).hexdigest()
            additions.append((directory / f"{digest}.json", observation))
        else:
            new_posts.append(post)
    if not dry_run:
        for post in new_posts:
            _save_post(_post_path(state_dir, post["platform"], post["post_id"]), post)
        for observation_path, observation in additions:
            _save_post(observation_path, observation)
    return {
        "source_csv": source_csv, "rows": len(posts),
        "imported": len(new_posts),
        "observations_added": len(additions), "already_present": already_present,
        "platforms": summarize(posts),
    }
