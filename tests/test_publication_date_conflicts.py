"""Offline regressions for civil dates, UTC instants and immutable history."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from helmos.social_connectors import InstagramConnector, MetricMetadata
from helmos.social_import import (SocialImportError, _persist_posts, _post_path,
                                  import_csv, list_posts)
from helmos.sync_diagnostics import SyncDiagnostics
from test_instagram_sync import MEDIA, listing, metrics, provider


class PublicationDateConflictTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"

    def history(self, day):
        path = self.root / "history.csv"
        path.write_text("platform,post_id,published_at,format,topic,views\n"
                        f"Instagram,123,{day},video,Reviewed,5\n", encoding="utf-8")
        import_csv(path, self.state)

    def collect(self, timestamp):
        connector = InstagramConnector(
            provider(listing([dict(MEDIA, timestamp=timestamp)]), *metrics()),
            api_version="v24.0", partial_insights=True, allow_unclassified=True)
        return connector.collect({"123": MetricMetadata("Reviewed", "video")},
                                 captured_at=datetime(2026, 10, 27, tzinfo=timezone.utc))

    def snapshot(self):
        return {str(p.relative_to(self.state)): p.read_bytes()
                for p in self.state.rglob("*.json")}

    def test_equivalent_offsets_use_same_utc_day_without_rewriting_publication(self):
        self.history("2026-09-30")
        original = _post_path(self.state, "Instagram", "123").read_bytes()
        utc = self.collect("2026-09-30T22:30:00Z")
        offset = self.collect("2026-10-01T00:30:00+02:00")
        self.assertEqual(utc, offset)
        self.assertEqual(utc[0]["published_at"], "2026-09-30")
        result = _persist_posts(offset, self.state, precise_captures=True)
        self.assertEqual(result["observations_added"], 1)
        self.assertEqual(_post_path(self.state, "Instagram", "123").read_bytes(), original)

    def test_adjacent_civil_day_is_blocked_and_reference_survives_batch_reordering(self):
        self.history("2026-10-01")
        for folder in ("recommendations", "evaluations"):
            directory = self.state / "social" / folder
            directory.mkdir()
            (directory / "existing.json").write_text('{"historical": true}')
        original = self.snapshot()
        incoming = self.collect("2026-10-01T00:30:00+02:00")
        fresh = dict(incoming[0], post_id="124")
        references = []
        for batch, position in ((incoming, 1), ([fresh, *incoming], 2)):
            diagnostic = SyncDiagnostics()
            with self.assertRaises(SocialImportError):
                _persist_posts(batch, self.state, precise_captures=True, diagnostics=diagnostic)
            self.assertEqual(diagnostic.context["persistence_post_index"], position)
            references.append(diagnostic.context["persistence_publication_ref"])
            self.assertEqual(diagnostic.context["persistence_published_at"], {
                "historical": {"value": "2026-10-01", "source": "csv"},
                "incoming": {"value": "2026-09-30", "source": "official_api"}})
            self.assertEqual(self.snapshot(), original)
        self.assertEqual(references, [_post_path(self.state, "Instagram", "123").stem] * 2)

    def test_invalid_date_and_untrusted_provenance_are_not_copied_to_diagnostic(self):
        self.history("2026-10-01")
        original = self.snapshot()
        incoming = list_posts(self.state)
        incoming[0]["published_at"] = "PRIVATE-REMOTE-PAYLOAD"
        incoming[0]["source_row"] = {}
        incoming[0]["initial_observation"]["provenance"] = {"source": "PRIVATE-SOURCE"}
        diagnostic = SyncDiagnostics()
        with self.assertRaises(SocialImportError):
            _persist_posts(incoming, self.state, diagnostics=diagnostic)
        self.assertEqual(diagnostic.context["persistence_published_at"]["incoming"],
                         {"value": None, "source": "unknown"})
        self.assertNotIn("PRIVATE", json.dumps(diagnostic.context))
        self.assertEqual(self.snapshot(), original)
