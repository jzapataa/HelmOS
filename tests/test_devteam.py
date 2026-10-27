"""D2 policy, sequencing and recovery tests; all repositories are fictional."""

import copy
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from helmos.agent import AgentExecutionError
from helmos.approvals import decide_proposal
from helmos.core import read_execution
from helmos.devteam import (Budget, Coordinator, read_project, resume_project, start_project,
                            write_json)
from helmos.devteam_contracts import SCHEMAS, validate
from helmos.devteam_runtime import apply_files, digest, snapshot, workspace_lock


def response(role):
    common = {"outcome": "completed", "summary": role, "limitations": []}
    if role == "planner":
        return {**common, "acceptance": ["AC-1 returns correct sum"], "steps": ["Implement and test"],
                "manual_qa": ["Call add(2,3); expected 5"]}
    if role == "developer":
        return {**common, "files": [{"path": "app.py", "content": "def add(a,b): return a+b\n"},
                                    {"path": "test_dev_app.py", "content": "# fictional test proposal\n"}]}
    if role == "qa":
        return {**common, "files": [{"path": "test_qa_app.py", "content": "# independent tests\n"}],
                "recipe": "unittest", "defects": [], "coverage": ["AC-1"]}
    return {**common, "defects": [], "manual_qa": ["Call add(2,3); expected 5"]}


class ScriptedEngine:
    def __init__(self):
        self.roles = []
        self.results = {}

    def __call__(self, prompt, directory, schema, **options):
        role = next(key for key, value in SCHEMAS.items() if value == schema)
        self.roles.append(role)
        value = self.results.get(role, response(role))
        return {"result": copy.deepcopy(value), "consumption": {"tokens": 10, "cost": None},
                "thread_id": f"isolated-{len(self.roles)}"}


class DevelopmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "fictional"
        self.workspace.mkdir()
        self.state = self.root / "state"
        self.engine = ScriptedEngine()
        self.deps = {"engine": self.engine, "probe": lambda *a, **kw: {"passed": True, "checks": {}},
                     "tester": lambda *a: {"passed": True, "counts": {"tests": 2}, "log": "2 tests OK"}}

    def start(self, **kwargs):
        return start_project(self.workspace, "Add a sum function and tests", self.state,
                             authorized=True, fictional=True, **{**self.deps, **kwargs})

    def test_happy_path_has_independent_roles_delivery_and_core_approval(self):
        record = self.start()
        self.assertEqual(self.engine.roles, ["planner", "developer", "qa", "reviewer"])
        self.assertEqual(record["state"], "ready_for_acceptance")
        self.assertEqual(record["tokens"], 40)
        self.assertEqual(record["review_snapshot"], record["tests"][-1]["snapshot"])
        delivery = self.state / "devteam" / record["id"] / "delivery.md"
        self.assertIn("Manual QA plan", delivery.read_text(encoding="utf-8"))
        self.assertEqual(read_execution(record["id"], self.state)["status"], "succeeded")
        decide_proposal(record["approval_id"], "approved", self.state)
        self.assertEqual(read_project(self.state, record["id"])["state"], "ready_for_acceptance")
        resume_project(self.state, record["id"], **self.deps)
        self.assertEqual(len(self.engine.roles), 4)

    def test_qa_failure_automatically_repairs_then_repeats_qa_and_reviews(self):
        results = iter([{"passed": False, "log": "test_sum: expected 5, got 4"},
                        {"passed": True, "log": "2 tests OK"}])
        record = self.start(tester=lambda *a: next(results))
        self.assertEqual(record["state"], "ready_for_acceptance")
        self.assertEqual(record["repairs"], 1)
        self.assertEqual(self.engine.roles, ["planner", "developer", "qa", "developer", "qa", "reviewer"])
        self.assertIn("repairing", [h["state"] for h in record["history"]])
        self.assertIn("test_sum", record["tests"][0]["log"])

    def test_qa_repair_context_does_not_reopen_results_of_an_old_snapshot(self):
        original = self.engine.__call__
        qa_payloads = []
        def engine(prompt, *args, **kwargs):
            result = original(prompt, *args, **kwargs)
            if args[1] == SCHEMAS["qa"]:
                data = prompt.split("<project_data>\n", 1)[1].split("\n</project_data>", 1)[0]
                qa_payloads.append(json.loads(data))
            if len(self.engine.roles) == 4:
                result["result"]["files"][0]["content"] = "def add(a,b): return sum((a,b))\n"
            return result
        results = iter([{"passed": False, "log": "old-snapshot-failure-only"},
                        {"passed": True, "log": "2 tests OK"}])
        record = self.start(engine=engine, tester=lambda *a: next(results))
        self.assertEqual(record["state"], "ready_for_acceptance")
        self.assertEqual(record["repairs"], 1)
        self.assertEqual(qa_payloads[1]["test_results"], [])
        self.assertEqual(qa_payloads[1]["verification_targets"][0]["id"], "QA-TEST")
        self.assertNotIn("old-snapshot-failure-only", json.dumps(qa_payloads[1]))
        self.assertIn("old-snapshot-failure-only", record["tests"][0]["log"])

    def test_reviewer_can_return_work_without_a_human_decision(self):
        original = self.engine.__call__
        reviews = []
        def engine(*args, **kwargs):
            value = original(*args, **kwargs)
            if args[2] == SCHEMAS["reviewer"] and not reviews:
                value["result"]["defects"] = [{"id": "R1", "severity": "P1", "path": "app.py",
                    "reproduction": "add invalid types", "expected": "reject", "observed": "accepts"}]
                reviews.append(True)
            return value
        record = self.start(engine=engine)
        self.assertEqual(record["repairs"], 1)
        self.assertEqual(self.engine.roles.count("reviewer"), 2)
        self.assertEqual(record["state"], "ready_for_acceptance")

    def test_missing_authorization_never_calls_engine(self):
        for authorized, fictional in ((False, True), (True, False)):
            with self.assertRaises(ValueError):
                start_project(self.workspace, "request", self.state,
                              authorized=authorized, fictional=fictional, **self.deps)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.engine.roles, [])

    def test_path_escape_and_role_escalation_rejected_without_writes(self):
        canary = self.root / "canary.py"
        canary.write_text("unchanged", encoding="utf-8")
        for name, role in (("../canary.py", "developer"), (str(canary), "developer"),
                           (".git/config", "developer"), ("test_qa_app.py", "developer"),
                           ("app.py", "qa"), ("app.py", "reviewer"),
                           ("C:escape.py", "developer"), ("con.py", "developer")):
            with self.assertRaises(ValueError):
                apply_files(self.workspace, [{"path": name, "content": "bad"}], role, {})
        self.assertEqual(canary.read_text(), "unchanged")
        self.assertEqual(snapshot(self.workspace), {})

    def test_entire_batch_validated_before_first_write(self):
        with self.assertRaises(ValueError):
            apply_files(self.workspace, [{"path": "app.py", "content": "ok"},
                                        {"path": "../bad.py", "content": "bad"}], "developer", {})
        self.assertEqual(snapshot(self.workspace), {})

    def test_hardlinks_and_changed_base_rejected(self):
        (self.workspace / "app.py").write_text("changed")
        with self.assertRaises(ValueError):
            apply_files(self.workspace, [{"path": "app.py", "content": "overwrite"}], "developer", {})
        try:
            os.link(self.workspace / "app.py", self.workspace / "other.py")
        except OSError:
            self.skipTest("Hardlinks not available")
        with self.assertRaises(ValueError):
            snapshot(self.workspace)

    def test_limits_are_shared_and_stop_before_next_call(self):
        record = self.start(budget=Budget(calls=2))
        self.assertEqual(record["state"], "stopped_limits")
        self.assertEqual(record["calls"], 2)
        self.assertEqual(self.engine.roles, ["planner", "developer"])

    def test_repair_budget_and_no_progress_stop(self):
        record = self.start(budget=Budget(repairs=1),
                            tester=lambda *a: {"passed": False, "log": "same reproducible failure"})
        self.assertEqual(record["state"], "stopped_limits")
        self.assertEqual(record["repairs"], 1)

    def test_bad_contract_is_recoverable_and_not_applied(self):
        self.engine.results["developer"] = {"summary": "invalid"}
        record = self.start()
        self.assertEqual(record["state"], "failed")
        self.assertEqual(snapshot(self.workspace), {})
        self.assertEqual(record["attempts"][-1]["status"], "returned")

    def test_transport_error_and_blocked_preflight_do_not_execute_tests(self):
        tester = unittest.mock.Mock(side_effect=AssertionError("must never execute"))
        record = self.start(probe=lambda *a, **kw: {"passed": False, "checks": {}}, tester=tester)
        self.assertEqual(record["state"], "blocked_environment")
        self.assertEqual(self.engine.roles[-1], "reviewer")
        tester.assert_not_called()
        self.assertFalse(record["tests"][-1]["passed"])
        self.assertIsNotNone(record["approval_id"])
        def broken(*a, **kw):
            raise AgentExecutionError("fixture transport unavailable")
        record = self.start(engine=broken)
        self.assertEqual(record["state"], "blocked_environment")

    def test_crash_leaves_call_reserved_and_resume_refuses_replay(self):
        def crash(*a, **kw):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.start(engine=crash)
        identifier = next(p.name for p in (self.state / "devteam").iterdir() if p.is_dir())
        recovered = resume_project(self.state, identifier, **self.deps)
        self.assertEqual(recovered["state"], "interrupted")
        self.assertEqual(recovered["calls"], 1)
        self.assertEqual(self.engine.roles, [])

    def test_partial_apply_is_diagnosed_without_replay(self):
        with patch("helmos.devteam.apply_files", side_effect=OSError("fixture apply failure")):
            record = self.start()
        self.assertIsNotNone(record["pending_apply"])
        self.assertEqual(record["state"], "failed")
        recovered = resume_project(self.state, record["id"], **self.deps)
        self.assertEqual(recovered["state"], "failed")
        self.assertEqual(snapshot(self.workspace), {})

    def test_resume_preserves_budget_and_rejects_authorization_change(self):
        record = self.start(budget=Budget(calls=1))
        directory = self.state / "devteam" / record["id"]
        authorization = json.loads((directory / "authorization.json").read_text())
        authorization["deploy"] = True
        write_json(directory / "authorization.json", authorization)
        with self.assertRaises(ValueError):
            resume_project(self.state, record["id"], **self.deps)

    def test_state_cannot_be_inside_workspace(self):
        with self.assertRaises(ValueError):
            start_project(self.workspace, "request", self.workspace / "state",
                          authorized=True, fictional=True, **self.deps)

    def test_kernel_lock_excludes_second_writer(self):
        with workspace_lock(self.root / "lock"):
            with self.assertRaises(ValueError):
                with workspace_lock(self.root / "lock"):
                    self.fail("Second writer acquired lock")

    def test_unknown_usage_and_budget_validation(self):
        original = self.engine.__call__
        def engine(*args, **kw):
            value = original(*args, **kw)
            value["consumption"] = {"tokens": None}
            return value
        record = self.start(engine=engine)
        self.assertTrue(record["usage_unknown"])
        self.assertIsNone(read_execution(record["id"], self.state)["consumption"]["tokens"])
        for bad in (0, -1, True, 1000):
            with self.assertRaises(ValueError):
                Budget(calls=bad)

    def test_snapshot_and_contract_are_deterministic(self):
        self.assertEqual(digest({"b": 2, "a": 1}), digest({"a": 1, "b": 2}))
        with self.assertRaises(ValueError):
            validate({**response("reviewer"), "commands": ["bad"]}, SCHEMAS["reviewer"])

    def test_token_and_wall_time_budgets_stop_without_further_effects(self):
        record = self.start(budget=Budget(tokens=10))
        self.assertEqual(record["state"], "stopped_limits")
        self.assertEqual(record["calls"], 1)
        self.assertEqual(snapshot(self.workspace), {})
        self.engine.roles.clear()
        original = Coordinator.remaining
        count = []
        def expired(coordinator):
            if count:
                from helmos.devteam import LimitsReached
                raise LimitsReached("fixture deadline")
            count.append(True)
            return original(coordinator)
        with patch.object(Coordinator, "remaining", expired):
            record = self.start()
        self.assertEqual(record["state"], "stopped_limits")
        self.assertEqual(self.engine.roles, [])

    def test_invalid_transition_and_terminal_conflict(self):
        record = self.start()
        coordinator = Coordinator(record, self.state, **self.deps)
        with self.assertRaises(ValueError):
            coordinator.transition("implementing")
        (self.workspace / "app.py").write_text("external edit")
        with self.assertRaises(ValueError):
            resume_project(self.state, record["id"], **self.deps)

    def test_malformed_engine_envelope_is_recorded_and_usage_unknown(self):
        record = self.start(engine=lambda *a, **kw: [])
        self.assertEqual(record["state"], "failed")
        self.assertTrue(record["usage_unknown"])

    def test_cli_show_recovers_in_another_process_without_calling_model(self):
        record = self.start()
        result = subprocess.run([sys.executable, "-m", "helmos", "--state-dir", str(self.state),
                                 "devteam", "show", record["id"]], capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["id"], record["id"])

    def test_cli_missing_authorization_is_a_controlled_error(self):
        result = subprocess.run([sys.executable, "-m", "helmos", "--state-dir", str(self.state),
                                 "devteam", "start", "--workspace", str(self.workspace),
                                 "--request", "fixture", "--fictional"],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--authorize", result.stderr)
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()
