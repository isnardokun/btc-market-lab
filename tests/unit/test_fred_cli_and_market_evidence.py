"""Offline operator evidence: fail-closed on incomplete SQLite and FRED direct CLI."""
import contextlib
import datetime as dt
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from scripts.market_context_evidence import collect, main
from storage.market_context import install, raw_payload, store_derivative, store_etf, store_event

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Path(self.temp.name)/"btc_research.db"
        with sqlite3.connect(self.db) as conn:
            install(conn)
    def tearDown(self):
        self.temp.cleanup()

    def test_missing_sqlite_is_not_created(self):
        absent=Path(self.temp.name)/"missing.db"
        with self.assertRaises(FileNotFoundError):
            collect(absent)
        self.assertFalse(absent.exists())

    def test_missing_market_tables_returns_incomplete_status_not_exception(self):
        bare=Path(self.temp.name)/"legacy_only.db"
        with sqlite3.connect(bare) as conn:
            conn.execute("CREATE TABLE macro_fred(series_id TEXT,date TEXT,value REAL)")
            conn.execute("INSERT INTO macro_fred VALUES('CPIAUCSL','2026-08-01',321.2)")
        state=collect(bare,full_check=True)
        self.assertEqual(state["operational_state"],"INCOMPLETE_OR_FAILED")
        self.assertFalse(state["sqlite"]["market_schema_installed"])
        self.assertEqual(len(state["sqlite"]["market_tables_missing"]),8)
        self.assertEqual(state["sqlite"]["foreign_key_violations"],0)
        self.assertEqual(state["provider_status"]["binance"]["status"],"NOT_INSTALLED")
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            code=main(["--db",str(bare),"--strict","--full-check"])
        self.assertEqual(code,1)
        self.assertIn('"operational_state": "INCOMPLETE_OR_FAILED"',stream.getvalue())
        self.assertNotIn("KeyError",stream.getvalue())

    def test_migration_without_data_does_not_pass(self):
        state=collect(self.db)
        self.assertEqual(state["sqlite"]["quick_check"],"ok")
        self.assertEqual(state["operational_state"],"INCOMPLETE_OR_FAILED")
        self.assertIn("farside: no collection attempt recorded",state["missing_or_incomplete"])
        self.assertIn("ETF: no flow observations",state["missing_or_incomplete"])

    def test_structured_evidence_shows_source_and_unit_without_raw_bodies(self):
        asof="2026-10-09T11:00:00+00:00"
        with sqlite3.connect(self.db) as db:
            sha=raw_payload(db,"binance","https://fapi.binance.com/test",b"fixture","application/json")
            for provider in ("binance","bybit"):
                for metric in ("open_interest","funding_settled"):
                    src_sha=raw_payload(db,provider,"https://example.invalid/source",
                                        ("payload "+provider+metric).encode(),"application/json")
                    store_derivative(db,provider=provider,symbol="BTCUSDT",metric=metric,
                                     observed_utc=asof,interval_label="5m" if metric=="open_interest" else "settlement",
                                     value=500 if metric=="open_interest" else .0001,
                                     unit="BTC" if metric=="open_interest" else "fraction",
                                     endpoint="https://example.invalid/source",sha=src_sha)
            sha=raw_payload(db,"farside","https://farside.co.uk/test",b"ETF private fixture","text/html")
            store_etf(db,provider="farside",trade_date="2026-10-09",ticker="IBIT",
                       amount_m=123.5,state="preliminary",sha=sha)
            sha=raw_payload(db,"bls","https://www.bls.gov/test",b"calendar fixture","text/calendar")
            store_event(db,provider="bls",uid="event",title="CPI Release",event_date="2026-10-11",
                       scheduled_utc="2026-10-11T12:30:00+00:00",precision="utc",
                       source_url="https://www.bls.gov/schedule/news_release/",sha=sha)
            sha=raw_payload(db,"fred","https://api.stlouisfed.org/test",b"fred fixture","application/json")
            store_event(db,provider="fred",uid="event",title="Unemployment Release",
                        event_date="2026-10-12",scheduled_utc=None,precision="date_only",
                        source_url="https://fred.stlouisfed.org/docs/api/fred/releases_dates.html",sha=sha)
            for provider in ("binance","bybit","farside","bls","fred"):
                db.execute("INSERT INTO market_source_runs VALUES(?,?,?,?,?,?,?,?)",
                           (provider+"-1",provider,asof,asof,"success",1,1,None))
            db.execute("CREATE TABLE macro_fred(series_id TEXT,date TEXT,value REAL)")
            db.execute("INSERT INTO macro_fred VALUES('DGS10','2026-10-08',4.25)")
        state=collect(self.db,now=dt.datetime(2026,10,10,tzinfo=dt.timezone.utc))
        self.assertEqual(state["operational_state"],"PASS_EVIDENCE_PRESENT")
        self.assertEqual(state["sqlite"]["foreign_key_violations"],0)
        self.assertTrue(state["raw_payloads_sha256_sample_verified"])
        self.assertEqual(state["fred_macro_series"][0]["series"],"DGS10")
        self.assertEqual({x["provider"] for x in state["derivatives"]},{"binance","bybit"})
        self.assertIn("historical_completeness_claim",state)
        self.assertNotIn("fixture",str(state["raw_payloads"]))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--db",str(self.db),"--strict"]),0)

    def test_strict_mode_returns_nonzero_when_no_ingestion(self):
        with contextlib.redirect_stdout(io.StringIO()):
            status=main(["--db",str(self.db),"--strict"])
        self.assertEqual(status,1)

