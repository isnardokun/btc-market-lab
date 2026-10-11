"""Package A first end-to-end multi-provider integration tests (NO network/production)."""
import ast
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from ingestion.package_a_contracts import SeriesSpec, parse_fixture
from scripts.package_a_ingest_fixture import (
    FixtureResponse, MARKER, MARKER_VALUE, LOCK_NAMESPACE,
    run_fixture_page, inspect_fixture,
)
from scripts.market_r10e11_writer_exclusion import hold_writer_lock
from storage import package_a_ledger as ledger
from scripts.package_a_batch_fixture import FixturePage, run_synthetic_batch

DAY = "2026-10-08T00:00:00Z"
NEXT = "2026-10-09T00:00:00Z"
AFTER = "2026-10-10T00:00:00Z"
NOW = "2026-10-11T00:00:00Z"
OI1 = "2026-10-08T00:05:00Z"
OI2 = "2026-10-08T00:10:00Z"
OI_END = "2026-10-08T00:10:00Z"


def unix(value, *, millis=False):
    stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    return int(stamp * (1000 if millis else 1))


def sources():
    """A fixture-contract per provider; not assumed to be a verified live schema."""
    return [
        (SeriesSpec("researchbitcoin", "sopr_sth", "BTC", "d1", "ratio"),
         DAY, AFTER,
         {"data": [{"date": "2026-10-08", "value": 0.997},
                   {"date": "2026-10-09", "value": 1.002}]}),
        (SeriesSpec("bitview", "mvrv", "BTC", "d1", "ratio"),
         DAY, AFTER,
         {"index": "1d", "start": (dt.date(2026, 10, 8) - dt.date(2009, 1, 1)).days,
          "data": [1.7, 1.9]}),
        (SeriesSpec("fred", "CPIAUCSL", "USA", "monthly", "index"),
         "2026-08-01T00:00:00Z", "2026-10-01T00:00:00Z",
         {"observations": [
             {"date": "2026-08-01", "value": "321.4", "realtime_start": "2026-09-12"},
             {"date": "2026-09-01", "value": "323.1", "realtime_start": "2026-10-11"}]}),
        (SeriesSpec("yahoo", "close", "BTC-USD", "d1", "USD"),
         DAY, AFTER,
         {"chart": {"result": [{
             "timestamp": [unix(DAY), unix(NEXT)],
             "indicators": {"quote": [{"close": [70000.0, 71000.0]}]}
         }]}}),
        (SeriesSpec("binance", "open_interest", "BTCUSDT", "5m", "BTC"),
         DAY, OI_END,
         [{"timestamp": unix(DAY, millis=True), "sumOpenInterest": "50100"},
          {"timestamp": unix(OI1, millis=True), "sumOpenInterest": "50110"}]),
        (SeriesSpec("bybit", "open_interest", "BTCUSDT", "5m", "BTC"),
         DAY, OI_END,
         {"result": {"list": [
             {"timestamp": str(unix(DAY, millis=True)), "openInterest": "48990"},
             {"timestamp": str(unix(OI1, millis=True)), "openInterest": "49010"}]}}),
    ]


