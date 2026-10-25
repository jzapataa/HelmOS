"""Deterministic calculations for a small publication metrics sample."""

from __future__ import annotations

import csv
from pathlib import Path


SKILL_NAME = "analyze-social-sample"
SKILL_PATH = ".agents/skills/analyze-social-sample/SKILL.md"
COLUMNS = ("post_id", "platform", "impressions", "likes", "comments", "shares")
COUNT_COLUMNS = ("impressions", "likes", "comments", "shares")
PLATFORMS = {"Instagram", "TikTok", "LinkedIn"}
MAX_POSTS = 20


class SampleValidationError(ValueError):
    """The supplied sample does not meet the documented CSV contract."""


def analyze_csv(path: Path) -> dict[str, object]:
    """Validate source rows and return only arithmetic supported by those rows."""
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            if reader.fieldnames != list(COLUMNS):
                raise SampleValidationError(
                    "CSV columns must be: " + ",".join(COLUMNS)
                )
            posts: list[dict[str, str | int]] = []
            seen_ids: set[str] = set()
            for line_number, raw in enumerate(reader, start=2):
                if len(posts) >= MAX_POSTS:
                    raise SampleValidationError(
                        f"CSV sample cannot exceed {MAX_POSTS} publications"
                    )
                if None in raw or any(value is None for value in raw.values()):
                    raise SampleValidationError(f"Malformed CSV row {line_number}")
                post_id = raw["post_id"].strip()
                platform = raw["platform"].strip()
                if not post_id or post_id in seen_ids:
                    raise SampleValidationError(
                        f"Missing or duplicate post_id at row {line_number}"
                    )
                if platform not in PLATFORMS:
                    raise SampleValidationError(
                        f"Unsupported platform at row {line_number}: {platform}"
                    )
                counts: dict[str, int] = {}
                for column in COUNT_COLUMNS:
                    value = raw[column]
                    if not value.isascii() or not value.isdecimal():
                        raise SampleValidationError(
                            f"{column} must be a non-negative integer at row {line_number}"
                        )
                    try:
                        counts[column] = int(value)
                    except ValueError as error:
                        raise SampleValidationError(
                            f"{column} is too large at row {line_number}"
                        ) from error
                seen_ids.add(post_id)
                posts.append({"post_id": post_id, "platform": platform, **counts})
    except (UnicodeError, csv.Error) as error:
        raise SampleValidationError(f"Invalid CSV: {error}") from error

    if not posts:
        raise SampleValidationError("CSV must contain at least one publication")

    totals = {column: sum(int(post[column]) for post in posts) for column in COUNT_COLUMNS}
    per_post = [
        {
            "post_id": post["post_id"],
            "interactions": sum(int(post[column]) for column in ("likes", "comments", "shares")),
        }
        for post in posts
    ]
    totals["interactions"] = sum(item["interactions"] for item in per_post)
    return {
        "skill": {"name": SKILL_NAME, "path": SKILL_PATH},
        "data": posts,
        "results": {"post_count": len(posts), "totals": totals, "per_post": per_post},
        "warnings": [
            "La muestra es pequeña; no permite inferir tendencias ni causalidad.",
            "No hay periodo ni definiciones de plataforma para comparar rendimiento relativo.",
        ],
    }
