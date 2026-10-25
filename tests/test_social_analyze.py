from __future__ import annotations

import json
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from helmos import agent, core, social_analyze
from helmos.social_import import import_csv


ROOT = Path(__file__).resolve().parent.parent


class SocialAnalyzeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "state"

    def import_fictitious_templates(self) -> None:
        for platform in ("instagram", "tiktok", "linkedin"):
            import_csv(ROOT / "examples" / f"social_{platform}_template.csv", self.state)

    @staticmethod
    def valid_answer() -> dict[str, object]:
        return {
            "findings": [{"claim": "La muestra recoge una publicación de Instagram.",
                          "kind": "observation", "post_refs": ["P001"],
                          "metric_evidence": []}],
            "limitations": ["La muestra es pequeña."],
            "recommendations": [{"action": "Probar otro tema.",
                                 "rationale": "Comprobar si interesa a la audiencia.",
                                 "post_refs": ["P001"]}],
            "calendar": [{"slot": "Semana inicial", "platform": "Instagram",
                          "topic": "Tema de prueba", "format": "reel",
                          "rationale": "Explorar una hipótesis editorial.",
                          "post_refs": ["P001"]}],
        }

    def fake_ai(self, answer: dict[str, object] | None = None) -> dict[str, object]:
        return {"result": answer if answer is not None else self.valid_answer(),
                "consumption": {"tokens": 27, "cost": None,
                                "measurement": "codex_cli_usage"},
                "thread_id": "mock-thread"}

    def test_no_imported_posts_has_recoverable_error_without_ai(self) -> None:
        with patch.object(social_analyze, "run_structured") as ai:
            run = core.execute("social.analyze", self.state)
        ai.assert_not_called()
        self.assertEqual(run["status"], "failed")
        self.assertIn("No imported publications", run["error"])
        self.assertEqual(core.read_execution(run["id"], self.state), run)
        self.assertFalse((self.state / "social" / "reports").exists())

    def test_fictitious_templates_generate_persistent_report_and_recovery(self) -> None:
        self.import_fictitious_templates()
        with patch.object(social_analyze, "run_structured", return_value=self.fake_ai()) as ai:
            run = core.execute("social.analyze", self.state)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["agent"]["thread_id"], "mock-thread")
        self.assertEqual(run["consumption"]["tokens"], 27)
        dataset = run["result"]["dataset"]
        self.assertEqual(dataset["post_count"], 3)
        self.assertEqual(set(dataset["coverage"]), {"Instagram", "TikTok", "LinkedIn"})
        self.assertIsNone(dataset["coverage"]["LinkedIn"]["metrics"]["views"]["sum"])
        self.assertEqual(dataset["coverage"]["LinkedIn"]["metrics"]["views"]["missing_posts"], 1)
        self.assertIn("Dataset JSON:", ai.call_args.args[0])
        report = Path(run["result"]["report_path"])
        self.assertTrue(report.is_file())
        content = report.read_text(encoding="utf-8")
        self.assertIn(run["id"], content)
        self.assertIn("ausente", content)
        self.assertIn("pendiente de revisión", content)
        recovered = subprocess.run(
            [sys.executable, "-m", "helmos", "--state-dir", str(self.state),
             "show", run["id"]], cwd=ROOT,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(json.loads(recovered.stdout), run)

    def test_filters_select_period_and_platform(self) -> None:
        self.import_fictitious_templates()
        selected = social_analyze.prepare_dataset(self.state, "TikTok", "2026-01-01", "2026-12-31")
        self.assertEqual(selected["post_count"], 1)
        self.assertEqual(selected["posts"][0]["platform"], "TikTok")
        with self.assertRaisesRegex(social_analyze.SocialAnalysisError, "No imported"):
            social_analyze.prepare_dataset(self.state, "Instagram", "2020-01-01", "2020-12-31")

    def test_rejects_missing_reference_without_report(self) -> None:
        self.import_fictitious_templates()
        answer = self.valid_answer()
        answer["findings"][0]["post_refs"] = ["P999"]
        with patch.object(social_analyze, "run_structured", return_value=self.fake_ai(answer)):
            run = core.execute("social.analyze", self.state)
        self.assertEqual(run["status"], "failed")
        self.assertIn("missing publication reference", run["error"])
        self.assertFalse((self.state / "social" / "reports" / f"{run['id']}.md").exists())
        self.assertEqual(core.read_execution(run["id"], self.state), run)

    def test_rejects_invalid_ai_response_and_unverifiable_numbers(self) -> None:
        self.import_fictitious_templates()
        cases = [{"findings": "invalid"},
                 dict(self.valid_answer(), findings=[]),
                 dict(self.valid_answer(), recommendations=[]),
                 dict(self.valid_answer(), calendar=[]),
                 dict(self.valid_answer(), limitations=["Hay 999 publicaciones."])]
        for answer in cases:
            with self.subTest(answer=answer):
                with patch.object(social_analyze, "run_structured", return_value=self.fake_ai(answer)):
                    run = core.execute("social.analyze", self.state)
                self.assertEqual(run["status"], "failed")
                self.assertFalse((self.state / "social" / "reports" / f"{run['id']}.md").exists())

    def test_rejects_wrong_structured_metric_and_preserves_core_agent_route(self) -> None:
        self.import_fictitious_templates()
        answer = self.valid_answer()
        answer["findings"][0]["metric_evidence"] = [{
            "platform": "Instagram", "metric": "likes", "sum": 999,
            "observed_posts": 1,
        }]
        with patch.object(social_analyze, "run_structured", return_value=self.fake_ai(answer)):
            run = core.execute("social.analyze", self.state)
        self.assertEqual(run["status"], "failed")
        self.assertIn("unverifiable metric", run["error"])
        self.assertEqual(core.route("core.agent")["handler"], "codex_exec")

    def test_editorial_schema_uses_real_transport_envelope(self) -> None:
        self.import_fictitious_templates()

        def fake_launch(command, *, stdin, stdout, stderr, shell):
            self.assertIn("--output-schema", command)
            self.assertIn("read-only", command)
            self.assertFalse(shell)
            schema = Path(command[command.index("--output-schema") + 1])
            self.assertEqual(json.loads(schema.read_text(encoding="utf-8")),
                             social_analyze.EDITORIAL_SCHEMA)
            self.assertIn(b"Dataset JSON:", stdin.read())
            message = Path(command[command.index("--output-last-message") + 1])
            message.write_text(json.dumps(self.valid_answer()), encoding="utf-8")
            stdout.write(b'{"type":"thread.started","thread_id":"transport-thread"}\n')
            stdout.write(b'{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":12}}\n')
            return SimpleNamespace(stdin=io.BytesIO(), returncode=0, poll=lambda: 0)

        with patch.object(agent, "_codex_executable", return_value=Path("codex.exe")), \
             patch.object(agent.subprocess, "Popen", side_effect=fake_launch):
            run = core.execute("social.analyze", self.state)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["agent"]["thread_id"], "transport-thread")
        self.assertEqual(run["consumption"]["tokens"], 22)
        self.assertTrue(Path(run["result"]["report_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
