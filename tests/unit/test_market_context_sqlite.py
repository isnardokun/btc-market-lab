"""Offline provider-contract, provenance and SQLite integrity tests."""
import datetime as dt
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))

from ingestion import market_context_sources as sources
from storage import market_context as dbm
from rendering.market_context import render_market_context
from scripts import market_context_ingest as ingest

FARSIDE=b'''<html><table><tr><th>Date</th><th>IBIT</th><th>FBTC</th><th>GBTC</th><th>Total</th></tr>
<tr><td>08 Oct 2026</td><td>20.0</td><td>(5.5)</td><td>-</td><td>14.5</td></tr>
<tr><td>09 Oct 2026</td><td>-</td><td>0.0</td><td>1.1</td><td>1.1</td></tr></table></html>'''
ICS=b'''BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:bls-cpi-202610\r\nSUMMARY:Consumer Price Index\r\nDTSTART;TZID=America/New_York:20261014T083000\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:bls-survey-202611\r\nSUMMARY:Employment Situation\r\nDTSTART;VALUE=DATE:20261106\r\nEND:VEVENT\r\nEND:VCALENDAR'''
BINANCE_OI=b'[{"symbol":"BTCUSDT","sumOpenInterest":"1234.50","sumOpenInterestValue":"90000000","timestamp":1791504000000}]'
BINANCE_FUND=b'[{"symbol":"BTCUSDT","fundingRate":"-0.00012","fundingTime":1791504000000}]'
BYBIT_OI=b'{"retCode":0,"result":{"category":"linear","symbol":"BTCUSDT","list":[{"openInterest":"6543.21","timestamp":"1791504000000"}],"nextPageCursor":""}}'
BYBIT_FUND=b'{"retCode":0,"result":{"category":"linear","symbol":"BTCUSDT","list":[{"fundingRate":"0.00008","fundingRateTimestamp":"1791504000000"}]}}'

