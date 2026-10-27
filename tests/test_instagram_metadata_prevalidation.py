"""Metadata prevalidation through fake HTTP; no live secrets or scheduler."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from helmos.__main__ import main
from helmos.social_import import import_csv
from helmos.instagram_credentials import InstagramCredentials
from test_instagram_sync import FakeHTTPResponse, IDENTITY, MEDIA, MARKER, listing


class InstagramMetadataPrevalidationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.connection = Mock()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"HELMOS_INSTAGRAM_ACCESS_TOKEN": MARKER}).start()
        patch("helmos.instagram_credentials.WindowsCredentialStore",
              side_effect=AssertionError("real credentials forbidden")).start()
        patch("helmos.instagram_http.http.client.HTTPSConnection",
              return_value=self.connection).start()
        patch("helmos.windows_tasks.subprocess.run",
              side_effect=AssertionError("scheduler forbidden")).start()

    def history(self):
        path = self.root / "history.csv"
        path.write_text("platform,post_id,published_at,format,topic,views\n"
                        "Instagram,123,2026-10-01,video,Reviewed,5\n"
                        "Instagram,124,2026-10-02,video,Reviewed,7\n", encoding="utf-8")
        import_csv(path, self.state)

    def snapshot(self):
        return {str(p.relative_to(self.state)): p.read_bytes()
                for p in self.state.rglob("*") if p.is_file()}

    def run_prevalidation(self, pages, *options):
        self.connection.getresponse.side_effect = [FakeHTTPResponse(IDENTITY),
                                                  *(FakeHTTPResponse(p.body) for p in pages)]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--state-dir", str(self.state), "social", "sync", "instagram",
                         "--prevalidate-metadata", "--api-version", "v26.0",
                         "--expected-username", "JorgeBytes", *options])
        self.assertNotIn(MARKER, output.getvalue())
        for call in self.connection.request.call_args_list:
            self.assertNotIn("/insights", call.args[1])
        return code, json.loads(output.getvalue())

    def test_all_conflicts_across_pages_are_reported_without_insights_or_writes(self):
        self.history()
        original = self.snapshot()
        pages = [listing([dict(MEDIA, timestamp="2026-10-01T00:30:00+02:00")], more=True),
                 listing([dict(MEDIA, id="124", timestamp="2026-10-03T12:00:00Z")])]
        code, report = self.run_prevalidation(pages, "--max-pages", "2")
        self.assertEqual(code, 2)
        self.assertEqual(report["conflicting_posts"], 2)
        self.assertEqual(report["conflict_count"], 2)
        self.assertEqual(report["requests_made"], 3)  # Identity and two media pages.
        first, second = report["conflicts"]
        self.assertEqual(first["post_id"], "123")
        self.assertEqual(first["historical"]["value"], "2026-10-01")
        self.assertEqual(first["historical"]["source"], "csv")
        self.assertEqual(first["received"]["value"], "2026-09-30")
        self.assertTrue(first["difference"]["offset_changes_received_day"])
        self.assertEqual(first["difference"]["timezone_cause"], "undetermined")
        self.assertFalse(second["difference"]["offset_changes_received_day"])
        self.assertEqual(self.snapshot(), original)

    def test_clear_utc_equivalent_and_new_posts_do_not_create_social_state(self):
        code, report = self.run_prevalidation([listing()])
        self.assertEqual((code, report["status"]), (0, "clear"))
        self.assertEqual(report["conflicts"], [])
        self.assertFalse(self.state.exists())

    def test_same_utc_day_is_not_a_conflict(self):
        self.history()
        code, report = self.run_prevalidation([
            listing([dict(MEDIA, timestamp="2026-10-02T00:30:00+02:00")])])
        self.assertEqual(code, 0)
        self.assertEqual(report["conflicts"], [])

    def test_partial_window_reports_coverage_and_never_claims_complete_success(self):
        self.history()
        code, report = self.run_prevalidation([listing(more=True)])
        self.assertEqual((code, report["status"]), (1, "partial"))
        self.assertTrue(report["bounded_listing"])
        self.assertEqual(report["posts_consulted"], 1)
        self.assertEqual(report["insights_requested"], 0)

    def test_post_limit_and_duplicates_reuse_listing_rules(self):
        code, report = self.run_prevalidation([
            listing([MEDIA, MEDIA, dict(MEDIA, id="124")])], "--max-posts", "1")
        self.assertEqual(code, 1)
        self.assertTrue(report["bounded_listing"])
        self.assertEqual(report["posts_consulted"], 1)

    def test_editorial_file_conflicts_are_reported_together_with_dates(self):
        self.history()
        metadata = self.root / "metadata.json"
        metadata.write_text(json.dumps({"123": {"topic": "Changed", "format": "reel",
                                               "editorial_piece_id": "piece"}}))
        code, report = self.run_prevalidation([
            listing([dict(MEDIA, timestamp="2026-10-03T00:00:00Z")])],
            "--metadata", str(metadata))
        self.assertEqual(code, 2)
        self.assertEqual({c["field"] for c in report["conflicts"]}, {"published_at", "topic", "format"})
        self.assertEqual(report["conflicting_posts"], 1)

    def test_authentication_error_does_not_become_clear_report(self):
        self.connection.getresponse.side_effect = [FakeHTTPResponse(IDENTITY),
            FakeHTTPResponse({"error": {"code": 190, "message": MARKER}}, status=400)]
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = main(["--state-dir", str(self.state), "social", "sync", "instagram",
                         "--prevalidate-metadata", "--api-version", "v26.0",
                         "--expected-username", "JorgeBytes"])
        self.assertEqual(code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn(MARKER, error.getvalue())
        self.assertFalse(self.state.exists())

    def test_read_only_credentials_never_refresh_or_save_even_when_due(self):
        store = Mock()
        store.transaction.return_value = contextlib.nullcontext()
        store.load.return_value = {"token": MARKER, "kind": "dashboard", "username": "jorgebytes"}
        self.connection.getresponse.side_effect = [FakeHTTPResponse(IDENTITY)]
        with patch.dict(os.environ, {"HELMOS_INSTAGRAM_ACCESS_TOKEN": ""}), \
                patch.object(InstagramCredentials, "_reason", return_value="due"):
            manager = InstagramCredentials(api_version="v26.0", expected_username="jorgebytes", store=store)
            manager.prepare(read_only=True)
        store.save.assert_not_called()
        self.assertEqual(self.connection.request.call_count, 1)

    def test_incomplete_publication_timestamp_fails_without_insights(self):
        self.connection.getresponse.side_effect = [FakeHTTPResponse(IDENTITY),
            FakeHTTPResponse(listing([dict(MEDIA, timestamp="2026-10-01T12:00:00")]).body)]
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = main(["--state-dir", str(self.state), "social", "sync", "instagram",
                         "--prevalidate-metadata", "--api-version", "v26.0",
                         "--expected-username", "JorgeBytes"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(error.getvalue())["error"], "invalid_publication_date")
        self.assertEqual(self.connection.request.call_count, 2)
        self.assertFalse(self.state.exists())
