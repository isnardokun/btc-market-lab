from contextlib import closing
import importlib.util
import sqlite3
import tracemalloc
import unittest
from tests.unit.live_fixture_helpers import new_fixture,grant_for,capability,ASOF

class LiveStorageTests(unittest.TestCase):
    def setUp(self):self.root,self.path,self.locks=new_fixture(self)
    def modules(self):
        self.assertIsNotNone(importlib.util.find_spec('storage.installed_live_preservation'),'Scaled preservation missing')
        from storage import installed_live_preservation as p,installed_live_ledger as l
        return p,l
    def test_complete_scan_exceeds_old_limit_and_preserves_types(self):
        p,l=self.modules()
        with closing(sqlite3.connect(self.path)) as db,db:
            db.executemany('INSERT INTO daily(series_id,ts,value) VALUES(1,?,?)',((i,float(i)) for i in range(100004)))
            a=p.stream_legacy_fingerprint(db);self.assertEqual(a.table_counts['daily'],100007)
            db.execute("UPDATE meta SET value=X'00ff'");b=p.stream_legacy_fingerprint(db)
            self.assertNotEqual(a.table_shas['meta'],b.table_shas['meta'])
    def test_large_blob_is_hashed_incrementally(self):
        p,l=self.modules()
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("UPDATE meta SET value=?",(b'x'*(4*1024*1024),))
            tracemalloc.start()
            try:a=p.stream_legacy_fingerprint(db,batch_rows=2048,batch_bytes=8388608,value_chunk_bytes=65536);_,peak=tracemalloc.get_traced_memory()
            finally:tracemalloc.stop()
            self.assertEqual(a.table_counts['meta'],1);self.assertLess(peak,8*1024*1024)
    def test_cancelled_scan_never_returns_complete(self):
        p,l=self.modules()
        with closing(sqlite3.connect(self.path)) as db:
            with self.assertRaises(p.IncompleteScan):p.stream_legacy_fingerprint(db,cancel=lambda:True)
    def test_nullable_primary_keys_still_identify_each_physical_row(self):
        p,l=self.modules()
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('CREATE TABLE legacy_null_key(k TEXT PRIMARY KEY,v BLOB)')
            db.executemany('INSERT INTO legacy_null_key VALUES(NULL,?)',[(b'a',),(b'b',)])
            before=p.stream_legacy_fingerprint(db)
            db.execute("UPDATE legacy_null_key SET v=X'63' WHERE rowid=2")
            after=p.stream_legacy_fingerprint(db)
            self.assertNotEqual(before.table_shas['legacy_null_key'],after.table_shas['legacy_null_key'])
    def test_schema_sql_and_null_have_distinct_digests(self):
        p,l=self.modules()
        with closing(sqlite3.connect(self.path)) as db,db:
            a=p.stream_legacy_fingerprint(db);db.execute('CREATE INDEX synthetic_extra ON meta(value)');b=p.stream_legacy_fingerprint(db)
            self.assertNotEqual(a.schema_sha,b.schema_sha)
            db.execute('UPDATE meta SET value=NULL');c=p.stream_legacy_fingerprint(db)
            self.assertNotEqual(b.table_shas['meta'],c.table_shas['meta'])
    def test_migration_is_additive_idempotent_and_rollback_preserves_legacy(self):
        p,l=self.modules();from scripts.installed_live_guard import hold_runtime_session
        from scripts.installed_live_migration import rehearse_live_migration
        cap=capability(grant_for(self.root,self.path,self.locks))
        with hold_runtime_session(cap) as session:
            result=rehearse_live_migration(session)
            self.assertEqual(result['before'],result['after']);self.assertEqual(result['backup'],result['restored'])
            result2=rehearse_live_migration(session);self.assertEqual(result['after'],result2['after'])
    def test_migration_fault_rolls_back_addons(self):
        p,l=self.modules();from scripts.installed_live_guard import hold_runtime_session
        from scripts.installed_live_migration import rehearse_live_migration
        cap=capability(grant_for(self.root,self.path,self.locks))
        with hold_runtime_session(cap) as session:
            with self.assertRaisesRegex(ValueError,'INJECTED'):rehearse_live_migration(session,fault='after_first_ddl')
            with session.connect(readonly=True) as (db,v):self.assertEqual(db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'live_%'").fetchone()[0],0)
    def test_granted_migration_kernel_preserves_complete_legacy_snapshot(self):
        p,l=self.modules();from scripts.installed_live_guard import hold_runtime_session
        from scripts.installed_live_migration import migrate_installed
        with hold_runtime_session(capability(grant_for(self.root,self.path,self.locks))) as session:
            result=migrate_installed(session)
            self.assertEqual(result['before'],result['after'])
    def test_wal_only_row_survives_online_copy_and_private_restore(self):
        p,l=self.modules();from scripts.installed_live_guard import hold_runtime_session
        from scripts.installed_live_migration import rehearse_live_migration
        with closing(sqlite3.connect(self.path)) as active,active:
            active.execute('PRAGMA journal_mode=WAL');active.execute('PRAGMA wal_autocheckpoint=0')
            active.execute("INSERT INTO meta VALUES('wal-live','SYNTHETIC')");active.commit()
            with hold_runtime_session(capability(grant_for(self.root,self.path,self.locks))) as session:
                result=rehearse_live_migration(session)
            self.assertEqual(result['backup']['table_counts']['meta'],2)

if __name__=='__main__':unittest.main()