class TestPackageAIntegratedSlice(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="package-a-fixture-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / "package_a_synthetic.db"
        self.lockdir = self.root / "locks"
        self.lockdir.mkdir()
        (self.root / MARKER).write_text(MARKER_VALUE, encoding="utf-8")
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE legacy_untouched(id INTEGER PRIMARY KEY, raw BLOB)")
            db.execute("INSERT INTO legacy_untouched VALUES(1,?)", (b"keep-everthing",))

    def apply(self, spec, start, end, payload, *, status=200, fault=None):
        return run_fixture_page(
            self.path, self.lockdir, spec, start, end,
            FixtureResponse(status, payload), now=NOW, fault=fault)

    def read(self, sql, params=()):
        with sqlite3.connect(self.path) as db:
            return db.execute(sql, params).fetchall()

    def test_six_providers_persist_to_one_synthetic_sqlite(self):
        for spec, start, end, payload in sources():
            with self.subTest(provider=spec.provider):
                result = self.apply(spec, start, end, payload)
                self.assertEqual(result["state"], "PAGE_COMMITTED_OFFLINE", result)
                self.assertEqual(result["new_rows"], 2)
                self.assertTrue(result["cursor_advanced"])
        audit = inspect_fixture(self.path, self.lockdir)
        self.assertEqual(audit["state"], "LOCAL_FIXTURE_INTEGRITY_PASS")
        self.assertEqual((audit["rows"], audit["receipts"]), (12, 6))
        self.assertEqual(len(audit["source_coverage"]), 6)
        self.assertTrue(all(x["historical_completeness"] == "NOT_VERIFIED"
                            for x in audit["source_coverage"]))

    def test_rbn_catalog_raw_scale_kept_as_fraction_without_100x(self):
        metric = SeriesSpec("researchbitcoin", "supply_in_profit_percent",
                            "BTC", "d1", "percent", "fraction_0_1")
        self.apply(metric, DAY, NEXT,
                   {"data": [{"date": "2026-10-08", "value": 0.71}]})
        self.assertEqual(
            self.read("SELECT value,raw_scale FROM package_a_observations")[0],
            (0.71, "fraction_0_1"))

    def test_rbn_mismatched_scale_rejected_before_database(self):
        with self.assertRaises(ValueError):
            SeriesSpec("researchbitcoin", "supply_in_profit_percent", "BTC", "d1", "percent", "native")
        self.assertEqual(self.read(
            "SELECT name FROM sqlite_master WHERE name LIKE 'package_a_%'"), [])

    def test_fred_vintage_is_separate_from_observation_date(self):
        spec, start, end, payload = sources()[2]
        self.apply(spec, start, end, payload)
        rows = self.read(
            "SELECT observed_utc,dimension_key FROM package_a_observations ORDER BY observed_utc")
        self.assertEqual(rows, [("2026-08-01T00:00:00Z", "2026-09-12"),
                                ("2026-09-01T00:00:00Z", "2026-10-11")])

    def test_replay_exact_page_keeps_pk_and_cursor(self):
        spec, start, end, payload = sources()[0]
        first = self.apply(spec,start,end,payload)
        before = self.read("SELECT observed_utc,row_sha256,receipt_id "
                           "FROM package_a_observations ORDER BY observed_utc")
        second = self.apply(spec,start,end,payload)
        self.assertEqual(second["new_rows"], 0)
        self.assertFalse(second["cursor_advanced"])
        self.assertEqual(self.read("SELECT observed_utc,row_sha256,receipt_id "
                                   "FROM package_a_observations ORDER BY observed_utc"), before)
        self.assertEqual(self.read("SELECT COUNT(*) FROM package_a_receipts")[0][0], 2)
        self.assertEqual(self.read("SELECT advanced_by_receipt FROM package_a_cursors")[0][0],
                         first["receipt_id"])

    def test_next_contiguous_window_advances_forward(self):
        spec = sources()[0][0]
        self.apply(spec, DAY, NEXT, {"data":[{"date":"2026-10-08","value":0.997}]})
        result = self.apply(spec, NEXT, AFTER,
                            {"data":[{"date":"2026-10-09","value":1.002}]})
        self.assertTrue(result["cursor_advanced"])
        self.assertEqual(self.read("SELECT end_exclusive_utc FROM package_a_cursors")[0][0], AFTER)

    def test_skipped_cursor_window_fails_closed(self):
        spec = sources()[0][0]
        self.apply(spec, DAY, NEXT, {"data":[{"date":"2026-10-08","value":1.0}]})
        n = self.read("SELECT COUNT(*) FROM package_a_receipts")[0][0]
        with self.assertRaisesRegex(ValueError, "cursor"):
            self.apply(spec, AFTER, "2026-10-11T00:00:00Z",
                       {"data":[{"date":"2026-10-10","value":1.0}]})
        self.assertEqual(self.read("SELECT COUNT(*) FROM package_a_receipts")[0][0], n)

    def test_conflicting_replay_rolls_back_without_replacing_value(self):
        spec = sources()[0][0]
        self.apply(spec, DAY, NEXT, {"data":[{"date":"2026-10-08","value":1.0}]})
        original = self.read("SELECT value,row_sha256 FROM package_a_observations")
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            self.apply(spec, DAY, NEXT, {"data":[{"date":"2026-10-08","value":7.0}]})
        self.assertEqual(self.read("SELECT value,row_sha256 FROM package_a_observations"), original)
        self.assertEqual(self.read("SELECT COUNT(*) FROM package_a_receipts")[0][0], 1)

    def test_rate_limited_http_429_records_failed_receipt_no_cursor(self):
        spec,start,end,_ = sources()[4]
        result = self.apply(spec,start,end,{"error":"fake 429"},status=429)
        self.assertEqual(result["state"], "PAGE_NOT_COMMITTED")
        self.assertEqual(result["failure_class"], "SYNTHETIC_HTTP_429")
        self.assertEqual(self.read("SELECT status,http_status FROM package_a_receipts"),
                         [("failed",429)])
        self.assertEqual(self.read("SELECT COUNT(*) FROM package_a_cursors")[0][0], 0)
        self.assertEqual(self.read("SELECT COUNT(*) FROM package_a_observations")[0][0], 0)

    def test_empty_does_not_claim_complete_or_advance(self):
        spec,start,end,_ = sources()[0]
        result = self.apply(spec,start,end,{"data":[]})
        self.assertEqual(result["state"], "PAGE_EMPTY_UNVERIFIED")
        self.assertFalse(result["cursor_advanced"])
        self.assertEqual(self.read("SELECT status FROM package_a_receipts"), [("empty",)])
        self.assertEqual(self.read("SELECT COUNT(*) FROM package_a_cursors")[0][0], 0)

    def test_invalid_shape_records_rejected_without_points(self):
        spec,start,end,_ = sources()[2]
        result = self.apply(spec,start,end,{"observations":[{"date":"2026-08-01","value":"321"}]})
        self.assertEqual(result["state"], "PAGE_NOT_COMMITTED")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.read("SELECT COUNT(*) FROM package_a_observations")[0][0], 0)

    def test_oi_gap_rejected_without_cursor(self):
        spec,start,end,payload = sources()[4]
        bad = [dict(payload[0]),dict(payload[1])]
        bad[1]["timestamp"] = unix(OI2,millis=True)
        result = self.apply(spec,start,end,bad)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.read("SELECT COUNT(*) FROM package_a_cursors")[0][0], 0)

    def test_oi_truncated_final_five_minute_bar_rejected_no_cursor(self):
        spec, start, end, payload = sources()[4]
        result = self.apply(spec, start, "2026-10-08T00:15:00Z", payload)
        self.assertEqual(result["state"], "PAGE_NOT_COMMITTED")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.read("SELECT COUNT(*) FROM package_a_cursors")[0][0], 0)
        self.assertEqual(self.read("SELECT COUNT(*) FROM package_a_observations")[0][0], 0)

    def test_oi_window_requires_exact_five_minute_alignment(self):
        spec, start, end, payload = sources()[5]
        result = self.apply(spec, start, "2026-10-08T00:11:00Z", payload)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.read("SELECT COUNT(*) FROM package_a_cursors")[0][0], 0)

    def test_time_window_exclusive_end_rejects_extra_day(self):
        spec,start,end,payload = sources()[0]
        result = self.apply(spec,start,NEXT,payload)
        self.assertEqual(result["status"], "rejected")

    def test_three_crash_injection_points_rollback_entire_page(self):
        spec,start,end,payload = sources()[0]
        for point in ("after_receipt","after_rows","after_cursor"):
            with self.subTest(point=point):
                with self.assertRaises(ledger.SimulatedCrash):
                    self.apply(spec,start,end,payload,fault=point)
                self.assertEqual(self.read(
                    "SELECT COUNT(*) FROM package_a_receipts")[0][0], 0)
                self.assertEqual(self.read(
                    "SELECT COUNT(*) FROM package_a_observations")[0][0], 0)
                self.assertEqual(self.read(
                    "SELECT COUNT(*) FROM package_a_cursors")[0][0], 0)
        self.assertEqual(self.apply(spec,start,end,payload)["new_rows"], 2)

    def test_lock_busy_blocks_before_sqlite_connection(self):
        from unittest.mock import patch
        spec,start,end,payload = sources()[0]
        with hold_writer_lock(self.lockdir, namespace=LOCK_NAMESPACE):
            with patch("scripts.package_a_ingest_fixture.sqlite3.connect",
                       side_effect=AssertionError("Must not touch fixture DB")):
                result = self.apply(spec,start,end,payload)
        self.assertEqual(result["state"], "BLOCKED_WRITER_BUSY")
        self.assertEqual(result["sqlite_connections_started"], 0)
        self.assertEqual(self.read("SELECT name FROM sqlite_master WHERE name='package_a_receipts'"), [])

    def test_other_process_lock_blocks_before_schema_migration(self):
        spec,start,end,payload = sources()[0]
        code = (
            "import sys\n"
            "from scripts.market_r10e11_writer_exclusion import hold_writer_lock\n"
            "with hold_writer_lock(sys.argv[1],namespace=sys.argv[2]):\n"
            " print('HELD',flush=True)\n"
            " sys.stdin.readline()\n"
        )
        env = dict(os.environ)
        root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH","")
        child = subprocess.Popen([sys.executable,"-c",code,str(self.lockdir),LOCK_NAMESPACE],
                                 cwd=root,env=env,stdout=subprocess.PIPE,
                                 stdin=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            # The child only writes one HELD line; it is not an external API worker.
            self.assertEqual(child.stdout.readline().strip(), "HELD")
            self.assertEqual(self.apply(spec,start,end,payload)["state"], "BLOCKED_WRITER_BUSY")
            child.stdin.write("\n")
            child.stdin.flush()
            self.assertEqual(child.wait(timeout=5),0)
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
        self.assertEqual(self.apply(spec,start,end,payload)["new_rows"], 2)

    def test_wrong_namespace_is_not_global_protection(self):
        spec,start,end,payload=sources()[0]
        with hold_writer_lock(self.lockdir,namespace="wrong-namespace"):
            self.assertEqual(self.apply(spec,start,end,payload)["new_rows"], 2)

    def test_existing_legacy_table_remains_bytewise_untouched(self):
        previous = self.read("SELECT id,raw FROM legacy_untouched")
        for spec,start,end,payload in sources():
            self.apply(spec,start,end,payload)
        self.assertEqual(self.read("SELECT id,raw FROM legacy_untouched"), previous)

    def test_corrupted_raw_payload_sha_makes_integrity_gate_fail(self):
        spec,start,end,payload=sources()[0]
        self.apply(spec,start,end,payload)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE package_a_receipts SET raw_sha256=?",
                       ("f"*64,))
        audit=inspect_fixture(self.path,self.lockdir)
        self.assertEqual(audit["state"], "LOCAL_FIXTURE_INTEGRITY_FAIL")
        self.assertEqual(audit["raw_digest_errors"], 1)

    def test_fixture_guard_rejects_prod_style_file_and_missing_marker(self):
        spec,start,end,payload=sources()[0]
        with self.assertRaises(ValueError):
            run_fixture_page(self.root/"btc_research.db",self.lockdir,spec,
                             start,end,FixtureResponse(200,payload),now=NOW)
        (self.root/MARKER).unlink()
        with self.assertRaises(ValueError):
            self.apply(spec,start,end,payload)

    def test_batch_six_providers_one_integrated_offline_run(self):
        pages = [FixturePage(spec, start, end, FixtureResponse(200, payload))
                 for spec,start,end,payload in sources()]
        report = run_synthetic_batch(self.path,self.lockdir,pages,now=NOW)
        self.assertEqual(report["state"],"BATCH_OFFLINE_ACCEPTED",report)
        self.assertEqual(report["requests_attempted"],6)
        self.assertEqual(report["new_observations"],12)
        self.assertEqual(report["audit"]["rows"],12)
        self.assertFalse(report["real_provider_windows_verified"])
        self.assertFalse(report["production_authorized"])

    def test_batch_fails_on_429_and_resumes_from_safe_checkpoint(self):
        a,b,c = sources()[0], sources()[4], sources()[2]
        first = [FixturePage(a[0],a[1],a[2],FixtureResponse(200,a[3])),
                 FixturePage(b[0],b[1],b[2],FixtureResponse(429,{"rate_limit":"synthetic"})),
                 FixturePage(c[0],c[1],c[2],FixtureResponse(200,c[3]))]
        r = run_synthetic_batch(self.path,self.lockdir,first,now=NOW)
        self.assertEqual(r["state"],"BATCH_BLOCKED")
        self.assertEqual((r["requests_attempted"],r["remaining_requests_not_run"]),(2,1))
        self.assertEqual(r["audit"]["rows"],2)
        self.assertEqual(self.read("SELECT status FROM package_a_receipts ORDER BY rowid"),
                         [("ok",),("failed",)])
        resumed = [FixturePage(b[0],b[1],b[2],FixtureResponse(200,b[3])),
                   FixturePage(c[0],c[1],c[2],FixtureResponse(200,c[3]))]
        retry=run_synthetic_batch(self.path,self.lockdir,resumed,now=NOW)
        self.assertEqual(retry["state"],"BATCH_OFFLINE_ACCEPTED")
        self.assertEqual(retry["audit"]["rows"],6)
        self.assertEqual(self.read("SELECT status,COUNT(*) FROM package_a_receipts GROUP BY status"),
                         [("failed",1),("ok",3)])

    def test_backward_historical_page_adds_older_data_without_regressing_cursor(self):
        spec=sources()[0][0]
        first=self.apply(spec,NEXT,AFTER,{"data":[{"date":"2026-10-09","value":1.002}]})
        self.assertTrue(first["cursor_advanced"])
        back=self.apply(spec,DAY,NEXT,{"data":[{"date":"2026-10-08","value":0.997}]})
        self.assertEqual(back["new_rows"],1)
        self.assertFalse(back["cursor_advanced"])
        self.assertEqual(self.read("SELECT end_exclusive_utc FROM package_a_cursors"),[(AFTER,)])
        self.assertEqual(self.read("SELECT archived_rows FROM package_a_coverage"),[(2,)])

    def test_batch_budget_blocks_without_initializing_schema(self):
        page=sources()[0]
        fixtures=[FixturePage(page[0],page[1],page[2],FixtureResponse(200,page[3]))]
        for limit in (0,-1,33,True):
            with self.subTest(limit=limit),self.assertRaises(ValueError):
                run_synthetic_batch(self.path,self.lockdir,fixtures,now=NOW,max_pages=limit)
        self.assertFalse(self.read("SELECT name FROM sqlite_master "
                                   "WHERE name='package_a_receipts'"))

    def test_batch_lock_busy_keeps_database_uninitialized(self):
        spec,start,end,payload=sources()[0]
        pages=[FixturePage(spec,start,end,FixtureResponse(200,payload))]
        with hold_writer_lock(self.lockdir,namespace=LOCK_NAMESPACE):
            r=run_synthetic_batch(self.path,self.lockdir,pages,now=NOW)
        self.assertEqual(r["state"],"BATCH_BLOCKED")
        self.assertIsNone(r["audit"])
        self.assertEqual(r["pages"][0]["state"],"BLOCKED_WRITER_BUSY")
        self.assertEqual(self.read("SELECT name FROM sqlite_master "
                                   "WHERE name='package_a_receipts'"),[])

    def test_normalized_hash_and_coverage_tampering_fail_integrity(self):
        spec,start,end,payload=sources()[0]
        self.apply(spec,start,end,payload)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE package_a_observations SET value=42 "
                       "WHERE observed_utc=?", (DAY,))
        self.assertEqual(inspect_fixture(self.path,self.lockdir)["normalized_digest_errors"],1)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE package_a_coverage SET archived_rows=99")
        bad=inspect_fixture(self.path,self.lockdir)
        self.assertEqual(bad["state"],"LOCAL_FIXTURE_INTEGRITY_FAIL")
        self.assertEqual(bad["coverage_errors"],1)

    def test_runner_registers_package_suite_without_hardcoded_old_count(self):
        path=Path(__file__).resolve().parents[1]/"run_all.py"
        t=ast.parse(path.read_text(encoding="utf-8"))
        items=[ast.literal_eval(x.value) for x in t.body
               if isinstance(x,ast.Assign)
               and any(isinstance(k,ast.Name) and k.id=="TESTS" for k in x.targets)]
        self.assertEqual(len(items),1)
        self.assertGreaterEqual(len(items[0]),53)
        self.assertEqual(sum("test_package_a_integrated_ingestion" in cmd
                             for _,cmd in items[0]),1)


if __name__ == "__main__":
    unittest.main()
