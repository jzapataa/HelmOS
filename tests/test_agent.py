from __future__ import annotations

import json
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from helmos import agent, core


class AgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state_dir = Path(self.directory.name)

    def test_codex_result_and_usage_are_recoverable(self) -> None:
        def fake_launch(command, *, stdin, stdout, stderr, shell):
            self.assertEqual(command[0], Path("codex.exe"))
            self.assertIn("read-only", command)
            self.assertIn("--ignore-user-config", command)
            self.assertIn("multi_agent", command)
            self.assertIn("shell_tool", command)
            self.assertIn("approval_policy=never", command)
            self.assertEqual(command[-1], "-")
            self.assertIn(b"Summarize the Core", stdin.read())
            self.assertFalse(shell)
            message = Path(command[command.index("--output-last-message") + 1])
            message.write_text(json.dumps({
                "outcome": "completed",
                "summary": "The Core stores runs locally.",
                "evidence": ["helmos/core.py"], "limitations": [],
            }), encoding="utf-8")
            stdout.write(b'{"type":"thread.started","thread_id":"real-thread"}\n')
            stdout.write(b'{"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":8}}\n')
            return SimpleNamespace(
                stdin=io.BytesIO(), returncode=0, poll=lambda: 0,
            )

        with patch.object(agent, "_codex_executable", return_value=Path("codex.exe")), \
             patch.object(agent.subprocess, "Popen", side_effect=fake_launch):
            record = core.execute(
                "core.agent", self.state_dir, task="Summarize the Core", context_path=Path("README.md")
            )
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(record["agent"]["engine"], "codex exec")
        self.assertEqual(record["agent"]["thread_id"], "real-thread")
        self.assertEqual(record["consumption"]["tokens"], 20)
        self.assertEqual(core.read_execution(record["id"], self.state_dir), record)

    def test_codex_failure_is_persisted_without_stderr(self) -> None:
        with patch.object(agent, "_codex_executable", return_value=Path("codex.exe")), \
             patch.object(agent.subprocess, "Popen", return_value=SimpleNamespace(
                 stdin=io.BytesIO(), returncode=7, poll=lambda: 7,
             )):
            record = core.execute(
                "core.agent", self.state_dir, task="Inspect README", context_path=Path("README.md")
            )
        self.assertEqual(record["status"], "failed")
        self.assertIn("exit 7", record["error"])
        self.assertEqual(core.read_execution(record["id"], self.state_dir), record)

    def test_invalid_task_never_starts_agent(self) -> None:
        with patch.object(agent.subprocess, "Popen") as launch:
            with self.assertRaises(ValueError):
                core.execute(
                    "core.agent", self.state_dir, task="a" * (agent.MAX_TASK_LENGTH + 1),
                    context_path=Path("README.md"),
                )
            launch.assert_not_called()
        self.assertFalse((self.state_dir / "runs").exists())

    def test_agent_blocked_result_is_failed_and_recoverable(self) -> None:
        outcome = {
            "result": {
                "outcome": "blocked", "summary": "The document lacks that detail.",
                "evidence": [], "limitations": ["No matching information"],
            },
            "consumption": {"tokens": None, "cost": None, "measurement": "unknown"},
            "thread_id": "thread-1",
        }
        with patch.object(core, "run_agent", return_value=outcome):
            record = core.execute(
                "core.agent", self.state_dir, task="Find unavailable detail",
                context_path=Path("README.md"),
            )
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["result"]["outcome"], "blocked")
        self.assertEqual(core.read_execution(record["id"], self.state_dir), record)

    def test_context_cannot_escape_repository(self) -> None:
        with self.assertRaises(ValueError):
            core.execute(
                "core.agent", self.state_dir, task="Read secrets",
                context_path=Path("../secret.md"),
            )
        self.assertFalse((self.state_dir / "runs").exists())

    def test_timeout_kills_process_and_records_failure(self) -> None:
        class HungProcess:
            returncode = None

            def poll(self):
                return self.returncode

            def kill(self):
                self.returncode = -9

            def wait(self, timeout=None):
                return self.returncode

        process = HungProcess()
        with patch.object(agent, "_codex_executable", return_value=Path("codex.exe")), \
             patch.object(agent.subprocess, "Popen", return_value=process), \
             patch.object(agent.time, "monotonic", side_effect=[0, agent.TIMEOUT_SECONDS + 1]):
            record = core.execute(
                "core.agent", self.state_dir, task="Inspect README",
                context_path=Path("README.md"),
            )
        self.assertEqual(process.returncode, -9)
        self.assertEqual(record["status"], "failed")
        self.assertIn("exceeded", record["error"])
        self.assertEqual(core.read_execution(record["id"], self.state_dir), record)


if __name__ == "__main__":
    unittest.main()
