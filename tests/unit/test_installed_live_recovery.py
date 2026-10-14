from contextlib import closing
from dataclasses import replace
import importlib.util
import sqlite3
import unittest
from scripts.package_b_report_fixture import example_jobs
from tests.unit.live_fixture_helpers import new_fixture,grant_for,capability,ASOF

class LiveRecoveryTests(unittest.TestCase):
    def setUp(self):self.root,self.path,self.locks=new_fixture(self)
    def modules(self):
        self.assertIsNotNone(importlib.util.find_spec('ingestion.installed_live_acquisition'),'Live acquisition missing')
        from ingestion import installed_live_acquisition as a,installed_live_sources as s,installed_live_transport as t
        from storage import installed_live_ledger as l
        return a,s,t,l
    def setup_page(self,*,max_requests=16,status=200,body=None):
        a,s,t,l=self.modules();job=example_jobs()[0];r=s.request_from_example(job)
        g=grant_for(self.root,self.path,self.locks,requests=(r,),max_requests=max_requests,max_dp=100000)
        transport=t.FrozenLiveTransport({r.sha256:((status,job.raw_bytes if body is None else body),)})
        return a,r,transport,capability(g),l
    def apply(self,**kw):
        a,r,t,cap,l=self.setup_page(**kw);from scripts.installed_live_guard import hold_runtime_session
        with hold_runtime_session(cap) as session:return a.acquire_live_page(session,r,t,run_id='live-run',now=ASOF),t
    def test_acquisition_commits_raw_five_streams_and_replay_without_refetch(self):
        result,t=self.apply();self.assertEqual(result.status,'ok');self.assertEqual(t.calls,1)
        again,t=self.apply();self.assertTrue(again.replayed);self.assertEqual(t.calls,0)
        with closing(sqlite3.connect(self.path)) as db:self.assertEqual(db.execute('SELECT COUNT(DISTINCT metric) FROM live_observations').fetchone()[0],5)
    def test_commit_before_checkpoint_reuses_durable_receipt(self):
        a,r,t,cap,l=self.setup_page();from scripts.installed_live_guard import hold_runtime_session
        with hold_runtime_session(cap) as session:
            with self.assertRaises(a.InjectedCrash):a.acquire_live_page(session,r,t,run_id='live-run',now=ASOF,fault='after_commit')
            again=a.acquire_live_page(session,r,t,run_id='live-run',now=ASOF)
            self.assertTrue(again.replayed);self.assertEqual(t.calls,1)
    def test_reserved_crash_and_new_run_id_cannot_refund_budget(self):
        a,r,t,cap,l=self.setup_page(max_requests=1);from scripts.installed_live_guard import hold_runtime_session
        with hold_runtime_session(cap) as session:
            with self.assertRaises(a.InjectedCrash):a.acquire_live_page(session,r,t,run_id='one',now=ASOF,fault='after_reserve')
            with self.assertRaisesRegex(ValueError,'BUDGET'):a.acquire_live_page(session,r,t,run_id='two',now=ASOF)
        self.assertEqual(t.calls,0)
    def test_deleting_all_receipts_cannot_reset_high_water(self):
        a,r,t,cap,l=self.setup_page();from scripts.installed_live_guard import hold_runtime_session
        with hold_runtime_session(cap) as session:
            with self.assertRaises(a.InjectedCrash):a.acquire_live_page(session,r,t,run_id='one',now=ASOF,fault='after_reserve')
            with session.connect(readonly=False) as (db,v):db.execute('DELETE FROM live_requests')
            with self.assertRaisesRegex(ValueError,'HISTORY'):a.acquire_live_page(session,r,t,run_id='two',now=ASOF)
    def test_deleted_cursor_blocks_audit(self):
        self.apply();a,r,t,cap,l=self.setup_page();from scripts.installed_live_guard import hold_runtime_session
        with hold_runtime_session(cap) as session:
            with session.connect(readonly=False) as (db,v):db.execute('DELETE FROM live_cursors')
            with self.assertRaisesRegex(ValueError,'CURSOR'):a.acquire_live_page(session,r,t,run_id='live-run',now=ASOF)
    def test_raw_corruption_blocks_replay(self):
        self.apply();a,r,t,cap,l=self.setup_page();from scripts.installed_live_guard import hold_runtime_session
        with hold_runtime_session(cap) as session:
            with session.connect(readonly=False) as (db,v):db.execute("UPDATE live_raw_payloads SET body=X'00'")
            with self.assertRaisesRegex(ValueError,'RAW'):a.acquire_live_page(session,r,t,run_id='live-run',now=ASOF)
    def test_errors_keep_raw_and_do_not_advance_cursor(self):
        result,t=self.apply(status=429,body=b'{"error":"SYNTHETIC"}');self.assertEqual(result.status,'failed')
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM live_raw_payloads').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM live_cursors').fetchone()[0],0)
    def test_raw_rows_rollback_is_atomic_then_recovery(self):
        for fault in ('after_raw','after_observations'):
            self.root,self.path,self.locks=new_fixture(self)
            a,r,t,cap,l=self.setup_page();from scripts.installed_live_guard import hold_runtime_session
            with hold_runtime_session(cap) as session:
                with self.assertRaises(a.InjectedCrash):a.acquire_live_page(session,r,t,run_id=fault,now=ASOF,fault=fault)
                with session.connect(readonly=True) as (db,v):self.assertEqual(db.execute('SELECT COUNT(*) FROM live_observations').fetchone()[0],0)
                done=a.acquire_live_page(session,r,t,run_id=fault,now=ASOF);self.assertEqual(done.status,'ok')
    def test_all_six_provider_pages_keep_simulated_custody(self):
        a,s,t,l=self.modules();jobs=[next(j for j in example_jobs() if j.specs[0].provider==p) for p in ('yahoo','fred','bitview','researchbitcoin','binance','bybit')]
        requests=tuple(s.request_from_example(j) for j in jobs)
        cap=capability(grant_for(self.root,self.path,self.locks,requests=requests,max_dp=1000000))
        transport=t.FrozenLiveTransport({r.sha256:((200,j.raw_bytes),) for r,j in zip(requests,jobs)})
        from scripts.installed_live_guard import hold_runtime_session
        with hold_runtime_session(cap) as session:
            for r in requests:self.assertEqual(a.acquire_live_page(session,r,transport,run_id='six',now=ASOF).status,'ok')
            with session.connect(readonly=True) as (db,v):self.assertEqual(l.audit_live(db,session=session)['custody_mode'],'SIMULATED_HTTP')

if __name__=='__main__':unittest.main()
