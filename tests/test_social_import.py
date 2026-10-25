from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REQUIRED = "platform,post_id,published_at,format,topic"


class SocialImportTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.state = self.base / "state"

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "helmos", "--state-dir", str(self.state), *args],
            cwd=ROOT, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            capture_output=True, text=True, check=False,
        )

    def csv(self, content: str) -> Path:
        path = self.base / "input.csv"
        path.write_text(content, encoding="utf-8")
        return path

    def import_text(self, content: str) -> dict[str, object]:
        completed = self.cli("run", "social.import", "--csv", str(self.csv(content)))
        self.assertIn(completed.returncode, (0, 1), completed.stderr)
        return json.loads(completed.stdout)

    def test_three_platform_templates_and_reimport(self) -> None:
        for platform in ("instagram", "tiktok", "linkedin"):
            source = ROOT / "examples" / f"social_{platform}_template.csv"
            run = self.cli("run", "social.import", "--csv", str(source))
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)["result"]["imported"], 1)
            repeated = self.cli("run", "social.import", "--csv", str(source))
            self.assertEqual(json.loads(repeated.stdout)["result"]["already_present"], 1)
        posts = json.loads(self.cli("social", "posts").stdout)
        self.assertEqual(len(posts), 3)
        self.assertEqual(len(list((self.state / "social" / "posts").glob("*.json"))), 3)
        self.assertEqual(posts[0]["platform"], "Instagram")
        self.assertNotIn("clicks", posts[0]["metrics"])
        summary = json.loads(self.cli("social", "summary").stdout)
        self.assertEqual(summary["TikTok"]["metrics"]["views"], {"sum": 310, "observed_posts": 1})
        self.assertNotIn("views", summary["LinkedIn"]["metrics"])
        self.assertEqual(summary["LinkedIn"]["metrics"]["linkedin_reactions"]["sum"], 21)

    def test_invalid_rows_leave_no_posts_and_failed_run_is_recoverable(self) -> None:
        invalid = (
            f"{REQUIRED},likes\nInstagram,a,2026-01-01,reel,tema,2\nTikTok,b,2026-02-30,video,tema,1\n",
            f"{REQUIRED},likes\nInstagram,a,2026-01-01,reel,tema,-1\n",
            f"{REQUIRED},likes\nInstagram,a,2026-01-01,reel,tema,1.5\n",
            f"{REQUIRED},likes\nInstagram,a,2026-01-01,reel,tema,1\nInstagram,a,2026-01-02,reel,tema,2\n",
            f"{REQUIRED},likes\nInstagram,a,2026-01-01,reel,,1\n",
            f"{REQUIRED},likes\nInstagram,a,2026-01-01,reel,tema,1,extra\n",
            f"{REQUIRED},unknown\nInstagram,a,2026-01-01,reel,tema,1\n",
            f"{REQUIRED},likes,likes\nInstagram,a,2026-01-01,reel,tema,1,2\n",
            f"{REQUIRED},followers\nInstagram,a,2026-01-01,reel,tema,20\n",
            f"{REQUIRED},linkedin_reactions\nTikTok,a,2026-01-01,video,tema,3\n",
        )
        for content in invalid:
            with self.subTest(content=content):
                record = self.import_text(content)
                self.assertEqual(record["status"], "failed")
                self.assertEqual(json.loads(self.cli("show", record["id"]).stdout), record)
                self.assertEqual(json.loads(self.cli("social", "posts").stdout), [])

    def test_conflict_does_not_change_original_or_write_other_posts(self) -> None:
        first = f"{REQUIRED},likes\nInstagram,a,2026-01-01,reel,tema,1\n"
        self.assertEqual(self.import_text(first)["status"], "succeeded")
        changed = (
            f"{REQUIRED},likes\nTikTok,b,2026-01-01,video,tema,9\n"
            "Instagram,a,2026-01-01,reel,tema,2\n"
        )
        record = self.import_text(changed)
        self.assertEqual(record["status"], "failed")
        posts = json.loads(self.cli("social", "posts").stdout)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["metrics"]["likes"], 1)
        self.assertEqual(posts[0]["source_row"]["likes"], "1")

    def test_blank_metrics_remain_absent_and_totals_show_coverage(self) -> None:
        content = (
            f"{REQUIRED},likes,comments\n"
            "Instagram,a,2026-01-01,reel,tema,,0\n"
            "Instagram,b,2026-01-02,reel,tema,4,\n"
        )
        self.assertEqual(self.import_text(content)["status"], "succeeded")
        posts = json.loads(self.cli("social", "posts", "--platform", "Instagram").stdout)
        self.assertEqual(posts[0]["metrics"], {"comments": 0})
        summary = json.loads(self.cli("social", "summary", "--platform", "Instagram").stdout)
        self.assertEqual(summary["Instagram"]["metrics"]["likes"], {"sum": 4, "observed_posts": 1})
        self.assertEqual(summary["Instagram"]["metrics"]["comments"], {"sum": 0, "observed_posts": 1})

    def test_same_post_id_on_different_platforms_is_distinct(self) -> None:
        content = (
            f"{REQUIRED},likes\n"
            "Instagram,same,2026-01-01,reel,tema,2\n"
            "TikTok,same,2026-01-01,video,tema,3\n"
        )
        record = self.import_text(content)
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(record["result"]["imported"], 2)
        self.assertEqual(len(json.loads(self.cli("social", "posts").stdout)), 2)

    def test_missing_csv_is_rejected_before_recording(self) -> None:
        completed = self.cli("run", "social.import")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("requires --csv", completed.stderr)
        self.assertFalse((self.state / "runs").exists())

    def test_dated_observations_are_idempotent_and_persist_between_processes(self) -> None:
        from helmos.social_import import list_observations, list_posts

        header = f"{REQUIRED},observed_at,measurement_type,period_start,period_end,likes,comments\n"
        first = header + "TikTok,post,2026-01-01,video,fictional,2026-01-03,cumulative,,,10,\n"
        second = header + "TikTok,post,2026-01-01,video,fictional,2026-01-05,cumulative,,,14,2\n"
        self.assertEqual(self.import_text(first)["result"]["imported"], 1)
        self.assertEqual(self.import_text(second)["result"]["observations_added"], 1)
        self.assertEqual(self.import_text(second)["result"]["already_present"], 1)
        post = list_posts(self.state)[0]
        observations = list_observations(self.state, post)
        self.assertEqual([item["metrics"] for item in observations],
                         [{"likes": 10}, {"likes": 14, "comments": 2}])
        self.assertEqual(len(list((self.state / "social" / "observations").rglob("*.json"))), 1)
        self.assertEqual(len(json.loads(self.cli("social", "posts").stdout)), 1)

    def test_legacy_record_is_preserved_when_new_observation_arrives(self) -> None:
        from helmos.social_import import list_observations, list_posts

        old = f"{REQUIRED},likes\nLinkedIn,post,2026-01-01,text,fictional,5\n"
        self.assertEqual(self.import_text(old)["status"], "succeeded")
        post_path = next((self.state / "social" / "posts").glob("*.json"))
        legacy = json.loads(post_path.read_text(encoding="utf-8"))
        legacy.pop("initial_observation")
        post_path.write_text(json.dumps(legacy), encoding="utf-8")
        original_bytes = post_path.read_bytes()
        new = (f"{REQUIRED},observed_at,measurement_type,likes\n"
               "LinkedIn,post,2026-01-01,text,fictional,2026-01-10,cumulative,8\n")
        self.assertEqual(self.import_text(new)["result"]["observations_added"], 1)
        self.assertEqual(post_path.read_bytes(), original_bytes)
        observations = list_observations(self.state, list_posts(self.state)[0])
        self.assertIsNone(observations[0]["observed_at"])
        self.assertEqual(observations[0]["measurement_type"], "unspecified")
        self.assertIsNone(observations[0]["source_csv"])
        self.assertEqual(observations[1]["metrics"], {"likes": 8})

    def test_conflicting_date_period_and_metadata_leave_saved_data_intact(self) -> None:
        header = f"{REQUIRED},observed_at,measurement_type,period_start,period_end,views\n"
        first = header + "TikTok,post,2026-01-01,video,fictional,2026-01-10,period,2026-01-02,2026-01-09,20\n"
        self.assertEqual(self.import_text(first)["status"], "succeeded")
        post_path = next((self.state / "social" / "posts").glob("*.json"))
        original_bytes = post_path.read_bytes()
        conflicts = (
            header + "TikTok,post,2026-01-01,video,fictional,2026-01-10,period,2026-01-03,2026-01-09,20\n",
            header + "TikTok,post,2026-01-01,video,fictional,2026-01-10,cumulative,,,20\n",
            header + "TikTok,post,2026-01-01,video,changed,2026-01-12,period,2026-01-10,2026-01-11,3\n",
            header + "TikTok,post,2026-01-01,video,fictional,2026-01-12,period,2026-01-13,2026-01-14,3\n",
        )
        for content in conflicts:
            with self.subTest(content=content):
                self.assertEqual(self.import_text(content)["status"], "failed")
                self.assertEqual(post_path.read_bytes(), original_bytes)
                self.assertFalse((self.state / "social" / "observations").exists())


if __name__ == "__main__":
    unittest.main()
