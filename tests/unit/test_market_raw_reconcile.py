"""Verify genuine source-to-SQLite reconciliation independently and without network."""
import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from storage.market_context import install,raw_payload,store_derivative
from scripts.market_raw_reconcile import audit,main
from rendering.market_context import render_market_context

class RawReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"btc_research.db"
        self.time="2026-10-10T00:00:00+00:00"
        self.ms=str(int(dt.datetime.fromisoformat(self.time).timestamp()*1000))
        self.db=sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        install(self.db)

    def _put(self,provider,metric,body,endpoint,raw,unit,quote=None):
        serialized=json.dumps(body,separators=(",",":")).encode()
        sha=raw_payload(self.db,provider,endpoint,serialized,"application/json")
        store_derivative(
            self.db,provider=provider,symbol="BTCUSDT",
            metric=metric,observed_utc=self.time,
            interval_label="5m" if metric=="open_interest" else "settlement",
            value=raw,unit=unit,endpoint=endpoint,sha=sha,quote_usd=quote)
        self.db.commit()

    def seeded(self):
        self._put(
            "binance","open_interest",
            [{"symbol":"BTCUSDT","timestamp":int(self.ms),
              "sumOpenInterest":"100.5","sumOpenInterestValue":"8292137.4"}],
            "https://fapi.binance.com/futures/data/openInterestHist",
            100.5,"BTC",8292137.4)
        self._put(
            "binance","funding_settled",
            [{"symbol":"BTCUSDT","fundingTime":int(self.ms),
              "fundingRate":"0.00005232"}],
            "https://fapi.binance.com/fapi/v1/fundingRate",
            0.00005232,"fraction")
        self._put(
            "bybit","open_interest",
            {"retCode":0,"result":{"category":"linear","symbol":"BTCUSDT","list":[
             {"timestamp":self.ms,"openInterest":"33.2"}]}},
            "https://api.bybit.com/v5/market/open-interest",
            33.2,"BTC")
        self._put(
            "bybit","funding_settled",
            {"retCode":0,"result":{"category":"linear","symbol":"BTCUSDT","list":[
             {"fundingRateTimestamp":self.ms,"fundingRate":"-0.00001182","symbol":"BTCUSDT"}]}},
            "https://api.bybit.com/v5/market/funding/history",
            -0.00001182,"fraction")

    def test_all_four_raw_blobs_reconcile_to_stored_derivatives(self):
        self.seeded()
        result=audit(self.path)
        self.assertEqual(result["state"],"PASS_SQLITE_TO_RAW",result)
        self.assertEqual(result["rows"],4)
        self.assertEqual(result["verified"],4)
        self.assertEqual(result["blobs_seen"],4)
        self.assertEqual(result["issues_count"],0)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--db",str(self.path)]),0)
        markup=render_market_context(
            self.path,now=dt.datetime(2026,10,10,1,tzinfo=dt.timezone.utc))
        self.assertIn("Nocional reportado:",markup)
        self.assertIn("unidad cotizada no validada como USD fiat",markup)
        self.assertNotIn("USD equivalente:",markup)

    def test_edited_derivative_is_rejected(self):
        self.seeded()
        self.db.execute("UPDATE market_derivatives SET raw_value=999 "
                        "WHERE provider='bybit' AND metric='open_interest'")
        self.db.commit()
        r=audit(self.path)
        self.assertEqual(r["state"],"REJECTED")
        self.assertEqual(r["verified"],3)
        self.assertTrue(any("value/unit mismatch" in x for x in r["issues"]))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--db",str(self.path)]),1)

    def test_tampered_body_checksum_is_rejected(self):
        self.seeded()
        self.db.execute("UPDATE market_raw_payloads SET body=? WHERE provider='binance' "
                        "AND endpoint LIKE '%openInterestHist'",(b'[]',))
        self.db.commit()
        r=audit(self.path)
        self.assertEqual(r["state"],"REJECTED")
        self.assertTrue(any("raw sha256 mismatch" in x for x in r["issues"]))

    def test_deleted_archived_source_observation_is_rejected(self):
        self.seeded()
        self.db.execute(
            "DELETE FROM market_derivatives "
            "WHERE provider='bybit' AND metric='open_interest'"
        )
        self.db.commit()
        result = audit(self.path)
        self.assertEqual(result["state"], "REJECTED")
        self.assertTrue(any(
            "archived source observation missing from SQLite" in issue
            for issue in result["issues"]
        ), result["issues"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--db", str(self.path)]), 1)

    def test_empty_legacy_db_never_passes_or_gets_created(self):
        r=audit(self.path)
        self.assertEqual(r["state"],"REJECTED")
        self.assertEqual(r["rows"],0)
        self.assertEqual(r["issues_count"],4)
        missing=Path(self.tmp.name)/"no-data.db"
        with self.assertRaises(FileNotFoundError):
            audit(missing)
        self.assertFalse(missing.exists())

if __name__=="__main__":
    unittest.main()
