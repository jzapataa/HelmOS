from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from helmos.social_connectors import (Account, ConnectorError, EditorialMetadata,
                                      InstagramConnector, LinkedInConnector,
                                      Response, TikTokConnector)
from helmos.social_import import SocialImportError, import_csv, list_observations, list_posts
from helmos.social_history import evolution


CAPTURE = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
META = {"123": EditorialMetadata("Python", "video", "jorgebytes-video-1")}
VIDEO = {"id": "123", "create_time": 1790812800, "view_count": 10,
         "like_count": 0, "comment_count": 2, "share_count": 1}
MEDIA = {"id": "123", "timestamp": "2026-10-01T00:00:00+0000",
         "media_type": "VIDEO", "media_product_type": "REELS"}


def tt(videos=None, *, more=False, cursor=1):
    return Response(200, {"data": {"videos": [copy.deepcopy(VIDEO)] if videos is None else videos,
                                   "has_more": more, "cursor": cursor}, "error": {"code": "ok"}})


def ig_list(media=None, **extra):
    return Response(200, {"data": [copy.deepcopy(MEDIA)] if media is None else media, **extra})


def ig_metrics(**metrics):
    return Response(200, {"data": [{"name": name, "period": "lifetime", "values": [{"value": value}]}
                                    for name, value in metrics.items()]})


def provider(platform, *responses, account_id=None):
    stub = Mock()
    stub.account = Account(platform, account_id or ("456" if platform == "Instagram" else "fictional-open-id"))
    stub.request.side_effect = list(responses)
    return stub


class SocialConnectorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "state"

    def test_tiktok_transform_and_safe_provenance(self):
        response = tt()
        response.body["access_token"] = "DO-NOT-STORE"
        response.body["data"]["videos"][0]["private_extra"] = "DO-NOT-STORE"
        stub = provider("TikTok", response)
        connector = TikTokConnector(stub)
        result = connector.import_snapshot(self.state, META, captured_at=CAPTURE)
        self.assertEqual(result["imported"], 1)
        post = list_posts(self.state)[0]
        self.assertEqual(post["metrics"], {"views": 10, "likes": 0, "comments": 2, "shares": 1})
        origin = post["initial_observation"]["provenance"]
        self.assertEqual(origin["account_id"], "fictional-open-id")
        self.assertEqual(origin["captured_at"], CAPTURE.isoformat())
        self.assertEqual(post["initial_observation"]["measurement_type"], "cumulative")
        self.assertNotIn("DO-NOT-STORE", json.dumps(post))
        self.assertEqual(stub.request.call_args.kwargs["body"], {"max_count": 20})
        self.assertEqual(stub.request.call_args.args, ("POST", "/v2/video/list/"))

    def test_instagram_reels_feed_and_mapping(self):
        for kind, product in (("VIDEO", "REELS"), ("IMAGE", "FEED"), ("CAROUSEL_ALBUM", "FEED")):
            media = dict(MEDIA, media_type=kind, media_product_type=product)
            stub = provider("Instagram", ig_list([media]), ig_metrics(views=40, saved=3, reach=20, likes=0))
            post = InstagramConnector(stub, api_version="v24.0").collect(META, captured_at=CAPTURE)[0]
            self.assertEqual(post["metrics"], {"views": 40, "saves": 3, "reach": 20, "likes": 0})
            self.assertNotIn("impressions", post["metrics"])
            self.assertEqual(stub.request.call_args.args, ("GET", "/v24.0/123/insights"))

    def test_missing_metrics_are_absent_and_zero_is_preserved(self):
        video = {"id": "123", "create_time": VIDEO["create_time"], "like_count": 0, "view_count": None}
        posts = TikTokConnector(provider("TikTok", tt([video]))).collect(META, captured_at=CAPTURE)
        self.assertEqual(posts[0]["metrics"], {"likes": 0})
        stub = provider("Instagram", ig_list(), Response(200, {"data": []}))
        posts = InstagramConnector(stub, api_version="v24.0").collect(META, captured_at=CAPTURE)
        self.assertEqual(posts[0]["metrics"], {})

    def test_tiktok_pagination_uses_integer_cursor(self):
        stub = provider("TikTok", tt([], more=True, cursor=123456), tt())
        posts = TikTokConnector(stub).collect(META, captured_at=CAPTURE)
        self.assertEqual(len(posts), 1)
        self.assertEqual(stub.request.call_args.kwargs["body"]["cursor"], 123456)

    def test_instagram_pagination_ignores_remote_next_url(self):
        stub = provider("Instagram", ig_list([], paging={"next": "https://evil.invalid?access_token=SECRET",
                                                        "cursors": {"after": "cursor-A"}}),
                        ig_list(), ig_metrics(views=1))
        posts = InstagramConnector(stub, api_version="v24.0").collect(META, captured_at=CAPTURE)
        self.assertEqual(len(posts), 1)
        self.assertEqual(stub.request.call_args_list[1].kwargs["params"]["after"], "cursor-A")
        self.assertNotIn("evil", str(stub.request.call_args_list))

    def test_bad_pagination_and_budgets_fail_without_writes(self):
        cases = [provider("TikTok", tt([], more=True, cursor=1), tt([], more=True, cursor=1)),
                 provider("TikTok", tt([], more=True, cursor="bad")),
                 provider("TikTok", tt([], more=True), tt())]
        for index, stub in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(ConnectorError):
                TikTokConnector(stub, max_requests=1 if index == 2 else 10).import_snapshot(
                    self.state, META, captured_at=CAPTURE)
            self.assertFalse(self.state.exists())
        stub = provider("Instagram", ig_list([], paging={"next": "next", "cursors": {}}))
        with self.assertRaisesRegex(ConnectorError, "invalid_pagination"):
            InstagramConnector(stub, api_version="v24.0").collect(META, captured_at=CAPTURE)

    def test_http_errors_are_sanitized_and_not_retried(self):
        for status, code in ((401, "authentication_required"), (403, "access_denied"),
                             (429, "rate_limited"), (500, "http_error")):
            with self.subTest(status=status):
                stub = provider("TikTok", Response(status, {"secret": "SECRET"}, {"Retry-After": "30"}))
                with self.assertRaises(ConnectorError) as caught:
                    TikTokConnector(stub).import_snapshot(self.state, META, captured_at=CAPTURE)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.status, status)
                self.assertEqual(caught.exception.retry_after, 30)
                self.assertNotIn("SECRET", str(caught.exception))
                stub.request.assert_called_once()
                self.assertFalse(self.state.exists())

    def test_provider_exceptions_do_not_escape(self):
        for error in (TimeoutError("SECRET"), OSError("SECRET"), ValueError("SECRET invalid JSON")):
            stub = provider("TikTok", error)
            with self.assertRaisesRegex(ConnectorError, "provider_failure") as caught:
                TikTokConnector(stub).collect(META, captured_at=CAPTURE)
            self.assertIsNone(caught.exception.__cause__)
            self.assertTrue(caught.exception.__suppress_context__)
            self.assertNotIn("SECRET", str(caught.exception))

    def test_invalid_payloads_and_metrics(self):
        bad = [Response(200, []), Response(200, {}),
               Response(200, {"error": {"code": "bad", "message": "SECRET"}}),
               tt([dict(VIDEO, view_count=-1)]), tt([dict(VIDEO, view_count=True)]),
               tt([dict(VIDEO, view_count="10")]), tt([dict(VIDEO, id="../123")]),
               tt([dict(VIDEO, create_time=None)]), tt([dict(VIDEO, create_time=10**30)]),
               Response(200, {"data": {"videos": {}, "has_more": False}, "error": {"code": "ok"}})]
        for response in bad:
            with self.subTest(response=response), self.assertRaises(ConnectorError):
                TikTokConnector(provider("TikTok", response)).import_snapshot(self.state, META, captured_at=CAPTURE)
            self.assertFalse(self.state.exists())

    def test_instagram_invalid_insights_and_unsupported_media(self):
        invalid = [Response(200, {"error": {"message": "SECRET"}}),
                   Response(200, {"data": [{"name": "views", "period": "day", "values": [{"value": 2}]}]}),
                   ig_metrics(views=-1), Response(200, {"data": [{"name": "views", "period": "lifetime", "values": {}}]})]
        for response in invalid:
            with self.subTest(response=response), self.assertRaises(ConnectorError):
                InstagramConnector(provider("Instagram", ig_list(), response), api_version="v24.0").collect(
                    META, captured_at=CAPTURE)
        with self.assertRaisesRegex(ConnectorError, "unsupported_media_type"):
            InstagramConnector(provider("Instagram", ig_list([dict(MEDIA, media_product_type="STORY")])),
                               api_version="v24.0").collect(META, captured_at=CAPTURE)

    def test_duplicate_posts_and_late_remote_failure_leave_no_data(self):
        for responses in ((tt(more=True), tt()), (tt(more=True), Response(500, {}))):
            with self.subTest(responses=responses), self.assertRaises(ConnectorError):
                TikTokConnector(provider("TikTok", *responses)).import_snapshot(self.state, META, captured_at=CAPTURE)
            self.assertFalse(self.state.exists())

    def test_explicit_editorial_metadata_and_aware_capture_required(self):
        for metadata, capture in (({}, CAPTURE), (META, CAPTURE.replace(tzinfo=None)),
                                  (META, datetime(2020, 1, 1, tzinfo=timezone.utc))):
            with self.subTest(capture=capture), self.assertRaises(ConnectorError):
                TikTokConnector(provider("TikTok", tt())).import_snapshot(self.state, metadata, captured_at=capture)
            self.assertFalse(self.state.exists())

    def test_history_idempotence_conflicts_and_no_overwrites(self):
        def collect(response= None, capture=CAPTURE, account_id=None, metadata=META):
            return TikTokConnector(provider("TikTok", response or tt(), account_id=account_id)).import_snapshot(
                self.state, metadata, captured_at=capture)
        collect()
        original = {path: path.read_bytes() for path in self.state.rglob("*.json")}
        self.assertEqual(collect()["already_present"], 1)
        for kwargs in ({"response": tt([dict(VIDEO, view_count=11)])},
                       {"account_id": "another-account"},
                       {"metadata": {"123": EditorialMetadata("Python", "video", "other-piece")}}):
            with self.assertRaises(SocialImportError):
                collect(**kwargs)
            self.assertEqual({path: path.read_bytes() for path in self.state.rglob("*.json")}, original)
        later = CAPTURE.replace(day=27)
        self.assertEqual(collect(tt([dict(VIDEO, view_count=15)]), later)["observations_added"], 1)
        post = list_posts(self.state)[0]
        series = evolution(self.state, post)["metrics"]["views"]
        self.assertEqual(series[-1]["change"], 5)
        for path, content in original.items():
            self.assertEqual(path.read_bytes(), content)

    def test_csv_coexistence_both_directions_preserves_optional_provenance(self):
        for api_first in (False, True):
            state = self.state / str(api_first)
            csv_path = self.state.parent / "fictional.csv"
            csv_path.write_text("platform,post_id,published_at,format,topic,observed_at,measurement_type,views\n"
                                "TikTok,123,2026-10-01,video,Python,2026-10-25,cumulative,8\n", encoding="utf-8")
            connector = TikTokConnector(provider("TikTok", tt()))
            actions = [lambda: import_csv(csv_path, state),
                       lambda: connector.import_snapshot(state, META, captured_at=CAPTURE)]
            for action in reversed(actions) if api_first else actions:
                action()
            items = list_observations(state, list_posts(state)[0])
            self.assertEqual(len(items), 2)
            self.assertEqual(sum("provenance" in item for item in items), 1)

    def test_cross_platform_piece_identity_preserves_distinct_results(self):
        TikTokConnector(provider("TikTok", tt())).import_snapshot(self.state, META, captured_at=CAPTURE)
        InstagramConnector(provider("Instagram", ig_list(), ig_metrics(views=20)), api_version="v24.0").import_snapshot(
            self.state, META, captured_at=CAPTURE)
        posts = list_posts(self.state)
        self.assertEqual(len(posts), 2)
        self.assertEqual({post["initial_observation"]["provenance"]["editorial_piece_id"] for post in posts},
                         {"jorgebytes-video-1"})
        self.assertEqual({post["metrics"]["views"] for post in posts}, {10, 20})

    def test_linkedin_is_blocked_without_network_or_writes(self):
        connector = LinkedInConnector()
        with self.assertRaisesRegex(ConnectorError, "linkedin_member_access_unavailable"):
            connector.import_snapshot(self.state, META, captured_at=CAPTURE)
        self.assertFalse(self.state.exists())

    def test_configuration_validation(self):
        with self.assertRaises(ConnectorError):
            Account("Other", "1")
        with self.assertRaises(ConnectorError):
            Account("TikTok", "../1")
        with self.assertRaises(ConnectorError):
            EditorialMetadata("", "video", "piece")
        with self.assertRaises(ConnectorError):
            TikTokConnector(provider("Instagram"))
        with self.assertRaises(ConnectorError):
            TikTokConnector(provider("TikTok"), max_requests=0)
        with self.assertRaises(ConnectorError):
            InstagramConnector(provider("Instagram"), api_version="latest")


if __name__ == "__main__":
    unittest.main()
