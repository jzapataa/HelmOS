from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from helmos import approvals, core


ROOT = Path(__file__).resolve().parent.parent


class ApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state_dir = Path(self.directory.name)

    def cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "helmos", "--state-dir", str(self.state_dir), *arguments],
            cwd=ROOT,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            capture_output=True,
            text=True,
            check=False,
        )

    def run_id(self) -> str:
        completed = self.cli("run", "core.status")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)["id"]

    def create(self, run_id: str, action: str = "manual_follow_up") -> subprocess.CompletedProcess[str]:
        return self.cli(
            "approvals", "create", "--run-id", run_id, "--action", action,
            "--description", "Check the result manually", "--scope", "Core local review",
        )

    def test_create_list_show_and_approve_persist_across_processes(self) -> None:
        run_id = self.run_id()
        created = self.create(run_id)
        self.assertEqual(created.returncode, 0, created.stderr)
        proposal = json.loads(created.stdout)
        self.assertEqual(proposal["origin_run_id"], run_id)
        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(len(proposal["transitions"]), 1)
        identifier = proposal["id"]

        listed = self.cli("approvals", "list")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(json.loads(listed.stdout), [proposal])
        shown = self.cli("approvals", "show", identifier)
        self.assertEqual(json.loads(shown.stdout), proposal)

        approved = self.cli("approvals", "approve", identifier)
        self.assertEqual(approved.returncode, 0, approved.stderr)
        resolved = json.loads(approved.stdout)
        self.assertEqual(resolved["status"], "approved")
        self.assertEqual(resolved["transitions"][1]["from"], "pending")
        self.assertEqual(resolved["transitions"][1]["to"], "approved")
        self.assertEqual(json.loads(self.cli("approvals", "show", identifier).stdout), resolved)
        self.assertEqual(len(list((self.state_dir / "runs").glob("*.json"))), 1)

    def test_reject_and_second_decision_is_refused_without_change(self) -> None:
        proposal = json.loads(self.create(self.run_id()).stdout)
        identifier = proposal["id"]
        rejected = self.cli("approvals", "reject", identifier)
        self.assertEqual(rejected.returncode, 0, rejected.stderr)
        resolved = json.loads(rejected.stdout)
        self.assertEqual(resolved["status"], "rejected")
        repeat = self.cli("approvals", "approve", identifier)
        self.assertEqual(repeat.returncode, 2)
        self.assertIn("already rejected", repeat.stderr)
        self.assertEqual(json.loads(self.cli("approvals", "show", identifier).stdout), resolved)

    def test_unknown_route_does_not_invoke_handlers_or_create_records(self) -> None:
        with patch.object(core, "run_agent") as agent, patch.object(core, "local_status") as status:
            with self.assertRaisesRegex(ValueError, "Unsupported operation"):
                core.execute("shell.exec", self.state_dir, task="echo hello")
            agent.assert_not_called()
            status.assert_not_called()
        self.assertFalse((self.state_dir / "runs").exists())

    def test_unknown_action_and_invalid_origin_do_not_create_proposals(self) -> None:
        run_id = self.run_id()
        invalid = self.create(run_id, "shell.exec")
        self.assertEqual(invalid.returncode, 2)
        self.assertFalse((self.state_dir / "approvals").exists())
        missing = self.create("00000000-0000-0000-0000-000000000000")
        self.assertEqual(missing.returncode, 2)
        self.assertFalse((self.state_dir / "approvals").exists())

    def test_invalid_id_and_corrupt_json_are_controlled_errors(self) -> None:
        bad_id = self.cli("approvals", "show", "../AGENTS.md")
        self.assertEqual(bad_id.returncode, 2)
        self.assertIn("Invalid proposal ID", bad_id.stderr)
        proposal = json.loads(self.create(self.run_id()).stdout)
        path = self.state_dir / "approvals" / f"{proposal['id']}.json"
        path.write_text('{"status":', encoding="utf-8")
        for command in (("show", proposal["id"]), ("list",), ("approve", proposal["id"])):
            with self.subTest(command=command):
                result = self.cli("approvals", *command)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("Traceback", result.stderr)

    def test_invalid_persisted_state_is_rejected(self) -> None:
        proposal = json.loads(self.create(self.run_id()).stdout)
        path = self.state_dir / "approvals" / f"{proposal['id']}.json"
        proposal["status"] = "executed"
        path.write_text(json.dumps(proposal), encoding="utf-8")
        shown = self.cli("approvals", "show", proposal["id"])
        self.assertEqual(shown.returncode, 2)
        self.assertIn("Proposal record is invalid", shown.stderr)
        self.assertNotIn("Traceback", shown.stderr)

    def test_empty_list_does_not_create_storage(self) -> None:
        result = self.cli("approvals", "list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])
        self.assertFalse((self.state_dir / "approvals").exists())

    def test_linked_runs_directory_cannot_redirect_reads_or_writes(self) -> None:
        with tempfile.TemporaryDirectory() as other_directory:
            destination = Path(other_directory)
            link_context = nullcontext()
            try:
                (self.state_dir / "runs").symlink_to(destination, target_is_directory=True)
            except (OSError, NotImplementedError):
                original = Path.is_symlink
                linked_path = self.state_dir / "runs"
                link_context = patch.object(
                    Path, "is_symlink",
                    lambda path: path == linked_path or original(path),
                )
            with link_context:
                identifier = "00000000-0000-0000-0000-000000000000"
                with self.assertRaisesRegex(ValueError, "symbolic link"):
                    core.read_execution(identifier, self.state_dir)
                with self.assertRaisesRegex(ValueError, "symbolic link"):
                    core.execute("core.status", self.state_dir)
            self.assertEqual(list(destination.iterdir()), [])

    def test_failed_replace_preserves_pending_proposal(self) -> None:
        proposal = approvals.create_proposal(
            self.run_id(), "review_output", "Review status result", "Core", self.state_dir,
        )
        with patch.object(approvals.os, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                approvals.decide_proposal(proposal["id"], "approved", self.state_dir)
        self.assertEqual(approvals.read_proposal(proposal["id"], self.state_dir), proposal)
        self.assertEqual(list((self.state_dir / "approvals").glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
