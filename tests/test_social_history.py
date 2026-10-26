from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helmos import core, social_analyze
from helmos.social_import import import_csv


ROOT = Path(__file__).resolve().parent.parent
BASE = "platform,post_id,published_at,format,topic"
DATED = BASE + ",observed_at,measurement_type,period_start,period_end,likes,views"


class SocialHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.state = self.base / "state"

    def import_text(self, text: str) -> None:
        path = self.base / f"input-{len(list(self.base.glob('*.csv')))}.csv"
        path.write_text(text, encoding="utf-8")
        import_csv(path, self.state)

    def cli(self, *args: str) -> dict[str, object]:
        completed = subprocess.run(
            [sys.executable, "-m", "helmos", "--state-dir", str(self.state), "social", *args],
            cwd=ROOT, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def populate(self) -> None:
        self.import_text(DATED + "\nTikTok,a,2026-01-01,video,fictional,2026-01-03,cumulative,,,10,100\n")
        self.import_text(DATED + "\nTikTok,a,2026-01-01,video,fictional,2026-01-05,cumulative,,,15,\n")
        self.import_text(DATED + "\nTikTok,a,2026-01-01,video,fictional,2026-01-07,period,2026-01-04,2026-01-06,3,7\n")
        self.import_text(DATED + "\nTikTok,b,2026-01-02,video,fictional,2026-01-05,cumulative,,,5,\n")
        self.import_text(BASE + ",likes\nTikTok,legacy,2026-01-01,video,fictional,2\n")

    def test_observations_latest_and_comparable_evolution(self) -> None:
        self.populate()
        history = self.cli("observations", "--platform", "TikTok", "--post-id", "a")
        self.assertEqual(len(history["observations"]), 3)
        current = self.cli("latest", "--platform", "TikTok", "--post-id", "a")
        groups = {item["measurement_type"]: item for item in current["measurements"]}
        self.assertEqual(groups["cumulative"]["metrics"]["likes"],
                         {"value": 15, "observed_at": "2026-01-05"})
        self.assertEqual(groups["cumulative"]["metrics"]["views"],
                         {"value": 100, "observed_at": "2026-01-03"})
        self.assertEqual(groups["period"]["period_start"], "2026-01-04")
        evolution = self.cli("evolution", "--platform", "TikTok", "--post-id", "a")
        self.assertEqual([point["change"] for point in evolution["metrics"]["likes"]],
                         [None, 5])
        self.assertEqual(len(evolution["metrics"]["views"]), 1)
        self.assertEqual(self.cli("evolution", "--platform", "TikTok", "--post-id", "legacy")["metrics"], {})

    def test_summary_separates_measurement_types_and_counts_each_post_once(self) -> None:
        self.populate()
        group = self.cli("summary", "--platform", "TikTok")["TikTok"]
        self.assertEqual(group["post_count"], 3)
        self.assertEqual(group["metric_basis"], "cumulative")
        self.assertEqual(group["metrics"]["likes"], {"sum": 20, "observed_posts": 2})
        self.assertEqual(group["metrics"]["views"], {"sum": 100, "observed_posts": 1})
        buckets = {item["measurement_type"]: item for item in group["measurement_groups"]}
        self.assertEqual(buckets["period"]["metrics"]["likes"]["sum"], 3)
        self.assertEqual(buckets["unspecified"]["metrics"]["likes"]["sum"], 2)
        self.assertEqual(buckets["unspecified"]["metrics"]["likes"]["undated_posts"], 1)
        legacy = self.cli("latest", "--platform", "TikTok", "--post-id", "legacy")
        self.assertIsNone(legacy["measurements"][0]["metrics"]["likes"]["observed_at"])
        self.assertTrue(legacy["limitations"])

    def test_undated_unknown_measurement_is_not_ordered_against_dated_unknown(self) -> None:
        self.import_text(BASE + ",likes\nTikTok,a,2026-01-01,video,fictional,2\n")
        self.import_text(DATED + "\nTikTok,a,2026-01-01,video,fictional,2026-01-05,unspecified,,,4,\n")
        latest = self.cli("latest", "--platform", "TikTok", "--post-id", "a")
        self.assertEqual(len(latest["measurements"]), 2)
        values = {group["capture_status"]: group["metrics"]["likes"]["value"]
                  for group in latest["measurements"]}
        self.assertEqual(values, {"dated": 4, "undated": 2})
        summary = self.cli("summary")["TikTok"]
        self.assertEqual(summary["metrics"]["likes"]["sum"], 4)
        self.assertEqual(len(summary["measurement_groups"]), 2)

    def test_single_period_window_can_be_summarized_without_mixing_windows(self) -> None:
        self.import_text(DATED + "\nTikTok,a,2026-01-01,video,fictional,2026-01-08,period,2026-01-02,2026-01-07,3,\n"
                         "TikTok,b,2026-01-01,video,fictional,2026-01-08,period,2026-01-02,2026-01-07,4,\n")
        one = self.cli("summary")["TikTok"]
        self.assertEqual(one["metric_basis"], "period")
        self.assertEqual(one["period_start"], "2026-01-02")
        self.assertEqual(one["metrics"]["likes"], {"sum": 7, "observed_posts": 2})
        dataset = social_analyze.prepare_dataset(self.state, "TikTok")
        self.assertEqual(dataset["coverage"]["TikTok"]["metric_basis"], "period")
        self.assertEqual(dataset["coverage"]["TikTok"]["metrics"]["likes"]["sum"], 7)
        evidence = next(item for item in dataset["evidence"].values() if item["metric"] == "likes")
        self.assertEqual((evidence["measurement_type"], evidence["period_start"], evidence["period_end"]),
                         ("period", "2026-01-02", "2026-01-07"))
        self.import_text(DATED + "\nTikTok,c,2026-01-01,video,fictional,2026-01-09,period,2026-01-03,2026-01-08,5,\n")
        mixed = self.cli("summary")["TikTok"]
        self.assertIsNone(mixed["metric_basis"])
        self.assertEqual(mixed["metrics"], {})
        self.assertEqual(len(mixed["measurement_groups"]), 2)
        self.assertIsNone(social_analyze.prepare_dataset(self.state, "TikTok")
                          ["coverage"]["TikTok"]["metrics"]["likes"]["sum"])

    def test_editorial_dataset_and_report_use_selected_temporal_evidence(self) -> None:
        self.populate()
        dataset = social_analyze.prepare_dataset(self.state, "TikTok")
        self.assertEqual(dataset["coverage"]["TikTok"]["metrics"]["likes"]["sum"], 20)
        self.assertEqual(dataset["posts"][0]["metrics"]["likes"], 15)
        self.assertTrue(dataset["temporal_limitations"])
        identifier = next(key for key, item in dataset["evidence"].items()
                          if item["metric"] == "likes")
        answer = {
            "findings": [{"claim": "Hay datos de interacción.", "kind": "observation",
                          "post_refs": ["P001"], "metric_evidence": [identifier]}],
            "limitations": ["La muestra es pequeña."],
            "recommendations": [{"action": "Revisar el tema.", "rationale": "Explorar la respuesta.",
                                 "post_refs": ["P001"]}],
            "calendar": [{"slot": "Semana inicial", "platform": "TikTok", "topic": "Prueba",
                          "format": "video", "rationale": "Explorar el tema.", "post_refs": ["P001"]}],
        }
        outcome = {"result": answer, "consumption": {"tokens": 0}, "thread_id": "mock"}
        with patch.object(social_analyze, "run_structured", return_value=outcome):
            run = core.execute("social.analyze", self.state, platform="TikTok")
        self.assertEqual(run["status"], "succeeded", run["error"])
        report = Path(run["result"]["report_path"]).read_text(encoding="utf-8")
        self.assertIn("2026-01-05", report)
        self.assertIn("ventana 2026-01-04 a 2026-01-06", report)
        self.assertIn("período", report)
        self.assertEqual(run["result"]["editorial"]["findings"][0]["metric_evidence"][0]["sum"], 20)
        answer["findings"][0]["metric_evidence"][0] = "E999"
        with patch.object(social_analyze, "run_structured", return_value=outcome):
            rejected = core.execute("social.analyze", self.state, platform="TikTok")
        self.assertEqual(rejected["status"], "failed")
        self.assertIn("unknown or unavailable metric evidence", rejected["error"])


if __name__ == "__main__":
    unittest.main()
