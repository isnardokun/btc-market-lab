"""Test v1 -> v2 migration, exact request lineage and fail-closed history without network."""
import contextlib
import hashlib
import datetime as dt
from io import StringIO
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from storage import market_context as dbm
from scripts import market_context_migrate as migrate
from scripts import market_history_pilot as history

BASE=dt.datetime(2026,10,10,0,45,tzinfo=dt.timezone.utc)
ENDPOINT="https://fapi.binance.com/futures/data/openInterestHist"


class LineageMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"btc_research.db"

    def seed_old_schema(self):
        with sqlite3.connect(self.path) as db:
            db.executescript(dbm.DDL)
            db.execute(
                "INSERT INTO market_context_migrations(version,applied_at_utc) VALUES(1,?)",
                (dbm.now_utc(),))
            db.execute("CREATE TABLE legacy_stable(id INTEGER PRIMARY KEY,value TEXT)")
            db.execute("INSERT INTO legacy_stable VALUES(1,'original')")
            sha=dbm.raw_payload(db,"binance",ENDPOINT,b'{"fixture":true}',"application/json")
            dbm.store_derivative(db,provider="binance",symbol="BTCUSDT",metric="open_interest",
                                 observed_utc=BASE.isoformat(),interval_label="5m",
                                 value=1234.0,unit="BTC",endpoint=ENDPOINT,sha=sha)

    def test_migration_is_additive_backed_up_and_idempotent(self):
        self.seed_old_schema()
        with sqlite3.connect(self.path) as db:
            self.assertFalse(dbm.request_lineage_installed(db))
        with contextlib.redirect_stdout(StringIO()) as log:
            self.assertEqual(migrate.main(["--apply","--db",str(self.path)]),0,log.getvalue())
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            self.assertTrue(dbm.request_lineage_installed(db))
            self.assertEqual(db.execute("SELECT version FROM market_context_migrations ORDER BY version").fetchall(),[(1,),(2,),(3,)])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM market_derivatives").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT value FROM legacy_stable").fetchone()[0],"original")
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(),[])
        backups=list((self.path.parent/"backups").glob("btc_research_*.db"))
        self.assertEqual(len(backups),1)
        with sqlite3.connect(backups[0].resolve().as_uri()+"?mode=ro",uri=True) as backup:
            self.assertFalse(dbm.request_lineage_installed(backup))
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0],"ok")
        with contextlib.redirect_stdout(StringIO()):
            self.assertEqual(migrate.main(["--apply","--db",str(self.path)]),0)
        self.assertEqual(len(list((self.path.parent/"backups").glob("btc_research_*.db"))),1)

    def test_no_historical_call_on_v1_schema(self):
        self.seed_old_schema()
        requests=[]
        with sqlite3.connect(self.path) as db:
            result=history.execute(db,"binance","open_interest",
                  fetch=lambda url,params:requests.append(1))
            self.assertEqual(result["error_code"],"SchemaV2Required")
            self.assertEqual(result["requests"],0)
            self.assertEqual(requests,[])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM market_source_runs").fetchone()[0],0)

    def _migrate(self):
        self.seed_old_schema()
        with contextlib.redirect_stdout(StringIO()):
            self.assertEqual(migrate.main(["--apply","--db",str(self.path)]),0)

    def test_successful_single_page_captures_exact_public_request(self):
        self._migrate()
        prev=BASE-dt.timedelta(minutes=5)
        body=json.dumps([{"symbol":"BTCUSDT",
               "timestamp":int(prev.timestamp()*1000),
               "sumOpenInterest":"1221.7","sumOpenInterestValue":"100000000"}]).encode()
        asked=[]
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            def fetch(url,params):
                asked.append((url,dict(params)))
                return body
            out=history.execute(db,"binance","open_interest",fetch=fetch)
            self.assertEqual(out["status"],"success")
            self.assertTrue(out["request_lineage_logged"])
            self.assertEqual(len(asked),1)
            self.assertEqual(asked[0][1]["limit"],500)
            actual=db.execute(
                "SELECT provider,stream,symbol,interval_label,endpoint_path,"
                "requested_end_ms,requested_limit,http_attempts,status,raw_sha256,"
                "returned_rows,persisted_rows,first_observed_utc,last_observed_utc "
                "FROM market_request_lineage").fetchone()
            self.assertEqual(actual[0:5],("binance","open_interest","BTCUSDT","5m",
                              "/futures/data/openInterestHist"))
            self.assertEqual(actual[5:9],(int(BASE.timestamp()*1000)-1,500,1,"success"))
            self.assertEqual(actual[9],hashlib.sha256(body).hexdigest())
            self.assertEqual(actual[10:12],(1,1))
            self.assertEqual(actual[12],prev.isoformat(timespec="seconds"))
            self.assertEqual(actual[13],prev.isoformat(timespec="seconds"))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM market_derivatives").fetchone()[0],2)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(),[])
            self.assertNotIn("api_key",str(actual))
            self.assertNotIn("?",actual[4])

    def test_failed_network_attempt_records_receipt_without_advancing_cursor(self):
        self._migrate()
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            def denied(url,params):
                raise HTTPError(url,429,"private header",None,None)
            out=history.execute(db,"binance","open_interest",fetch=denied)
            self.assertEqual(out["status"],"failed")
            self.assertTrue(out["request_lineage_logged"])
            self.assertEqual(out["error_code"],"HTTPError_429")
            receipt=db.execute(
                "SELECT status,error_class,http_attempts,raw_sha256,persisted_rows,requested_limit "
                "FROM market_request_lineage").fetchone()
            self.assertEqual(receipt,("failed","HTTPError_429",1,None,0,500))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM market_derivatives").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM market_source_cursors").fetchone()[0],0)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(),[])

if __name__=="__main__":
    unittest.main()
