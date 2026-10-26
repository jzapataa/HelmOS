"""Read-only temporal views of imported Social observations."""

from __future__ import annotations

from pathlib import Path

from .social_import import (COMMON_METRICS, SPECIFIC_METRICS, SocialImportError,
                            list_observations, list_posts, observation_sort_key)


def publication(state_dir: Path, platform: str, post_id: str) -> dict[str, object]:
    for post in list_posts(state_dir, platform):
        if post["post_id"] == post_id:
            return post
    raise SocialImportError(f"Publication not found: {platform}/{post_id}")


def observations(state_dir: Path, post: dict[str, object]) -> list[dict[str, object]]:
    return sorted(list_observations(state_dir, post), key=observation_sort_key)


def _group_key(item: dict[str, object]) -> tuple[str, str | None, str | None, str]:
    capture_status = "dated" if item["observed_at"] is not None else "undated"
    if item["measurement_type"] == "period":
        return "period", item["period_start"], item["period_end"], capture_status
    return item["measurement_type"], None, None, capture_status


def latest_groups(items: list[dict[str, object]]) -> list[dict[str, object]]:
    """Keep the last known value of each metric within each comparable group."""
    groups: dict[tuple[str, str | None, str | None, str], dict[str, object]] = {}
    for item in sorted(items, key=observation_sort_key):
        kind, start, end, capture_status = _group_key(item)
        group = groups.setdefault((kind, start, end, capture_status), {
            "measurement_type": kind, "period_start": start, "period_end": end,
            "capture_status": capture_status, "metrics": {},
        })
        for name, value in item["metrics"].items():
            group["metrics"][name] = {"value": value, "observed_at": item["observed_at"]}
    return sorted(groups.values(), key=lambda group: (
        group["measurement_type"], group["period_start"] or "", group["period_end"] or ""))


def latest(state_dir: Path, post: dict[str, object]) -> dict[str, object]:
    items = observations(state_dir, post)
    limitations = []
    if any(item["observed_at"] is None for item in items):
        limitations.append("Some observations have no capture date; their recency is unknown.")
    if any(item["measurement_type"] == "unspecified" for item in items):
        limitations.append("Unspecified measurements have an unknown coverage period.")
    return {"platform": post["platform"], "post_id": post["post_id"],
            "measurements": latest_groups(items), "limitations": limitations}


def evolution(state_dir: Path, post: dict[str, object]) -> dict[str, object]:
    series: dict[str, list[dict[str, object]]] = {}
    for item in observations(state_dir, post):
        if item["measurement_type"] != "cumulative" or item["observed_at"] is None:
            continue
        for name, value in item["metrics"].items():
            points = series.setdefault(name, [])
            previous = points[-1] if points else None
            points.append({"observed_at": item["observed_at"], "value": value,
                           "change": value - previous["value"] if previous else None})
    return {"platform": post["platform"], "post_id": post["post_id"], "metrics": series,
            "limitations": ["Only dated cumulative measurements of the same publication and metric are compared; missing values, period measurements and unspecified measurements are excluded."]}


def summarize_history(state_dir: Path, posts: list[dict[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for platform in sorted(SPECIFIC_METRICS):
        selected = [post for post in posts if post["platform"] == platform]
        if not selected:
            continue
        buckets: dict[tuple[str, str | None, str | None, str], dict[str, object]] = {}
        for post in selected:
            for group in latest_groups(observations(state_dir, post)):
                key = (group["measurement_type"], group["period_start"],
                       group["period_end"], group["capture_status"])
                bucket = buckets.setdefault(key, {
                    "measurement_type": key[0], "period_start": key[1], "period_end": key[2],
                    "capture_status": key[3], "metrics": {},
                })
                for name, datum in group["metrics"].items():
                    metric = bucket["metrics"].setdefault(name, {"sum": 0, "observed_posts": 0,
                                                                 "undated_posts": 0,
                                                                 "oldest_capture": None,
                                                                 "newest_capture": None})
                    metric["sum"] += datum["value"]
                    metric["observed_posts"] += 1
                    captured = datum["observed_at"]
                    if captured is None:
                        metric["undated_posts"] += 1
                    else:
                        if metric["oldest_capture"] is None or captured < metric["oldest_capture"]:
                            metric["oldest_capture"] = captured
                        if metric["newest_capture"] is None or captured > metric["newest_capture"]:
                            metric["newest_capture"] = captured
        groups = sorted(buckets.values(), key=lambda group: (
            group["measurement_type"], group["period_start"] or "", group["period_end"] or ""))
        cumulative = next((group for group in groups if group["measurement_type"] == "cumulative"), None)
        periods = [group for group in groups if group["measurement_type"] == "period"]
        unspecified = next((group for group in groups if group["measurement_type"] == "unspecified"
                            and group["capture_status"] == "dated"), None)
        if unspecified is None:
            unspecified = next((group for group in groups if group["measurement_type"] == "unspecified"), None)
        primary = cumulative or (periods[0] if len(periods) == 1 else None) or unspecified
        # Preserve the I3-A metric shape for legacy-only datasets.
        metrics = {name: {"sum": primary["metrics"][name]["sum"],
                          "observed_posts": primary["metrics"][name]["observed_posts"]}
                   for name in COMMON_METRICS + SPECIFIC_METRICS[platform]
                   if primary and name in primary["metrics"]}
        result[platform] = {"post_count": len(selected), "metric_basis":
                            primary["measurement_type"] if primary else None,
                            "capture_status": primary["capture_status"] if primary else None,
                            "period_start": primary["period_start"] if primary else None,
                            "period_end": primary["period_end"] if primary else None,
                            "metrics": metrics, "measurement_groups": groups}
    return result
