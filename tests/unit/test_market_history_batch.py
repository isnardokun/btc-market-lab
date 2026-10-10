"""Bounded history batches never invent completeness or silently ignore failures."""
import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from scripts import market_history_batch as batch
from storage.market_context import install, raw_payload, store_derivative

BASE=dt.datetime(2026,10,10,0,45,tzinfo=dt.timezone.utc)

class MarketHistoryBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dbpath=Path(self.temp.name)/"btc_research.db"
        with sqlite3.connect(self.dbpath) as db:
            install(db)
            sha=raw_payload(db,"binance","https://fapi.binance.com/futures/data/openInterestHist",
                            b'{"seed":"fixture"}',"application/json")
            store_derivative(db,provider="binance",symbol="BTCUSDT",
                             metric="open_interest",observed_utc=BASE.isoformat(),
                             interval_label="5m",value=100,unit="BTC",
                             endpoint="https://fapi.binance.com/futures/data/openInterestHist",sha=sha)

    def _pass_audits(self):
        return (patch.object(batch,"reconcile",return_value={"state":"PASS_SQLITE_TO_RAW","rows":1,"verified":1,"issues_count":0}),
                patch.object(batch,"temporal",return_value={"quality_state":"SNAPSHOT_INTERNAL_QA_OK","historical_completeness":"NOT_VERIFIED","issues":[]}),
                patch.object(batch,"lineage",return_value={
                    "quality_state":"PASS_REQUEST_LINEAGE","requests_total":1,
                    "requests_verified":1,"success":1,"failed":0,"issues":[]}))

    def test_plan_only_has_no_network_or_missing_db_creation(self):
        missing=Path(self.temp.name)/"absent.db"
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code=batch.main(["--db",str(missing),"--provider","binance",
                             "--metric","open_interest","--max-pages","5"])
        self.assertEqual(code,0)
        self.assertFalse(missing.exists())
        self.assertIn("PLAN_ONLY",output.getvalue())

    def test_maximum_budget_enforced_and_cooldown(self):
        touched=[];pause=[]
        def fetch(url,params):
            touched.append(params.copy())
            t=BASE-dt.timedelta(minutes=5*len(touched))
            return json.dumps([{"symbol":"BTCUSDT","timestamp":int(t.timestamp()*1000),
                                "sumOpenInterest":"100","sumOpenInterestValue":"8200000"}]).encode()
        first,second,third=self._pass_audits()
        with first,second,third:
            result=batch.process(self.dbpath,"binance","open_interest",max_pages=3,
                                 pause_seconds=1,fetch=fetch,sleep=pause.append)
        self.assertEqual(result["pages_attempted"],3)
        self.assertEqual(result["pages_successful"],3)
        self.assertEqual(result["rows_added"],3)
        self.assertEqual(result["stopped_because"],"page_budget_exhausted")
        self.assertEqual(result["batch_outcome"],"BUDGET_COMPLETED")
        self.assertEqual(len(pause),2)
        self.assertTrue(result["post_audit_ok"])
        with sqlite3.connect(self.dbpath) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM market_derivatives").fetchone()[0],4)

    def test_empty_page_stops_without_retry_and_without_claim_of_all_history(self):
        calls=[]
        def fetch(url,params):
            calls.append(1)
            return b"[]"
        first,second,third=self._pass_audits()
        with first,second,third:
            result=batch.process(self.dbpath,"binance","open_interest",max_pages=20,
                                 pause_seconds=1,fetch=fetch,sleep=lambda x:None)
        self.assertEqual(len(calls),1)
        self.assertEqual(result["stopped_because"],"empty")
        self.assertEqual(result["batch_outcome"],"SOURCE_RETURNED_EMPTY")
        self.assertEqual(result["rows_added"],0)
        self.assertEqual(result["historical_completeness"],"NOT_VERIFIED")

    def test_http_failure_stops_without_retry_and_preserves_cursor(self):
        from urllib.error import HTTPError
        calls=[]
        def fetch(url,params):
            calls.append(1)
            raise HTTPError(url,429,"rate limited",None,None)
        first,second,third=self._pass_audits()
        with first,second,third:
            result=batch.process(self.dbpath,"binance","open_interest",max_pages=20,
                                 pause_seconds=1,fetch=fetch,sleep=lambda x:None)
        self.assertEqual(len(calls),1)
        self.assertEqual(result["stopped_because"],"failed")
        self.assertEqual(result["batch_outcome"],"STOPPED_WITHOUT_SUCCESS")
        self.assertTrue(result["post_audit_ok"],"preserved data quality does not imply successful batch")
        self.assertEqual(result["pages_successful"],0)
        self.assertEqual(result["page_evidence"][0]["error_code"],"HTTPError_429")

    def test_invalid_lineage_after_first_page_blocks_second_http_call(self):
        seen=[]
        def fetch(url,params):
            seen.append(dict(params))
            t=BASE-dt.timedelta(minutes=5*len(seen))
            return json.dumps([{"symbol":"BTCUSDT","timestamp":int(t.timestamp()*1000),
                                "sumOpenInterest":"100","sumOpenInterestValue":"8200000"}]).encode()
        with patch.object(batch,"reconcile",return_value={
             "state":"PASS_SQLITE_TO_RAW"}),patch.object(batch,"temporal",
             return_value={"quality_state":"SNAPSHOT_INTERNAL_QA_OK","issues":[]}),patch.object(
             batch,"lineage",return_value={"quality_state":"REVIEW_REQUIRED",
             "requests_total":1,"requests_verified":0,"issues":["tampered receipt"]}):
            result=batch.process(self.dbpath,"binance","open_interest",
                                 max_pages=10,pause_seconds=1,fetch=fetch,sleep=lambda s:None)
        self.assertEqual(len(seen),1,"must audit previous request before any next API call")
        self.assertEqual(result["pages_successful"],1)
        self.assertEqual(result["stopped_because"],"per_page_provenance_audit_failed")
        self.assertFalse(result["page_qa"][0]["ok"])
        self.assertEqual(result["batch_outcome"],"STOPPED_POST_AUDIT_FAILED")
        with sqlite3.connect(self.dbpath) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM market_derivatives").fetchone()[0],2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM market_request_lineage").fetchone()[0],1)

    def test_environment_and_bounds_preflight(self):
        with self.assertRaises(ValueError):
            batch.process(self.dbpath,"bybit","open_interest",max_pages=101,pause_seconds=1)
        with self.assertRaises(ValueError):
            batch.process(self.dbpath,"bybit","open_interest",max_pages=1,pause_seconds=0)
        with patch.dict("os.environ",{"MARKET_CONTEXT_DAILY_ENABLED":"1"}):
            with self.assertRaises(ValueError):
                batch.process(self.dbpath,"bybit","open_interest",max_pages=1,pause_seconds=1)

if __name__=="__main__":
    unittest.main()
