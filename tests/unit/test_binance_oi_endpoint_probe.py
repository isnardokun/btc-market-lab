"""Offline tests for exactly one controlled Binance historical OI request."""
import contextlib
import datetime as dt
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
import socket

from scripts import binance_oi_endpoint_probe as probe
from storage.market_context import install,raw_payload,store_derivative,save_cursor


class StubResponse:
    def __init__(self,status,body):
        self.status=status
        self.body=body
        self.reads=[]
    def getcode(self):
        return self.status
    def read(self,n):
        self.reads.append(n)
        return self.body
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False


class MinimalOITest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"btc_research.db"
        self.oldest=dt.datetime(2026,10,6,13,30,tzinfo=dt.timezone.utc)
        self.previous=self.oldest-dt.timedelta(minutes=5)
        with sqlite3.connect(self.path) as db:
            install(db)
            sha=raw_payload(db,"binance","https://fapi.binance.com/futures/data/openInterestHist",
                            b'{"seed":"fixture"}',"application/json")
            store_derivative(db,provider="binance",symbol="BTCUSDT",metric="open_interest",
                             observed_utc=self.oldest.isoformat(),interval_label="5m",
                             value=90_000,unit="BTC",
                             endpoint="https://fapi.binance.com/futures/data/openInterestHist",sha=sha)
            save_cursor(db,"binance",probe.CURSOR_STREAM,
                        str(int(self.oldest.timestamp()*1000)-1))

    def payload(self,date=None):
        when=date or self.previous
        return json.dumps([{"symbol":"BTCUSDT",
                            "timestamp":int(when.timestamp()*1000),
                            "sumOpenInterest":"90212.432",
                            "sumOpenInterestValue":"7500000000"}]).encode()

    def verify_no_changes(self):
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM market_derivatives").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM market_raw_payloads").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM market_source_runs").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT cursor FROM market_source_cursors").fetchone()[0],
                str(int(self.oldest.timestamp()*1000)-1))

    def test_plan_only_never_opens_db_or_http(self):
        missing=Path(self.tmp.name)/"nonexistent.db"
        with patch.object(probe,"transport",side_effect=AssertionError("no internet")):
            with contextlib.redirect_stdout(StringIO()) as output:
                rc=probe.main(["--db",str(missing)])
        self.assertEqual(rc,0)
        self.assertFalse(missing.exists())
        self.assertIn("PLAN_ONLY",output.getvalue())

    def test_one_get_limit_one_and_matching_last_slot(self):
        reqs=[];res=StubResponse(200,self.payload())
        def fake_transport(req,timeout):
            reqs.append((req.full_url,req.get_method(),timeout))
            return res
        result=probe.check(self.path,open_request=fake_transport)
        self.assertEqual(result["outcome"],"PASS_OI_ENDPOINT_AND_BOUNDARY")
        self.assertEqual(result["http_attempts"],1)
        self.assertEqual(result["observations_persisted"],0)
        self.assertEqual(result["sqlite_writes"],0)
        self.assertEqual(result["received_records"],1)
        self.assertEqual(res.reads,[probe.MAX_BODY+1])
        self.assertEqual(len(reqs),1)
        self.assertEqual(reqs[0][1: ],("GET",8))
        self.assertIn("limit=1",reqs[0][0])
        self.assertIn("period=5m",reqs[0][0])
        self.assertIn("endTime="+str(int(self.oldest.timestamp()*1000)-1),reqs[0][0])
        self.verify_no_changes()

    def test_malformed_empty_or_gap_fail_and_never_write(self):
        cases=[
            (b"[]","EMPTY_HISTORY"),
            (self.payload(self.previous-dt.timedelta(minutes=5)),"BOUNDARY_MISMATCH"),
            (b'{"code":-1000}',"UNEXPECTED_LIST"),
            (b"not JSON","MALFORMED_JSON"),
        ]
        for body,expected in cases:
            with self.subTest(expected=expected):
                result=probe.check(self.path,open_request=lambda req,timeout:StubResponse(200,body))
                self.assertEqual(result["response_contract"],expected)
                self.assertEqual(result["outcome"],"FAILED")
                self.verify_no_changes()

    def test_http_error_is_categorized_without_retry(self):
        attempts=[]
        def reject(req,timeout):
            attempts.append(req)
            raise HTTPError(req.full_url,403,"secret response",None,None)
        result=probe.check(self.path,open_request=reject)
        self.assertEqual(result["error_class"],"HTTPError_403")
        self.assertEqual(result["http_attempts"],1)
        self.assertEqual(len(attempts),1)
        self.assertNotIn("secret",str(result))
        self.verify_no_changes()

    def test_transport_failure_is_categorized_without_retry(self):
        attempts=[]
        def fail(req,timeout):
            attempts.append(req)
            raise URLError(socket.gaierror(-3,"private resolver address"))
        result=probe.check(self.path,open_request=fail)
        self.assertEqual(result["error_class"],"URLError_DNS")
        self.assertEqual(len(attempts),1)
        self.assertNotIn("private",str(result))
        self.verify_no_changes()

    def test_cursor_mismatch_blocks_before_http(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE market_source_cursors SET cursor='1234'")
        attempts=[]
        def should_not_call(req,timeout):
            attempts.append(1)
            raise AssertionError("unexpected HTTP")
        result=probe.check(self.path,open_request=should_not_call)
        self.assertEqual(result["outcome"],"BLOCKED")
        self.assertEqual(result["error_class"],"ValueError")
        self.assertEqual(result["http_attempts"],0)
        self.assertEqual(attempts,[])

if __name__=="__main__":
    unittest.main()
