"""Offline regression tests for optional on-chain ResearchBitcoin complement."""
import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ingestion.researchbitcoin_catalog import CATALOG
from ingestion.researchbitcoin_v2 import (
    initialize_database, inventory, last_completed_day, parse_scalar_rows,
    query_params, store_rows, describe_shape,
)
from rendering.onchain_complement import render_complement, read_complement

NOW = dt.datetime(2026, 10, 9, 13, 59, tzinfo=dt.timezone.utc)


class ResearchBitcoinComplementTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dbpath = Path(temp.name) / "research.db"
        with sqlite3.connect(self.dbpath) as db:
            db.execute("CREATE TABLE series (id INTEGER PRIMARY KEY, name TEXT)")
            db.execute("INSERT INTO series (id,name) VALUES (1,'mvrv_sth')")

    def test_catalog_is_verified_and_prioritized(self):
        self.assertGreaterEqual(len(CATALOG), 10)
        self.assertEqual(CATALOG["sopr_sth"].endpoint, "/v2/spent_output_profit_ratio")
        self.assertEqual(CATALOG["supply_in_profit_sth_percent"].unit, "percent")

    def test_completed_day_cutoff_uses_exclusive_end(self):
        p = query_params(NOW, days=7)
        self.assertEqual(p["from_time"], "2026-10-02")
        self.assertEqual(p["to_time"], "2026-10-09")
        self.assertEqual(last_completed_day(NOW), dt.date(2026, 10, 8))
        with self.assertRaises(ValueError):
            query_params(NOW, days=120)

    def test_probe_only_shares_schema(self):
        metadata = describe_shape({"data": [{"date": "2026-10-08", "value": 9.5}]})
        self.assertEqual(metadata["data_type"], "list")
        self.assertNotIn("9.5", json.dumps(metadata))

    def test_unknown_shape_rejected(self):
        with self.assertRaises(ValueError):
            parse_scalar_rows({"data": {"rows": []}}, "sopr_sth", NOW)
        self.assertEqual(read_complement(self.dbpath, "2026-10-08"), [])

    def test_wrong_percent_range_rejected(self):
        with self.assertRaises(ValueError):
            parse_scalar_rows(
                {"data": [{"date": "2026-10-08", "value": 150.0}]},
                "supply_in_profit_sth_percent", NOW,
            )

    def test_provider_slug_key_is_supported_without_guessing_field(self):
        rows = parse_scalar_rows({"data": [
            {"time": "2026-10-08T00:00:00Z", "sopr_lth": 1.054}
        ]}, "sopr_lth", NOW)
        self.assertEqual(rows["2026-10-08"][1], 1.054)

    def test_wrong_provider_metric_must_not_be_relabelled(self):
        with self.assertRaisesRegex(ValueError, "sin valor"):
            parse_scalar_rows({"data": [
                {"time": "2026-10-08T00:00:00Z", "realized_profit_sth": 250000.0}
            ]}, "realized_loss_sth", NOW)

    def test_ambiguous_generic_with_other_slug_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "otra métrica"):
            parse_scalar_rows({"data": [
                {"time": "2026-10-08T00:00:00Z", "value": 250000.0,
                 "realized_profit_sth": 200000.0}
            ]}, "realized_loss_sth", NOW)

    def test_open_day_not_archived(self):
        with self.assertRaises(ValueError):
            parse_scalar_rows(
                {"data": [{"date": "2026-10-09", "value": 1.01}]},
                "sopr_sth", NOW,
            )

    def test_sidecar_maintains_source_provenance_without_changing_bitview(self):
        observations = parse_scalar_rows({"data": [
            {"time": "2026-10-08T00:00:00Z", "value": 76000.4},
            {"time": "2026-10-09T00:00:00Z", "value": 78000.0},
        ]}, "realized_price_sth", NOW)
        self.assertEqual(len(observations), 1)
        store_rows(self.dbpath, "realized_price_sth", observations)
        with sqlite3.connect(self.dbpath) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM onchain_external_observations"
            ).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM series").fetchone()[0], 1)
        html = render_complement(self.dbpath, "2026-10-08")
        self.assertIn("Costo base STH", html)
        self.assertIn("76,000", html)
        self.assertIn("2026-10-08", html)
        self.assertIn("researchbitcoin.net", html)
        self.assertNotIn("78,000", html)

    def test_stale_or_future_data_omitted(self):
        initialize_database(self.dbpath)
        store_rows(self.dbpath, "sopr_sth",
                   {"2026-09-25": ("2026-09-25T00:00:00+00:00", 0.94)})
        self.assertEqual(render_complement(self.dbpath, "2026-10-08"), "")
        self.assertEqual(render_complement(self.dbpath, "2026-09-24"), "")

    def test_inventory_reports_overlap_without_claiming_equivalence(self):
        data = inventory(self.dbpath)
        mvrv = next(x for x in data if x["metric"] == "mvrv_sth")
        self.assertTrue(mvrv["needs_methodology_comparison"])
        self.assertEqual(mvrv["researchbitcoin_points"], 0)

    def test_empty_extension_is_backward_compatible(self):
        self.assertEqual(render_complement(self.dbpath, "2026-10-08"), "")


if __name__ == "__main__":
    unittest.main()
