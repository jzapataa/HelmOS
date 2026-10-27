"""Offline fault-injection tests; all records and CSV contents are synthetic."""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from helmos import instagram_date_reconciliation as recon
from helmos import recommendation_evaluations as evaluations
from helmos.recommendations import register_run, decide_recommendation, associate_post
from helmos.social_import import import_csv, list_posts, _post_path, _persist_posts
from helmos.social_analyze import prepare_dataset
from helmos.sync_lock import execution_lock
from helmos.social_connectors import ConnectorError


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.state = self.base / "state"
        self.batch = self.base / "batch.json"
        self.batch.write_text(json.dumps({
            "expected": [[str(index), day, timestamp] for index, day, timestamp in (
                (101, "2026-09-24", "2026-09-25T05:00:00+00:00"),
                (102, "2026-09-17", "2026-09-18T05:00:00+00:00"),
                (103, "2026-09-10", "2026-09-11T05:00:00+00:00"),
                (104, "2026-09-03", "2026-09-04T05:00:00+00:00"))],
            "evidence": {"source": "official_api", "account_id": "456",
                         "api_version": "v26.0", "timezone_cause": "undetermined"}
        }), encoding="utf-8")
        previous = recon.EXPECTED, recon.EVIDENCE
        self.addCleanup(setattr, recon, "EXPECTED", previous[0])
        self.addCleanup(setattr, recon, "EVIDENCE", previous[1])
        recon.configure_batch(self.batch)
        self.csv = self.base / "fixture.csv"
        self.csv.write_text("platform,post_id,published_at,format,topic,likes\n" + "".join(
            f"Instagram,{pid},{day},Reel,synthetic,1\n" for pid, day, _ in recon.EXPECTED),
            encoding="utf-8")
        import_csv(self.csv, self.state)
        self.plan = recon.prepare(self.state)
        self.approval = recon.digest(recon.encoded(self.plan))
        self.before = self.snapshot()

    def snapshot(self):
        return {p.relative_to(self.state).as_posix(): p.read_bytes()
                for p in self.state.rglob("*.json")}

    def apply(self):
        return recon.run(self.state, self.plan, apply=True, approval=self.approval, actor="test")

    def test_private_batch_is_required_and_rejects_expanded_scope(self):
        with patch.object(recon, "EXPECTED", ()):
            with self.assertRaisesRegex(ValueError, "private reviewed batch"):
                recon.prepare(self.state)
            with self.assertRaises(ValueError):
                self.apply()
        batch = json.loads(self.batch.read_bytes())
        batch["expected"].append(["105", "2026-09-01", "2026-09-02T00:00:00+00:00"])
        self.batch.write_text(json.dumps(batch), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Exactly four"):
            recon.configure_batch(self.batch)
        self.assertEqual(self.snapshot(), self.before)

    def test_dry_run_no_writes_and_four_exact_paths(self):
        report = recon.run(self.state, self.plan)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["modified"], 0)
        self.assertEqual(len(report["records"]), 4)
        self.assertEqual(self.snapshot(), self.before)

    def test_application_preserves_all_fields_and_exact_original_bytes_in_receipt(self):
        self.assertEqual(self.apply()["modified"], 4)
        for item in self.plan["records"]:
            old = json.loads(self.before[item["path"]])
            new = json.loads((self.state / item["path"]).read_bytes())
            self.assertEqual(new.pop("published_at"), item["after_date"])
            evidence = new.pop("date_reconciliation")
            self.assertEqual(evidence["timestamp"], item["timestamp"])
            old.pop("published_at")
            self.assertEqual(new, old)
        receipt = json.loads(recon.receipt_path(self.state).read_bytes())
        self.assertEqual(receipt["plan"], self.plan)
        self.assertEqual(receipt["actor"], "test")

    def test_idempotent_replay_does_not_write(self):
        self.apply()
        before = self.snapshot()
        self.assertEqual(self.apply()["status"], "already_applied")
        self.assertEqual(recon.run(self.state, self.plan)["status"], "already_applied")
        self.assertEqual(self.snapshot(), before)

    def test_approval_and_actor_required(self):
        for options in ({}, {"approval": self.approval}, {"approval": "wrong", "actor": "test"}):
            with self.assertRaises(ValueError):
                recon.run(self.state, self.plan, apply=True, **options)
        self.assertEqual(self.snapshot(), self.before)

    def test_one_modified_record_blocks_all_four(self):
        path = self.state / self.plan["records"][3]["path"]
        path.write_bytes(path.read_bytes() + b" ")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "four-record"):
            self.apply()
        self.assertEqual(self.snapshot(), before)

    def test_changed_dependency_and_csv_block_before_writes(self):
        extra = self.state / "runs" / "new.json"
        extra.parent.mkdir()
        extra.write_text("{}")
        with self.assertRaisesRegex(ValueError, "Dependencies changed"):
            self.apply()
        extra.unlink()
        self.csv.write_bytes(self.csv.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "CSV changed"):
            self.apply()
        self.assertEqual(self.snapshot(), self.before)

    def test_changed_plan_scope_and_timestamp_rejected(self):
        for key, value in (("post_id", "other"), ("timestamp", "2026-09-25T06:00:00+00:00")):
            plan = json.loads(json.dumps(self.plan))
            plan["records"][0][key] = value
            with self.assertRaises(ValueError):
                recon.run(self.state, plan)
        plan = {**self.plan, "records": self.plan["records"][:3]}
        with self.assertRaises(ValueError):
            recon.run(self.state, plan)

    def test_second_process_and_csv_cannot_enter_shared_lock(self):
        with execution_lock(self.base, ".state.instagram.lock"):
            with self.assertRaises(ConnectorError):
                import_csv(self.csv, self.state)
            script = ("from pathlib import Path; from helmos.sync_lock import execution_lock; "
                      f"lock=execution_lock(Path({str(self.base)!r}), '.state.instagram.lock'); lock.__enter__()")
            result = subprocess.run([sys.executable, "-c", script], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"scheduled_sync_busy", result.stderr)
        self.assertEqual(self.snapshot(), self.before)

    def test_write_failure_restores_every_original_byte(self):
        original = recon.atomic_write
        count = 0

        def fail_third_post(path, content):
            nonlocal count
            if path.parent.name == "posts":
                count += 1
                if count == 3:
                    raise OSError("simulated disk failure")
            original(path, content)

        with patch.object(recon, "atomic_write", side_effect=fail_third_post):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual(self.snapshot(), self.before)

    def test_atomic_replace_failure_keeps_original_and_removes_temp(self):
        path = self.state / self.plan["records"][0]["path"]
        before = path.read_bytes()
        with patch.object(recon.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                recon.atomic_write(path, b"new data")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.glob(".reconcile-*.tmp")), [])

    def interrupted(self):
        changes = recon.validate_plan(self.plan, self.state)
        journal = self.state / "social" / recon.JOURNAL
        recon.atomic_write(journal, recon.encoded({"plan_sha256": self.approval}))
        recon.atomic_write(changes[0][0], changes[0][2])
        return changes

    def test_interrupted_transaction_blocks_read_and_write_until_explicit_rollback(self):
        self.interrupted()
        with self.assertRaisesRegex(ValueError, "recovery"):
            list_posts(self.state)
        with self.assertRaisesRegex(ValueError, "recovery"):
            import_csv(self.csv, self.state)
        with self.assertRaisesRegex(ValueError, "rollback"):
            self.apply()
        result = recon.run(self.state, self.plan, rollback=True, approval=self.approval, actor="test")
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(self.snapshot(), self.before)
        self.assertEqual(self.apply()["status"], "applied")

    def test_recovery_never_overwrites_unknown_version(self):
        changes = self.interrupted()
        changes[3][0].write_bytes(b"unexpected version")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "unexpected"):
            recon.run(self.state, self.plan, rollback=True, approval=self.approval, actor="test")
        self.assertEqual(self.snapshot(), before)

    def test_future_conflicts_and_old_csv_remain_blocked(self):
        self.apply()
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "metadata differs"):
            import_csv(self.csv, self.state)
        post = list_posts(self.state)[0]
        post["published_at"] = "2026-09-01"
        with self.assertRaisesRegex(ValueError, "metadata differs"):
            _persist_posts([post], self.state)
        self.assertEqual(self.snapshot(), before)

    def test_observation_window_incompatible_with_new_day_blocks_preparation(self):
        path = _post_path(self.state, "Instagram", recon.EXPECTED[0][0])
        post = json.loads(path.read_bytes())
        post["initial_observation"]["observed_at"] = recon.EXPECTED[0][1]
        recon.atomic_write(path, recon.encoded(post))
        with self.assertRaisesRegex(ValueError, "window"):
            recon.prepare(self.state)

    def test_evaluation_revision_preserves_previous_evaluation_and_recommendation(self):
        pid = recon.EXPECTED[0][0]
        run = {"id": str(uuid4()), "status": "succeeded", "input": {"operation": "social.analyze"},
               "result": {"dataset": {"posts": [{"ref": "P001", "platform": "Instagram", "post_id": pid}]},
                          "editorial": {"recommendations": [{"action": "Synthetic test",
                                       "rationale": "Synthetic test", "post_refs": ["P001"]}]}}}
        identifier = register_run(run, self.state)[0]["id"]
        decide_recommendation(identifier, "accepted", self.state)
        associate_post(identifier, "Instagram", pid, self.state)
        previous = evaluations.evaluate_recommendation(identifier, self.state)
        previous_files = self.snapshot()
        self.plan = recon.prepare(self.state)
        self.approval = recon.digest(recon.encoded(self.plan))
        self.apply()
        current = evaluations.evaluate_recommendation(identifier, self.state)
        self.assertNotEqual(previous["id"], current["id"])
        self.assertEqual(evaluations.read_evaluation(previous["id"], self.state), previous)
        self.assertEqual(evaluations.evaluate_recommendation(identifier, self.state), current)
        for name, content in previous_files.items():
            if not name.startswith("social/posts/"):
                self.assertEqual((self.state / name).read_bytes(), content)
        self.assertEqual(current["publications"][0]["published_at"], recon.EXPECTED[0][2][:10])

    def test_filter_moves_to_new_day_without_changing_metric_counts(self):
        before = prepare_dataset(self.state, "Instagram", "2026-09-24", "2026-09-24")
        self.apply()
        with self.assertRaisesRegex(ValueError, "No imported"):
            prepare_dataset(self.state, "Instagram", "2026-09-24", "2026-09-24")
        after = prepare_dataset(self.state, "Instagram", "2026-09-25", "2026-09-25")
        self.assertEqual(before["posts"][0]["metrics"], after["posts"][0]["metrics"])

    def test_cli_dry_run_and_missing_approval(self):
        plan_path = self.base / "private-plan.json"
        args = ["--state-dir", str(self.state), "--plan", str(plan_path), "--batch", str(self.batch)]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(recon.main([*args, "--dry-run"]), 0)
            self.assertEqual(recon.main([*args, "--apply"]), 2)
        self.assertEqual(self.snapshot(), self.before)

    def test_receipt_write_failure_rolls_back_entire_batch(self):
        original = recon.atomic_write

        def fail_receipt(path, content):
            if path == recon.receipt_path(self.state):
                raise OSError("receipt unavailable")
            original(path, content)

        with patch.object(recon, "atomic_write", side_effect=fail_receipt):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual(self.snapshot(), self.before)

    def test_actual_process_termination_leaves_recoverable_journal(self):
        plan_path = self.base / "plan.json"
        plan_path.write_bytes(recon.encoded(self.plan))
        script = "\n".join([
            "import json, os", "from pathlib import Path",
            "from helmos import instagram_date_reconciliation as r",
            f"r.configure_batch(Path({str(self.batch)!r}))",
            "original = r.atomic_write",
            "def terminate_after_first_post(path, content):",
            "    original(path, content)",
            "    if path.parent.name == 'posts': os._exit(71)",
            "r.atomic_write = terminate_after_first_post",
            f"plan = json.loads(Path({str(plan_path)!r}).read_bytes())",
            f"r.run(Path({str(self.state)!r}), plan, apply=True, approval={self.approval!r}, actor='test')",
        ])
        result = subprocess.run([sys.executable, "-c", script], capture_output=True)
        self.assertEqual(result.returncode, 71, result.stderr)
        with self.assertRaisesRegex(ValueError, "recovery"):
            list_posts(self.state)
        recon.run(self.state, self.plan, rollback=True, approval=self.approval, actor="test")
        self.assertEqual(self.snapshot(), self.before)

    def test_rollback_failure_keeps_recovery_guard(self):
        self.interrupted()
        with patch.object(recon, "atomic_write", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                recon.run(self.state, self.plan, rollback=True, approval=self.approval, actor="test")
        with self.assertRaisesRegex(ValueError, "recovery"):
            list_posts(self.state)
        recon.run(self.state, self.plan, rollback=True, approval=self.approval, actor="test")
        self.assertEqual(self.snapshot(), self.before)

    def test_duplicate_identity_and_csv_evidence_mismatch_block_preparation(self):
        path = self.state / self.plan["records"][0]["path"]
        duplicate = path.parent / "duplicate.json"
        duplicate.write_bytes(path.read_bytes())
        with self.assertRaisesRegex(ValueError, "identity mismatch or duplicate"):
            recon.prepare(self.state)
        duplicate.unlink()
        self.csv.write_text(self.csv.read_text().replace("synthetic", "changed"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "CSV row mismatch"):
            recon.prepare(self.state)


if __name__ == "__main__":
    unittest.main()