class MarketStorageTests(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory()
        self.path=Path(self.dir.name)/"btc_research.db"
        self.db=sqlite3.connect(self.path)
        self.db.execute("PRAGMA foreign_keys=ON")
        dbm.install(self.db)
        self.db.commit()
    def tearDown(self):
        self.db.close()
        self.dir.cleanup()
    def sha(self,provider="farside",body=FARSIDE):
        return dbm.raw_payload(self.db,provider,"https://example.invalid/test",body,"text/plain")

    def test_schema_additive_and_sqlite_only(self):
        self.assertTrue(dbm.installed(self.db))
        self.assertEqual(self.db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
        self.assertTrue({"market_raw_payloads","market_source_runs","market_derivatives",
                         "market_etf_flows","market_calendar_events","market_context_revisions",
                         "market_source_cursors"}<={
             x[0] for x in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")})
        self.assertEqual(sorted(p.name for p in Path(self.dir.name).iterdir()),["btc_research.db"])

    def test_negative_flow_zero_flow_and_missing_are_different(self):
        self.assertEqual(sources.parse_farside(FARSIDE),[
             ("2026-10-08","IBIT",20.0),("2026-10-08","FBTC",-5.5),
             ("2026-10-08","TOTAL",14.5),("2026-10-09","FBTC",0.0),
             ("2026-10-09","GBTC",1.1),("2026-10-09","TOTAL",1.1)])

    def test_etf_idempotent_and_revision_auditable(self):
        sha=self.sha()
        self.assertTrue(dbm.store_etf(self.db,provider="farside",trade_date="2026-10-09",
                         ticker="IBIT",amount_m=-5,state="preliminary",sha=sha))
        self.assertFalse(dbm.store_etf(self.db,provider="farside",trade_date="2026-10-09",
                         ticker="IBIT",amount_m=-5,state="preliminary",sha=sha))
        self.assertTrue(dbm.store_etf(self.db,provider="farside",trade_date="2026-10-09",
                         ticker="IBIT",amount_m=-7,state="preliminary",sha=sha))
        old=self.db.execute("SELECT old_json FROM market_context_revisions").fetchone()[0]
        self.assertIn('"net_flow_usd_m": -5.0',old)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM market_context_revisions").fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT net_flow_usd_m FROM market_etf_flows").fetchone()[0],-7)

    def test_no_false_revision_on_refetched_unchanged_snapshot(self):
        sha=self.sha()
        new_sha=self.sha(body=FARSIDE+b"<!-- changed source footer -->")
        dbm.store_etf(self.db,provider="farside",trade_date="2026-10-08",
                      ticker="IBIT",amount_m=20,state="preliminary",sha=sha)
        self.assertFalse(dbm.store_etf(self.db,provider="farside",trade_date="2026-10-08",
                        ticker="IBIT",amount_m=20,state="preliminary",sha=new_sha))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM market_context_revisions").fetchone()[0],0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM market_raw_payloads").fetchone()[0],2)

    def test_reject_invalid_units_nan_and_funding_probability(self):
        sha=self.sha(provider="bybit")
        args=dict(provider="bybit",symbol="BTCUSDT",metric="funding_settled",
                  observed_utc="2026-10-08T00:00:00+00:00",interval_label="settlement",
                  endpoint="https://api.bybit.com/v5/market/funding/history",sha=sha)
        with self.assertRaises(ValueError):
            dbm.store_derivative(self.db,unit="fraction",value=float("nan"),**args)
        with self.assertRaises(ValueError):
            dbm.store_derivative(self.db,unit="BTC",value=0.1,**args)
        with self.assertRaises(ValueError):
            dbm.store_derivative(self.db,unit="fraction",value=0.5,**args)

    def test_resume_cursors_persist_sqlite(self):
        dbm.save_cursor(self.db,"binance","funding-history",12345)
        self.db.commit()
        self.assertEqual(dbm.get_cursor(self.db,"binance","funding-history"),"12345")

    def test_bls_timezone_and_unknown_event_time_preserved(self):
        entries=sources.parse_bls_calendar(ICS)
        self.assertEqual(entries[0][2],"2026-10-14")
        self.assertEqual(entries[0][3],"2026-10-14T12:30:00+00:00")
        self.assertEqual(entries[0][4],"utc")
        self.assertIsNone(entries[1][3])
        self.assertEqual(entries[1][4],"date_only")
        sha=self.sha(provider="bls",body=ICS)
        for uid,title,date,stamp,precision,state in entries:
            dbm.store_event(self.db,provider="bls",uid=uid,title=title,
                            scheduled_utc=stamp,event_date=date,precision=precision,
                            source_url="https://www.bls.gov/schedule/news_release/",sha=sha,state=state)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM market_calendar_events").fetchone()[0],2)

    def test_bls_unknown_timezone_must_not_be_invented(self):
        ics=ICS.replace(b"TZID=America/New_York",b"TZID=Unknown/Mars")
        entries=sources.parse_bls_calendar(ics)
        self.assertIsNone(entries[0][3])
        self.assertEqual(entries[0][4],"unconfirmed")

    def test_fake_forecast_or_missing_result_never_generated(self):
        sha=self.sha(provider="bls",body=ICS)
        dbm.store_event(self.db,provider="bls",uid="cpi",title="Consumer Price Index",
                        scheduled_utc="2026-10-14T12:30:00+00:00",
                        event_date="2026-10-14",precision="utc",
                        source_url="https://www.bls.gov/schedule/news_release/",sha=sha)
        self.db.commit()
        html=render_market_context(self.path,now=dt.datetime(2026,10,10,tzinfo=dt.timezone.utc))
        self.assertIn("Consumer Price Index",html)
        self.assertNotIn("Consensus",html)
        self.assertNotIn("Forecast",html)

    def test_renderer_excludes_stale_derivatives(self):
        sha=self.sha(provider="binance",body=BINANCE_OI)
        dbm.store_derivative(self.db,provider="binance",symbol="BTCUSDT",
          metric="open_interest",observed_utc="2026-10-08T00:00:00+00:00",
          interval_label="5m",value=1234.5,unit="BTC",
          endpoint="https://fapi.binance.com/futures/data/openInterestHist",sha=sha)
        self.db.commit()
        self.assertEqual(render_market_context(self.path,now=dt.datetime(2026,10,12,tzinfo=dt.timezone.utc)),"")
        html=render_market_context(self.path,now=dt.datetime(2026,10,8,1,tzinfo=dt.timezone.utc))
        self.assertIn("1,234.50 BTC",html)
        self.assertIn("sin conversión USD verificada",html)

    def test_source_ingest_rows_and_raw_bytes_in_sqlite(self):
        def binance(url,params=None):
            return BINANCE_OI if "openInterestHist" in url else BINANCE_FUND
        req,points=sources.fetch_binance(self.db,fetch=binance,max_pages=1)
        self.assertEqual(req,2)
        self.assertEqual(points,2)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM market_derivatives").fetchone()[0],2)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM market_raw_payloads").fetchone()[0],2)

    def test_bybit_settled_funding_and_btc_open_interest(self):
        def bybit(url,params=None):
            return BYBIT_OI if "open-interest" in url else BYBIT_FUND
        req,points=sources.fetch_bybit(self.db,fetch=bybit,max_pages=1)
        self.assertEqual(req,2)
        self.assertEqual(points,2)
        oi=self.db.execute("SELECT raw_unit,quote_usd FROM market_derivatives WHERE metric='open_interest'").fetchone()
        self.assertEqual(oi,("BTC",None))

    def test_safely_logs_provider_error_to_sqlite(self):
        with patch.object(ingest,"fetch_bls",side_effect=ValueError("bad private secret")):
            state,requests,points=ingest.run(self.db,"bls")
        self.assertEqual(state,"failed")
        row=self.db.execute("SELECT status,error_code FROM market_source_runs").fetchone()
        self.assertEqual(row,("failed","ValueError"))
        self.assertNotIn("secret",str(row))

    def test_dry_run_and_no_implicit_migration(self):
        self.assertEqual(ingest.main(["--db",str(self.path),"--sources","bls"]),0)
        db=self.db.execute("SELECT COUNT(*) FROM market_source_runs").fetchone()[0]
        self.assertEqual(db,0)

    def test_html_error_skips_incomplete_farside_rows(self):
        with self.assertRaises(ValueError):
            sources.parse_farside(FARSIDE.replace(b'<td>1.1</td></tr>',
                                                  b'</tr>'))

    def test_disallow_wrong_source_and_no_provenance(self):
        with self.assertRaises(ValueError):
            sources.download("https://evil.example/steal",opener=lambda *_:None)
        with self.assertRaises(ValueError):
            dbm.store_etf(self.db,provider="farside",trade_date="2026-10-09",
                          ticker="BTC/W",amount_m=0,state="reported",sha="none")

if __name__=="__main__":
    unittest.main()
