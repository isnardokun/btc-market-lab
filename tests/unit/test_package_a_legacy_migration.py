"""Package A: additive migration on a faithful legacy SQLite WAL fixture ONLY.

This test NEVER reads the user's repository DB or any live API. It calls the
real WAL-safe backup code and the actual additive Package A DDL on disposable
TemporaryDirectory fixtures with 1,035 synthetic legacy funding rows.
"""
import datetime as dt
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.package_a_migration_rehearsal import (
    MARKER, MARKER_TEXT, LOCK_NAMESPACE, PACKAGE_TABLES, _digest_legacy,
    rehearse_additive_migration,
)
from scripts.market_r10e11_writer_exclusion import hold_writer_lock
from storage import archive_schema, market_context as market
from storage import package_a_ledger as ledger

NOW="2026-10-11T12:00:00Z"


class TestPackageALegacyMigration(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix="package-a-fixture-")
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.db=self.root/"package_a_synthetic.db"
        self.lockdir=self.root/"locks"
        self.lockdir.mkdir()
        (self.root/MARKER).write_text(MARKER_TEXT,encoding="utf-8")
        with sqlite3.connect(self.db) as c:
            c.execute("PRAGMA foreign_keys=ON")
            c.executescript("""
                CREATE TABLE series(id INTEGER PRIMARY KEY,name TEXT UNIQUE,idx TEXT);
                CREATE TABLE daily(series_id INTEGER NOT NULL,ts INTEGER NOT NULL,
                    block_height INTEGER,value REAL,UNIQUE(series_id,ts));
                CREATE TABLE price_btc(ts INTEGER PRIMARY KEY,price REAL);
                CREATE TABLE macro_fred(series_id TEXT NOT NULL,date TEXT NOT NULL,value REAL,
                    UNIQUE(series_id,date));
                INSERT INTO series VALUES (9,'mvrv','1d');
                INSERT INTO daily VALUES(9,1791417600,NULL,1.35);
                INSERT INTO price_btc VALUES(1791417600,63000.5);
                INSERT INTO macro_fred VALUES('PAYEMS','2026-09-01',158990.0);
            """)
            c.execute("PRAGMA user_version=17")
            archive_schema.ensure_archive_schema(c)
            market.install(c)
            sha=market.raw_payload(c,"binance","/fapi/v1/fundingRate",
                                   b'{"synthetic_legacy":true}',"application/json")
            base=dt.datetime(2026,1,1,tzinfo=dt.timezone.utc)
            legacy=[]
            for i in range(1035):
                provider="binance" if i<635 else "bybit"
                stamp=(base+dt.timedelta(minutes=i*480)).isoformat()
                legacy.append((provider,"BTCUSDT","funding_settled",stamp,
                               "settlement",0.0001,"fraction",None,
                               "/fapi/v1/fundingRate",sha,NOW))
            c.executemany(
                "INSERT INTO market_derivatives "
                "(provider,symbol,metric,observed_utc,interval_label,"
                "raw_value,raw_unit,quote_usd,source_endpoint,raw_sha256,fetched_utc)"
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",legacy)
        # WAL mode is written ONLY to this temporary SQLite file.
        with sqlite3.connect(self.db) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA wal_autocheckpoint=0")

    def run_migration(self,**kwargs):
        return rehearse_additive_migration(self.db,self.lockdir,now=NOW,**kwargs)

    def tables(self):
        with sqlite3.connect(self.db) as db:
            return {r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'package_a_%'")}

    def legacy(self):
        with sqlite3.connect(self.db) as db:
            return _digest_legacy(db)

    def test_actual_legacy_archive_and_market_schema_coexist(self):
        before=self.legacy()
        result=self.run_migration()
        self.assertEqual(result["state"],"PACKAGE_A_MIGRATION_REHEARSAL_PASS")
        self.assertEqual(result["legacy_digest_before"],before)
        self.assertEqual(result["legacy_digest_after"],before)
        self.assertEqual(self.tables(),set(PACKAGE_TABLES))
        self.assertEqual(result["migrated_schema_version"],2)

    def test_preserves_all_1035_synthetic_funding_without_inventing_receipts(self):
        self.run_migration()
        with sqlite3.connect(self.db) as db:
            distribution=db.execute(
                "SELECT provider,COUNT(*) FROM market_derivatives "
                "WHERE metric='funding_settled' GROUP BY provider").fetchall()
            self.assertEqual(distribution,[("binance",635),("bybit",400)])
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM market_request_lineage").fetchone()[0],0)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM package_a_receipts").fetchone()[0],0)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM package_a_observations").fetchone()[0],0)

    def test_retains_real_archive_schema_version_and_tier_unverified(self):
        self.run_migration()
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],17)
            self.assertEqual(db.execute(
                "SELECT MAX(version) FROM archive_schema_migrations").fetchone()[0],1)
            self.assertEqual(db.execute(
                "SELECT entitlement_verified FROM archive_sources "
                "WHERE source_id='researchbitcoin'").fetchone()[0],0)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM market_context_migrations").fetchone()[0],3)

    def test_wal_safe_backup_reconstructs_original_legacy_db(self):
        before=self.legacy()
        result=self.run_migration()
        backup=Path(result["backup"])
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.stat().st_mode & 0o777,0o600)
        with sqlite3.connect(backup.resolve().as_uri()+"?mode=ro",uri=True) as restored:
            self.assertEqual(_digest_legacy(restored),before)
            self.assertFalse(restored.execute(
                "SELECT 1 FROM sqlite_master WHERE name='package_a_receipts'").fetchone())
            self.assertEqual(restored.execute(
                "PRAGMA integrity_check").fetchone()[0],"ok")
            self.assertEqual(restored.execute(
                "SELECT COUNT(*) FROM market_derivatives").fetchone()[0],1035)

    def test_migration_retry_is_idempotent_and_not_destructive(self):
        first=self.run_migration()
        second=self.run_migration()
        self.assertEqual(first["legacy_digest_after"],second["legacy_digest_after"])
        self.assertEqual(second["previous_package_tables"],sorted(PACKAGE_TABLES))
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM package_a_schema_migrations").fetchone()[0],1)

    def test_crash_after_first_ddl_rolls_back_all_new_tables(self):
        before=self.legacy()
        with self.assertRaisesRegex(ledger.SimulatedCrash,"after_first_ddl"):
            self.run_migration(crash_after="after_first_ddl")
        self.assertEqual(self.tables(),set())
        self.assertEqual(self.legacy(),before)
        self.assertGreaterEqual(len(list((self.root/"backup-only").glob("*.db"))),1)
        self.assertEqual(self.run_migration()["state"],
                         "PACKAGE_A_MIGRATION_REHEARSAL_PASS")

    def test_crash_before_commit_rolls_back_complete_new_schema(self):
        before=self.legacy()
        with self.assertRaisesRegex(ledger.SimulatedCrash,"before_commit"):
            self.run_migration(crash_after="before_commit")
        self.assertEqual(self.tables(),set())
        self.assertEqual(self.legacy(),before)
        self.assertEqual(self.run_migration()["state"],
                         "PACKAGE_A_MIGRATION_REHEARSAL_PASS")

    def test_occupied_lock_blocks_even_backup_and_sqlite_open(self):
        with hold_writer_lock(self.lockdir,namespace=LOCK_NAMESPACE):
            with patch("scripts.package_a_migration_rehearsal.create_backup",
                       side_effect=AssertionError("backup must not start")):
                out=self.run_migration()
        self.assertEqual(out["state"],"BLOCKED_WRITER_BUSY")
        self.assertEqual(out["sqlite_connections_started"],0)
        self.assertFalse(out["backup_started"])
        self.assertFalse((self.root/"backup-only").exists())
        self.assertFalse(self.tables())

    def test_rejects_nonfixture_paths_and_production_style_names(self):
        with self.assertRaises(ValueError):
            rehearse_additive_migration(
                self.root/"btc_research.db",self.lockdir,now=NOW)
        wrong=self.root/"another"
        wrong.mkdir()
        with self.assertRaises(ValueError):
            rehearse_additive_migration(self.db,wrong,now=NOW)
        self.assertFalse(self.tables())

    def test_missing_or_forged_fixture_marker_fails_closed(self):
        mark=self.root/MARKER
        mark.write_text("not approved",encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_migration()
        mark.unlink()
        with self.assertRaises(ValueError):
            self.run_migration()
        self.assertFalse(self.tables())

    def test_symlink_to_fixture_file_may_not_be_used(self):
        linked=self.root/"btc_research.db"
        linked.symlink_to(self.db)
        with self.assertRaises(ValueError):
            rehearse_additive_migration(linked,self.lockdir,now=NOW)

    def test_unsupported_fault_does_not_create_backup_or_tables(self):
        with self.assertRaises(ValueError):
            self.run_migration(crash_after="not-supported")
        self.assertFalse((self.root/"backup-only").exists())
        self.assertFalse(self.tables())

    def test_rehearsal_never_grants_production_or_source_rights(self):
        result=self.run_migration()
        self.assertFalse(result["production_authorized"])
        self.assertEqual(result["real_api_requests"],0)
        self.assertEqual(result["historical_completeness"],"NOT_VERIFIED")
        self.assertEqual(result["real_provider_entitlement"],"NOT_VERIFIED")

    def test_forged_same_column_names_but_missing_receipt_pk_rejected(self):
        before=self.legacy()
        with sqlite3.connect(self.db) as db:
            db.execute(
                "CREATE TABLE package_a_receipts("
                "receipt_id TEXT, provider TEXT, metric TEXT, symbol TEXT,"
                "frequency TEXT,start_utc TEXT,end_exclusive_utc TEXT,status TEXT,"
                "http_status INTEGER,raw_payload BLOB,raw_sha256 TEXT,"
                "parsed_count INTEGER,captured_utc TEXT)")
        existing=self.tables()
        with self.assertRaisesRegex(ValueError,"Incompatible Package A primary key"):
            self.run_migration()
        self.assertEqual(self.tables(),existing)
        self.assertEqual(self.legacy(),before)

    def test_forged_observations_table_with_expected_pk_but_missing_fk_rejected(self):
        with sqlite3.connect(self.db) as db:
            db.execute(
                "CREATE TABLE package_a_observations("
                "provider TEXT NOT NULL,metric TEXT NOT NULL,symbol TEXT NOT NULL,"
                "frequency TEXT NOT NULL,observed_utc TEXT NOT NULL,"
                "dimension_key TEXT NOT NULL,value REAL NOT NULL,"
                "unit TEXT NOT NULL,raw_scale TEXT NOT NULL,"
                "row_sha256 TEXT NOT NULL,receipt_id TEXT NOT NULL,"
                "PRIMARY KEY(provider,metric,symbol,frequency,observed_utc,dimension_key))")
        original=self.tables()
        with self.assertRaisesRegex(ValueError,"Incompatible Package A foreign key"):
            self.run_migration()
        self.assertEqual(self.tables(),original)

    def test_schema_shape_rechecked_on_idempotent_second_run(self):
        self.run_migration()
        with sqlite3.connect(self.db) as db:
            # Replacing a live table is NEVER allowed; make corruption
            # explicit in the fixture to assert the retry fails closed.
            db.execute("ALTER TABLE package_a_cursors RENAME TO old_package_a_cursors")
            db.execute(
                "CREATE TABLE package_a_cursors("
                "provider TEXT,metric TEXT,symbol TEXT,frequency TEXT,"
                "end_exclusive_utc TEXT,advanced_by_receipt TEXT)")
        with self.assertRaisesRegex(ValueError,"Incompatible Package A primary key"):
            self.run_migration()

    def test_runner_includes_synthetic_migration_rehearsal_once(self):
        import ast
        tree=ast.parse(
            (Path(__file__).resolve().parents[1]/"run_all.py").read_text(encoding="utf-8"))
        values=[ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
                and any(isinstance(v,ast.Name) and v.id=="TESTS" for v in n.targets)]
        self.assertEqual(len(values),1)
        self.assertGreaterEqual(len(values[0]),56)
        self.assertEqual(sum("test_package_a_legacy_migration" in cmd
                             for _,cmd in values[0]),1)


if __name__=="__main__":
    unittest.main()
