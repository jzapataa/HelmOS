from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helmos import core


ROOT = Path(__file__).resolve().parent.parent


class CoreCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state_dir = Path(self.directory.name)

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

    def test_run_can_be_recovered_from_another_process(self) -> None:
        completed = self.cli("run", "core.status")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        record = json.loads(completed.stdout)
        self.assertEqual(record["route"]["domain"], "core")
        self.assertEqual(record["status"], "succeeded")
        self.assertTrue(record["result"]["ready"])
        self.assertIsNone(record["consumption"]["tokens"])

        recovered = self.cli("show", record["id"])
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(json.loads(recovered.stdout), record)
        self.assertEqual(len(list((self.state_dir / "runs").glob("*.json"))), 1)

    def test_unsupported_operation_does_not_create_an_execution(self) -> None:
        completed = self.cli("run", "social.unknown")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("Unsupported operation", completed.stderr)
        self.assertFalse((self.state_dir / "runs").exists())

    def test_invalid_id_is_rejected_without_path_traversal(self) -> None:
        completed = self.cli("show", "../AGENTS.md")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("Invalid execution ID", completed.stderr)

    def test_corrupt_record_is_reported_without_traceback(self) -> None:
        identifier = "3d7cb5f6-4ae5-4cc0-87f1-7d32df58769b"
        runs = self.state_dir / "runs"
        runs.mkdir()
        (runs / f"{identifier}.json").write_text(
            json.dumps({"id": identifier, "status": "succeeded"}), encoding="utf-8"
        )

        completed = self.cli("show", identifier)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("Execution record is invalid", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_handler_failure_is_recoverable(self) -> None:
        with patch.object(core, "local_status", side_effect=OSError("unavailable")):
            record = core.execute("core.status", self.state_dir)
        self.assertEqual(record["status"], "failed")
        self.assertIn("unavailable", record["error"])
        self.assertEqual(core.read_execution(record["id"], self.state_dir), record)

    def test_persistence_failure_is_visible_and_leaves_no_partial_record(self) -> None:
        with patch.object(core.os, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                core.execute("core.status", self.state_dir)
        self.assertEqual(list((self.state_dir / "runs").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
