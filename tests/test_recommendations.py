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
from helmos.recommendations import (associate_post, associated_posts, decide_recommendation,
                                    list_recommendations, read_recommendation, register_run)
from helmos.social_import import import_csv


ROOT = Path(__file__).resolve().parent.parent


class RecommendationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "state"
        import_csv(ROOT / "examples" / "social_instagram_template.csv", self.state)

    def run_analysis(self, recommendations=None):
        answer = {
            "findings": [{"claim": "La muestra es limitada.", "kind": "observation",
                          "post_refs": ["P001"], "metric_evidence": []}],
            "limitations": ["La muestra es pequeña."],
            "recommendations": recommendations or [
                {"action": "Probar otro tema.", "rationale": "Explorar la respuesta.",
                 "post_refs": ["P001"]}],
            "calendar": [{"slot": "Próxima semana", "platform": "Instagram",
                          "topic": "Tema ficticio", "format": "reel",
                          "rationale": "Explorar una hipótesis.", "post_refs": ["P001"]}],
        }
        outcome = {"result": answer, "consumption": {"tokens": 1, "cost": None,
                                                  "measurement": "test"}, "thread_id": "mock"}
        with patch.object(social_analyze, "run_structured", return_value=outcome):
            return core.execute("social.analyze", self.state)

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "helmos", "--state-dir", str(self.state),
                               "social", "recommendations", *args], cwd=ROOT,
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                              capture_output=True, text=True, check=False)

    def test_registration_and_idempotent_retry_preserve_decision(self):
        run = self.run_analysis()
        self.assertEqual(run["status"], "succeeded")
        identifier = f"{run['id']}:1"
        record = read_recommendation(identifier, self.state)
        self.assertEqual(record["origin_run_id"], run["id"])
        self.assertEqual(record["status"], "pending")
        self.assertEqual(record["evidence"], [{"ref": "P001", "platform": "Instagram",
                                                "post_id": "FICTIONAL_IG_001"}])
        self.assertIsNone(record["platform"])
        self.assertIsNone(record["topic"])
        decided = decide_recommendation(identifier, "accepted", self.state)
        self.assertEqual(register_run(run, self.state)[0], decided)
        self.assertEqual(len(list_recommendations(self.state)), 1)
        self.assertEqual(len(read_recommendation(identifier, self.state)["history"]), 2)

    def test_distinct_runs_do_not_deduplicate_same_text(self):
        first, second = self.run_analysis(), self.run_analysis()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(list_recommendations(self.state, "pending")), 2)

    def test_decisions_reject_invalid_transitions_and_ids(self):
        run = self.run_analysis()
        identifier = f"{run['id']}:1"
        with self.assertRaisesRegex(ValueError, "Invalid recommendation ID"):
            read_recommendation("../bad", self.state)
        with self.assertRaisesRegex(ValueError, "Only accepted"):
            associate_post(identifier, "Instagram", "FICTIONAL_IG_001", self.state)
        decided = decide_recommendation(identifier, "rejected", self.state)
        self.assertEqual(decided["status"], "rejected")
        with self.assertRaisesRegex(ValueError, "already rejected"):
            decide_recommendation(identifier, "accepted", self.state)
        with self.assertRaisesRegex(ValueError, "Only accepted"):
            associate_post(identifier, "Instagram", "FICTIONAL_IG_001", self.state)

    def test_associations_verify_posts_and_avoid_duplicates(self):
        run = self.run_analysis()
        identifier = f"{run['id']}:1"
        decide_recommendation(identifier, "accepted", self.state)
        with self.assertRaisesRegex(ValueError, "Publication not found"):
            associate_post(identifier, "Instagram", "MISSING", self.state)
        first = associate_post(identifier, "Instagram", "FICTIONAL_IG_001", self.state)
        self.assertEqual(len(first["associated_posts"]), 1)
        self.assertEqual(len(first["history"]), 3)
        with self.assertRaisesRegex(ValueError, "already associated"):
            associate_post(identifier, "Instagram", "FICTIONAL_IG_001", self.state)
        self.assertEqual(associated_posts(identifier, self.state), first["associated_posts"])

    def test_multiple_distinct_publications_can_be_associated(self):
        csv = self.state.parent / "more.csv"
        csv.write_text("platform,post_id,published_at,format,topic,likes\n"
                       "Instagram,FICTIONAL_IG_002,2026-02-01,reel,Otro ejemplo,5\n",
                       encoding="utf-8")
        import_csv(csv, self.state)
        run = self.run_analysis()
        identifier = f"{run['id']}:1"
        decide_recommendation(identifier, "accepted", self.state)
        associate_post(identifier, "Instagram", "FICTIONAL_IG_001", self.state)
        updated = associate_post(identifier, "Instagram", "FICTIONAL_IG_002", self.state)
        self.assertEqual(len(updated["associated_posts"]), 2)
        self.assertEqual(len(updated["history"]), 4)

    def test_cli_recovers_records_across_processes(self):
        run = self.run_analysis()
        identifier = f"{run['id']}:1"
        listing = self.cli("list", "--status", "pending")
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertEqual(json.loads(listing.stdout)[0]["id"], identifier)
        accepted = self.cli("accept", identifier)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(json.loads(accepted.stdout)["status"], "accepted")
        association = self.cli("associate", identifier, "--platform", "Instagram",
                               "--post-id", "FICTIONAL_IG_001")
        self.assertEqual(association.returncode, 0, association.stderr)
        linked = self.cli("posts", identifier)
        self.assertEqual(json.loads(linked.stdout), [{"platform": "Instagram",
                                                      "post_id": "FICTIONAL_IG_001"}])
        self.assertEqual(self.cli("reject", identifier).returncode, 2)

    def test_failed_analysis_registers_nothing_and_old_runs_remain_readable(self):
        with patch.object(social_analyze, "run_structured", side_effect=OSError("offline")):
            failed = core.execute("social.analyze", self.state)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(list_recommendations(self.state), [])
        with self.assertRaisesRegex(ValueError, "successful"):
            register_run(core.read_execution(failed["id"], self.state), self.state)

    def test_saved_run_can_be_registered_explicitly_after_write_failure(self):
        with patch.object(core, "register_run", side_effect=OSError("disk failure")):
            with self.assertRaisesRegex(OSError, "disk failure"):
                self.run_analysis()
        runs = list((self.state / "runs").glob("*.json"))
        self.assertEqual(len(runs), 1)
        saved = core.read_execution(runs[0].stem, self.state)
        self.assertEqual(saved["status"], "succeeded")
        self.assertEqual(list_recommendations(self.state), [])
        recovered = self.cli("register-run", saved["id"])
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(json.loads(recovered.stdout)[0]["id"], f"{saved['id']}:1")


if __name__ == "__main__":
    unittest.main()
