import importlib.util
import json
import sqlite3
import unittest
from unittest.mock import patch

from tests.unit.installed_fixture_helpers import new_fixture, ASOF
from scripts.package_b_report_fixture import example_jobs
from storage.installed_schema import legacy_fingerprint
from scripts.package_a_parent_lock import hold_offline_parent_lease
from scripts.market_r10e11_writer_exclusion import WriterLockBusy


class CompatibilityAcquisitionTests(unittest.TestCase):
    def setUp(self): self.root,self.path,self.locks=new_fixture(self)

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('ingestion.compatibility_acquisition'),'Acquisition route missing')
        from ingestion import compatibility_acquisition
        return compatibility_acquisition

    def request(self,index=0): return self.module().request_from_job(example_jobs()[index])

    def apply(self,request=None,raw=None,status=200,**kwargs):
        m=self.module(); job=example_jobs()[0]
        request=request or m.request_from_job(job)
        transport=m.FakeTransport({request.sha256:((status,job.raw_bytes if raw is None else raw),)})
        result=m.acquire_fake_page(self.path,self.locks,request,transport,run_id=kwargs.pop('run_id','run-1'),
            now=ASOF,max_requests=kwargs.pop('max_requests',3),max_attempts=kwargs.pop('max_attempts',3),**kwargs)
        return result,transport

    def test_additive_migration_keeps_34_tables_and_is_idempotent(self):
        m=self.module()
        with sqlite3.connect(self.path) as db: before=legacy_fingerprint(db)
        result,transport=self.apply()
        with sqlite3.connect(self.path) as db:
            self.assertEqual(before,legacy_fingerprint(db))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM compat_raw_payloads').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM compat_requests').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(DISTINCT metric) FROM compat_observations').fetchone()[0],5)
        self.assertEqual(result['status'],'ok');self.assertEqual(transport.calls,1)
        repeated,transport=self.apply()
        self.assertEqual(repeated['receipt_id'],result['receipt_id']); self.assertEqual(transport.calls,0)

    def test_all_six_providers_use_existing_parser_contracts(self):
        m=self.module();jobs=example_jobs()
        picked=[next(j for j in jobs if j.specs[0].provider==p) for p in ('yahoo','fred','bitview','researchbitcoin','binance','bybit')]
        for i,job in enumerate(picked):
            request=m.request_from_job(job);t=m.FakeTransport({request.sha256:((200,job.raw_bytes),)})
            result=m.acquire_fake_page(self.path,self.locks,request,t,run_id='source-'+str(i),now=ASOF,max_requests=3,max_attempts=3)
            self.assertEqual(result['status'],'ok',result)
        with sqlite3.connect(self.path) as db:
            providers={json.loads(row[0])['specs'][0]['provider'] for row in db.execute('SELECT request_json FROM compat_requests')}
            self.assertEqual(len(providers),6)
            from storage.compatibility_ledger import audit
            self.assertEqual(audit(db)['state'],'SIMULATED_ACQUISITIONS_RECONCILED')

    def test_empty_failure_rejection_do_not_advance_cursor(self):
        for status,raw,expected in [(429,b'{}','failed'),(503,b'{}','failed'),(None,b'','failed'),
            (200,b'{"chart":{"result":[],"error":null}}','rejected')]:
            result,_=self.apply(status=status,raw=raw,run_id='failure-'+str(status)+str(len(raw)))
            self.assertEqual(result['status'],expected)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM compat_cursors').fetchone()[0],0)

    def test_reserved_crash_consumes_budget_and_never_refetches_success(self):
        m=self.module()
        with self.assertRaises(m.InjectedCrash): self.apply(fault='after_reserve',max_requests=1)
        with self.assertRaisesRegex(ValueError,'BUDGET'): self.apply(max_requests=1)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT status FROM compat_requests').fetchone()[0],'reserved')

    def test_after_observations_rollback_then_recovery_is_atomic(self):
        m=self.module()
        with self.assertRaises(m.InjectedCrash): self.apply(fault='after_observations')
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM compat_observations').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM compat_cursors').fetchone()[0],0)
        result,_=self.apply();self.assertEqual(result['status'],'ok')
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM compat_requests').fetchone()[0],2)

    def test_after_commit_resume_reuses_receipt_without_transport(self):
        m=self.module()
        with self.assertRaises(m.InjectedCrash): self.apply(fault='after_commit')
        result,t=self.apply(); self.assertEqual(result['status'],'ok');self.assertEqual(t.calls,0)

    def test_policy_drift_and_frozen_body_drift_are_blocked(self):
        self.apply()
        with self.assertRaisesRegex(ValueError,'POLICY'): self.apply(max_requests=4)
        with self.assertRaisesRegex(ValueError,'FROZEN_RESPONSE_DRIFT'): self.apply(raw=b'{}')

    def test_lock_contender_starts_zero_sql_and_transport(self):
        m=self.module();request=self.request();t=m.FakeTransport({request.sha256:((200,example_jobs()[0].raw_bytes),)})
        with hold_offline_parent_lease(self.locks):
            with patch('ingestion.compatibility_acquisition.sqlite3.connect',side_effect=AssertionError('No SQL')):
                with self.assertRaises(WriterLockBusy):
                    m.acquire_fake_page(self.path,self.locks,request,t,run_id='contender',now=ASOF,max_requests=3,max_attempts=3)
        self.assertEqual(t.calls,0)

    def test_raw_corruption_blocks_evidence_and_replay(self):
        self.apply()
        with sqlite3.connect(self.path) as db: db.execute("UPDATE compat_raw_payloads SET body=X'00'")
        with self.assertRaisesRegex(ValueError,'RAW'): self.apply()

    def test_partial_schema_not_silently_installed(self):
        with sqlite3.connect(self.path) as db: db.execute('CREATE TABLE compat_requests(bad TEXT)')
        with self.assertRaisesRegex(ValueError,'ADDON_SCHEMA'): self.apply()

    def test_backup_restore_rehearsal_preserves_private_previous_backup(self):
        self.module()
        self.assertIsNotNone(importlib.util.find_spec('scripts.installed_migration_rehearsal'))
        from scripts.installed_migration_rehearsal import rehearsal
        previous=self.root/'keep.db';previous.write_bytes(b'KEEP')
        result=rehearsal(self.path,self.locks,now=ASOF)
        self.assertEqual(result['state'],'INSTALLED_MIGRATION_REHEARSAL_PASS')
        self.assertEqual(result['legacy_before'],result['legacy_after'])
        self.assertEqual(result['backup_fingerprint'],result['restored_fingerprint'])
        self.assertEqual(previous.read_bytes(),b'KEEP')

    def test_oi_gap_is_rejected_without_cursor(self):
        m=self.module();job=next(j for j in example_jobs() if j.specs[0].provider=='binance' and j.specs[0].metric=='open_interest')
        request=m.request_from_job(job);raw=json.dumps(json.loads(job.raw_bytes)[:1]).encode()
        result,t=self.apply(request=request,raw=raw,run_id='gap')
        self.assertEqual(result['status'],'rejected')
        with sqlite3.connect(self.path) as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM compat_cursors').fetchone()[0],0)

    def test_empty_fred_is_recorded_without_cursor(self):
        job=next(j for j in example_jobs() if j.specs[0].provider=='fred')
        result,t=self.apply(request=self.module().request_from_job(job),raw=b'{"observations":[]}',run_id='empty')
        self.assertEqual(result['status'],'empty')
        repeated,t=self.apply(request=self.module().request_from_job(job),raw=b'{"observations":[]}',run_id='empty')
        self.assertEqual(repeated['status'],'empty');self.assertEqual(t.calls,0)

    def test_missing_daily_point_cannot_advance_cursor(self):
        m=self.module();job=next(j for j in example_jobs() if j.specs[0].provider=='researchbitcoin')
        payload=json.loads(job.raw_bytes);payload['data']=payload['data'][1:]
        result,t=self.apply(request=m.request_from_job(job),raw=json.dumps(payload).encode(),run_id='daily-gap')
        self.assertEqual(result['status'],'rejected')
        with sqlite3.connect(self.path) as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM compat_cursors').fetchone()[0],0)

    def test_equal_raw_across_sources_keeps_distinct_request_provenance(self):
        m=self.module()
        for provider in ('binance','bybit'):
            job=next(j for j in example_jobs() if j.specs[0].provider==provider)
            self.apply(request=m.request_from_job(job),status=429,raw=b'{}',run_id=provider)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM compat_raw_payloads').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(DISTINCT request_sha256) FROM compat_requests').fetchone()[0],2)

    def test_inconsistent_transport_modes_cannot_be_written_or_audited(self):
        self.apply()
        with sqlite3.connect(self.path) as db:
            with self.assertRaises(sqlite3.IntegrityError): db.execute("UPDATE compat_requests SET transport='HTTP'")
            db.execute("UPDATE compat_requests SET transport='HTTP',mode='REAL_ACQUISITION'")
            from storage.compatibility_ledger import audit
            with self.assertRaisesRegex(ValueError,'NOT_AUTHORIZED'): audit(db)

    def test_migration_crash_rolls_back_all_addons(self):
        self.module()
        from scripts.installed_migration_rehearsal import rehearsal
        with sqlite3.connect(self.path) as db: before=legacy_fingerprint(db)
        with self.assertRaisesRegex(ValueError,'INJECTED_MIGRATION'): rehearsal(self.path,self.locks,now=ASOF,fault='after_first_ddl')
        with sqlite3.connect(self.path) as db:
            self.assertEqual(before,legacy_fingerprint(db))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'compat_%'").fetchone()[0],0)

    def test_wal_backup_includes_uncheckpointed_legacy_row(self):
        self.module()
        from scripts.installed_migration_rehearsal import rehearsal
        with sqlite3.connect(self.path) as active:
            active.execute('PRAGMA journal_mode=WAL');active.execute('PRAGMA wal_autocheckpoint=0')
            active.execute("INSERT INTO meta VALUES('wal-row','SYNTHETIC_UNCHECKPOINTED')");active.commit()
            wal=self.path.with_name(self.path.name+'-wal')
            before=wal.read_bytes()
            result=rehearsal(self.path,self.locks,now=ASOF)
            with sqlite3.connect(result['backup']) as backup:
                self.assertEqual(backup.execute("SELECT value FROM meta WHERE key='wal-row'").fetchone()[0],'SYNTHETIC_UNCHECKPOINTED')
            self.assertEqual(result['backup_fingerprint']['tables']['meta']['rows'],2)
            self.assertTrue(before)


if __name__=='__main__': unittest.main()
