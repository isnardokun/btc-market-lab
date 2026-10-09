"""Offline migration tests with existing user observations left unchanged."""
import datetime as dt
from pathlib import Path
import sqlite3
import tempfile
import unittest

from storage.archive_schema import (
    SCHEMA_VERSION, ensure_archive_schema, archive_schema_installed,
    require_archive_schema, save_window, register_dataset,
)
from scripts.archive_db_migrate import run as migrate_run
from ingestion.researchbitcoin_v2 import store_rows
from ingestion.researchbitcoin_archive import run as rbn_archive
from ingestion.yahoo_history import archive as archive_yahoo
from scripts.history_query import query_history


NOW=dt.datetime(2026,10,9,17,tzinfo=dt.timezone.utc)


class ArchiveSchemaTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root=Path(folder.name)
        self.path=self.root/"btc_research.db"
        with sqlite3.connect(self.path) as db:
            db.executescript("""
            CREATE TABLE series(id INTEGER PRIMARY KEY,name TEXT UNIQUE,idx TEXT);
            CREATE TABLE daily(
                series_id INTEGER NOT NULL,ts INTEGER NOT NULL,
                block_height INTEGER,value REAL,UNIQUE(series_id,ts));
            CREATE TABLE macro_fred (
                series_id TEXT NOT NULL,date TEXT NOT NULL,value REAL,
                quality_status TEXT,fetched_at TEXT,
                UNIQUE(series_id,date));
            INSERT INTO series(id,name,idx) VALUES (9,'mvrv','1d');
            INSERT INTO daily(series_id,ts,value) VALUES (9,1791417600,1.35);
            INSERT INTO macro_fred(series_id,date,value)
            VALUES ('PAYEMS','2026-09-01',158990);
            """)

    def test_planning_has_no_ddl_and_apply_creates_verified_backup(self):
        plan=migrate_run(self.path)
        self.assertEqual(plan["mode"],"PLAN")
        with sqlite3.connect(self.path) as db:
            self.assertFalse(archive_schema_installed(db))
        result=migrate_run(self.path,apply=True)
        self.assertEqual(result["integrity"],"ok")
        self.assertTrue(Path(result["backup_local"]).is_file())
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute(
                "SELECT value FROM daily WHERE series_id=9").fetchone()[0],1.35)
            self.assertEqual(db.execute(
                "SELECT value FROM macro_fred WHERE series_id='PAYEMS'"
            ).fetchone()[0],158990)
            self.assertEqual(db.execute(
                "SELECT version FROM archive_schema_migrations"
            ).fetchone()[0],SCHEMA_VERSION)
            tier,verified=db.execute(
                "SELECT plan_tier,entitlement_verified FROM archive_sources "
                "WHERE source_id='researchbitcoin'").fetchone()
            self.assertEqual((tier,verified),(2,0))
        # Retrying additive migration preserves archived market observations.
        with sqlite3.connect(self.path) as db:
            ensure_archive_schema(db)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM archive_schema_migrations").fetchone()[0],1)

    def test_cannot_run_historical_backfill_before_migration(self):
        with self.assertRaisesRegex(RuntimeError,"migración segura"):
            rbn_archive(
                self.path,mode="history",slugs=["sopr_sth"],tier=2,
                history_start="2009-01-01",max_requests=1,apply=True,
                client=lambda *a,**k:{"data":[]},today=NOW,
            )
        with sqlite3.connect(self.path) as db:
            self.assertFalse(archive_schema_installed(db))

    def test_fred_and_bitview_revisions_are_append_only(self):
        with sqlite3.connect(self.path) as db:
            ensure_archive_schema(db)
            db.execute("UPDATE macro_fred SET value=159050 "
                       "WHERE series_id='PAYEMS'")
            db.execute("UPDATE daily SET value=1.48 WHERE series_id=9")
            db.execute("UPDATE daily SET value=1.48 WHERE series_id=9")
            rows=db.execute(
                "SELECT source_id,metric,old_value,new_value "
                "FROM archive_observation_revisions ORDER BY revision_id"
            ).fetchall()
        self.assertEqual(rows,[
            ("fred","PAYEMS",158990.0,159050.0),
            ("bitview","mvrv",1.35,1.48),
        ])

    def test_researchbitcoin_and_yahoo_late_sidecar_tables_are_audited(self):
        with sqlite3.connect(self.path) as db:
            ensure_archive_schema(db)
        store_rows(self.path,"sopr_sth",{
            "2026-10-08":("2026-10-08T00:00:00+00:00",1.02)
        })
        store_rows(self.path,"sopr_sth",{
            "2026-10-08":("2026-10-08T00:00:00+00:00",1.03)
        })
        now_ts=int(dt.datetime(2026,10,8,tzinfo=dt.timezone.utc).timestamp())
        archive_yahoo(self.path,"BTC-USD",[(now_ts,1.,2.,1.,1.5,10.)])
        archive_yahoo(self.path,"BTC-USD",[(now_ts,1.,2.,1.,1.7,10.)])
        with sqlite3.connect(self.path) as db:
            seen=db.execute(
                "SELECT source_id,metric,old_value,new_value "
                "FROM archive_observation_revisions ORDER BY revision_id"
            ).fetchall()
            source_count=db.execute(
                "SELECT COUNT(*) FROM archive_datasets WHERE "
                "(source_id='researchbitcoin' AND metric='sopr_sth') OR "
                "(source_id='yahoo' AND metric='BTC-USD')"
            ).fetchone()[0]
        self.assertEqual(seen,[
            ("researchbitcoin","sopr_sth",1.02,1.03),
            ("yahoo","BTC-USD",1.5,1.7),
        ])
        self.assertEqual(source_count,2)

    def test_tier2_empty_early_history_has_a_checkpoint_and_resume(self):
        with sqlite3.connect(self.path) as db:
            ensure_archive_schema(db)
        calls=[]
        def empty(slug,*,start_day,end_day):
            calls.append((start_day,end_day))
            return {"data":[]}
        opts=dict(db_path=self.path,mode="history",slugs=["sopr_sth"],
                  tier=2,history_start="2009-01-01",max_requests=1,
                  apply=True,client=empty,today=NOW)
        first=rbn_archive(**opts)
        second=rbn_archive(**opts)
        self.assertEqual(first["saved_observations"],0)
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0][0],dt.date(2009,1,1))
        self.assertEqual(calls[0][1],calls[1][0])
        with sqlite3.connect(self.path) as db:
            statuses=db.execute(
                "SELECT status,COUNT(*) FROM archive_ingest_windows "
                "WHERE source_id='researchbitcoin' GROUP BY status"
            ).fetchall()
            runs=db.execute(
                "SELECT status,data_points FROM archive_ingest_runs "
                "ORDER BY started_at_utc"
            ).fetchall()
        self.assertEqual(statuses,[("empty",2)])
        self.assertEqual(len(runs),2)

    def test_tier2_distribution_shape_has_isolated_schema(self):
        with sqlite3.connect(self.path) as db:
            ensure_archive_schema(db)
            register_dataset(db,"researchbitcoin","utxo_price_bins",
                             unit="BTC",shape="distribution",
                             earliest="2009-01-01")
            db.execute(
                "INSERT INTO archive_multidimensional_observations "
                "(source_id,metric,observed_at_utc,dimensions_key,value,unit,"
                "source_endpoint,fetched_at_utc) VALUES (?,?,?,?,?,?,?,?)",
                ("researchbitcoin","utxo_price_bins",
                 "2026-10-08T00:00:00+00:00",
                 '{"price_bucket":"60000-61000","cohort":"STH"}',
                 12.5,"BTC","/v2/unverified_bin_sample",
                 "2026-10-09T00:00:00+00:00"),
            )
            shape=db.execute(
                "SELECT data_shape FROM archive_datasets WHERE "
                "source_id='researchbitcoin' AND metric='utxo_price_bins'"
            ).fetchone()[0]
            self.assertEqual(shape,"distribution")
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM archive_multidimensional_observations"
            ).fetchone()[0],1)
            # No scalar sidecar data should be fabricated by the new table.
            self.assertFalse(db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='onchain_external_observations'"
            ).fetchone())
            with self.assertRaises(ValueError):
                register_dataset(db,"researchbitcoin","badshape",shape="vector_unsafe")

    def test_history_query_supports_vintages_and_researchbitcoin_bins(self):
        with sqlite3.connect(self.path) as db:
            ensure_archive_schema(db)
            db.execute(
                "INSERT INTO archive_fred_vintages "
                "(series_id,observed_date,realtime_start,realtime_end,"
                "value,captured_at_utc) VALUES (?,?,?,?,?,?)",
                ("PAYEMS","2026-09-01","2026-10-08",None,159050.0,
                 "2026-10-09T00:00:00+00:00"),
            )
            db.execute(
                "INSERT INTO archive_multidimensional_observations "
                "(source_id,metric,observed_at_utc,dimensions_key,value,unit,"
                "source_endpoint,fetched_at_utc) VALUES (?,?,?,?,?,?,?,?)",
                ("researchbitcoin","utxo_price_bins",
                 "2026-10-08T00:00:00+00:00","cohort=STH|bucket=60000-61000",
                 2.5,"BTC","/v2/provider-specific",
                 "2026-10-09T00:00:00+00:00"),
            )
        fred=query_history(self.path,"fred-vintage","PAYEMS",
                           "2026-09-01","2026-09-30")
        self.assertEqual(fred["rows"][0]["realtime_start"],"2026-10-08")
        values=query_history(self.path,"rbn-distribution","utxo_price_bins",
                             "2026-10-08","2026-10-08")
        self.assertEqual(values["rows"][0]["value"],2.5)
        self.assertIn("cohort=STH",values["rows"][0]["dimensions_key"])

    def test_strict_window_and_metric_registration(self):
        with sqlite3.connect(self.path) as db:
            ensure_archive_schema(db)
            register_dataset(db,"bitview","mvrv",unit="ratio",
                             earliest="2009-01-01")
            save_window(db,"bitview","mvrv",
                        dt.date(2010,1,1),dt.date(2010,2,1),"ok",12)
            with self.assertRaises(ValueError):
                save_window(db,"bitview","mvrv",
                            dt.date(2010,2,1),dt.date(2010,1,1),"ok",1)
            self.assertEqual(db.execute(
                "SELECT data_points FROM archive_ingest_windows "
                "WHERE source_id='bitview'").fetchone()[0],12)


if __name__=="__main__":
    unittest.main()
