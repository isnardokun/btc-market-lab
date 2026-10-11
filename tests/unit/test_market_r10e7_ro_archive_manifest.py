"""R10-E.7: four-provider RO archive manifest on synthetic SQLite only."""
import ast
import contextlib
import datetime as dt
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from ingestion.config import FRED_SERIES
from ingestion.researchbitcoin_catalog import CATALOG
from scripts.market_r10e7_ro_archive_manifest import main, read_manifest

NOW = dt.datetime(2026, 10, 10, 22, tzinfo=dt.timezone.utc)


def _fixture(path):
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE onchain_external_observations(
                provider TEXT, metric TEXT, observed_date TEXT);
            CREATE TABLE series(id INTEGER, name TEXT, idx TEXT);
            CREATE TABLE daily(series_id INTEGER, ts INTEGER);
            CREATE TABLE macro_fred(series_id TEXT, date TEXT);
            CREATE TABLE market_ohlc_history(provider TEXT, symbol TEXT, ts INTEGER);
            CREATE TABLE archive_sources(
                source_id TEXT, plan_tier INTEGER, entitlement_verified INTEGER);
            CREATE TABLE archive_ingest_windows(
                source_id TEXT, metric TEXT, status TEXT, data_points INTEGER);
            CREATE TABLE archive_fred_vintages(
                series_id TEXT, observed_date TEXT, realtime_start TEXT);
            INSERT INTO onchain_external_observations VALUES
              ('researchbitcoin','sopr_sth','2026-10-07'),
              ('researchbitcoin','sopr_sth','2026-10-09'),
              ('researchbitcoin','supply_in_profit_percent','2026-10-09'),
              ('other','sopr_sth','2026-10-09');
            INSERT INTO series VALUES
              (1,'mvrv','1d'),(2,'blocktime','height');
            INSERT INTO daily VALUES (1,1791331200),(1,1791504000),(2,1791331200);
            INSERT INTO macro_fred VALUES
              ('CPIAUCSL','2026-08-01'),('CPIAUCSL','2026-09-01'),
              ('DGS10','2026-10-09');
            INSERT INTO market_ohlc_history VALUES
              ('Yahoo Finance','BTC-USD',1791331200),
              ('Yahoo Finance','BTC-USD',1791504000),
              ('Different Provider','BTC-USD',1791504000);
            INSERT INTO archive_sources VALUES ('researchbitcoin',2,1);
            INSERT INTO archive_ingest_windows VALUES
              ('researchbitcoin','sopr_sth','ok',14),
              ('researchbitcoin','sopr_sth','empty',0),
              ('researchbitcoin','sopr_sth','failed',0),
              ('bitview','mvrv','ok',180);
            INSERT INTO archive_fred_vintages VALUES
              ('CPIAUCSL','2026-08-01','2026-09-12'),
              ('CPIAUCSL','2026-08-01','2026-10-01');
        """)


class TestR10E7ReadOnlyManifest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "synthetic.db"
        _fixture(self.path)

    def _read(self):
        return read_manifest(self.path, now=NOW)

    def test_all_four_families_are_distinct(self):
        data = self._read()
        self.assertEqual([s["provider"] for s in data["sources"]],
                         ["researchbitcoin", "bitview", "fred", "yahoo"])
        self.assertTrue(all(s["historical_completeness"] == "NOT_VERIFIED"
                            for s in data["sources"]))

    def test_rbn_catalog_13_contains_missing_metrics_without_fake_rows(self):
        rbn = self._read()["sources"][0]
        self.assertEqual(len(rbn["series"]), len(CATALOG))
        self.assertEqual(len(rbn["series"]), 13)
        indexed = {s["metric"]: s for s in rbn["series"]}
        self.assertEqual(indexed["sopr_sth"]["observations"], 2)
        self.assertEqual(indexed["sopr_sth"]["first_archived_utc"], "2026-10-07")
        self.assertEqual(indexed["sopr_sth"]["last_archived_utc"], "2026-10-09")
        self.assertEqual(indexed["mvrv_sth"]["observations"], 0)
        self.assertEqual(indexed["mvrv_sth"]["coverage_state"], "NO_OBSERVATIONS")

    def test_rbn_preserves_raw_scale(self):
        rows = {s["metric"]: s for s in self._read()["sources"][0]["series"]}
        self.assertEqual(rows["supply_in_profit_percent"]["raw_scale"], "fraction_0_1")
        self.assertEqual(rows["sopr_sth"]["raw_scale"], "native")
        self.assertEqual(rows["sopr_sth"]["shape"], "scalar")

    def test_db_tier_flag_does_not_prove_real_entitlement(self):
        r = self._read()
        self.assertEqual(r["sources"][0]["declared_tier_db"], 2)
        self.assertTrue(r["sources"][0]["entitlement_flag_db"])
        self.assertFalse(r["sources"][0]["independent_tier2_access_proved"])
        self.assertFalse(r["real_tier2_entitlement_verified"])
        self.assertFalse(r["execution_authorized"])

    def test_bitview_height_series_is_not_counted_as_daily(self):
        indexed = {s["metric"]: s for s in self._read()["sources"][1]["series"]}
        self.assertEqual(set(indexed), {"mvrv"})
        self.assertEqual(indexed["mvrv"]["observations"], 2)
        self.assertEqual(indexed["mvrv"]["first_archived_utc"], "2026-10-07")

    def test_fred_catalog_not_interpreted_as_daily_gap(self):
        fred = self._read()["sources"][2]
        indexed = {s["metric"]: s for s in fred["series"]}
        self.assertEqual(len(indexed), len(FRED_SERIES))
        self.assertEqual(indexed["CPIAUCSL"]["observations"], 2)
        self.assertEqual(indexed["CPIAUCSL"]["first_archived_utc"], "2026-08-01")
        self.assertEqual(indexed["CPIAUCSL"]["last_archived_utc"], "2026-09-01")
        self.assertNotIn("missing_calendar_days", indexed["CPIAUCSL"])

    def test_yahoo_other_provider_rows_excluded(self):
        yahoo = self._read()["sources"][3]
        self.assertEqual(len(yahoo["series"]), 1)
        self.assertEqual(yahoo["series"][0]["metric"], "BTC-USD")
        self.assertEqual(yahoo["series"][0]["observations"], 2)

    def test_failed_empty_and_ok_are_distinct_archive_windows(self):
        r = self._read()
        vals = {(x["provider"], x["metric"], x["status"]): x
                for x in r["window_summaries"]}
        self.assertEqual(vals["researchbitcoin","sopr_sth","ok"]["reported_points"], 14)
        self.assertEqual(vals["researchbitcoin","sopr_sth","empty"]["reported_points"], 0)
        self.assertEqual(vals["researchbitcoin","sopr_sth","failed"]["windows"], 1)
        self.assertEqual(r["fred_vintage_count"], 2)

    def test_missing_tables_return_schema_not_initialized(self):
        with sqlite3.connect(self.path) as db:
            for name in ("onchain_external_observations", "daily", "macro_fred",
                         "market_ohlc_history", "archive_sources",
                         "archive_fred_vintages", "archive_ingest_windows"):
                db.execute("DROP TABLE " + name)
        data = self._read()
        self.assertTrue(all(s["schema_state"] == "NOT_INITIALIZED"
                            for s in data["sources"]))
        self.assertTrue(all(s["observations"] == 0 for s in data["sources"][0]["series"]))
        self.assertIsNone(data["sources"][0]["declared_tier_db"])
        self.assertIsNone(data["fred_vintage_count"])

    def test_partial_existing_schema_blocks_instead_of_ignoring(self):
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE macro_fred")
            db.execute("CREATE TABLE macro_fred (date TEXT)")
        with self.assertRaises(ValueError):
            self._read()

    def test_malformed_date_blocks(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO onchain_external_observations VALUES "
                       "('researchbitcoin','sopr_sth','garbled')")
        with self.assertRaises(ValueError):
            self._read()

    def test_unknown_metric_labeled_unsupported_not_assigned_catalog_scale(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO onchain_external_observations VALUES "
                       "('researchbitcoin','unknown_unverified','2026-10-09')")
        indexed = {s["metric"]: s for s in self._read()["sources"][0]["series"]}
        self.assertEqual(indexed["unknown_unverified"]["shape"], "UNSUPPORTED_UNCATALOGUED")
        self.assertNotIn("raw_scale", indexed["unknown_unverified"])

    def test_read_only_no_sql_write_no_wal_created(self):
        before = self.path.read_bytes()
        r = self._read()
        self.assertEqual(r["state"], "ARCHIVED_RANGES_READ_ONLY")
        self.assertEqual(r["http_requests"], 0)
        self.assertEqual(r["sqlite_writes"], 0)
        self.assertEqual(r["provider_availability"], "NOT_CHECKED")
        self.assertEqual(r["historical_completeness"], "NOT_VERIFIED")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.path.with_name(self.path.name + "-wal").exists())
        self.assertEqual(r["db_location"], "REDACTED")

    def test_cli_explicit_db_and_reject_apply(self):
        with contextlib.redirect_stdout(StringIO()) as out:
            rc = main(["--db", str(self.path)])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue())["state"], "ARCHIVED_RANGES_READ_ONLY")
        with contextlib.redirect_stderr(StringIO()) as out:
            with self.assertRaises(SystemExit) as exc:
                main(["--db", str(self.path), "--apply"])
        self.assertEqual(exc.exception.code, 2)
        self.assertIn("unrecognized arguments", out.getvalue())

    def test_missing_db_and_naive_clock_block(self):
        with contextlib.redirect_stdout(StringIO()) as out:
            self.assertEqual(main(["--db", str(self.path.parent / "none.db")]), 2)
        self.assertEqual(json.loads(out.getvalue())["state"], "BLOCKED")
        with self.assertRaises(ValueError):
            read_manifest(self.path, now=dt.datetime(2026, 10, 10, 22))

    def test_runner_required_suites_unique_with_extensibility(self):
        path = Path(__file__).resolve().parents[1] / "run_all.py"
        t = ast.parse(path.read_text(encoding="utf-8"))
        values = [ast.literal_eval(n.value) for n in t.body
                  if isinstance(n, ast.Assign)
                  and any(isinstance(x, ast.Name) and x.id == "TESTS" for x in n.targets)]
        self.assertEqual(len(values), 1)
        registered = values[0]
        self.assertGreaterEqual(len(registered), 45)
        self.assertEqual(len({k for k,_ in registered}), len(registered))
        self.assertEqual(len({v for _,v in registered}), len(registered))
        for suffix in (
            "test_market_r10e_plan_only", "test_market_r10e_dispatch_offline",
            "test_market_r10e2_shadow_gate", "test_market_r10e3_mock_e2e",
            "test_market_r10e4_synthetic_cycle", "test_market_r10e5_backward_fixture_plan",
            "test_market_r10e6_rbn_tier2_shadow",
            "test_market_r10e7_ro_archive_manifest",
        ):
            self.assertEqual(sum(suffix in command for _, command in registered), 1)


if __name__ == "__main__":
    unittest.main()
