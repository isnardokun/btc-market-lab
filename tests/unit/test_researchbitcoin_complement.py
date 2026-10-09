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
from validation.rbn_reconciliation import audit_rbn_against_html

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

    def test_fraction_scale_is_specific_to_supply_profit_fields(self):
        self.assertEqual(CATALOG["supply_in_profit_percent"].raw_scale, "fraction_0_1")
        self.assertEqual(CATALOG["supply_in_profit_sth_percent"].raw_scale, "fraction_0_1")
        self.assertEqual(CATALOG["sopr_lth"].raw_scale, "native")
        self.assertEqual(CATALOG["supply_in_profit_percent"].unit, "percent")

    def test_raw_fraction_stored_unchanged_and_displayed_with_caveat(self):
        fixtures = [
            ("supply_in_profit_percent", 0.683561, "≈68.4%*"),
            ("supply_in_profit_sth_percent", 0.709276, "≈70.9%*"),
        ]
        for slug, observed_value, _ in fixtures:
            result = parse_scalar_rows({"data": [{
                "time": "2026-10-08T00:00:00Z", slug: observed_value
            }]}, slug, NOW)
            self.assertEqual(result["2026-10-08"][1], observed_value)
            store_rows(self.dbpath, slug, result)
        with sqlite3.connect(self.dbpath) as db:
            actual = dict(db.execute(
                "SELECT metric, value FROM onchain_external_observations"
            ).fetchall())
        self.assertEqual(actual, {slug: value for slug, value, _ in fixtures})
        html = render_complement(self.dbpath, "2026-10-08")
        for slug, value, output in fixtures:
            self.assertIn(f'data-metric="{slug}"', html)
            self.assertIn(f'data-api-raw="{value}"', html)
            self.assertIn(output, html)
        self.assertIn("Normalización provisional", html)
        self.assertIn("dato bruto × 100", html)
        self.assertNotIn(">0.7%</div>", html)

    def test_raw_fraction_rejects_68_instead_of_silently_accepting(self):
        slug = "supply_in_profit_percent"
        for observed_value in (68.3561, -0.1, 1.001):
            with self.subTest(value=observed_value):
                with self.assertRaisesRegex(ValueError, "fraccional 0..1"):
                    parse_scalar_rows({"data": [{
                        "time": "2026-10-08T00:00:00Z",
                        slug: observed_value
                    }]}, slug, NOW)

    def test_old_fraction_storage_outside_0_1_is_omitted(self):
        initialize_database(self.dbpath)
        with sqlite3.connect(self.dbpath) as db:
            db.execute(
                """INSERT INTO onchain_external_observations
                (provider, metric, observed_date, observed_at_utc, value,
                 unit, source_endpoint, fetched_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("researchbitcoin", "supply_in_profit_percent", "2026-10-08",
                 "2026-10-08T00:00:00+00:00", 68.3561, "percent",
                 "/v2/supply_in_profitloss/supply_in_profit_percent",
                 "2026-10-09T00:00:00+00:00")
            )
        self.assertNotIn("Oferta total en ganancias",
                         render_complement(self.dbpath, "2026-10-08"))

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

    def _make_gate_fixture(self):
        samples = [
            ("supply_in_profit_percent", 0.6835608896),
            ("supply_in_profit_sth_percent", 0.7092757091),
            ("sopr_lth", 1.139),
            ("realized_price_lth", 49277.0),
        ]
        for slug, value in samples:
            store_rows(self.dbpath, slug, {
                "2026-10-08": ("2026-10-08T00:00:00+00:00", value),
            })
        rendered = render_complement(self.dbpath, "2026-10-08")
        self.assertIn("onchain-complement", rendered)
        return "<!doctype html><html><body>" + rendered + "</body></html>"

    def test_gate_reconciles_full_html_with_original_db(self):
        html = self._make_gate_fixture()
        self.assertEqual(audit_rbn_against_html(html, self.dbpath, "2026-10-09"), [])
        self.assertEqual(audit_rbn_against_html(html, self.dbpath,
                                               dt.date(2026, 10, 9)), [])

    def test_gate_stays_optional_with_no_provider_rows(self):
        self.assertEqual(audit_rbn_against_html(
            "<html><body>Base BTC sin ResearchBitcoin</body></html>",
            self.dbpath, "2026-10-09"), [])

    def test_gate_blocks_tampered_rbn_display(self):
        html = self._make_gate_fixture()
        for before, after in [
            ("≈68.4%*", "0.7%"),
            ("≈70.9%*", "70.0%"),
            ("US$\\xa049,277", "US$\\xa050,000"),
            ("1.139", "1.293"),
        ]:
            before = before.replace("\\xa0", "\xa0")
            after = after.replace("\\xa0", "\xa0")
            with self.subTest(changed=after):
                self.assertIn(before, html)
                errors = audit_rbn_against_html(
                    html.replace(before, after, 1), self.dbpath, "2026-10-09")
                self.assertTrue(any("valor HTML/SQLite no coincide" in e for e in errors),
                                errors)

    def test_gate_blocks_wrong_raw_date_source_and_missing_caveat(self):
        html = self._make_gate_fixture()
        for before, after, code in [
            ('data-api-raw="0.6835608896"',
             'data-api-raw="0.7000000000"', "API raw"),
            ('data-asof-utc="2026-10-08"',
             'data-asof-utc="2026-10-07"', "fecha HTML/SQLite"),
            ('href="https://researchbitcoin.net/metrics/supply_in_profit_percent/"',
             'href="https://example.org/metrics/fake/"', "fuente metodológica"),
            ("*Normalización provisional", "*Conversión definitiva",
             "advertencia de escala provisional"),
        ]:
            with self.subTest(code=code):
                self.assertIn(before, html)
                errors = audit_rbn_against_html(
                    html.replace(before, after, 1), self.dbpath, "2026-10-09")
                self.assertTrue(any(code in e for e in errors), errors)

    def test_gate_blocks_omitted_duplicate_and_unexpected_rbn_cards(self):
        html = self._make_gate_fixture()
        rendered = render_complement(self.dbpath, "2026-10-08")
        self.assertTrue(any("faltan métricas" in e for e in
                            audit_rbn_against_html("<html></html>",
                                                   self.dbpath, "2026-10-09")))
        duplicate = "<html>" + rendered + rendered + "</html>"
        self.assertTrue(any("duplicados" in e for e in
                            audit_rbn_against_html(duplicate, self.dbpath,
                                                   "2026-10-09")))
        self.assertTrue(any("métrica inesperada" in e for e in
                            audit_rbn_against_html(
                                html.replace('data-metric="sopr_lth"',
                                             'data-metric="unknown_metric"'),
                                self.dbpath, "2026-10-09")))
        extra = html.replace('</body>',
                             '<div id="onchain-complement"></div></body>')
        self.assertTrue(any("duplicados" in e for e in
                            audit_rbn_against_html(extra, self.dbpath,
                                                   "2026-10-09")))

    def test_gate_blocks_rbn_card_without_sqlite_provenance(self):
        html = self._make_gate_fixture()
        with self.assertRaises(AssertionError):
            self.assertEqual(html, "")
        self.assertTrue(any("sin observaciones" in e for e in
                            audit_rbn_against_html(
                                html, self.dbpath, "2026-09-01")))
        orphan = "<div id='onchain-complement'><div class='stat-item' " \
                 "data-provider='researchbitcoin' data-metric='sopr_lth' " \
                 "data-asof-utc='2026-10-08'><div class='sval'>1.139" \
                 "</div></div></div>"
        self.assertTrue(any("sin observaciones" in e for e in
                            audit_rbn_against_html(
                                orphan, self.dbpath.with_name("missing.db"),
                                "2026-10-09")))

    def test_empty_extension_is_backward_compatible(self):
        self.assertEqual(render_complement(self.dbpath, "2026-10-08"), "")


if __name__ == "__main__":
    unittest.main()
