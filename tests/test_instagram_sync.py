from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from helmos.__main__ import main
from helmos.instagram_http import InstagramHTTPProvider
from helmos.instagram_sync import load_metadata, sync_instagram
from helmos.social_connectors import Account, ConnectorError, Response
from helmos.social_history import evolution, latest, observations
from helmos.social_import import SocialImportError, import_csv, list_observations, list_posts
from helmos import social_analyze


CAPTURE = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
MEDIA = {"id": "123", "timestamp": "2026-10-01T00:00:00+0000",
         "media_type": "VIDEO", "media_product_type": "REELS"}
IDENTITY = {"user_id": "456", "username": "JorgeBytes", "account_type": "BUSINESS"}
# Deliberately fictional values and credential marker; no live API in this module.
MARKER = "FICTIONAL-CREDENTIAL-DO-NOT-EXPOSE"


def listing(items=None, *, more=False, cursor="A"):
    payload = {"data": [dict(MEDIA)] if items is None else items}
    if more:
        payload["paging"] = {"next": "https://untrusted.invalid/?access_token=" + MARKER,
                             "cursors": {"after": cursor}}
    return Response(200, payload)


def metrics(value=7):
    return [Response(200, {"data": [{"name": name, "period": "lifetime",
                                    "values": [{"value": value}]}]})
            for name in ("views", "reach", "likes", "comments", "shares", "saved")]


def provider(*responses):
    stub = Mock()
    stub.account = Account("Instagram", "456")
    stub.request.side_effect = responses
    stub.requests_made = 0
    return stub


class FakeHTTPResponse:
    def __init__(self, payload, status=200, headers=None):
        self.status = status
        self.headers = headers or {}
        self.stream = io.BytesIO(payload if isinstance(payload, bytes) else json.dumps(payload).encode())

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read1(self, size):
        return self.stream.read(size)


class InstagramSyncTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.metadata = self.root / "editorial.json"
        self.metadata.write_text(json.dumps({"123": {"topic": "Python", "format": "video",
                                                    "editorial_piece_id": "example-piece"}}))

    def sync(self, stub=None, **kwargs):
        options = {"api_version": "v24.0", "expected_username": "JorgeBytes",
                   "metadata_path": self.metadata, "captured_at": CAPTURE,
                   "provider": stub or provider(listing(), *metrics())}
        options.update(kwargs)
        return sync_instagram(self.state, **options)

    def snapshot(self):
        return {str(path.relative_to(self.state)): path.read_bytes()
                for path in self.state.rglob("*") if path.is_file()}

    def test_preview_runs_preflight_without_creating_state(self):
        result = self.sync(preview=True)
        self.assertEqual(result["new_posts"], 1)
        self.assertEqual(result["observations_planned"], 1)
        self.assertEqual(result["observations_saved"], 0)
        self.assertEqual(result["preview_posts"][0]["metrics"]["views"], 7)
        self.assertFalse(self.state.exists())

    def test_new_pending_publication_preview_and_save_without_metadata(self):
        result = self.sync(metadata_path=None, preview=True)
        self.assertEqual(result["unclassified_posts"][0]["post_id"], "123")
        self.assertEqual(result["unclassified_posts"][0]["metrics"]["views"], 7)
        self.assertEqual(result["observations_planned"], 1)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.sync(metadata_path=None)["observations_saved"], 1)
        post = list_posts(self.state)[0]
        self.assertEqual(post["classification_status"], "pending")
        self.assertIsNone(post["topic"])
        self.assertIsNone(post["format"])
        self.assertIsNone(post["initial_observation"]["provenance"]["editorial_piece_id"])
        original = self.snapshot()
        self.assertEqual(self.sync(metadata_path=None)["unchanged_posts"], 1)
        preview = self.sync(provider(listing(), *metrics(9)), metadata_path=None, preview=True,
                            captured_at=CAPTURE + timedelta(hours=1))
        self.assertEqual(preview["existing_posts"], 1)
        self.assertEqual(preview["observations_planned"], 1)
        self.assertEqual(len(preview["unclassified_posts"]), 1)
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(self.sync(provider(listing(), *metrics(9)), metadata_path=None,
                                   captured_at=CAPTURE + timedelta(hours=1))["observations_saved"], 1)
        self.assertEqual(len(list_observations(self.state, post)), 2)
        self.assertEqual(latest(self.state, post)["measurements"][0]["metrics"]["views"]["value"], 9)
        for name, content in original.items():
            self.assertEqual((self.state / name).read_bytes(), content)

    def test_csv_classification_without_piece_identity_is_reused(self):
        csv_path = self.root / "posts.csv"
        csv_path.write_text("platform,post_id,published_at,format,topic,observed_at,measurement_type,views\n"
                            "Instagram,123,2026-10-01,reel,Existing topic,2026-10-25,cumulative,5\n")
        import_csv(csv_path, self.state)
        original = self.snapshot()
        result = self.sync(metadata_path=None, preview=True)
        self.assertEqual(result["existing_posts"], 1)
        self.assertEqual(result["observations_planned"], 1)
        self.assertEqual(result["unclassified_posts"], [])
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(self.sync(metadata_path=None)["observations_saved"], 1)
        self.assertEqual(self.sync(provider(listing(), *metrics(9)), metadata_path=None,
                                   captured_at=CAPTURE + timedelta(hours=1))["observations_saved"], 1)
        post = list_posts(self.state)[0]
        self.assertEqual((post["topic"], post["format"]), ("Existing topic", "reel"))
        self.assertIsNone(observations(self.state, post)[-1]["provenance"]["editorial_piece_id"])
        self.assertEqual(social_analyze.prepare_dataset(self.state)["posts"][0]["topic"], "Existing topic")
        self.assertEqual(import_csv(csv_path, self.state)["already_present"], 1)
        for name, content in original.items():
            self.assertEqual((self.state / name).read_bytes(), content)
        self.metadata.write_text(json.dumps({"123": {"topic": "Existing topic", "format": "reel",
                                                    "editorial_piece_id": "reviewed-piece"}}))
        self.sync(provider(listing(), *metrics(10)), captured_at=CAPTURE + timedelta(days=1))
        self.assertEqual(self.sync(metadata_path=None, captured_at=CAPTURE + timedelta(days=2))[
            "observations_saved"], 1)
        saved = self.snapshot()
        self.metadata.write_text(self.metadata.read_text().replace("reviewed-piece", "other-piece"))
        with self.assertRaises(SocialImportError):
            self.sync(preview=True, captured_at=CAPTURE + timedelta(days=3))
        self.assertEqual(self.snapshot(), saved)

    def test_pending_classification_analyze_keeps_metrics_and_reports_unknowns(self):
        self.sync(metadata_path=None)
        answer = {
            "findings": [{"claim": "Hay métricas observadas.", "kind": "observation",
                          "post_refs": ["P001"], "metric_evidence": []}],
            "limitations": ["Clasificación pendiente."],
            "recommendations": [{"action": "Revisar clasificación.", "rationale": "Falta contexto editorial.",
                                 "post_refs": ["P001"]}],
            "calendar": [{"slot": "Pendiente de revisión", "platform": "Instagram",
                          "topic": "Por decidir", "format": "Por decidir",
                          "rationale": "Revisar contexto antes de planificar.", "post_refs": ["P001"]}],
        }
        with patch.object(social_analyze, "run_structured", return_value={
                "result": answer, "consumption": {}, "thread_id": "fake"}) as ai:
            result = social_analyze.analyze(self.state, "pending-test")
        dataset = result["result"]["dataset"]
        self.assertIsNone(dataset["posts"][0]["topic"])
        self.assertEqual(dataset["coverage"]["Instagram"]["metrics"]["views"]["sum"], 7)
        self.assertTrue(any("clasificación editorial pendiente" in item for item in dataset["temporal_limitations"]))
        self.assertIn("never infer", ai.call_args.args[0])
        self.assertIn("tema pendiente", Path(result["result"]["report_path"]).read_text(encoding="utf-8"))

    def test_mixed_batch_classified_and_pending_both_prepare_observations(self):
        self.sync()
        original = self.snapshot()
        result = self.sync(provider(listing([MEDIA, dict(MEDIA, id="124")]), *metrics(8), *metrics(9)),
                           metadata_path=None, captured_at=CAPTURE + timedelta(hours=1))
        self.assertEqual((result["existing_posts"], result["new_posts"]), (1, 1))
        self.assertEqual(result["observations_saved"], 2)
        self.assertEqual([post["post_id"] for post in result["unclassified_posts"]], ["124"])
        for name, content in original.items():
            self.assertEqual((self.state / name).read_bytes(), content)

    def test_absence_null_zero_and_partial_metric_errors(self):
        responses = metrics()
        responses[0] = Response(200, {"data": []})
        responses[1] = Response(200, {"data": [{"name": "reach", "period": "lifetime", "values": [{"value": None}]}]})
        responses[2] = Response(400, {"error": {"code": 100, "message": MARKER}})
        responses[3] = Response(403, {"error": {"message": MARKER}})
        responses[4:] = metrics(0)[4:]
        result = self.sync(provider(listing(), *responses))
        self.assertEqual(len(result["partial_errors"]), 2)
        post = list_posts(self.state)[0]
        self.assertEqual(post["metrics"], {"shares": 0, "saves": 0})
        self.assertNotIn(MARKER, json.dumps(result) + json.dumps(post))

    def test_pagination_rebuilds_safe_path_and_deduplicates(self):
        second = dict(MEDIA, id="124")
        stub = provider(listing(more=True), *metrics(), listing([dict(MEDIA), second]), *metrics(8))
        result = self.sync(stub, preview=True, max_pages=3)
        self.assertEqual(result["posts_consulted"], 2)
        self.assertEqual(result["duplicates_skipped"], 1)
        self.assertEqual(stub.request.call_args_list[7].kwargs["params"]["after"], "A")
        self.assertNotIn(MARKER, str(stub.request.call_args_list) + json.dumps(result))

    def test_request_page_and_post_limits_are_explicit(self):
        result = self.sync(provider(listing(more=True), *metrics()), preview=True)
        self.assertTrue(result["bounded_listing"])
        result = self.sync(provider(listing([dict(MEDIA), dict(MEDIA, id="124")]), *metrics()),
                           preview=True, max_posts=1)
        self.assertTrue(result["bounded_listing"])
        with self.assertRaisesRegex(ConnectorError, "request_budget_exceeded"):
            self.sync(max_requests=2)
        self.assertFalse(self.state.exists())

    def test_repeated_cursor_and_conflicting_duplicate_abort_without_writes(self):
        for tail in (listing([], more=True), listing([dict(MEDIA, timestamp="2026-10-02T00:00:00Z")])):
            with self.subTest(tail=tail), self.assertRaises(ConnectorError):
                self.sync(provider(listing(more=True), *metrics(), tail), max_pages=3)
        self.assertFalse(self.state.exists())

    def test_global_failures_abort_even_after_successful_metrics(self):
        for response, code in ((Response(400, {"error": {"code": 190, "message": MARKER}}), "authentication_required"),
                               (Response(401, {}), "authentication_required"),
                               (Response(429, {}, {"Retry-After": "45"}), "rate_limited"),
                               (Response(400, {"error": {"code": 4, "message": MARKER}}), "rate_limited"),
                               (TimeoutError(MARKER), "provider_failure")):
            with self.subTest(code=code), self.assertRaisesRegex(ConnectorError, code) as caught:
                self.sync(provider(listing(), metrics()[0], response))
            self.assertNotIn(MARKER, str(caught.exception))
            self.assertFalse(self.state.exists())

    def test_late_listing_failure_does_not_persist_partial_batch(self):
        with self.assertRaisesRegex(ConnectorError, "http_error"):
            self.sync(provider(listing(more=True), *metrics(), Response(500, {"message": MARKER})), max_pages=2)
        self.assertFalse(self.state.exists())

    def test_unsupported_media_and_bad_scalar_are_not_invented(self):
        with self.assertRaisesRegex(ConnectorError, "unsupported_media_type"):
            self.sync(provider(listing([dict(MEDIA, media_product_type="STORY")])))
        bad = metrics()
        bad[0] = metrics(True)[0]
        result = self.sync(provider(listing(), *bad))
        self.assertEqual(result["partial_errors"][0]["code"], "invalid_metric")
        self.assertNotIn("views", list_posts(self.state)[0]["metrics"])

    def test_same_day_changes_reversions_ordering_and_existing_metadata_reuse(self):
        self.sync()
        original = self.snapshot()
        self.assertEqual(self.sync(metadata_path=None)["unchanged_posts"], 1)
        self.assertEqual(self.sync(provider(listing(), *metrics(9)), metadata_path=None,
                                   captured_at=CAPTURE + timedelta(hours=1))["observations_saved"], 1)
        self.assertEqual(self.sync(provider(listing(), *metrics(7)), metadata_path=None,
                                   captured_at=CAPTURE + timedelta(hours=2))["observations_saved"], 1)
        post = list_posts(self.state)[0]
        self.assertEqual(len(list_observations(self.state, post)), 3)
        self.assertEqual([point["change"] for point in evolution(self.state, post)["metrics"]["views"]], [None, 2, -2])
        self.assertEqual(latest(self.state, post)["measurements"][0]["metrics"]["views"]["value"], 7)
        self.assertEqual(observations(self.state, post)[-1]["provenance"]["captured_at"],
                         (CAPTURE + timedelta(hours=2)).isoformat())
        for name, content in original.items():
            self.assertEqual((self.state / name).read_bytes(), content)

    def test_timestamp_metadata_and_account_conflicts_never_overwrite(self):
        self.sync()
        original = self.snapshot()
        with self.assertRaises(SocialImportError):
            self.sync(provider(listing(), *metrics(9)))
        bad_account = provider(listing(), *metrics())
        bad_account.account = Account("Instagram", "999")
        with self.assertRaises(SocialImportError):
            self.sync(bad_account)
        data = json.loads(self.metadata.read_text())
        data["123"]["topic"] = "Conflicting topic"
        self.metadata.write_text(json.dumps(data))
        with self.assertRaises(SocialImportError):
            self.sync(preview=True)
        self.assertEqual(self.snapshot(), original)

    def test_next_day_observation_and_preview_preserve_existing_files(self):
        self.sync()
        original = self.snapshot()
        self.sync(provider(listing(), *metrics(8)), preview=True,
                  captured_at=CAPTURE + timedelta(days=1))
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(self.sync(provider(listing(), *metrics(8)),
                                   captured_at=CAPTURE + timedelta(days=1))["observations_saved"], 1)

    def test_metadata_loader_sanitizes_invalid_json_and_schema(self):
        for content in (MARKER, '[]', '{"123": {"topic": "x"}}'):
            self.metadata.write_text(content)
            with self.assertRaisesRegex(ConnectorError, "invalid_metadata_file") as caught:
                load_metadata(self.metadata)
            self.assertNotIn(MARKER, str(caught.exception))

    def test_cli_preview_through_real_transport_contract(self):
        connection = Mock()
        connection.getresponse.side_effect = [FakeHTTPResponse(IDENTITY), FakeHTTPResponse(listing().body),
                                             *(FakeHTTPResponse(item.body) for item in metrics())]
        output = io.StringIO()
        with patch.dict("os.environ", {"HELMOS_INSTAGRAM_ACCESS_TOKEN": MARKER}), \
                patch("helmos.instagram_http.http.client.HTTPSConnection", return_value=connection), \
                contextlib.redirect_stdout(output):
            code = main(["--state-dir", str(self.state), "social", "sync", "instagram", "--preview",
                         "--api-version", "v24.0", "--expected-username", "JorgeBytes", "--metadata", str(self.metadata)])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(result["requests_made"], 8)
        self.assertNotIn(MARKER, output.getvalue())
        self.assertFalse(self.state.exists())

    def test_cli_transport_to_storage_and_safe_fatal_error(self):
        connection = Mock()
        connection.getresponse.side_effect = [FakeHTTPResponse(IDENTITY), FakeHTTPResponse(listing().body),
                                             *(FakeHTTPResponse(item.body) for item in metrics())]
        args = ["--state-dir", str(self.state), "social", "sync", "instagram",
                "--api-version", "v24.0", "--expected-username", "JorgeBytes", "--metadata", str(self.metadata)]
        output, errors = io.StringIO(), io.StringIO()
        with patch.dict("os.environ", {"HELMOS_INSTAGRAM_ACCESS_TOKEN": MARKER}), \
                patch("helmos.instagram_http.http.client.HTTPSConnection", return_value=connection), \
                contextlib.redirect_stdout(output):
            self.assertEqual(main(args), 0)
        self.assertEqual(json.loads(output.getvalue())["observations_saved"], 1)
        original = self.snapshot()
        self.assertEqual(list_posts(self.state)[0]["metrics"]["views"], 7)
        connection.getresponse.side_effect = [FakeHTTPResponse({"error": {"code": 190, "message": MARKER}}, 400)]
        with patch.dict("os.environ", {"HELMOS_INSTAGRAM_ACCESS_TOKEN": MARKER}), \
                patch("helmos.instagram_http.http.client.HTTPSConnection", return_value=connection), \
                contextlib.redirect_stderr(errors):
            self.assertEqual(main(args), 2)
        self.assertNotIn(MARKER, errors.getvalue() + output.getvalue() + str(self.snapshot()))
        self.assertEqual(json.loads(errors.getvalue())["error"], "authentication_required")
        self.assertEqual(self.snapshot(), original)

    def test_feed_media_only_requests_the_supported_scalar_set(self):
        for kind in ("IMAGE", "VIDEO", "CAROUSEL_ALBUM"):
            stub = provider(listing([dict(MEDIA, media_product_type="FEED", media_type=kind)]), *metrics())
            self.sync(stub, preview=True)
            self.assertEqual([call.kwargs["params"]["metric"] for call in stub.request.call_args_list[1:]],
                             ["views", "reach", "likes", "comments", "shares", "saved"])
        with self.assertRaisesRegex(ConnectorError, "unsupported_media_type"):
            self.sync(provider(listing([dict(MEDIA, media_type="IMAGE")])), preview=True)

    def test_rate_limit_retry_after_and_unsafe_provider_error_are_safe(self):
        with self.assertRaises(ConnectorError) as caught:
            self.sync(provider(listing(), Response(429, {"message": MARKER}, {"Retry-After": "30"})))
        self.assertEqual(caught.exception.retry_after, 30)
        with self.assertRaisesRegex(ConnectorError, "provider_failure") as caught:
            self.sync(provider(listing(), ConnectorError(MARKER)))
        self.assertNotIn(MARKER, str(caught.exception))
        self.assertFalse(self.state.exists())


