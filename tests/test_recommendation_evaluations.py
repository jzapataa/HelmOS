from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from helmos import recommendation_evaluations as evaluations
from helmos.recommendations import (associate_post, decide_recommendation,
                                    read_recommendation, register_run)
from helmos.social_import import import_csv


ROOT = Path(__file__).resolve().parent.parent
HEADER = "platform,post_id,published_at,format,topic,observed_at,measurement_type,period_start,period_end,likes,views\n"


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.state = self.base / "state"
        self.capture("2026-01-03", likes="10", views="100")
        run = {"id": str(uuid4()), "status": "succeeded", "input": {"operation": "social.analyze"},
               "result": {"dataset": {"posts": [{"ref": "P001", "platform": "TikTok", "post_id": "fictional"}]},
                          "editorial": {"recommendations": [{"action": "Probar otro tema.",
                                       "rationale": "Explorar la respuesta.", "post_refs": ["P001"]}]}}}
        self.identifier = register_run(run, self.state)[0]["id"]

    def capture(self, day, likes="", views="", kind="cumulative", start="", end="",
                platform="TikTok", post_id="fictional"):
        path = self.base / "input.csv"
        path.write_text(HEADER + f"{platform},{post_id},2026-01-01,video,fictional,{day},{kind},{start},{end},{likes},{views}\n",
                        encoding="utf-8")
        import_csv(path, self.state)

    def accept_and_link(self):
        decide_recommendation(self.identifier, "accepted", self.state)
        associate_post(self.identifier, "TikTok", "fictional", self.state)

    def evaluate(self):
        return evaluations.evaluate_recommendation(self.identifier, self.state)

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "helmos", "--state-dir", str(self.state),
                               "social", *args], cwd=ROOT,
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                              capture_output=True, text=True, check=False)

    def test_pending_and_rejected_are_refused_without_records(self):
        with self.assertRaisesRegex(ValueError, "Only accepted"):
            self.evaluate()
        decide_recommendation(self.identifier, "rejected", self.state)
        with self.assertRaisesRegex(ValueError, "Only accepted"):
            self.evaluate()
        self.assertEqual(evaluations.list_evaluations(self.identifier, self.state), [])

    def test_accepted_without_associations_is_refused(self):
        decide_recommendation(self.identifier, "accepted", self.state)
        with self.assertRaisesRegex(ValueError, "no associated"):
            self.evaluate()

    def test_sufficient_observations_references_and_no_mutations(self):
        self.accept_and_link()
        self.capture("2026-01-05", likes="15", views="180")
        before = {path: path.read_bytes() for path in self.state.rglob("*.json")}
        record = self.evaluate()
        self.assertEqual(record["status"], "observed")
        post = record["publications"][0]
        self.assertEqual(post["available_metrics"], ["likes", "views"])
        changes = {item["metric"]: item for item in post["changes"]}
        self.assertEqual(changes["likes"]["difference"], 5)
        self.assertEqual(changes["views"]["difference"], 80)
        self.assertEqual(changes["likes"]["elapsed_days"], 2)
        refs = {item["ref"] for item in post["observations"]}
        self.assertIn(changes["likes"]["from_observation"], refs)
        self.assertIn(changes["likes"]["to_observation"], refs)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertEqual(read_recommendation(self.identifier, self.state)["status"], "accepted")

    def test_single_snapshot_even_with_many_views_is_insufficient(self):
        self.accept_and_link()
        record = self.evaluate()
        self.assertEqual(record["status"], "insufficient_data")
        self.assertEqual(record["publications"][0]["changes"], [])

    def test_empty_metric_capture_does_not_invent_values(self):
        self.accept_and_link()
        self.capture("2026-01-05")
        record = self.evaluate()
        self.assertEqual(record["status"], "insufficient_data")
        self.assertEqual(record["publications"][0]["observations"][1]["metrics"], {})

    def test_period_and_unspecified_are_never_subtracted(self):
        self.accept_and_link()
        self.capture("2026-01-05", likes="15", kind="period", start="2026-01-02", end="2026-01-04")
        self.capture("2026-01-07", likes="20", kind="unspecified")
        record = self.evaluate()
        self.assertEqual(record["status"], "insufficient_data")
        snapshots = record["publications"][0]["observations"]
        self.assertEqual(snapshots[1]["period_start"], "2026-01-02")
        self.assertEqual(snapshots[1]["period_end"], "2026-01-04")
        self.assertEqual(len(snapshots), 3)

    def test_missing_metrics_have_independent_intervals_and_zero_is_real(self):
        self.accept_and_link()
        self.capture("2026-01-05", likes="10")
        self.capture("2026-01-09", views="120")
        changes = self.evaluate()["publications"][0]["changes"]
        self.assertEqual([(item["metric"], item["difference"], item["elapsed_days"])
                          for item in changes], [("likes", 0, 2), ("views", 20, 6)])
        self.assertEqual(len(changes), 2)

    def test_decrease_is_flagged_and_excluded(self):
        self.accept_and_link()
        self.capture("2026-01-05", likes="5")
        record = self.evaluate()
        self.assertEqual(record["status"], "insufficient_data")
        self.assertIn("decreasing", " ".join(record["publications"][0]["limitations"]))

    def test_identical_dates_are_not_comparable(self):
        self.accept_and_link()
        from helmos.social_history import observations, publication
        post = publication(self.state, "TikTok", "fictional")
        items = observations(self.state, post)
        with patch.object(evaluations, "observations", return_value=items + items):
            self.assertEqual(self.evaluate()["status"], "insufficient_data")

    def test_repeated_evaluations_preserve_history_and_idempotence(self):
        self.accept_and_link()
        first = self.evaluate()
        self.assertEqual(self.evaluate(), first)
        self.capture("2026-01-05", likes="15")
        second = self.evaluate()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.evaluate(), second)
        self.assertEqual(evaluations.read_evaluation(first["id"], self.state), first)
        self.assertEqual(len(evaluations.list_evaluations(self.identifier, self.state)), 2)

    def test_cli_evaluates_lists_and_recovers_across_processes(self):
        self.accept_and_link()
        self.capture("2026-01-05", likes="15")
        completed = self.cli("recommendations", "evaluate", self.identifier)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        record = json.loads(completed.stdout)
        recovered = self.cli("evaluations", "show", record["id"])
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(json.loads(recovered.stdout), record)
        repeated = self.cli("recommendations", "evaluate", self.identifier)
        self.assertEqual(json.loads(repeated.stdout), record)
        listing = self.cli("recommendations", "evaluations", self.identifier)
        self.assertEqual(json.loads(listing.stdout), [record])

    def test_new_association_creates_new_record_and_reimport_does_not(self):
        self.accept_and_link()
        first = self.evaluate()
        self.capture("2026-01-03", likes="10", views="100")
        self.assertEqual(self.evaluate(), first)
        self.capture("2026-01-03", likes="50", platform="Instagram")
        self.assertEqual(self.evaluate(), first)
        associate_post(self.identifier, "Instagram", "fictional", self.state)
        second = self.evaluate()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(second["publications"]), 2)
        self.assertEqual(evaluations.read_evaluation(first["id"], self.state), first)
        self.assertEqual(len(evaluations.list_evaluations(self.identifier, self.state)), 2)

    def test_legacy_undated_data_is_retained_without_fabricating_coverage(self):
        self.accept_and_link()
        path = next((self.state / "social" / "posts").glob("*.json"))
        legacy = json.loads(path.read_text(encoding="utf-8"))
        del legacy["initial_observation"]
        path.write_text(json.dumps(legacy), encoding="utf-8")
        self.capture("2026-01-09", likes="20")
        record = self.evaluate()
        self.assertEqual(record["status"], "insufficient_data")
        old = record["publications"][0]["observations"][0]
        self.assertIsNone(old["observed_at"])
        self.assertEqual(old["measurement_type"], "unspecified")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), legacy)

    def test_historical_observations_are_ordered_by_capture_not_import(self):
        self.accept_and_link()
        self.capture("2026-01-09", likes="30")
        self.capture("2026-01-05", likes="20")
        changes = self.evaluate()["publications"][0]["changes"]
        self.assertEqual([item["difference"] for item in changes], [10, 10])
        self.assertEqual([item["to_date"] for item in changes], ["2026-01-05", "2026-01-09"])

    def test_multiple_platforms_are_separate_with_partial_coverage(self):
        self.accept_and_link()
        self.capture("2026-01-05", likes="15")
        for platform in ("Instagram", "LinkedIn"):
            self.capture("2026-01-03", likes="10000", platform=platform)
            associate_post(self.identifier, platform, "fictional", self.state)
        record = self.evaluate()
        self.assertEqual(record["status"], "observed")
        self.assertEqual([item["status"] for item in record["publications"]],
                         ["insufficient_data", "insufficient_data", "observed"])
        self.assertEqual(sum(len(item["changes"]) for item in record["publications"]), 1)

    def test_high_views_never_validate_hypothesis_or_invoke_ai(self):
        self.accept_and_link()
        self.capture("2026-01-05", views="1000000")
        with patch("helmos.social_analyze.run_structured", side_effect=AssertionError("Unexpected AI")):
            record = self.evaluate()
        self.assertEqual(record["status"], "observed")
        self.assertEqual(record["hypothesis_assessment"], "insufficient_data")
        self.assertIn("does not establish causality", record["explanation"])
        self.assertNotIn("success", {key for key in record})

    def test_missing_publication_fails_without_evaluation(self):
        self.accept_and_link()
        with patch.object(evaluations, "publication", side_effect=ValueError("Publication not found")):
            with self.assertRaisesRegex(ValueError, "Publication not found"):
                self.evaluate()
        self.assertEqual(evaluations.list_evaluations(self.identifier, self.state), [])

    def test_failed_atomic_write_leaves_no_partial_evaluation(self):
        self.accept_and_link()
        with patch.object(evaluations.os, "replace", side_effect=OSError("disk failure")):
            with self.assertRaisesRegex(OSError, "disk failure"):
                self.evaluate()
        self.assertEqual(evaluations.list_evaluations(self.identifier, self.state), [])
        self.assertEqual(list((self.state / "social" / "evaluations").iterdir()), [])
        self.assertEqual(self.evaluate()["status"], "insufficient_data")

    def test_invalid_ids_and_corrupt_json_are_controlled_cli_errors(self):
        self.assertEqual(self.cli("evaluations", "show", "../bad").returncode, 2)
        self.accept_and_link()
        record = self.evaluate()
        path = self.state / "social" / "evaluations" / f"{record['id']}.json"
        for text in ("{bad", "[]", '{"id": "wrong"}'):
            path.write_text(text, encoding="utf-8")
            self.assertEqual(self.cli("evaluations", "show", record["id"]).returncode, 2)


if __name__ == "__main__":
    unittest.main()
