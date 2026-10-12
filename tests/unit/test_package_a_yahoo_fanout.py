"""One Yahoo source request -> 5 independent atomic OHLCV ledger streams.

All tests only use marker-guarded disposable SQLite, legacy Yahoo's original
builder with a one-shot fake opener, and synthetic RAW bytes. NEVER real HTTP.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.package_a_yahoo_fanout import (
    METRICS,run_offline_yahoo_ohlcv_fanout,
)
from scripts.package_a_ingest_fixture import MARKER,MARKER_VALUE,inspect_fixture
from scripts.market_r10e11_writer_exclusion import hold_writer_lock
from storage.package_a_ledger import SimulatedCrash
from tests.unit.test_package_a_frozen_legacy_transport import (
    FrozenLegacyTransportTests,raw,DAY,
)


END="2026-10-13T00:00:00Z"
NOW="2026-10-14T00:00:00Z"


class TestPackageAYahooOHLCVFanout(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(prefix="package-a-fixture-")
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.db=self.root/"package_a_synthetic.db"
        self.locks=self.root/"locks"
        self.locks.mkdir()
        (self.root/MARKER).write_text(MARKER_VALUE,encoding="utf-8")
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE legacy_untouched(id INTEGER PRIMARY KEY,data TEXT)")
            c.execute("INSERT INTO legacy_untouched VALUES(1,'ORIGINAL')")

    def body(self,**changes):
        return raw(FrozenLegacyTransportTests._yahoo_daily_body(self,**changes))

    def apply(self,*,symbol="SPY",body=None,status=200,fault=None):
        return run_offline_yahoo_ohlcv_fanout(
            self.db,self.locks,symbol,DAY,END,
            raw_bytes=self.body() if body is None else body,
            status_code=status,now=NOW,fault=fault)

    def sql(self,statement):
        with sqlite3.connect(self.db) as c:
            return c.execute(statement).fetchall()

    def test_spy_five_metric_native_single_request_and_atomic_five_cursors(self):
        data=self.body()
        result=self.apply(body=data)
        self.assertEqual(result["state"],"YAHOO_FANOUT_COMMITTED_OFFLINE")
        self.assertEqual(result["frozen_http_requests"],1)
        self.assertEqual(result["receipts_persisted"],5)
        self.assertEqual(result["metric_order"],list(METRICS))
        self.assertEqual(result["new_observations"],15)
        self.assertEqual(result["parsed_observations"],15)
        self.assertTrue(result["all_five_cursors_advanced"])
        self.assertTrue(result["all_metric_states_identical"])
        self.assertTrue(result["all_five_metrics_one_sqlite_transaction"])
        self.assertEqual(result["precommit_evidence"]["native_request_lineage"],
                         "NATIVE_FROZEN_REQUEST_LINEAGE_PASS")
        self.assertEqual(result["raw_sha256"],hashlib.sha256(data).hexdigest())
        self.assertRegex(result["source_request_group_id"],r"^[0-9a-f]{64}$")
        self.assertFalse(result["group_id_persisted"])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),[(5,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_observations"),[(15,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_cursors"),[(5,)])
        self.assertEqual(self.sql("SELECT COUNT(DISTINCT raw_sha256) "
                                  "FROM package_a_receipts"),[(1,)])
        self.assertEqual(self.sql("SELECT COUNT(DISTINCT request_sha256) "
                                  "FROM package_a_request_provenance"),[(1,)])
        self.assertEqual(self.sql("SELECT COUNT(DISTINCT receipt_id) "
                                  "FROM package_a_request_provenance"),[(5,)])
        self.assertEqual(self.sql("SELECT COUNT(DISTINCT end_exclusive_utc) "
                                  "FROM package_a_cursors"),[(1,)])
        self.assertEqual(self.sql("SELECT data FROM legacy_untouched"),[("ORIGINAL",)])
        audit=inspect_fixture(self.db,self.locks)
        self.assertEqual(audit["state"],"LOCAL_FIXTURE_INTEGRITY_PASS")
        self.assertEqual(audit["receipt_reconstruction"]["successful_receipts_reparsed"],5)
        self.assertEqual(audit["native_request_lineage"]["native_requests_validated"],5)
        self.assertTrue(all(x["historical_completeness"]=="NOT_VERIFIED"
                            for x in audit["source_manifest"]["streams"]))

    def test_fanout_spy_nontrading_weekend_is_not_a_fabricated_gap(self):
        self.apply()
        report=inspect_fixture(self.db,self.locks)
        for stream in report["source_manifest"]["streams"]:
            self.assertEqual(stream["source_native_semantics"],"YAHOO_EXCHANGE_SESSIONS")
            self.assertEqual(stream["local_grid_state"],
                             "NOT_APPLICABLE_SOURCE_CALENDAR_OR_EVENTS")
            self.assertIsNone(stream["local_gap_evidence"])
            self.assertEqual(stream["archived_rows"],3)
        self.assertEqual(self.sql("SELECT DISTINCT observed_utc FROM "
                                  "package_a_observations ORDER BY observed_utc"),
                         [("2026-10-08T00:00:00Z",),("2026-10-09T00:00:00Z",),
                          ("2026-10-12T00:00:00Z",)])

    def test_spy_gld_btc_use_one_source_request_each_and_isolate_streams(self):
        for symbol in ("SPY","GLD","BTC-USD"):
            self.assertEqual(self.apply(symbol=symbol)["frozen_http_requests"],1)
        self.assertEqual(self.sql("SELECT symbol,COUNT(*) FROM package_a_observations "
                                  "GROUP BY symbol ORDER BY symbol"),
                         [("BTC-USD",15),("GLD",15),("SPY",15)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),[(15,)])
        audit=inspect_fixture(self.db,self.locks)
        self.assertEqual(audit["state"],"LOCAL_FIXTURE_INTEGRITY_PASS")
        self.assertEqual(audit["native_request_lineage"]["native_requests_validated"],15)
        self.assertEqual(self.sql("SELECT COUNT(DISTINCT request_sha256) "
                                  "FROM package_a_request_provenance"),[(3,)])

    def test_identical_replay_keeps_all_earlier_rows_and_cursor_owners(self):
        first=self.apply()
        owners=self.sql("SELECT receipt_id FROM package_a_observations ORDER BY rowid")
        cursors=self.sql("SELECT advanced_by_receipt FROM package_a_cursors "
                         "ORDER BY metric")
        again=self.apply()
        self.assertEqual(first["source_request_group_id"],
                         again["source_request_group_id"])
        self.assertEqual(again["state"],"YAHOO_FANOUT_COMMITTED_OFFLINE")
        self.assertEqual(again["frozen_http_requests"],1)
        self.assertEqual(again["new_observations"],0)
        self.assertFalse(again["all_five_cursors_advanced"])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),[(10,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_observations"),[(15,)])
        self.assertEqual(owners,self.sql("SELECT receipt_id FROM package_a_observations "
                                         "ORDER BY rowid"))
        self.assertEqual(cursors,self.sql("SELECT advanced_by_receipt FROM "
                                          "package_a_cursors ORDER BY metric"))
        self.assertEqual(inspect_fixture(self.db,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")

    def test_conflicting_close_after_four_prior_metrics_rolls_back_whole_fanout(self):
        self.apply()
        source=FrozenLegacyTransportTests._yahoo_daily_body(self)
        source["chart"]["result"][0]["indicators"]["quote"][0]["close"][0]=503.
        before=self.sql("SELECT COUNT(*) FROM package_a_receipts")
        with self.assertRaisesRegex(ValueError,"Conflicting historical observation"):
            self.apply(body=raw(source))
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),before)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_observations"),[(15,)])
        self.assertEqual(inspect_fixture(self.db,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")

    def test_fault_after_second_metric_rolls_back_all_five_receipts_and_cursors(self):
        with self.assertRaisesRegex(SimulatedCrash,"YAHOO_FANOUT_ATOMIC_ROLLBACK"):
            self.apply(fault="after_second_metric")
        for table in ("package_a_receipts","package_a_observations",
                      "package_a_cursors","package_a_request_provenance"):
            self.assertEqual(self.sql("SELECT COUNT(*) FROM "+table),[(0,)])
        self.assertEqual(self.sql("SELECT data FROM legacy_untouched"),[("ORIGINAL",)])
        self.assertEqual(self.apply()["new_observations"],15)

    def test_fault_after_fourth_metric_rolls_back_every_previously_written_metric(self):
        with self.assertRaisesRegex(SimulatedCrash,"YAHOO_FANOUT_ATOMIC_ROLLBACK"):
            self.apply(fault="after_fourth_metric")
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),[(0,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_coverage"),[(0,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])

    def test_fault_immediately_before_shared_gate_rolls_back_atomically(self):
        with self.assertRaisesRegex(SimulatedCrash,"YAHOO_FANOUT_ATOMIC_ROLLBACK"):
            self.apply(fault="before_precommit")
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_observations"),[(0,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),[(0,)])

    def test_cross_field_invalid_high_does_not_commit_any_metric(self):
        result=self.apply(body=self.body(bad="high_below_low"))
        self.assertEqual(result["state"],"YAHOO_FANOUT_NOT_COMMITTED")
        self.assertEqual(result["status"],"rejected")
        self.assertEqual(result["frozen_http_requests"],1)
        self.assertEqual(result["new_observations"],0)
        self.assertEqual(self.sql("SELECT status,COUNT(*) FROM package_a_receipts "
                                  "GROUP BY status"),[("rejected",5)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_observations"),[(0,)])
        self.assertEqual(inspect_fixture(self.db,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")

    def test_partial_fields_missing_volume_rejected_whole_group(self):
        data=FrozenLegacyTransportTests._yahoo_daily_body(self)
        data["chart"]["result"][0]["indicators"]["quote"][0].pop("volume")
        result=self.apply(body=raw(data))
        self.assertEqual(result["status"],"rejected")
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),[(5,)])

    def test_invalid_quote_short_array_rejects_five_metrics_not_just_close(self):
        result=self.apply(body=self.body(bad="short_close"))
        self.assertEqual(result["state"],"YAHOO_FANOUT_NOT_COMMITTED")
        self.assertEqual(result["status"],"rejected")
        self.assertEqual(result["frozen_http_requests"],1)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),[(5,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])

    def test_single_http_429_records_five_failed_attempts_without_cursors(self):
        result=self.apply(status=429,body=b"<html>mock 429</html>")
        self.assertEqual(result["state"],"YAHOO_FANOUT_NOT_COMMITTED")
        self.assertEqual(result["failure_class"],"SYNTHETIC_HTTP_429")
        self.assertEqual(result["frozen_http_requests"],1)
        self.assertEqual(self.sql("SELECT status,http_status,COUNT(*) FROM "
                                  "package_a_receipts GROUP BY status,http_status"),
                         [("failed",429,5)])
        self.assertEqual(self.sql("SELECT COUNT(DISTINCT raw_sha256) "
                                  "FROM package_a_receipts"),[(1,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_observations"),[(0,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])
        self.assertEqual(inspect_fixture(self.db,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")

    def test_503_failure_then_single_http_retry_fans_out_all_five(self):
        failed=self.apply(status=503,body=b"mock 503 failure")
        self.assertEqual(failed["frozen_http_requests"],1)
        success=self.apply()
        self.assertEqual(success["frozen_http_requests"],1)
        self.assertEqual(success["new_observations"],15)
        self.assertEqual(self.sql("SELECT status,COUNT(*) FROM package_a_receipts "
                                  "GROUP BY status ORDER BY status"),
                         [("failed",5),("ok",5)])
        self.assertEqual(inspect_fixture(self.db,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")

    def test_timeout_has_no_raw_response_and_no_cursor_for_any_metric(self):
        result=self.apply(status=None,body=b"")
        self.assertEqual(result["failure_class"],"SYNTHETIC_TRANSPORT_TIMEOUT")
        self.assertEqual(result["frozen_http_requests"],1)
        self.assertIsNone(result["raw_sha256"])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts "
                                  "WHERE raw_payload IS NULL"),[(5,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])
        self.assertEqual(inspect_fixture(self.db,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")

    def test_empty_all_five_fields_is_unverified_not_successful_hist_archive(self):
        data=FrozenLegacyTransportTests._yahoo_daily_body(self)
        q=data["chart"]["result"][0]["indicators"]["quote"][0]
        for metric in METRICS:
            q[metric]=[None,None,None]
        result=self.apply(body=raw(data))
        self.assertEqual(result["state"],"YAHOO_FANOUT_EMPTY_UNVERIFIED")
        self.assertEqual(result["status"],"empty")
        self.assertEqual(self.sql("SELECT status,COUNT(*) FROM package_a_receipts "
                                  "GROUP BY status"),[("empty",5)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])
        self.assertEqual(inspect_fixture(self.db,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")

    def test_global_flock_contention_prevents_even_fake_one_shot_http_and_sql(self):
        with hold_writer_lock(self.locks,namespace="btc-research-global-writer-v1"):
            result=self.apply()
        self.assertEqual(result["state"],"BLOCKED_WRITER_BUSY")
        self.assertEqual(result["frozen_http_requests"],0)
        self.assertEqual(result["receipts_persisted"],0)
        self.assertEqual(result["sqlite_connections_started"],0)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM sqlite_master WHERE "
                                  "name='package_a_receipts'"),[(0,)])

    def test_wrong_symbol_or_fault_blocked_before_lock_http_sql(self):
        for symbol in ("ETH-USD","",None,123):
            with self.subTest(symbol=symbol):
                with self.assertRaises(ValueError):
                    self.apply(symbol=symbol)
        for fault in ("after_first_metric","skip_gate",1):
            with self.subTest(fault=fault):
                with self.assertRaises(ValueError):
                    self.apply(fault=fault)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM sqlite_master WHERE "
                                  "name='package_a_receipts'"),[(0,)])

    def test_empty_or_malformed_json_200_rejected_with_byte_exact_raw(self):
        blob=b"{broken json"
        result=self.apply(body=blob)
        self.assertEqual(result["status"],"rejected")
        self.assertEqual(result["frozen_http_requests"],1)
        self.assertEqual(self.sql("SELECT DISTINCT raw_payload,raw_sha256 "
                                  "FROM package_a_receipts"),
                         [(blob,hashlib.sha256(blob).hexdigest())])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_cursors"),[(0,)])
        self.assertEqual(inspect_fixture(self.db,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_PASS")

    def test_tampered_original_provenance_blocks_second_symbol_fanout_precommit(self):
        self.apply()
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE package_a_request_provenance "
                      "SET request_params_json='{}'")
        with self.assertRaisesRegex(ValueError,"BLOCKED_RAW_SQL_PRECOMMIT_QUALITY_GATE"):
            self.apply(symbol="GLD")
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_receipts"),[(5,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_observations"),[(15,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM package_a_cursors"),[(5,)])
        self.assertEqual(inspect_fixture(self.db,self.locks)["state"],
                         "LOCAL_FIXTURE_INTEGRITY_FAIL")


if __name__=="__main__":
    unittest.main()
