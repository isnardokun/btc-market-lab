"""Offline one-page backwards history pilot, no network or production data."""
import datetime as dt
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from storage.market_context import install,raw_payload,store_derivative,get_cursor
from scripts import market_history_pilot as history
from scripts.market_raw_reconcile import audit as raw_audit

UTC=dt.timezone.utc
BASE=dt.datetime(2026,10,10,0,45,tzinfo=UTC)

class OnePageHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"btc_research.db"
        self.db=sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.execute("PRAGMA foreign_keys=ON")
        install(self.db)

    def seed(self,provider,metric,time=BASE):
        endpoint=history.SOURCE_URLS[provider]+history.CONFIG[(provider,metric)][0]
        sha=raw_payload(self.db,provider,endpoint,b'{"old":"fixture"}',"application/json")
        store_derivative(
            self.db,provider=provider,symbol="BTCUSDT",metric=metric,
            observed_utc=time.isoformat(),interval_label=(
                "5m" if metric=="open_interest" else "settlement"),
            value=200 if metric=="open_interest" else 0.00001,
            unit="BTC" if metric=="open_interest" else "fraction",
            endpoint=endpoint,sha=sha)
        self.db.commit()

    def payload(self,provider,metric,time):
        ms=int(time.timestamp()*1000)
        if provider=="binance":
            if metric=="open_interest":
                return [{"symbol":"BTCUSDT","timestamp":ms,
                         "sumOpenInterest":"192.5","sumOpenInterestValue":"15890000"}]
            return [{"symbol":"BTCUSDT","fundingTime":ms,"fundingRate":"0.00008"}]
        if metric=="open_interest":
            row={"timestamp":str(ms),"openInterest":"190.25"}
        else:
            row={"fundingRateTimestamp":str(ms),"fundingRate":"-0.00004",
                 "symbol":"BTCUSDT"}
        return {"retCode":0,"result":{"symbol":"BTCUSDT","category":"linear","list":[row]}}

    def test_backward_each_provider_metric_no_forward_or_duplicate(self):
        for provider in ("binance","bybit"):
            for metric in ("open_interest","funding_settled"):
                with self.subTest(provider=provider,metric=metric):
                    self.seed(provider,metric)
                    back=BASE-dt.timedelta(minutes=5 if metric=="open_interest" else 480)
                    seen=[]
                    def fetch(url,params):
                        seen.append((url,params))
                        self.assertLess(int(params["endTime"]),int(BASE.timestamp()*1000))
                        self.assertEqual(params["symbol"],"BTCUSDT")
                        return json.dumps(self.payload(provider,metric,back)).encode()
                    result=history.execute(self.db,provider,metric,fetch=fetch)
                    self.assertEqual(result["status"],"success",result)
                    self.assertEqual(result["points"],1)
                    self.assertEqual(len(seen),1)
                    self.assertEqual(self.db.execute(
                        "SELECT COUNT(*) FROM market_derivatives WHERE provider=? AND metric=?",
                        (provider,metric)).fetchone()[0],2)
                    self.assertEqual(get_cursor(self.db,provider,history.CURSOR_PREFIX+metric),
                                     str(int(back.timestamp()*1000)-1))
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(),[])

    def test_network_error_rolls_back_page_and_records_attempt(self):
        from urllib.error import HTTPError
        self.seed("binance","open_interest")
        def denied(url,params):
            raise HTTPError(url,429,"rate limited",None,None)
        result=history.execute(self.db,"binance","open_interest",fetch=denied)
        self.assertEqual(result["status"],"failed")
        self.assertEqual(result["error_code"],"HTTPError_429")
        self.assertEqual(result["requests"],1)
        self.assertEqual(get_cursor(self.db,"binance",history.CURSOR_PREFIX+"open_interest"),None)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM market_derivatives").fetchone()[0],1)
        self.assertEqual(self.db.execute(
            "SELECT status,error_code,requests FROM market_source_runs").fetchone(),
            ("failed","HTTPError_429",1))

    def test_out_of_range_response_does_not_advance_cursor(self):
        self.seed("bybit","funding_settled")
        response=self.payload("bybit","funding_settled",BASE+dt.timedelta(hours=8))
        result=history.execute(self.db,"bybit","funding_settled",
                               fetch=lambda url,params:json.dumps(response).encode())
        self.assertEqual(result["status"],"failed")
        self.assertEqual(result["error_code"],"ValueError")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM market_raw_payloads").fetchone()[0],1)
        self.assertIsNone(get_cursor(self.db,"bybit",history.CURSOR_PREFIX+"funding_settled"))

    def test_second_page_starts_before_first_historical_page(self):
        self.seed("bybit","open_interest")
        back1=BASE-dt.timedelta(minutes=5)
        back2=BASE-dt.timedelta(minutes=10)
        seen=[]
        def fetch(url,params):
            seen.append(int(params["endTime"]))
            t=back1 if len(seen)==1 else back2
            return json.dumps(self.payload("bybit","open_interest",t)).encode()
        one=history.execute(self.db,"bybit","open_interest",fetch=fetch)
        two=history.execute(self.db,"bybit","open_interest",fetch=fetch)
        self.assertEqual((one["status"],two["status"]),("success","success"))
        self.assertLess(seen[1],seen[0])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM market_derivatives").fetchone()[0],3)

    def test_direct_cli_plan_imports_from_unrelated_working_directory(self):
        # Exercise the actual script entry point, not the unittest import path.
        # An early ingestion import before sys.path setup previously failed here.
        import os
        import subprocess
        env=dict(os.environ)
        env.pop("PYTHONPATH",None)
        result=subprocess.run(
            [sys.executable,str(Path(__file__).resolve().parents[2] /
               "scripts" / "market_history_pilot.py"),
             "--provider","binance","--metric","open_interest"],
            cwd=self.dir.name if hasattr(self,"dir") else self.tmp.name,
            env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn("PLAN:",result.stdout)
        self.assertNotIn("ModuleNotFoundError",result.stderr)

    def test_plan_mode_has_zero_sqlite_or_network_effects(self):
        absent=Path(self.tmp.name)/"no-such.db"
        self.assertEqual(history.main(["--provider","bybit","--metric",
                                       "open_interest","--db",str(absent)]),0)
        self.assertFalse(absent.exists())

    def test_oi_missing_boundary_stops_before_raw_archive(self):
        self.seed("binance","open_interest")
        gap=BASE-dt.timedelta(minutes=10)
        res=history.execute(
            self.db,"binance","open_interest",
            fetch=lambda url,params:json.dumps(
                self.payload("binance","open_interest",gap)).encode())
        self.assertEqual(res["status"],"failed")
        self.assertEqual(res["error_code"],"ValueError")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM market_derivatives").fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM market_raw_payloads").fetchone()[0],1)
        self.assertIsNone(get_cursor(self.db,"binance",history.CURSOR_PREFIX+"open_interest"))

    def test_oi_inside_page_gap_stops_before_cursor_write(self):
        self.seed("bybit","open_interest")
        first=BASE-dt.timedelta(minutes=15)
        last=BASE-dt.timedelta(minutes=5)
        payload=self.payload("bybit","open_interest",first)
        payload["result"]["list"].append(
            self.payload("bybit","open_interest",last)["result"]["list"][0])
        res=history.execute(
            self.db,"bybit","open_interest",
            fetch=lambda url,params:json.dumps(payload).encode())
        self.assertEqual(res["status"],"failed")
        self.assertEqual(res["error_code"],"ValueError")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM market_derivatives").fetchone()[0],1)

    def test_empty_page_does_not_fabricate_earlier_history(self):
        self.seed("binance","open_interest")
        empty=history.execute(self.db,"binance","open_interest",
                              fetch=lambda url,params:b"[]")
        self.assertEqual(empty["status"],"empty")
        self.assertEqual(empty["points"],0)
        self.assertFalse(empty["cursor_advanced"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM market_derivatives").fetchone()[0],1)

if __name__=="__main__":
    unittest.main()
