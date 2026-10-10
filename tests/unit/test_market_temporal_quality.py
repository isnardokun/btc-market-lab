"""Read-only coverage diagnostics: gaps, missing archived raw rows and funding cadence."""
import datetime as dt
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from storage.market_context import install,raw_payload,store_derivative
from scripts.market_temporal_quality import inspect

UTC=dt.timezone.utc

class TemporalQualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"btc_research.db"
        self.db=sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        install(self.db)
        self.now=dt.datetime(2026,10,10,1,0,tzinfo=UTC)

    def _seed(self, provider, metric, times):
        desc={
            ("binance","open_interest"):("https://fapi.binance.com/futures/data/openInterestHist",
                                          "timestamp","sumOpenInterest"),
            ("binance","funding_settled"):("https://fapi.binance.com/fapi/v1/fundingRate",
                                            "fundingTime","fundingRate"),
            ("bybit","open_interest"):("https://api.bybit.com/v5/market/open-interest",
                                        "timestamp","openInterest"),
            ("bybit","funding_settled"):("https://api.bybit.com/v5/market/funding/history",
                                         "fundingRateTimestamp","fundingRate"),
        }
        endpoint,field,number=desc[(provider,metric)]
        values=[]
        for index,t in enumerate(times):
            d={field:str(int(t.timestamp()*1000)),number:(
               "100.5" if metric=="open_interest" else "0.0000523")}
            if provider=="binance":
                d["symbol"]="BTCUSDT"
                if metric=="open_interest":d["sumOpenInterestValue"]="8287875"
            elif metric=="funding_settled":
                d["symbol"]="BTCUSDT"
            values.append(d)
        if provider=="bybit":
            body={"retCode":0,"result":{"category":"linear","symbol":"BTCUSDT","list":values}}
        else:body=values
        sha=raw_payload(self.db,provider,endpoint,json.dumps(body).encode(),"application/json")
        for d,t in zip(values,times):
            store_derivative(
                self.db,provider=provider,symbol="BTCUSDT",metric=metric,
                observed_utc=t.isoformat(),interval_label=(
                    "5m" if metric=="open_interest" else "settlement"),
                value=d[number],unit=("BTC" if metric=="open_interest" else "fraction"),
                endpoint=endpoint,sha=sha,quote_usd=(
                    "8287875" if provider=="binance" and metric=="open_interest" else None))
        self.db.commit()

    def _all(self, *, gap=False):
        t=dt.datetime(2026,10,10,0,40,tzinfo=UTC)
        self._seed("binance","open_interest",[t,t+dt.timedelta(minutes=10 if gap else 5)])
        self._seed("bybit","open_interest",[t,t+dt.timedelta(minutes=5)])
        settled=dt.datetime(2026,10,10,0,0,tzinfo=UTC)
        self._seed("binance","funding_settled",[settled])
        self._seed("bybit","funding_settled",[settled])

    def test_covered_snapshot_reports_real_cadence_not_full_history(self):
        self._all()
        data=inspect(self.path,now=self.now)
        self.assertEqual(data["quality_state"],"SNAPSHOT_INTERNAL_QA_OK")
        self.assertEqual(data["historical_completeness"],"NOT_VERIFIED")
        self.assertEqual(len(data["original_payloads"]),4)
        self.assertEqual(data["source_rows_unstored"],[])
        self.assertEqual(data["streams"]["binance_open_interest"]["five_minute_gaps_inside_observed_window"],0)
        self.assertEqual(data["streams"]["binance_funding_settled"]["funding_interval_policy"],
                         "DESCRIPTIVE_ONLY_CHECK_INSTRUMENTS_INFO")

    def test_detects_one_missing_five_minute_interval(self):
        self._all(gap=True)
        data=inspect(self.path,now=self.now)
        self.assertEqual(data["quality_state"],"REVIEW_REQUIRED")
        self.assertEqual(data["streams"]["binance_open_interest"]["five_minute_gaps_inside_observed_window"],1)
        self.assertTrue(any("missing 5-minute slots" in s for s in data["issues"]))

    def test_detects_raw_record_not_inserted_even_if_other_rows_match(self):
        self._all()
        self.db.execute(
            "DELETE FROM market_derivatives WHERE provider='bybit' "
            "AND metric='open_interest' AND observed_utc=?",
            ("2026-10-10T00:45:00+00:00",))
        self.db.commit()
        data=inspect(self.path,now=self.now)
        self.assertEqual(data["quality_state"],"REVIEW_REQUIRED")
        self.assertEqual(data["source_rows_unstored"],[{"stream":"bybit_open_interest","count":1}])

    def test_missing_sqlite_is_never_created(self):
        missing=Path(self.tmp.name)/"never-create.db"
        with self.assertRaises(FileNotFoundError):
            inspect(missing)
        self.assertFalse(missing.exists())

if __name__=="__main__":
    unittest.main()
