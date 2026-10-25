from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "social_sample.csv"


class SocialSampleCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.temp_path = Path(self.directory.name)
        self.state_dir = self.temp_path / "state"

    def cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run(
            [sys.executable, "-m", "helmos", "--state-dir", str(self.state_dir), *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def write_csv(self, content: str) -> Path:
        path = self.temp_path / "sample.csv"
        path.write_text(content, encoding="utf-8")
        return path

    def test_example_is_calculated_and_recovered_without_codex(self) -> None:
        completed = self.cli("run", "social.sample", "--csv", str(EXAMPLE))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        record = json.loads(completed.stdout)
        self.assertEqual(record["route"]["domain"], "social_sample")
        self.assertEqual(record["input"]["csv_path"], str(EXAMPLE))
        self.assertEqual(record["result"]["skill"]["name"], "analyze-social-sample")
        self.assertEqual(record["result"]["results"]["post_count"], 3)
        self.assertEqual(record["result"]["results"]["totals"], {
            "impressions": 290,
            "likes": 24,
            "comments": 6,
            "shares": 3,
            "interactions": 33,
        })
        self.assertEqual(
            [item["interactions"] for item in record["result"]["results"]["per_post"]],
            [13, 9, 11],
        )
        self.assertEqual(len(record["result"]["data"]), 3)
        self.assertEqual(len(record["result"]["warnings"]), 2)

        recovered = self.cli("show", record["id"])
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(json.loads(recovered.stdout), record)

    def test_invalid_counts_are_recorded_as_failure(self) -> None:
        path = self.write_csv(
            "post_id,platform,impressions,likes,comments,shares\n"
            "ig_01,Instagram,120,-1,2,1\n"
        )
        completed = self.cli("run", "social.sample", "--csv", str(path))
        self.assertEqual(completed.returncode, 1, completed.stderr)
        record = json.loads(completed.stdout)
        self.assertEqual(record["status"], "failed")
        self.assertIn("likes must be a non-negative integer", record["error"])
        self.assertIsNone(record["result"])
        recovered = self.cli("show", record["id"])
        self.assertEqual(recovered.returncode, 1)
        self.assertEqual(json.loads(recovered.stdout), record)

    def test_missing_csv_argument_is_rejected_before_recording(self) -> None:
        completed = self.cli("run", "social.sample")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("requires --csv", completed.stderr)
        self.assertFalse((self.state_dir / "runs").exists())

    def test_bad_header_and_duplicate_ids_are_rejected(self) -> None:
        samples = (
            "post_id,platform,likes\nig_01,Instagram,10\n",
            "post_id,platform,impressions,likes,comments,shares\n"
            "ig_01,Instagram,120,10,2,1\nig_01,TikTok,90,6,1,2\n",
        )
        for sample in samples:
            with self.subTest(sample=sample[:20]):
                path = self.write_csv(sample)
                completed = self.cli("run", "social.sample", "--csv", str(path))
                self.assertEqual(completed.returncode, 1, completed.stderr)
                self.assertEqual(json.loads(completed.stdout)["status"], "failed")

    def test_more_than_twenty_posts_are_rejected(self) -> None:
        rows = ["post_id,platform,impressions,likes,comments,shares"]
        rows.extend(f"ig_{index},Instagram,10,1,0,0" for index in range(21))
        path = self.write_csv("\n".join(rows) + "\n")
        completed = self.cli("run", "social.sample", "--csv", str(path))
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertIn("cannot exceed 20", json.loads(completed.stdout)["error"])

    def test_missing_file_is_recorded_as_failure(self) -> None:
        path = self.temp_path / "missing.csv"
        completed = self.cli("run", "social.sample", "--csv", str(path))
        self.assertEqual(completed.returncode, 1, completed.stderr)
        record = json.loads(completed.stdout)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(json.loads(self.cli("show", record["id"]).stdout), record)


if __name__ == "__main__":
    unittest.main()
