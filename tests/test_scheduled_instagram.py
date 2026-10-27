from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

from helmos.__main__ import main
from helmos.scheduled_instagram import (SyncConfig, execution_lock, run_scheduled,
                                        sync_status, write_record)
from helmos.social_connectors import ConnectorError
from helmos.social_import import import_csv, list_posts, list_observations
from helmos.social_import import SocialImportError
from helmos.windows_tasks import NAMESPACE, TASK_NAME, manage_task, task_xml
from test_instagram_sync import FakeHTTPResponse, IDENTITY, MEDIA, MARKER, listing, metrics


class MemoryStore:
    def __init__(self):
        self.record = {"token": MARKER, "kind": "unknown", "username": "jorgebytes"}

    def transaction(self):
        return contextlib.nullcontext()

    def load(self):
        return self.record.copy() if self.record else None

    def save(self, value):
        self.record = value.copy()


class ScheduledSyncTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="HelmOS scheduled spaces ")
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "state with spaces"
        self.store = MemoryStore()
        # Both the credential and HTTP boundaries are replaced for every test.
        self.addCleanup(patch.stopall)
        patch("helmos.instagram_credentials.WindowsCredentialStore", return_value=self.store).start()
        patch("helmos.credential_store.WindowsCredentialStore.load",
              side_effect=AssertionError("real credential store forbidden")).start()
        patch("helmos.credential_store._dpapi",
              side_effect=AssertionError("real DPAPI forbidden")).start()
        self.connection = Mock()
        self.http = patch("helmos.instagram_http.http.client.HTTPSConnection",
                          return_value=self.connection).start()
        patch.dict(os.environ, {"HELMOS_INSTAGRAM_ACCESS_TOKEN": "IGNORED-FICTIONAL-ENV"}).start()
        patch("helmos.windows_tasks.subprocess.run",
              side_effect=AssertionError("scheduler forbidden in sync tests")).start()
        self.config = SyncConfig()

    def responses(self, tail=None, identity=None):
        values = [FakeHTTPResponse(identity or IDENTITY)]
        values += tail if tail is not None else [
            FakeHTTPResponse(listing().body), *(FakeHTTPResponse(item.body) for item in metrics())]
        self.connection.getresponse.side_effect = values

    def run_sync(self, tail=None):
        self.responses(tail)
        return run_scheduled(self.state, self.config)

    def test_complete_sync_history_and_repeat_are_safe(self):
        first, code = self.run_sync()
        self.assertEqual(code, 0)
        self.assertEqual(first["result"]["observations_saved"], 1)
        original = {str(p.relative_to(self.state)): p.read_bytes()
                    for p in (self.state / "social" / "posts").rglob("*.json")}
        second, code = self.run_sync()
        self.assertEqual(code, 0)
        self.assertEqual(second["result"]["observations_saved"], 0)
        changed = [FakeHTTPResponse(listing().body),
                   *(FakeHTTPResponse(item.body) for item in metrics(12))]
        third, code = self.run_sync(changed)
        self.assertEqual(code, 0)
        post = list_posts(self.state)[0]
        observations = list_observations(self.state, post)
        self.assertEqual(len(observations), 2)
        self.assertEqual(observations[-1]["provenance"]["captured_at"], third["result"]["captured_at"])
        for path, content in original.items():
            self.assertEqual((self.state / path).read_bytes(), content)
        self.assertEqual(sync_status(self.state)["last_success"]["id"], third["id"])
        self.assertEqual(self.connection.request.call_args.kwargs["headers"]["Authorization"], "Bearer " + MARKER)
        for path in self.state.rglob("*.json"):
            self.assertNotIn(MARKER, path.read_text())

    def test_missing_token_and_account_mismatch_never_sync(self):
        self.store.record = None
        record, code = run_scheduled(self.state, self.config)
        self.assertEqual(code, 2)
        self.assertTrue(record["reauthorization_required"])
        self.http.assert_not_called()
        self.store = MemoryStore()
        with patch("helmos.instagram_credentials.WindowsCredentialStore", return_value=self.store):
            self.responses(identity=dict(IDENTITY, username="somebody_else"))
            record, code = run_scheduled(self.state, self.config)
        self.assertEqual(code, 2)
        self.assertEqual(record["error"], "instagram_account_mismatch")
        self.assertEqual(list_posts(self.state), [])

    def test_csv_publication_conflict_identifies_field_without_changing_history(self):
        csv_path = self.state.parent / "history.csv"
        csv_path.write_text(
            "platform,post_id,published_at,format,topic,views\n"
            "Instagram,123,2026-09-30,reel,Private editorial topic,5\n",
            encoding="utf-8")
        import_csv(csv_path, self.state)
        original = {p: p.read_bytes() for p in (self.state / "social" / "posts").glob("*.json")}
        tail = [FakeHTTPResponse(listing([dict(MEDIA, id="124"), MEDIA]).body),
                *(FakeHTTPResponse(item.body) for item in metrics()),
                *(FakeHTTPResponse(item.body) for item in metrics())]
        record, code = self.run_sync(tail)
        self.assertEqual((code, record["error"]), (2, "scheduled_sync_failed"))
        self.assert_safe_diagnostic(record, "observations_write", "sync_observations_write_failed")
        self.assertEqual(record["diagnostic"]["persistence_step"], "publication_metadata_conflict")
        self.assertEqual(record["diagnostic"]["persistence_post_index"], 2)
        self.assertEqual(record["diagnostic"]["persistence_conflicting_fields"], ["published_at"])
        self.assertEqual(record["diagnostic"]["persistence_publication_ref"], next(iter(original)).stem)
        self.assertEqual(record["diagnostic"]["persistence_published_at"], {
            "historical": {"value": "2026-09-30", "source": "csv"},
            "incoming": {"value": "2026-10-01", "source": "official_api"},
        })
        persisted = sync_status(self.state)["last_run"]
        self.assertEqual(persisted["diagnostic"], record["diagnostic"])
        self.assertNotIn("Private editorial topic", json.dumps(record))
        self.assertEqual({p: p.read_bytes() for p in (self.state / "social" / "posts").glob("*.json")}, original)
        self.assertFalse((self.state / "social" / "observations").exists())

    def test_same_capture_conflict_is_diagnosed_and_preserves_prior_success(self):
        capture = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
        with patch("helmos.instagram_sync.datetime") as clock:
            clock.now.return_value = capture
            first, code = self.run_sync()
            self.assertEqual(code, 0)
            original = {p: p.read_bytes() for folder in ("posts", "observations")
                        for p in (self.state / "social" / folder).rglob("*.json")}
            changed = [FakeHTTPResponse(listing().body),
                       *(FakeHTTPResponse(item.body) for item in metrics(12))]
            record, code = self.run_sync(changed)
        self.assertEqual(code, 2)
        self.assert_safe_diagnostic(record, "observations_write", "sync_observations_write_failed")
        self.assertEqual(record["diagnostic"]["persistence_step"], "api_capture_conflict")
        self.assertEqual(sync_status(self.state)["last_success"]["id"], first["id"])
        self.assertEqual({p: p.read_bytes() for folder in ("posts", "observations")
                          for p in (self.state / "social" / folder).rglob("*.json")}, original)

    def test_existing_api_account_conflict_is_distinguished(self):
        self.assertEqual(self.run_sync()[1], 0)
        post = list_posts(self.state)[0]
        original = list_observations(self.state, post)
        self.responses(identity=dict(IDENTITY, user_id="999"))
        record, code = run_scheduled(self.state, self.config)
        self.assertEqual(code, 2)
        self.assert_safe_diagnostic(record, "observations_write", "sync_observations_write_failed")
        self.assertEqual(record["diagnostic"]["persistence_step"], "connector_identity_conflict")
        self.assertEqual(list_observations(self.state, post), original)

    def test_csv_same_day_conflict_is_distinguished_without_relaxing_validation(self):
        capture = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
        csv_path = self.state.parent / "dated.csv"
        csv_path.write_text(
            "platform,post_id,published_at,format,topic,observed_at,measurement_type,views\n"
            "Instagram,123,2026-10-01,reel,Reviewed topic,2026-10-27,cumulative,5\n",
            encoding="utf-8")
        import_csv(csv_path, self.state)
        post = list_posts(self.state)[0]
        original = list_observations(self.state, post)
        with patch("helmos.instagram_sync.datetime") as clock:
            clock.now.return_value = capture
            record, code = self.run_sync()
        self.assertEqual(code, 2)
        self.assert_safe_diagnostic(record, "observations_write", "sync_observations_write_failed")
        self.assertEqual(record["diagnostic"]["persistence_step"], "observation_date_conflict")
        self.assertEqual(list_observations(self.state, post), original)

    def test_observation_disk_failure_identifies_write_and_preserves_files(self):
        self.assertEqual(self.run_sync()[1], 0)
        original = {p: p.read_bytes() for folder in ("posts", "observations")
                    for p in (self.state / "social" / folder).rglob("*.json")}
        changed = [FakeHTTPResponse(listing().body),
                   *(FakeHTTPResponse(item.body) for item in metrics(12))]
        with patch("helmos.social_import._save_post", side_effect=PermissionError(13, MARKER)):
            record, code = self.run_sync(changed)
        self.assertEqual(code, 2)
        self.assert_safe_diagnostic(record, "observations_write", "sync_observations_write_failed")
        self.assertEqual(record["diagnostic"]["persistence_step"], "observation_write")
        self.assertEqual(record["diagnostic"]["category"], "permission")
        self.assertEqual(record["diagnostic"]["errno"], 13)
        self.assertEqual({p: p.read_bytes() for folder in ("posts", "observations")
                          for p in (self.state / "social" / folder).rglob("*.json")}, original)

    def test_rate_limit_keeps_completed_publication_and_retry_after(self):
        tail = [FakeHTTPResponse(listing([MEDIA, dict(MEDIA, id="124")]).body),
                *(FakeHTTPResponse(item.body) for item in metrics()),
                FakeHTTPResponse({"message": MARKER}, 429, {"Retry-After": "60"})]
        record, code = self.run_sync(tail)
        self.assertEqual(code, 2)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["result"]["failure"]["retry_after"], 60)
        self.assertEqual(record["result"]["posts_imported"], 1)
        self.assertEqual(len(list_posts(self.state)), 1)
        self.assertIsNone(sync_status(self.state)["last_success"])
        self.assertEqual(self.connection.getresponse.call_count, 9)
        retry = [FakeHTTPResponse(listing([MEDIA, dict(MEDIA, id="124")]).body),
                 *(FakeHTTPResponse(item.body) for item in metrics()),
                 *(FakeHTTPResponse(item.body) for item in metrics())]
        recovered, code = self.run_sync(retry)
        self.assertEqual(code, 0)
        self.assertEqual(recovered["result"]["posts_imported"], 1)
        self.assertEqual(len(list_posts(self.state)), 2)
        self.assertEqual(sync_status(self.state)["last_success"]["id"], recovered["id"])

    def test_auth_failure_after_first_post_keeps_partial_and_signals_reauthorization(self):
        tail = [FakeHTTPResponse(listing([MEDIA, dict(MEDIA, id="124")]).body),
                *(FakeHTTPResponse(item.body) for item in metrics()),
                FakeHTTPResponse({"error": {"code": 190, "message": MARKER}}, 400)]
        record, code = self.run_sync(tail)
        self.assertEqual(code, 2)
        self.assertTrue(record["reauthorization_required"])
        self.assertEqual(len(list_posts(self.state)), 1)

    def test_temporary_server_error_on_next_page_keeps_completed_posts(self):
        tail = [FakeHTTPResponse(listing(more=True).body),
                *(FakeHTTPResponse(item.body) for item in metrics()),
                FakeHTTPResponse({"message": MARKER}, 503)]
        record, code = self.run_sync(tail)
        self.assertEqual((record["status"], code), ("failed", 2))
        self.assertEqual(record["result"]["failure"]["status"], 503)
        self.assertEqual(record["result"]["posts_imported"], 1)
        self.assertEqual(len(list_posts(self.state)), 1)

    def test_transport_failure_is_finite_and_retains_last_success(self):
        success, _ = self.run_sync()
        self.connection.request.side_effect = TimeoutError(MARKER)
        record, code = run_scheduled(self.state, self.config)
        self.assertEqual(code, 2)
        self.assertEqual(record["error"], "transport_failure")
        self.assertEqual(sync_status(self.state)["last_success"]["id"], success["id"])
        self.assertNotIn(MARKER, json.dumps(record))

    def test_metric_failure_and_bounded_listing_are_not_success(self):
        tail = [FakeHTTPResponse(listing().body), FakeHTTPResponse({}, 503),
                *(FakeHTTPResponse(item.body) for item in metrics()[1:])]
        record, code = self.run_sync(tail)
        self.assertEqual((record["status"], code), ("partial", 1))
        tail[0] = FakeHTTPResponse(listing(more=True).body)
        self.responses([tail[0], *(FakeHTTPResponse(item.body) for item in metrics())])
        record, code = run_scheduled(self.state, SyncConfig(max_pages=1))
        self.assertEqual((record["status"], code), ("partial", 1))

    def test_request_budget_and_deadline_fail(self):
        self.responses()
        record, code = run_scheduled(self.state, SyncConfig(max_requests=2))
        self.assertEqual(code, 2)
        self.assertEqual(record["result"]["failure"]["code"], "request_budget_exceeded")
        self.assert_safe_diagnostic(record, "collection", "request_budget_exceeded")
        self.responses()
        with patch("helmos.instagram_http.time.monotonic", side_effect=[0, 0, 0, 1000]):
            record, code = run_scheduled(self.state, self.config)
        self.assertEqual(code, 2)
        self.assertEqual(record["diagnostic"]["code"], "deadline_exceeded")

    def test_lock_excludes_overlapping_run_without_reading_credentials(self):
        directory = self.state / "social" / "scheduled_instagram"
        with execution_lock(directory):
            record, code = run_scheduled(self.state, self.config)
        self.assertEqual(code, 3)
        self.http.assert_not_called()
        self.assertEqual(record["error"], "scheduled_sync_busy")
        self.assertEqual(self.run_sync()[1], 0)

    def test_manual_sync_and_scheduled_sync_share_data_lock(self):
        self.responses()
        with execution_lock(self.state.parent, f".{self.state.name}.instagram.lock"):
            record, code = run_scheduled(self.state, self.config)
        self.assertEqual((record["status"], code), ("busy", 3))
        self.assertEqual(list_posts(self.state), [])

    def test_interrupted_run_is_recovered_and_lock_file_survives(self):
        directory = self.state / "social" / "scheduled_instagram"
        path = directory / "runs" / "old.json"
        write_record(path, {"id": "old", "started_at": "2000-01-01", "status": "running"})
        record, code = self.run_sync()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(path.read_text())["status"], "interrupted")
        self.assertTrue((directory / "execution.lock").exists())
        self.assertEqual(sync_status(self.state)["last_run"]["id"], record["id"])

    def test_lock_is_cross_process_and_released_after_process_termination(self):
        directory = self.state / "social" / "scheduled_instagram"
        script = (
            "from pathlib import Path; import sys; "
            "from helmos.scheduled_instagram import execution_lock; "
            "lock = execution_lock(Path(sys.argv[1])); lock.__enter__(); "
            "print('locked', flush=True); sys.stdin.read()")
        process = subprocess.Popen([sys.executable, "-c", script, str(directory)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "locked")
            self.assertEqual(run_scheduled(self.state, self.config)[1], 3)
        finally:
            process.kill()
            process.communicate(timeout=10)
        self.assertEqual(self.run_sync()[1], 0)

    def test_persistence_failure_never_returns_success(self):
        self.responses()
        with patch("helmos.scheduled_instagram.write_record", side_effect=OSError(MARKER)):
            record, code = run_scheduled(self.state, self.config)
        self.assertEqual(code, 2)
        self.http.assert_not_called()
        self.assertNotIn(MARKER, json.dumps(record))

    def test_atomic_journal_failure_preserves_previous_record(self):
        path = self.state / "record.json"
        write_record(path, {"status": "running"})
        original = path.read_bytes()
        with patch("helmos.scheduled_instagram.os.replace", side_effect=OSError(MARKER)):
            with self.assertRaises(OSError):
                write_record(path, {"status": "succeeded"})
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.state.iterdir()), [path])

    def assert_safe_diagnostic(self, record, stage, code):
        self.assertEqual(record["diagnostic"]["stage"], stage)
        self.assertEqual(record["diagnostic"]["code"], code)
        for text in [json.dumps(record), *(p.read_text() for p in self.state.rglob("*.json"))]:
            for secret in (MARKER, "Authorization", "https://", "access_token="):
                self.assertNotIn(secret, text)

    def test_unexpected_failures_retain_legacy_error_and_identify_stage(self):
        cases = [
            ("helmos.instagram_credentials.InstagramCredentials._load", "credentials_load",
             "credential_load_unexpected", RuntimeError),
            ("helmos.instagram_credentials.InstagramCredentials._provider", "credentials_identity",
             "credential_identity_unexpected", TypeError),
            ("helmos.instagram_sync.list_posts", "history_read", "sync_history_read_failed", SocialImportError),
            ("helmos.instagram_sync.list_observations", "history_read", "sync_history_read_failed", KeyError),
            ("helmos.instagram_sync.InstagramConnector.collect", "collection", "sync_collection_failed", TypeError),
            ("helmos.instagram_sync._persist_posts", "observations_write", "sync_observations_write_failed", PermissionError),
        ]
        # Seed a real isolated publication so reading its observations is exercised.
        self.assertEqual(self.run_sync()[1], 0)
        for target, stage, detail, kind in cases:
            with self.subTest(stage=stage, target=target):
                self.responses()
                with patch(target, side_effect=kind("https://host/?access_token=" + MARKER)):
                    record, code = run_scheduled(self.state, self.config)
                self.assertEqual((record["error"], code), ("scheduled_sync_failed", 2))
                self.assertIsNone(record["result"])
                self.assertFalse(record["reauthorization_required"])
                self.assert_safe_diagnostic(record, stage, detail)
                self.assertEqual(sync_status(self.state)["last_run"], record)

    def test_credential_transaction_and_save_diagnostics(self):
        for method, stage in (("transaction", "credentials_lock"), ("save", "credentials_save")):
            with self.subTest(method=method):
                self.responses()
                with patch.object(self.store, method, side_effect=OSError(13, MARKER)):
                    record, code = run_scheduled(self.state, self.config)
                self.assertEqual(code, 2)
                self.assert_safe_diagnostic(record, stage, {
                    "transaction": "credential_lock_unexpected", "save": "credential_save_unexpected"}[method])
                self.assertEqual(record["diagnostic"]["errno"], 13)

        @contextlib.contextmanager
        def unlock_failure():
            yield
            raise ConnectorError("credential_unlock_failed")
        self.responses()
        with patch.object(self.store, "transaction", unlock_failure):
            record, code = run_scheduled(self.state, self.config)
        self.assertEqual(code, 2)
        self.assert_safe_diagnostic(record, "credentials_unlock", "credential_unlock_failed")

    def test_refresh_failure_does_not_log_response_or_change_policy(self):
        now = datetime.now(timezone.utc)
        self.store.record.update(kind="long-lived", issued_at=(now - timedelta(days=30)).isoformat(),
                                 expires_at=(now + timedelta(days=1)).isoformat())
        self.responses()
        with patch("helmos.instagram_http.InstagramHTTPProvider.refresh_token", side_effect=RuntimeError(MARKER)):
            record, code = run_scheduled(self.state, self.config)
        self.assertEqual(code, 2)
        self.assert_safe_diagnostic(record, "credentials_refresh", "credential_refresh_unexpected")

    def test_expected_dpapi_failures_retain_codes(self):
        for error in ("credential_protection_failed", "credential_read_failed", "invalid_credential_store"):
            with self.subTest(error=error), patch.object(self.store, "load", side_effect=ConnectorError(error)):
                record, code = run_scheduled(self.state, self.config)
                self.assertEqual((record["error"], code), (error, 2))
                self.assert_safe_diagnostic(record, "credentials_load", error)
        self.http.assert_not_called()

    def test_journal_failures_are_returned_even_when_the_journal_is_unwritable(self):
        for stage, nth in (("journal_start", 1), ("journal_finish", 2)):
            with self.subTest(stage=stage):
                self.responses()
                calls = 0
                def write(path, record):
                    nonlocal calls
                    calls += 1
                    if calls == nth:
                        raise PermissionError(13, MARKER)
                    write_record(path, record)
                # Isolate each journal so recovery does not add a write.
                state = self.state / stage
                with patch("helmos.scheduled_instagram.write_record", side_effect=write):
                    record, code = run_scheduled(state, self.config)
                self.assertEqual((record["error"], code), ("scheduled_record_failed", 2))
                self.assert_safe_diagnostic(record, stage, f"scheduled_record_{stage.removeprefix('journal_')}_failed")
                self.assertEqual(record["diagnostic"]["category"], "permission")

    def test_corrupt_journal_and_lock_io_are_distinguished(self):
        path = self.state / "social" / "scheduled_instagram" / "runs" / "broken.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"access_token": "' + MARKER)
        record, code = run_scheduled(self.state, self.config)
        self.assertEqual(code, 2)
        self.assertEqual(record["diagnostic"]["stage"], "journal_recovery")
        self.assertEqual(record["diagnostic"]["category"], "invalid_json")
        self.assertNotIn(MARKER, json.dumps(record))
        self.http.assert_not_called()
        with patch("helmos.scheduled_instagram.execution_lock", side_effect=PermissionError(13, MARKER)):
            record, code = run_scheduled(self.state, self.config)
        self.assertEqual(code, 2)
        self.assertEqual(record["diagnostic"]["stage"], "execution_lock")

    def test_network_context_distinguishes_identity_listing_metrics_and_pagination(self):
        self.connection.request.side_effect = TimeoutError("Authorization " + MARKER)
        record, code = run_scheduled(self.state, self.config)
        self.assertEqual((record["error"], code), ("transport_failure", 2))
        self.assert_safe_diagnostic(record, "credentials_identity", "transport_failure")
        self.assertEqual(record["diagnostic"]["operation"], "identity")
        self.assertEqual(record["diagnostic"]["transport"], "timeout")
        self.connection.request.side_effect = None
        for tail, operation in (([FakeHTTPResponse({}, 503)], "listing"),
                                ([FakeHTTPResponse(listing().body), FakeHTTPResponse({}, 429)], "metrics")):
            record, code = self.run_sync(tail)
            self.assertEqual(code, 2)
            self.assertEqual(record["diagnostic"]["stage"], "collection")
            self.assertEqual(record["diagnostic"]["operation"], operation)
            self.assertGreaterEqual(record["diagnostic"]["requests_made"], 2)
        bad_page = listing(more=True).body
        bad_page["paging"]["cursors"] = {"after": None}
        record, code = self.run_sync([FakeHTTPResponse(bad_page),
                                    *(FakeHTTPResponse(item.body) for item in metrics())])
        self.assertEqual(code, 2)
        self.assert_safe_diagnostic(record, "collection", "invalid_pagination")
        self.assertEqual(record["diagnostic"]["collection_step"], "pagination")

    def test_data_lock_and_result_contract_failure_diagnostics(self):
        self.responses()
        with execution_lock(self.state.parent, f".{self.state.name}.instagram.lock"):
            record, code = run_scheduled(self.state, self.config)
        self.assertEqual(code, 3)
        self.assert_safe_diagnostic(record, "data_lock", "scheduled_sync_busy")
        self.responses()
        with patch("helmos.scheduled_instagram.sync_instagram", return_value={}):
            record, code = run_scheduled(self.state, self.config)
        self.assertEqual(code, 2)
        self.assert_safe_diagnostic(record, "result", "sync_result_failed")

    def test_journal_finish_failure_retains_original_failure_in_cli_json(self):
        self.connection.request.side_effect = TimeoutError(MARKER)
        calls = 0
        def write(path, record):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError(28, MARKER)
            write_record(path, record)
        output = io.StringIO()
        with patch("helmos.scheduled_instagram.write_record", side_effect=write), \
                contextlib.redirect_stdout(output):
            code = main(["--state-dir", str(self.state), "social", "scheduled-sync", "run"])
        self.assertEqual(code, 2)
        record = json.loads(output.getvalue())
        self.assert_safe_diagnostic(record, "journal_finish", "scheduled_record_finish_failed")
        self.assertEqual(record["run_diagnostic"]["code"], "transport_failure")
        self.assertEqual(record["run_diagnostic"]["transport"], "timeout")
        self.assertEqual(sync_status(self.state)["last_run"]["status"], "running")

    def test_data_lock_io_and_unlock_failures_have_distinct_stages(self):
        self.responses()
        with patch("helmos.sync_lock.execution_lock", side_effect=PermissionError(13, MARKER)):
            record, code = run_scheduled(self.state, self.config)
        self.assertEqual(code, 2)
        self.assert_safe_diagnostic(record, "data_lock", "sync_lock_failed")
        @contextlib.contextmanager
        def unlock_failure(*args):
            yield
            raise OSError(5, MARKER)
        self.responses()
        with patch("helmos.sync_lock.execution_lock", unlock_failure):
            record, code = run_scheduled(self.state, self.config)
        self.assertEqual(code, 2)
        self.assert_safe_diagnostic(record, "data_unlock", "sync_unlock_failed")

    def test_absolute_state_is_independent_of_working_directory(self):
        self.responses()
        with patch("helmos.scheduled_instagram.Path.cwd", return_value=self.state.parent):
            record, code = run_scheduled(self.state, self.config)
        self.assertEqual(code, 0)
        self.assertFalse(record["runtime"]["cwd_matches_repository"])
        self.assertTrue(record["runtime"]["state_path_absolute"])
        self.assertEqual(sync_status(self.state)["last_run"], record)

    def test_cli_propagates_partial_fatal_and_busy_codes(self):
        args = ["--state-dir", str(self.state), "social", "scheduled-sync", "run"]
        tail = [FakeHTTPResponse(listing().body), FakeHTTPResponse({}, 503),
                *(FakeHTTPResponse(item.body) for item in metrics()[1:])]
        self.responses(tail)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 1)
        self.connection.request.side_effect = TimeoutError(MARKER)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 2)
        with execution_lock(self.state / "social" / "scheduled_instagram"), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 3)

    def test_cli_codes_and_status_are_json(self):
        args = ["--state-dir", str(self.state), "social", "scheduled-sync"]
        self.responses()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main([*args, "run"]), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "succeeded")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main([*args, "status"]), 0)
        self.assertIsNotNone(json.loads(output.getvalue())["last_success"])
        for options in (["--max-requests", "1"], ["--deadline-seconds", "nan"],
                        ["--interval-days", "0"], ["--api-version", "bad"]):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([*args, "run", *options]), 2)


