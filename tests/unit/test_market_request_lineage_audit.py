"""Offline request-level provenance acceptance: data, run, SHA, cursor and gaps."""
import datetime as dt
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from storage.market_context import install, raw_payload, store_derivative
from scripts import market_history_pilot as collect
from scripts.market_request_lineage_audit import audit

BASE=dt.datetime(2026,10,10,0,45,tzinfo=dt.timezone.utc)
ENDPOINT="https://fapi.binance.com/futures/data/openInterestHist"


class LineageAuditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"btc_research.db"
        self.db=sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.execute("PRAGMA foreign_keys=ON")
        install(self.db)
        sha=raw_payload(self.db,"binance",ENDPOINT,b'{"legacy":"true"}',"application/json")
        store_derivative(self.db,provider="binance",symbol="BTCUSDT",
                         metric="open_interest",observed_utc=BASE.isoformat(),
                         interval_label="5m",value=100.0,unit="BTC",
                         endpoint=ENDPOINT,sha=sha)
        self.db.commit()

    def _collect(self,minutes):
        # 1, 2 or more contiguous oldest-preceding 5m intervals.
        oldest=self.db.execute("SELECT MIN(observed_utc) FROM market_derivatives"
                 " WHERE provider='binance' AND metric='open_interest'").fetchone()[0]
        end=dt.datetime.fromisoformat(oldest)
        recs=[]
        for index in range(minutes):
            instant=end-dt.timedelta(minutes=5*(index+1))
            recs.append({"symbol":"BTCUSDT","timestamp":int(instant.timestamp()*1000),
                         "sumOpenInterest":str(99-index),
                         "sumOpenInterestValue":"8100000"})
        result=collect.execute(self.db,"binance","open_interest",
                               fetch=lambda url,params:json.dumps(recs).encode())
        self.assertEqual(result["status"],"success",result)
        return result

    def test_empty_new_schema_is_not_misrepresented_as_completed_requests(self):
        result=audit(self.path)
        self.assertEqual(result["quality_state"],"NO_REQUEST_RECEIPTS_YET")
        self.assertEqual(result["requests_total"],0)
        self.assertEqual(result["historical_completeness"],"NOT_VERIFIED")

    def test_one_valid_page_matches_sha_pagination_and_stored_rows(self):
        result=self._collect(3)
        view=audit(self.path)
        self.assertEqual(view["quality_state"],"PASS_REQUEST_LINEAGE",view)
        self.assertEqual(view["requests_verified"],1)
        self.assertEqual(view["source_records_verified"],3)
        self.assertEqual(view["observations_verified"],3)
        self.assertEqual(view["by_stream"]["binance_open_interest"]["points"],3)
        self.assertEqual(view["issues"],[])
        self.assertTrue(result["request_lineage_logged"])

    def test_two_contiguous_requests_have_verified_cursor(self):
        self._collect(3)
        self._collect(2)
        result=audit(self.path)
        self.assertEqual(result["quality_state"],"PASS_REQUEST_LINEAGE",result)
        self.assertEqual(result["requests_verified"],2)
        self.assertEqual(result["observations_verified"],5)
        self.assertEqual(result["by_stream"]["binance_open_interest"]["successful"],2)

    def test_edited_public_endtime_and_modified_run_count_are_rejected(self):
        self._collect(1)
        self.db.execute("UPDATE market_request_lineage SET requested_end_ms=requested_end_ms+300000")
        self.db.execute("UPDATE market_source_runs SET points=12")
        self.db.commit()
        report=audit(self.path)
        self.assertEqual(report["quality_state"],"REVIEW_REQUIRED")
        issues=str(report["issues"])
        self.assertIn("page boundary",issues)
        self.assertIn("source-run status or counts disagreement",issues)

    def test_missing_persisted_derivative_is_caught(self):
        self._collect(1)
        self.db.execute("DELETE FROM market_derivatives WHERE observed_utc!=?",
                        (BASE.isoformat(timespec="seconds"),))
        self.db.commit()
        view=audit(self.path)
        self.assertEqual(view["quality_state"],"REVIEW_REQUIRED")
        self.assertTrue(any("persisted count" in str(i) for i in view["issues"]))

    def test_failed_attempt_and_clean_legacy_are_reconciled(self):
        def denied(url,params):
            raise HTTPError(url,429,"private",None,None)
        bad=collect.execute(self.db,"binance","open_interest",fetch=denied)
        self.assertEqual(bad["status"],"failed")
        view=audit(self.path)
        self.assertEqual(view["quality_state"],"PASS_REQUEST_LINEAGE",view)
        self.assertEqual(view["failed"],1)
        self.assertEqual(view["requests_verified"],1)
        self.assertEqual(view["source_records_verified"],0)
        self.assertEqual(view["issues"],[])

    def test_absent_db_not_created(self):
        missing=Path(self.tmp.name)/"absent.db"
        with self.assertRaises(FileNotFoundError):
            audit(missing)
        self.assertFalse(missing.exists())

if __name__=="__main__":
    unittest.main()
