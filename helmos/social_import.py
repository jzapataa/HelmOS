"""Import canonical publication CSVs into local, per-publication JSON records."""

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


class SocialImportError(ValueError):
    """Invalid CSV or conflict with an already saved publication."""


def _post_path(state_dir: Path, platform: str, post_id: str) -> Path:
    digest = hashlib.sha256(f"{platform}\0{post_id}".encode("utf-8")).hexdigest()
    return state_dir / "social" / "posts" / f"{digest}.json"


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
        raise SocialImportError(f"Publication already exists: {record['platform']}/{record['post_id']}")
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
                or any(header not in REQUIRED + METRICS for header in headers)
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
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", published_at):
                    raise SocialImportError(f"published_at must be YYYY-MM-DD at row {line}")
                try:
                    date.fromisoformat(published_at)
                except ValueError as error:
                    raise SocialImportError(f"Invalid published_at at row {line}") from error
                metrics: dict[str, int] = {}
                for name in headers[len(REQUIRED):]:
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
                posts.append({
                    "platform": platform, "post_id": post_id, "published_at": published_at,
                    "format": raw["format"], "topic": raw["topic"],
                    "metrics": metrics, "source_row": raw,
                })
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
    directory = state_dir / "social" / "posts"
    if directory.is_symlink() or directory.parent.is_symlink():
        raise SocialImportError("Publication directory cannot be a symbolic link")
    existing = []
    for post in posts:
        saved_path = _post_path(state_dir, post["platform"], post["post_id"])
        if saved_path.exists() or saved_path.is_symlink():
            saved = _read_post(saved_path)
            if saved != post:
                raise SocialImportError(
                    f"Existing publication differs: {post['platform']}/{post['post_id']}"
                )
            existing.append(post)
    for post in posts:
        if post not in existing:
            _save_post(_post_path(state_dir, post["platform"], post["post_id"]), post)
    return {
        "source_csv": str(path), "rows": len(posts),
        "imported": len(posts) - len(existing), "already_present": len(existing),
        "platforms": summarize(posts),
    }