class InstagramTransportTests(unittest.TestCase):
    def build(self, response=None, **options):
        self.connection = Mock()
        self.connection.getresponse.return_value = response or FakeHTTPResponse(IDENTITY)
        with patch.dict("os.environ", {"HELMOS_INSTAGRAM_ACCESS_TOKEN": MARKER}), \
                patch("helmos.instagram_http.http.client.HTTPSConnection", return_value=self.connection) as factory:
            result = InstagramHTTPProvider(api_version="v24.0", expected_username="JorgeBytes", **options)
        self.factory = factory
        return result

    def test_host_header_timeout_and_no_token_in_url_or_repr(self):
        transport = self.build()
        self.factory.assert_called_once_with("graph.instagram.com", timeout=10)
        args, kwargs = self.connection.request.call_args
        self.assertEqual(args[0], "GET")
        self.assertNotIn(MARKER, args[1])
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + MARKER)
        self.assertEqual(transport.account, Account("Instagram", "456"))
        self.assertNotIn(MARKER, repr(transport))
        self.connection.close.assert_called_once()

    def test_identity_and_access_failures_are_sanitized(self):
        cases = [(FakeHTTPResponse(dict(IDENTITY, username="another")), "instagram_account_mismatch"),
                 (FakeHTTPResponse(dict(IDENTITY, account_type="PERSONAL")), "professional_account_required"),
                 (FakeHTTPResponse({"error": {"code": 190, "message": MARKER}}, 400), "authentication_required"),
                 (FakeHTTPResponse({"message": MARKER}, 403), "access_denied"),
                 (FakeHTTPResponse(b"not json " + MARKER.encode(), 500), "http_error"),
                 (FakeHTTPResponse(b"not json " + MARKER.encode()), "invalid_json"),
                 (FakeHTTPResponse({}, 302, {"Location": "https://evil.invalid/" + MARKER}), "redirect_rejected"),
                 (FakeHTTPResponse(IDENTITY, headers={"Instagram-API-Version": "v25.0"}), "api_version_mismatch")]
        for response, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(ConnectorError, code) as caught:
                self.build(response)
            self.assertNotIn(MARKER, str(caught.exception))

    def test_response_size_timeout_deadline_and_request_budget(self):
        with patch.object(InstagramHTTPProvider, "MAX_RESPONSE_BYTES", 10), \
                self.assertRaisesRegex(ConnectorError, "response_too_large"):
            self.build()
        transport = self.build(max_requests=2)
        with patch("helmos.instagram_http.http.client.HTTPSConnection", side_effect=TimeoutError(MARKER)), \
                self.assertRaisesRegex(ConnectorError, "transport_failure"):
            transport.request("GET", "/v24.0/456/media", params={})
        with self.assertRaisesRegex(ConnectorError, "request_budget_exceeded"):
            transport.request("GET", "/v24.0/456/media", params={})
        transport = self.build()
        with patch("helmos.instagram_http.time.monotonic", return_value=float("inf")), \
                self.assertRaisesRegex(ConnectorError, "deadline_exceeded"):
            transport.request("GET", "/v24.0/456/media", params={})

    def test_missing_token_invalid_limits_and_disallowed_requests_use_no_network(self):
        with patch.dict("os.environ", {"HELMOS_INSTAGRAM_ACCESS_TOKEN": ""}), \
                self.assertRaisesRegex(ConnectorError, "instagram_token_required"):
            InstagramHTTPProvider(api_version="v24.0", expected_username="JorgeBytes")
        for options in ({"timeout": 0}, {"timeout": float("nan")}, {"max_requests": 1}, {"deadline_seconds": 601}):
            with self.subTest(options=options), self.assertRaises(ConnectorError):
                self.build(**options)
        transport = self.build()
        for method, path, params in (("POST", "/v24.0/456/media", {}),
                                     ("GET", "https://evil.invalid/", {}),
                                     ("GET", "/v24.0/456/media", {"access_token": MARKER}),
                                     ("GET", "/v24.0/999/media", {}),
                                     ("GET", "/v24.0/999/insights", {}),
                                     ("GET", "/v24.0/456/messages", {})):
            with self.subTest(path=path), self.assertRaisesRegex(ConnectorError, "instagram_request_not_allowed"):
                transport.request(method, path, params=params)


if __name__ == "__main__":
    unittest.main()
