"""R10-E.8 source-native archived gap QA using synthetic SQLite exclusively."""
import ast
import contextlib
import datetime as dt
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import market_r10e8_ro_internal_gap_audit as gaps

NOW = dt.datetime(2026, 10, 10, 22, tzinfo=dt.timezone.utc)
T0 = 1791331200  # 2026-10-07 00:00 UTC
T2 = 1791504000  # 2026-10-09 00:00 UTC


def fixture(path):
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE onchain_external_observations(provider TEXT,metric TEXT,observed_date TEXT);
            CREATE TABLE series(id INTEGER,name TEXT,idx TEXT);
            CREATE TABLE daily(series_id INTEGER,ts INTEGER);
            CREATE TABLE macro_fred(series_id TEXT,date TEXT);
            CREATE TABLE market_ohlc_history(provider TEXT,symbol TEXT,ts INTEGER);
            INSERT INTO onchain_external_observations VALUES
             ('researchbitcoin','sopr_sth','2026-10-07'),
             ('researchbitcoin','sopr_sth','2026-10-09'),
             ('other','sopr_sth','2026-10-08');
            INSERT INTO series VALUES (1,'mvrv','1d'),(2,'block_height','height');
            INSERT INTO daily VALUES
             (1,1791331200),(1,1791504000),(2,1791417600);
            INSERT INTO macro_fred VALUES
             ('CPIAUCSL','2026-07-01'),
             ('CPIAUCSL','2026-09-01'),
             ('DGS10','2026-10-07'),('DGS10','2026-10-09'),
             ('NFCI','2026-10-02'),('NFCI','2026-10-09');
            INSERT INTO market_ohlc_history VALUES
             ('Yahoo Finance','BTC-USD',1791331200),
             ('Yahoo Finance','BTC-USD',1791504000),
             ('OTHER','BTC-USD',1791417600);
        """)


class TestR10E8InternalGaps(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "synthetic.db"
        fixture(self.path)

    def scan(self):
        return gaps.audit_internal_gaps(self.path, now=NOW)

    def by_source(self, source):
        return next(s for s in self.scan()["sources"] if s["provider"] == source)

    def by_metric(self, source, metric):
        return next(s for s in self.by_source(source)["series"] if s["metric"] == metric)

    def test_source_families_present(self):
        rows = self.scan()["sources"]
        self.assertEqual([r["provider"] for r in rows],
                         ["researchbitcoin", "bitview", "fred", "yahoo"])
        self.assertTrue(all(r["schema_state"] == "PRESENT" for r in rows))

    def test_rbn_detect_one_internal_daily_candidate(self):
        r = self.by_metric("researchbitcoin", "sopr_sth")
        self.assertEqual(r["internal_gap_candidates"], 1)
        self.assertEqual(r["sample_missing_slots"], ["2026-10-08"])
        self.assertEqual(r["observations"], 2)
        self.assertEqual(r["gap_method"], "UTC_CALENDAR_DAY")
        self.assertFalse(r["recoverable_window_verified"])

    def test_non_rbn_provider_rows_not_counted(self):
        r = self.by_metric("researchbitcoin", "sopr_sth")
        self.assertEqual(r["distinct_utc_dates"], 2)
        self.assertEqual(r["first_archived_utc"], "2026-10-07")
        self.assertEqual(r["last_archived_utc"], "2026-10-09")

    def test_bitview_only_1d_calendar_gap(self):
        r = self.by_metric("bitview", "mvrv")
        self.assertEqual(r["sample_missing_slots"], ["2026-10-08"])
        self.assertEqual(r["internal_gap_candidates"], 1)
        self.assertEqual(len(self.by_source("bitview")["series"]), 1)

    def test_fred_monthly_missing_august(self):
        r = self.by_metric("fred", "CPIAUCSL")
        self.assertEqual(r["gap_method"], "MONTHLY_OBSERVATION")
        self.assertEqual(r["sample_missing_slots"], ["2026-08"])
        self.assertEqual(r["internal_gap_candidates"], 1)
        self.assertEqual(r["first_archived_utc"], "2026-07-01")

    def test_fred_daily_does_not_mark_weekends_missing(self):
        for metric in ("DGS10", "NFCI"):
            with self.subTest(metric=metric):
                r = self.by_metric("fred", metric)
                self.assertEqual(r["gap_method"], "EXTERNAL_PUBLICATION_CALENDAR_REQUIRED")
                self.assertIsNone(r["internal_gap_candidates"])
                self.assertEqual(r["sample_missing_slots"], [])

    def test_yahoo_requires_trading_calendar(self):
        r = self.by_metric("yahoo", "BTC-USD")
        self.assertEqual(r["gap_method"], "EXTERNAL_PUBLICATION_CALENDAR_REQUIRED")
        self.assertIsNone(r["internal_gap_candidates"])
        self.assertEqual(r["observations"], 2)

    def test_yahoo_other_provider_rows_excluded(self):
        self.assertEqual(self.by_metric("yahoo", "BTC-USD")["distinct_utc_dates"], 2)

    def test_no_series_returns_empty_not_fake_coverage(self):
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM macro_fred")
        r = self.by_source("fred")
        self.assertEqual(r["schema_state"], "PRESENT")
        self.assertEqual(r["series"], [])
        self.assertEqual(r["historical_completeness"], "NOT_VERIFIED")

    def test_missing_tables_are_explicitly_uninitialized(self):
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE daily")
            db.execute("DROP TABLE macro_fred")
        r = self.scan()
        self.assertEqual(r["sources"][1]["schema_state"], "NOT_INITIALIZED")
        self.assertEqual(r["sources"][2]["schema_state"], "NOT_INITIALIZED")
        self.assertEqual(r["sources"][1]["series"], [])

    def test_existing_partial_schema_blocks(self):
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE series")
            db.execute("CREATE TABLE series(id INTEGER)")
        with self.assertRaises(ValueError):
            self.scan()

    def test_invalid_date_and_bad_unix_type_block(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO macro_fred VALUES ('CPIAUCSL','bad')")
        with self.assertRaises(ValueError):
            self.scan()
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM macro_fred WHERE date='bad'")
            db.execute("INSERT INTO daily VALUES (1, 'not_epoch')")
        with self.assertRaises(ValueError):
            self.scan()

    def test_duplicate_observation_does_not_fake_missing_date(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO onchain_external_observations VALUES "
                       "('researchbitcoin','sopr_sth','2026-10-07')")
        r = self.by_metric("researchbitcoin", "sopr_sth")
        self.assertEqual(r["observations"], 3)
        self.assertEqual(r["distinct_utc_dates"], 2)
        self.assertEqual(r["internal_gap_candidates"], 1)

    def test_observation_budget_fails_closed(self):
        with patch.object(gaps, "MAX_OBSERVATIONS", 2):
            with self.assertRaises(ValueError):
                self.scan()

    def test_readonly_byte_equivalence_and_no_wal_created(self):
        previous = self.path.read_bytes()
        r = self.scan()
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertFalse((self.path.parent / "synthetic.db-wal").exists())
        self.assertEqual(r["http_requests"], 0)
        self.assertEqual(r["sqlite_writes"], 0)
        self.assertIs(r["execution_authorized"], False)
        self.assertIs(r["real_provider_availability_verified"], False)
        self.assertEqual(r["historical_completeness"], "NOT_VERIFIED")
        self.assertEqual(r["db_location"], "REDACTED")

    def test_missing_db_naive_clock_and_apply_rejected(self):
        with contextlib.redirect_stdout(StringIO()) as result:
            rc = gaps.main(["--db", str(self.path.parent / "absent.sqlite")])
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(result.getvalue())["state"], "BLOCKED")
        with self.assertRaises(ValueError):
            gaps.audit_internal_gaps(self.path, now=dt.datetime(2026, 10, 10, 22))
        with contextlib.redirect_stderr(StringIO()) as err:
            with self.assertRaises(SystemExit) as raised:
                gaps.main(["--db", str(self.path), "--apply"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("unrecognized arguments", err.getvalue())

    def test_registration_extensible_and_singleton(self):
        runner = Path(__file__).resolve().parents[1] / "run_all.py"
        tree = ast.parse(runner.read_text(encoding="utf-8"))
        rows = [ast.literal_eval(n.value) for n in tree.body
                if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "TESTS" for t in n.targets)]
        self.assertEqual(len(rows), 1)
        self.assertGreaterEqual(len(rows[0]), 46)
        self.assertEqual(len({name for name,_ in rows[0]}), len(rows[0]))
        self.assertEqual(len({cmd for _,cmd in rows[0]}), len(rows[0]))
        for suffix in ("test_market_r10e_plan_only", "test_market_r10e_dispatch_offline",
                       "test_market_r10e2_shadow_gate", "test_market_r10e3_mock_e2e",
                       "test_market_r10e4_synthetic_cycle", "test_market_r10e5_backward_fixture_plan",
                       "test_market_r10e6_rbn_tier2_shadow", "test_market_r10e7_ro_archive_manifest",
                       "test_market_r10e8_ro_internal_gap_audit"):
            self.assertEqual(sum(suffix in cmd for _,cmd in rows[0]), 1)


if __name__ == "__main__":
    unittest.main()
