from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests.unit.installed_fixture_helpers import new_fixture, ASOF, CAPTURE
from scripts.package_b_report_fixture import example_jobs
from ingestion.compatibility_acquisition import request_from_job, FakeTransport, InjectedCrash
from scripts.package_c_pipeline import STAGES, OrchestrationPolicy
from scripts.package_a_parent_lock import hold_offline_parent_lease
from scripts import package_c_state as state
from storage.installed_schema import legacy_fingerprint


class InstalledPipelineTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('scripts.installed_schema_pipeline'),'Installed pipeline missing')
        from scripts import installed_schema_pipeline
        return installed_schema_pipeline

    def prepared(self,*,full=True,outcomes=None,policy=None,all_sources=False):
        root,path,locks=new_fixture(self)
        jobs=example_jobs() if all_sources else example_jobs()[:3] if full else ()
        requests=tuple(request_from_job(j) for j in jobs)
        values={r.sha256:((200,j.raw_bytes),) for r,j in zip(requests,jobs)}
        if outcomes:
            for i,codes in outcomes.items(): values[requests[i].sha256]=tuple((c,jobs[i].raw_bytes if c==200 else b'{}') for c in codes)
        transport=FakeTransport(values)
        m=self.module()
        selections=(m.LegacySelection(m.SeriesSpec('yahoo','close','BTC-USD','d1','USD'),'price_btc','BTC-USD'),)
        m.prepare_installed_run(root,asof=ASOF,snapshot_captured_utc=CAPTURE,selections=selections,
            requests=requests,policy=policy or OrchestrationPolicy(),transport=transport)
        return root,path,locks,transport

    def test_plan_only_does_not_read_path_connect_sql_or_transport(self):
        m=self.module()
        with patch.object(Path,'stat',side_effect=AssertionError('No path access')):
            with patch('scripts.installed_schema_pipeline.sqlite3.connect',side_effect=AssertionError('No SQL')):
                result=m.run_installed_pipeline('/home/ignotus/btc-research',apply=False)
        self.assertEqual(result['state'],'PLAN_ONLY_NO_IO')
        self.assertEqual(result['real_http_requests'],0)

    def test_full_acquired_report_is_private_reconciled_and_preserves_legacy(self):
        root,path,locks,t=self.prepared(all_sources=True)
        with closing(sqlite3.connect(path)) as db, db: before=legacy_fingerprint(db)
        result=self.module().run_installed_pipeline(root,apply=True,transport=t)
        self.assertEqual(result['state'],'COMPLETE_OFFLINE_COMPATIBILITY',result)
        self.assertTrue(result['report_gate_pass'])
        self.assertFalse(result['production_publication_authorized'])
        self.assertEqual(t.calls,45)
        with closing(sqlite3.connect(path)) as db, db: self.assertEqual(before,legacy_fingerprint(db))
        evidence=json.loads((root/'artifacts/evidence.json').read_text())
        self.assertEqual(len(evidence['snapshot']['series']),57)
        self.assertEqual(evidence['snapshot']['mode'],'OFFLINE_INSTALLED_SCHEMA')
        self.assertIn('SIMULATED_REQUEST_VERIFIED',(root/'artifacts/report.html').read_text())
        for file in (root/'artifacts').iterdir(): self.assertEqual(file.stat().st_mode&0o777,0o600)
        calls=t.calls
        self.assertEqual(self.module().run_installed_pipeline(root,apply=True,transport=t)['state'],result['state'])
        self.assertEqual(t.calls,calls)

    def test_legacy_only_finishes_blocked_private_diagnostic_without_transport(self):
        root,path,locks,t=self.prepared(full=False)
        result=self.module().run_installed_pipeline(root,apply=True,transport=t)
        self.assertEqual(result['state'],'COMPLETE_PRIVATE_DIAGNOSTIC',result)
        self.assertFalse(result['report_gate_pass']);self.assertEqual(t.calls,0)
        self.assertIn('NO_DATA',(root/'artifacts/report.html').read_text())

    def test_all_seven_stage_crashes_resume_without_duplicate_receipts(self):
        m=self.module()
        for stage in STAGES:
            for position in ('before','after','after_output'):
                with self.subTest(stage=stage,position=position):
                    root,path,locks,t=self.prepared(full=False)
                    with self.assertRaises(InjectedCrash): m.run_installed_pipeline(root,apply=True,transport=t,fault=position+':'+stage)
                    result=m.run_installed_pipeline(root,apply=True,transport=t)
                    self.assertEqual(result['state'],'COMPLETE_PRIVATE_DIAGNOSTIC',result)

    def test_receipt_commit_before_checkpoint_resumes_without_refetch(self):
        root,path,locks,t=self.prepared()
        with self.assertRaises(InjectedCrash): self.module().run_installed_pipeline(root,apply=True,transport=t,fault='acquire:after_commit')
        result=self.module().run_installed_pipeline(root,apply=True,transport=t)
        self.assertEqual(result['state'],'COMPLETE_OFFLINE_COMPATIBILITY',result)
        self.assertEqual(t.calls,3)

    def test_bounded_429_503_timeout_retries_then_success(self):
        for outcome in (429,503,None):
            with self.subTest(outcome=outcome):
                root,path,locks,t=self.prepared(outcomes={0:(outcome,200)})
                result=self.module().run_installed_pipeline(root,apply=True,transport=t)
                self.assertEqual(result['state'],'COMPLETE_OFFLINE_COMPATIBILITY',result)
                self.assertEqual(t.calls,4)
                self.assertIn('RETRY_ADVISORY_ONLY',(root/state.STATE).read_text())

    def test_reserved_unknown_attempt_cannot_refund_global_budget(self):
        root,path,locks,t=self.prepared(policy=OrchestrationPolicy(max_fake_requests=3))
        with self.assertRaises(InjectedCrash): self.module().run_installed_pipeline(root,apply=True,transport=t,fault='acquire:after_reserve')
        result=self.module().run_installed_pipeline(root,apply=True,transport=t)
        self.assertEqual(result['state'],'BLOCKED',result)
        self.assertFalse((root/'artifacts/report.html').exists())
        self.assertEqual(state.read_state(root)['status'],'BLOCKED')
        with closing(sqlite3.connect(path)) as db, db: self.assertEqual(db.execute('SELECT COUNT(*) FROM compat_requests').fetchone()[0],3)

    def test_lock_contender_has_no_sql_or_transport(self):
        root,path,locks,t=self.prepared()
        with hold_offline_parent_lease(locks):
            with patch('scripts.installed_schema_pipeline.sqlite3.connect',side_effect=AssertionError('No SQL')):
                result=self.module().run_installed_pipeline(root,apply=True,transport=t)
        self.assertEqual(result['state'],'BLOCKED_WRITER_BUSY');self.assertEqual(t.calls,0)

    def test_artifact_plan_and_inode_drift_are_blocked(self):
        root,path,locks,t=self.prepared()
        m=self.module();m.run_installed_pipeline(root,apply=True,transport=t)
        (root/'artifacts/report.html').write_text('TAMPERED')
        self.assertEqual(m.run_installed_pipeline(root,apply=True,transport=t)['state'],'BLOCKED')
        root,path,locks,t=self.prepared()
        original=path.read_bytes();path.rename(root/'old.db');path.write_bytes(original)
        with patch('scripts.installed_schema_pipeline.sqlite3.connect',side_effect=AssertionError('No SQL')):
            self.assertEqual(m.run_installed_pipeline(root,apply=True,transport=t)['state'],'BLOCKED')

    def test_real_child_sigkill_after_reservation_consumes_attempt(self):
        root,path,locks,t=self.prepared()
        payload=state.bounded_read(root/state.STATE,state.MAX_STATE)
        code='''import os,signal,sys
from pathlib import Path
from scripts import installed_schema_pipeline as p
from ingestion import compatibility_acquisition as a
from scripts.package_b_report_fixture import example_jobs
jobs=example_jobs()[:3]
requests=[a.request_from_job(j) for j in jobs]
t=a.FakeTransport({r.sha256:((200,j.raw_bytes),) for r,j in zip(requests,jobs)})
original=a._fault
def killed(fault,position):
    if position=='after_reserve': os.kill(os.getpid(),signal.SIGKILL)
    return original(fault,position)
a._fault=killed
p.run_installed_pipeline(Path(sys.argv[1]),apply=True,transport=t)
'''
        result=subprocess.run([sys.executable,'-c',code,str(root)],cwd=Path(__file__).resolve().parents[2],capture_output=True,timeout=30)
        self.assertEqual(result.returncode,-signal.SIGKILL,result.stderr.decode())
        self.assertEqual(self.module().run_installed_pipeline(root,apply=True,transport=t)['state'],'COMPLETE_OFFLINE_COMPATIBILITY')
        with closing(sqlite3.connect(path)) as db, db: self.assertEqual(db.execute('SELECT COUNT(*) FROM compat_requests').fetchone()[0],4)

    def test_finished_run_cannot_accept_changed_run_policy_or_unknown_event(self):
        root,path,locks,t=self.prepared()
        m=self.module();m.run_installed_pipeline(root,apply=True,transport=t)
        with closing(sqlite3.connect(path)) as db, db: db.execute("UPDATE compat_runs SET policy_json='{}'")
        self.assertEqual(m.run_installed_pipeline(root,apply=True,transport=t)['state'],'BLOCKED')

    def test_plan_and_fake_response_drift_block_before_sql(self):
        root,path,locks,t=self.prepared()
        key=next(iter(t.outcomes));t.outcomes[key]=((200,b'{}'),)
        with patch('scripts.installed_schema_pipeline.sqlite3.connect',side_effect=AssertionError('No SQL')):
            self.assertEqual(self.module().run_installed_pipeline(root,apply=True,transport=t)['state'],'BLOCKED')
        self.assertEqual(t.calls,0)

    def test_hardlinked_artifact_and_symlink_root_are_rejected(self):
        root,path,locks,t=self.prepared(full=False)
        self.module().run_installed_pipeline(root,apply=True,transport=t)
        os.link(root/'artifacts/report.html',root/'extra-link.html')
        self.assertEqual(self.module().run_installed_pipeline(root,apply=True,transport=t)['state'],'BLOCKED')

    def test_weighted_gate_reopens_evidence_and_rejects_html_mutation(self):
        root,path,locks,t=self.prepared()
        self.module().run_installed_pipeline(root,apply=True,transport=t)
        self.assertIsNotNone(importlib.util.find_spec('validation.installed_report_gate'),'Installed weighted gate missing')
        from validation.installed_report_gate import evaluate_installed_fixture
        from analysis.installed_evidence import LegacySelection
        from ingestion.package_a_contracts import SeriesSpec
        selections=(LegacySelection(SeriesSpec('yahoo','close','BTC-USD','d1','USD'),'price_btc','BTC-USD'),)
        document=(root/'artifacts/report.html').read_text()
        gate=evaluate_installed_fixture(document,db_path=path,lock_dir=locks,asof=ASOF,snapshot_captured_utc=CAPTURE,selections=selections)
        self.assertTrue(gate['pass'],gate);self.assertGreaterEqual(gate['score_total'],95)
        self.assertFalse(gate['production_publication_authorized'])
        changed=evaluate_installed_fixture(document.replace('stroke-width="2.6"','stroke-width="9"'),db_path=path,lock_dir=locks,asof=ASOF,snapshot_captured_utc=CAPTURE,selections=selections)
        self.assertFalse(changed['pass']);self.assertGreater(changed['critical_count'],0)

    def test_deleted_cursors_block_completed_run_and_further_acquisition(self):
        root,path,locks,t=self.prepared();m=self.module()
        self.assertEqual(m.run_installed_pipeline(root,apply=True,transport=t)['state'],'COMPLETE_OFFLINE_COMPATIBILITY')
        with closing(sqlite3.connect(path)) as db, db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM compat_cursors').fetchone()[0],15)
            db.execute('DELETE FROM compat_cursors')
        self.assertEqual(m.run_installed_pipeline(root,apply=True,transport=t)['state'],'BLOCKED')


if __name__=='__main__': unittest.main()
