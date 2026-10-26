"""Only fictional secrets, fake HTTPS and isolated credential/state directories."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import tempfile
import traceback
import unittest
from datetime import timedelta
from http.client import HTTPSConnection
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from helmos.__main__ import main
from helmos.credential_store import WindowsCredentialStore, _dpapi
from helmos.instagram_credentials import InstagramCredentials, secure_input
from helmos.instagram_http import InstagramHTTPProvider
from helmos.instagram_sync import sync_instagram
from helmos.social_connectors import ConnectorError
from test_instagram_sync import CAPTURE, IDENTITY, MARKER, FakeHTTPResponse, MEDIA


NEW = "FICTIONAL-REPLACEMENT-SECRET"


class MemoryStore:
    def __init__(self, record=None):
        self.record = copy.deepcopy(record)
        self.fail = False
        self.saves = 0

    def load(self):
        return copy.deepcopy(self.record)

    def save(self, record):
        if self.fail:
            raise ConnectorError("credential_write_failed")
        self.record = copy.deepcopy(record)
        self.saves += 1

    @contextlib.contextmanager
    def transaction(self):
        yield


def credential(**changes):
    record = {"token": MARKER, "kind": "long-lived", "account_id": "456",
              "username": "JorgeBytes", "issued_at": (CAPTURE - timedelta(days=55)).isoformat(),
              "observed_at": (CAPTURE - timedelta(days=10)).isoformat(),
              "expires_at": (CAPTURE + timedelta(days=5)).isoformat(), "expiry_source": "operator"}
    return dict(record, **changes)


def refreshed(**changes):
    return dict({"access_token": NEW, "token_type": "bearer", "expires_in": 5184000}, **changes)


class CredentialLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"HELMOS_INSTAGRAM_ACCESS_TOKEN": ""})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.store = MemoryStore(credential())
        self.connection = Mock()
        self.network = patch("helmos.instagram_http.http.client.HTTPSConnection", return_value=self.connection)
        self.factory = self.network.start()
        self.addCleanup(self.network.stop)
        self.manager = InstagramCredentials(api_version="v26.0", expected_username="JorgeBytes",
                                            store=self.store, clock=lambda: CAPTURE)

    def responses(self, *payloads):
        self.connection.getresponse.side_effect = [
            item if isinstance(item, (FakeHTTPResponse, Exception)) else FakeHTTPResponse(item)
            for item in payloads]

    def test_status_offline_exposes_only_safe_metadata_and_unknowns(self):
        self.store.record = credential(expires_at=None, issued_at=None)
        result = self.manager.status()
        self.assertIsNone(result["expires_at"])
        self.assertIsNone(result["issued_at"])
        self.assertEqual(result["authentication"], "not_checked")
        self.assertNotIn(MARKER, json.dumps(result))
        self.connection.request.assert_not_called()
        self.assertEqual(self.store.saves, 0)

    def test_absent_and_expired_do_not_use_network(self):
        self.store.record = None
        self.assertEqual(self.manager.status()["source"], "absent")
        with self.assertRaisesRegex(ConnectorError, "instagram_token_required"):
            self.manager.check()
        self.store.record = credential(expires_at=CAPTURE.isoformat())
        self.assertTrue(self.manager.status()["reauthorization_required"])
        with self.assertRaisesRegex(ConnectorError, "expired_reauthorize"):
            self.manager.refresh()
        self.connection.request.assert_not_called()

    def test_valid_check_does_not_refresh_even_when_due(self):
        self.responses(IDENTITY)
        result = self.manager.check()
        self.assertEqual(result["authentication"], "valid_now")
        self.assertEqual(result["refresh_reason"], "due")
        self.assertEqual(self.connection.request.call_count, 1)
        self.assertEqual(self.store.record["token"], MARKER)

    def test_due_refresh_is_atomic_validates_new_identity_and_is_idempotent(self):
        self.responses(IDENTITY, refreshed(), IDENTITY, IDENTITY)
        provider, result = self.manager.prepare()
        self.assertEqual(result["action"], "refreshed")
        self.assertEqual(provider.requests_made, 3)
        self.assertEqual(self.store.record["token"], NEW)
        self.assertEqual(self.store.record["expires_at"], (CAPTURE + timedelta(days=60)).isoformat())
        self.assertEqual(self.store.record["expiry_source"], "meta_refresh")
        self.assertEqual(self.manager.refresh()["action"], "checked")
        self.assertEqual(self.connection.request.call_count, 4)
        self.assertNotIn(NEW, repr(provider) + json.dumps(result))
        request = self.connection.request.call_args_list[1]
        self.assertEqual(request.args[0], "GET")
        self.assertEqual(urlsplit(request.args[1]).path, "/refresh_access_token")
        self.assertEqual(parse_qs(urlsplit(request.args[1]).query),
                         {"grant_type": ["ig_refresh_token"], "access_token": [MARKER]})
        self.assertNotIn("Authorization", request.kwargs["headers"])
        for request in (self.connection.request.call_args_list[0],
                        *self.connection.request.call_args_list[2:]):
            self.assertNotIn(MARKER, request.args[1])
            self.assertNotIn(NEW, request.args[1])

    def test_nonrenewable_too_young_and_not_due_are_noops(self):
        cases = [(credential(kind="unknown"), "long_lived_token_not_established"),
                 (credential(kind="short-lived"), "long_lived_token_not_established"),
                 (credential(issued_at=CAPTURE.isoformat()), "minimum_age_not_reached"),
                 (credential(expires_at=(CAPTURE + timedelta(days=8)).isoformat()), "not_due")]
        for record, reason in cases:
            with self.subTest(reason=reason):
                self.store.record = record
                self.responses(IDENTITY)
                result = self.manager.refresh()
                self.assertEqual(result["action"], "checked")
                self.assertEqual(result["refresh_reason"], reason)
                self.assertEqual(self.store.record["token"], MARKER)

    def test_refresh_query_encodes_secret_without_changing_read_authentication(self):
        fictional = "FICTIONAL+/&?=%#TOKEN"
        self.store.record = credential(token=fictional)
        self.responses(IDENTITY, refreshed(), IDENTITY)
        self.manager.refresh()
        calls = self.connection.request.call_args_list
        target = urlsplit(calls[1].args[1])
        self.assertEqual((target.scheme, target.netloc, target.fragment), ("", "", ""))
        self.assertEqual(target.path, "/refresh_access_token")
        self.assertEqual(parse_qs(target.query),
                         {"grant_type": ["ig_refresh_token"], "access_token": [fictional]})
        self.assertEqual(calls[1].kwargs["headers"], {"Accept": "application/json"})
        for call, token in ((calls[0], fictional), (calls[2], NEW)):
            self.assertNotIn("access_token", call.args[1])
            self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer " + token)
        self.assertEqual(self.factory.call_count, 3)
        for call in self.factory.call_args_list:
            self.assertEqual(call.args, ("graph.instagram.com",))
        self.assertEqual(self.connection.set_debuglevel.call_args_list,
                         [unittest.mock.call(0)] * 3)

    def test_refresh_redirects_are_never_followed_or_exposed(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.connection.reset_mock()
                original = copy.deepcopy(self.store.record)
                self.responses(IDENTITY, FakeHTTPResponse(
                    {"message": MARKER}, status,
                    {"Location": "https://evil.invalid/?access_token=" + MARKER}))
                with self.assertNoLogs(level="DEBUG"), \
                        self.assertRaisesRegex(ConnectorError, "redirect_rejected") as caught:
                    self.manager.refresh()
                self.assertEqual(self.connection.request.call_count, 2)
                self.assertEqual(self.store.record, original)
                self.assertNotIn(MARKER, "".join(traceback.format_exception(caught.exception)))
                self.assertEqual(self.store.saves, 0)

    def test_secret_bearing_transport_failures_have_no_exception_context(self):
        for stage in ("request", "getresponse", "read1", "close"):
            with self.subTest(stage=stage):
                self.connection.reset_mock(side_effect=True)
                self.responses(IDENTITY, refreshed())
                original = copy.deepcopy(self.store.record)
                error = OSError("https://graph.instagram.com/refresh_access_token?access_token=" + MARKER)
                if stage == "read1":
                    response = FakeHTTPResponse(refreshed())
                    response.read1 = Mock(side_effect=error)
                    self.responses(IDENTITY, response)
                else:
                    getattr(self.connection, stage).side_effect = [None, error]
                    if stage == "getresponse":
                        self.connection.getresponse.side_effect = [FakeHTTPResponse(IDENTITY), error]
                output, errors = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), \
                        self.assertNoLogs(level="DEBUG"), \
                        self.assertRaisesRegex(ConnectorError, "transport_failure") as caught:
                    self.manager.refresh()
                self.assertIsNone(caught.exception.__context__)
                self.assertIsNone(caught.exception.__cause__)
                exposed = output.getvalue() + errors.getvalue() + repr(caught.exception)
                exposed += "".join(traceback.format_exception(caught.exception))
                self.assertNotIn(MARKER, exposed)
                self.assertEqual(self.store.record, original)
                self.assertEqual(self.store.saves, 0)

    def test_real_http_serialization_disables_debug_and_uses_only_relative_target(self):
        # Exercise stdlib serialization with a fake socket: no DNS/TLS/network.
        self.network.stop()
        connections = iter([HTTPSConnection("graph.instagram.com", timeout=10)
                            for _ in range(3)])
        sockets = []
        responses = iter((IDENTITY, refreshed(), IDENTITY))

        def offline_connection(host, **kwargs):
            self.assertEqual(host, "graph.instagram.com")
            connection = next(connections)
            connection.set_debuglevel(1)
            connection.sock = Mock()
            sockets.append(connection.sock)
            connection.getresponse = Mock(return_value=FakeHTTPResponse(next(responses)))
            return connection

        output, errors = io.StringIO(), io.StringIO()
        with patch("helmos.instagram_http.http.client.HTTPSConnection", side_effect=offline_connection), \
                patch.dict(os.environ, {"HTTPS_PROXY": "http://evil.invalid:8080"}), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), \
                self.assertNoLogs(level="DEBUG"):
            self.manager.refresh()
        self.assertEqual(output.getvalue() + errors.getvalue(), "")
        wire = [b"".join(call.args[0] for call in sock.sendall.call_args_list)
                for sock in sockets]
        self.assertIn(b"GET /refresh_access_token?grant_type=ig_refresh_token&access_token="
                      + MARKER.encode() + b" HTTP/1.1\r\n", wire[1])
        self.assertIn(b"Host: graph.instagram.com\r\n", wire[1])
        self.assertNotIn(b"Authorization:", wire[1])
        for request in wire:
            self.assertNotIn(b"https://", request)
            self.assertNotIn(b"evil.invalid", request)
        self.assertEqual(self.store.record["token"], NEW)

    def test_public_transport_cannot_supply_refresh_tokens_or_arbitrary_targets(self):
        self.responses(IDENTITY)
        provider = self.manager.prepare(refresh=False)[0]
        for path, params in (("/refresh_access_token", {"grant_type": "ig_refresh_token"}),
                             ("https://evil.invalid/refresh_access_token", {}),
                             ("/v26.0/me", {"access_token": MARKER})):
            with self.subTest(path=path), \
                    self.assertRaisesRegex(ConnectorError, "instagram_request_not_allowed"):
                provider.request("GET", path, params=params)
        self.assertEqual(self.connection.request.call_count, 1)

    def test_unknown_expiry_requires_controlled_refresh_and_observation_age(self):
        self.store.record = credential(kind="dashboard", expires_at=None, issued_at=None)
        self.responses(IDENTITY, IDENTITY, refreshed(), IDENTITY)
        self.assertEqual(self.manager.prepare()[1]["refresh_reason"], "expiry_unknown_controlled_refresh_required")
        self.assertEqual(self.manager.refresh()["action"], "refreshed")
        self.store.record = credential(kind="dashboard", expires_at=None, issued_at=None,
                                       observed_at=(CAPTURE - timedelta(hours=23)).isoformat())
        self.responses(IDENTITY)
        self.assertEqual(self.manager.refresh()["refresh_reason"], "minimum_age_not_reached")

    def test_age_and_due_boundaries(self):
        self.store.record = credential(issued_at=(CAPTURE - timedelta(hours=24)).isoformat(),
                                       expires_at=(CAPTURE + timedelta(days=7)).isoformat())
        self.responses(IDENTITY, refreshed(), IDENTITY)
        self.assertEqual(self.manager.refresh()["action"], "refreshed")

    def test_revoked_expired_remote_and_permissions_never_replace_or_leak(self):
        for code, subcode, safe in [(190, 463, "authentication_required"),
                                    (190, 458, "authentication_required"),
                                    (10, None, "instagram_permissions_required"),
                                    (200, None, "instagram_permissions_required")]:
            with self.subTest(code=code, subcode=subcode):
                original = copy.deepcopy(self.store.record)
                self.responses(FakeHTTPResponse({"error": {"code": code, "error_subcode": subcode,
                                                            "message": MARKER}}, 400))
                with self.assertRaisesRegex(ConnectorError, safe) as caught:
                    self.manager.refresh()
                self.assertNotIn(MARKER, "".join(traceback.format_exception(caught.exception)))
                self.assertEqual(self.store.record, original)

    def test_refresh_failure_and_invalid_response_preserve_previous_envelope(self):
        cases = [FakeHTTPResponse({"error": {"code": 190, "message": MARKER}}, 400),
                 FakeHTTPResponse({"error": {"code": 100, "message": MARKER}}, 400),
                 FakeHTTPResponse(b"INVALID-JSON-" + MARKER.encode()),
                 TimeoutError("Authorization: Bearer " + MARKER),
                 FakeHTTPResponse({}, 302, {"Location": "https://evil.invalid/" + MARKER}),
                 refreshed(expires_in=0), refreshed(expires_in=True), refreshed(token_type=MARKER),
                 refreshed(access_token=""), refreshed(expires_in=5184001)]
        for response in cases:
            with self.subTest(response=type(response).__name__):
                original = copy.deepcopy(self.store.record)
                self.responses(IDENTITY, response)
                with self.assertRaises(ConnectorError) as caught:
                    self.manager.refresh()
                self.assertIsNone(caught.exception.__context__)
                self.assertNotIn(MARKER, "".join(traceback.format_exception(caught.exception)))
                self.assertEqual(self.store.record, original)

    def test_identity_mismatch_after_refresh_preserves_old(self):
        self.responses(IDENTITY, refreshed(), dict(IDENTITY, user_id="999"))
        with self.assertRaisesRegex(ConnectorError, "instagram_account_mismatch"):
            self.manager.refresh()
        self.assertEqual(self.store.record["token"], MARKER)
        self.assertEqual(self.store.saves, 0)

    def test_save_failure_keeps_old_record(self):
        self.responses(IDENTITY, refreshed(), IDENTITY)
        self.store.fail = True
        original = copy.deepcopy(self.store.record)
        with self.assertRaisesRegex(ConnectorError, "credential_write_failed"):
            self.manager.refresh()
        self.assertEqual(self.store.record, original)

    def test_import_checks_account_and_does_not_invent_expiration(self):
        self.responses(IDENTITY)
        result = self.manager.import_token(NEW, kind="dashboard")
        self.assertIsNone(result["expires_at"])
        self.assertIsNone(result["issued_at"])
        self.assertEqual(result["refresh_reason"], "minimum_age_not_reached")
        self.assertEqual(self.store.record["token"], NEW)
        original = copy.deepcopy(self.store.record)
        self.responses(dict(IDENTITY, username="another"))
        with self.assertRaisesRegex(ConnectorError, "instagram_account_mismatch"):
            self.manager.import_token(MARKER)
        self.assertEqual(self.store.record, original)

    def test_import_rejects_future_naive_and_expired_metadata(self):
        for changes in ({"issued_at": "2026-10-27T00:00:00Z"},
                        {"expires_at": "2026-10-25T00:00:00Z"},
                        {"issued_at": "2026-10-25T00:00:00"}):
            with self.subTest(changes=changes), self.assertRaises(ConnectorError):
                self.manager.import_token(NEW, **changes)
        self.connection.request.assert_not_called()

    def test_environment_precedence_and_no_refresh_or_store_write(self):
        with patch.dict(os.environ, {"HELMOS_INSTAGRAM_ACCESS_TOKEN": MARKER}):
            self.responses(IDENTITY)
            with patch.object(self.store, "load", side_effect=AssertionError("must not read")):
                result = self.manager.refresh()
        self.assertEqual(result["source"], "environment")
        self.assertEqual(result["refresh_reason"], "import_required_for_refresh")
        self.assertEqual(self.store.saves, 0)
        self.assertEqual(self.connection.request.call_count, 1)

    def test_sync_authentication_failure_leaves_social_state_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            for failure in [FakeHTTPResponse({"error": {"code": 190, "message": MARKER}}, 400),
                            FakeHTTPResponse({"error": {"code": 10, "message": MARKER}}, 400)]:
                self.responses(failure)
                with patch("helmos.instagram_credentials.WindowsCredentialStore", return_value=self.store), \
                        self.assertRaises(ConnectorError):
                    sync_instagram(state, api_version="v26.0", expected_username="JorgeBytes")
                self.assertFalse(state.exists())

    def test_sync_refreshes_then_persists_only_social_data_without_secrets_or_logs(self):
        insights = [{"data": [{"name": name, "period": "lifetime", "values": [{"value": 7}]}]}
                    for name in ("views", "reach", "likes", "comments", "shares", "saved")]
        self.responses(IDENTITY, refreshed(), IDENTITY, {"data": [MEDIA]}, *insights)
        with tempfile.TemporaryDirectory() as temporary, \
                patch("helmos.instagram_sync.InstagramCredentials", return_value=self.manager), \
                self.assertNoLogs(level="DEBUG"):
            state = Path(temporary) / "state"
            result = sync_instagram(state, api_version="v26.0", expected_username="JorgeBytes",
                                    captured_at=CAPTURE)
            self.assertEqual(result["credential"]["action"], "refreshed")
            self.assertEqual(result["requests_made"], 10)
            self.assertEqual(result["observations_saved"], 1)
            saved = json.dumps(result) + "".join(path.read_text() for path in state.rglob("*.json"))
            for marker in (MARKER, NEW, "Authorization", "Bearer"):
                self.assertNotIn(marker, saved)

    def test_refresh_shares_sync_network_budget_and_keeps_old_token_on_exhaustion(self):
        manager = InstagramCredentials(api_version="v26.0", expected_username="JorgeBytes",
                                       store=self.store, clock=lambda: CAPTURE, max_requests=2)
        self.responses(IDENTITY, refreshed())
        with self.assertRaisesRegex(ConnectorError, "request_budget_exceeded"):
            manager.prepare()
        self.assertEqual(self.store.record["token"], MARKER)
        self.assertEqual(self.store.saves, 0)

    def test_insights_permission_revocation_aborts_without_social_writes(self):
        self.store.record = credential(expires_at=(CAPTURE + timedelta(days=40)).isoformat())
        self.responses(IDENTITY, {"data": [MEDIA]},
                       FakeHTTPResponse({"error": {"code": 200, "message": MARKER}}, 403))
        with tempfile.TemporaryDirectory() as temporary, \
                patch("helmos.instagram_credentials.WindowsCredentialStore", return_value=self.store):
            state = Path(temporary) / "state"
            with self.assertRaisesRegex(ConnectorError, "instagram_permissions_required"):
                sync_instagram(state, api_version="v26.0", expected_username="JorgeBytes")
            self.assertFalse(state.exists())

    def test_cli_check_error_and_refresh_output_never_expose_secrets(self):
        arguments = ["social", "credentials", "instagram", "refresh",
                     "--api-version", "v26.0", "--expected-username", "JorgeBytes"]
        output, errors = io.StringIO(), io.StringIO()
        with patch("helmos.__main__.InstagramCredentials", return_value=self.manager), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.responses(IDENTITY, refreshed(), IDENTITY)
            self.assertEqual(main(arguments), 0)
            self.responses(FakeHTTPResponse({"error": {"code": 190, "message": NEW}}, 400))
            self.assertEqual(main(arguments), 2)
        self.assertTrue(json.loads(errors.getvalue())["reauthorization_required"])
        for marker in (MARKER, NEW, "Authorization"):
            self.assertNotIn(marker, output.getvalue() + errors.getvalue())

    def test_cli_import_uses_secure_input_and_preflight_uses_bounded_preview(self):
        arguments = ["social", "credentials", "instagram", "import",
                     "--api-version", "v26.0", "--expected-username", "JorgeBytes", "--kind", "dashboard"]
        with patch("helmos.__main__.InstagramCredentials", return_value=self.manager), \
                patch("helmos.__main__.secure_input", return_value=MARKER), \
                contextlib.redirect_stdout(io.StringIO()):
            self.responses(IDENTITY)
            self.assertEqual(main(arguments), 0)
        arguments[3] = "preflight"
        with patch("helmos.__main__.sync_instagram", return_value={"partial_errors": []}) as sync, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(arguments[:8]), 0)
        self.assertTrue(sync.call_args.kwargs["preview"])
        self.assertEqual(sync.call_args.kwargs["max_posts"], 1)

    def test_secure_input_refuses_redirected_stdin(self):
        with patch("helmos.instagram_credentials.sys.stdin.isatty", return_value=False), \
                self.assertRaisesRegex(ConnectorError, "interactive_terminal_required"):
            secure_input()


class DPAPIStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "credentials" / "instagram.dpapi"
        self.store = WindowsCredentialStore(self.path)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
    def test_native_dpapi_roundtrip_only_fictional_secret(self):
        with self.store.transaction():
            self.store.save(credential())
        self.assertEqual(self.store.load(), credential())
        for path in self.path.parent.iterdir():
            self.assertNotIn(MARKER.encode(), path.read_bytes())
            self.assertNotIn(b"Authorization", path.read_bytes())
        self.assertNotIn(MARKER, repr(self.store))

    def test_failed_atomic_replace_preserves_old_bytes_and_cleans_temporary(self):
        self.path.parent.mkdir()
        self.path.write_bytes(b"OLD-ENCRYPTED-ENVELOPE")
        with patch("helmos.credential_store._dpapi", return_value=b"NEW-ENCRYPTED-ENVELOPE"), \
                patch("helmos.credential_store.os.replace", side_effect=OSError(MARKER)), \
                self.assertRaisesRegex(ConnectorError, "credential_write_failed") as caught:
            self.store.save(credential(token=NEW))
        self.assertEqual(self.path.read_bytes(), b"OLD-ENCRYPTED-ENVELOPE")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        self.assertNotIn(MARKER, "".join(traceback.format_exception(caught.exception)))

    def test_encryption_failure_never_creates_plaintext(self):
        with patch("helmos.credential_store._dpapi", side_effect=OSError(MARKER)), \
                self.assertRaisesRegex(ConnectorError, "credential_write_failed"):
            self.store.save(credential())
        self.assertFalse(self.path.parent.exists())

    def test_lock_fails_closed_and_releases_after_failure(self):
        with self.store.transaction():
            with self.assertRaisesRegex(ConnectorError, "credential_store_busy"):
                with self.store.transaction():
                    self.fail("concurrent writer allowed")
        with self.assertRaisesRegex(ValueError, "simulated"):
            with self.store.transaction():
                raise ValueError("simulated")
        self.assertFalse(self.path.with_suffix(".lock").exists())

    def test_corrupt_store_returns_safe_error_and_no_plaintext_fallback(self):
        self.path.parent.mkdir()
        self.path.write_bytes(b"INVALID-CIPHERTEXT")
        with patch("helmos.credential_store._dpapi", side_effect=OSError(MARKER)), \
                self.assertRaisesRegex(ConnectorError, "credential_read_failed") as caught:
            self.store.load()
        self.assertNotIn(MARKER, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