class FredEntrypointTests(unittest.TestCase):
    def test_direct_python_script_help_outside_repo_without_pythonpath(self):
        """The exact standalone invocation in daily.sh must import storage."""
        env=os.environ.copy()
        env.pop("PYTHONPATH",None)
        with tempfile.TemporaryDirectory() as temp:
            proc=subprocess.run([sys.executable,str(ROOT/"ingestion"/"ingest_fred.py"),"--help"],
                                cwd=temp,env=env,text=True,capture_output=True,timeout=20)
        self.assertEqual(proc.returncode,0,proc.stderr[-1000:])
        self.assertIn("--history",proc.stdout)

    def test_fred_http_failure_is_counted_not_silently_empty(self):
        from ingestion import ingest_fred as mod
        from urllib.error import URLError
        from unittest.mock import patch
        with patch.object(mod, "urlopen", side_effect=URLError("unavailable")):
            before=mod.FETCH_ERRORS
            self.assertEqual(mod.fetch_series_incremental("DGS10","2026-09-01"),[])
            self.assertEqual(mod.FETCH_ERRORS,before+1)
            mod.FETCH_ERRORS=before

    def test_fred_main_nonzero_on_series_error_without_live_api(self):
        from ingestion import ingest_fred as mod
        from unittest.mock import patch
        with patch.object(mod,"FRED_API_KEY","test_key_no_network"), \
             patch.object(mod,"FRED_SERIES",[("DGS10","10Y","daily","")]), \
             patch.object(mod,"DB_PATH",":memory:"), \
             patch.object(mod,"archive_schema_installed",return_value=False), \
             patch.object(mod,"ingest_series",side_effect=RuntimeError("provider failure")):
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                status=mod.main([])
        self.assertEqual(status,1)

    def test_module_help_works(self):
        env=os.environ.copy()
        env.pop("PYTHONPATH",None)
        proc=subprocess.run([sys.executable,"-m","ingestion.ingest_fred","--help"],
                            cwd=ROOT,env=env,text=True,capture_output=True,timeout=20)
        self.assertEqual(proc.returncode,0,proc.stderr[-1000:])

if __name__=="__main__":
    unittest.main()