class TaskSchedulerTests(unittest.TestCase):
    SID = "S-1-5-21-111-222-333-1001"

    def xml(self, **kwargs):
        return task_xml(Path(r"C:\Program Files\Python\python.exe"),
                        Path(r"C:\Helm OS"), Path(r"C:\Helm OS\state data"),
                        kwargs.pop("sid", self.SID), kwargs.pop("config", SyncConfig()), **kwargs)

    def test_xml_paths_arguments_identity_and_privilege(self):
        xml = self.xml(config=SyncConfig(interval_days=2), at="23:15")
        root = ET.fromstring(xml)
        def value(tag):
            return root.find(".//{" + NAMESPACE + "}" + tag).text
        self.assertEqual(value("Command"), r"C:\Program Files\Python\python.exe")
        self.assertIn('"C:\\Helm OS\\state data"', value("Arguments"))
        self.assertIn("--interval-days 2", value("Arguments"))
        self.assertEqual(value("WorkingDirectory"), r"C:\Helm OS")
        self.assertEqual(value("UserId"), self.SID)
        self.assertEqual(value("LogonType"), "InteractiveToken")
        self.assertEqual(value("RunLevel"), "LeastPrivilege")
        self.assertEqual(value("MultipleInstancesPolicy"), "IgnoreNew")
        self.assertEqual(value("StartWhenAvailable"), "true")
        self.assertEqual(value("WakeToRun"), "false")
        self.assertEqual(value("ExecutionTimeLimit"), "PT15M")
        self.assertNotIn(MARKER, xml)

    def test_reject_system_other_service_accounts_relative_paths_and_invalid_time(self):
        for sid in ("S-1-5-18", "S-1-5-19", "S-1-5-20", "SYSTEM", "someone"):
            with self.assertRaises(ConnectorError):
                self.xml(sid=sid)
        for at in ("24:00", "9:00", "00:00; bad"):
            with self.assertRaises(ConnectorError):
                self.xml(at=at)
        with self.assertRaises(ConnectorError):
            task_xml(Path("python"), Path.cwd(), Path.cwd(), self.SID, SyncConfig())

    def test_management_only_uses_fixed_commands_without_shell_or_password(self):
        with patch("helmos.windows_tasks.subprocess.run") as command:
            command.return_value = Mock(returncode=0, stdout="safe output")
            for action in ("query", "last-run", "run", "disable", "remove"):
                result = manage_task(action, state_dir=Path.cwd(), repository=Path.cwd(), config=SyncConfig())
                self.assertEqual(result["action"], action)
                arguments = command.call_args.args[0]
                self.assertIn(TASK_NAME, arguments)
                self.assertFalse(command.call_args.kwargs["shell"])
                self.assertEqual(command.call_args.kwargs["timeout"], 30)
                self.assertNotIn("/RP", arguments)
            with self.assertRaises(ConnectorError):
                manage_task("arbitrary", state_dir=Path.cwd(), repository=Path.cwd(), config=SyncConfig())

    def test_install_uses_current_sid_absolute_interpreter_and_no_replace(self):
        def execute(arguments, **kwargs):
            if "/user" in arguments:
                return Mock(returncode=0, stdout='"user","' + self.SID + '"')
            self.assertIn("/Create", arguments)
            self.assertNotIn("/F", arguments)
            path = Path(arguments[-1])
            xml = path.read_text(encoding="utf-16")
            ET.fromstring(path.read_bytes())  # Declaration and on-disk encoding agree.
            self.assertIn(str(Path(sys.executable).resolve()), xml)
            self.assertIn(self.SID, xml)
            return Mock(returncode=0, stdout="")
        with patch("helmos.windows_tasks.subprocess.run", side_effect=execute) as command:
            result = manage_task("install", state_dir=Path.cwd() / "space data",
                                 repository=Path.cwd(), config=SyncConfig())
        self.assertEqual(result["status"], "registered")
        self.assertEqual(command.call_count, 2)

    def test_scheduler_failure_is_safe(self):
        with patch("helmos.windows_tasks.subprocess.run", side_effect=TimeoutError(MARKER)):
            with self.assertRaises(ConnectorError) as caught:
                manage_task("query", state_dir=Path.cwd(), repository=Path.cwd(), config=SyncConfig())
        self.assertNotIn(MARKER, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
