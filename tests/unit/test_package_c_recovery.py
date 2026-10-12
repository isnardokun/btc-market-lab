"""Failure injection exercises persistent A receipts and C stage boundaries."""
import json
import multiprocessing as mp
import os
import signal
import sqlite3
import unittest
from unittest.mock import patch

from scripts import package_c_pipeline as c
from scripts import package_c_state as state
from scripts import package_b_report_fixture as source
from scripts.package_a_parent_lock import hold_offline_parent_lease
from tests.unit.package_c_test_helpers import new_root


def _counts(root):
    with sqlite3.connect(root/'package_a_synthetic.db') as db:
        return tuple(db.execute('SELECT count(*) FROM '+table).fetchone()[0]
            for table in ('package_a_observations','package_a_receipts'))

def _crash_child(root,ready):
    def checkpoint(fault,position):
        if position=='after:job:'+source.example_jobs()[0].key:
            ready.put('commit-durable')
            signal.pause()
    with patch.object(c,'_maybe_fault',side_effect=checkpoint):
        c.run_fixture_pipeline(root,apply=True)


class PipelineRecoveryTests(unittest.TestCase):
    def test_each_stage_checkpoint_recovers_without_duplicate_requests_or_rows(self):
        for stage in c.STAGES:
            with self.subTest(stage=stage):
                root=new_root(self);c.prepare_fixture(root)
                with self.assertRaises(c.InjectedCrash):
                    c.run_fixture_pipeline(root,apply=True,fault='after:'+stage)
                prior=_counts(root)
                result=c.run_fixture_pipeline(root,apply=True)
                self.assertEqual(result['state'],'COMPLETE_OFFLINE_FIXTURE_ONLY',result)
                self.assertEqual(result['fake_requests_this_invocation'],0)
                self.assertEqual(_counts(root),prior)

    def test_commit_before_job_checkpoint_is_reconciled_without_new_receipt(self):
        root=new_root(self);c.prepare_fixture(root)
        key=source.example_jobs()[0].key
        with self.assertRaises(c.InjectedCrash):
            c.run_fixture_pipeline(root,apply=True,fault='after:job:'+key)
        self.assertEqual(_counts(root)[1],5)
        result=c.run_fixture_pipeline(root,apply=True)
        self.assertEqual(result['state'],'COMPLETE_OFFLINE_FIXTURE_ONLY',result)
        self.assertEqual(result['fake_requests_this_invocation'],44)
        self.assertEqual(_counts(root)[1],57)

    def test_uncertain_reserved_attempt_is_not_refunded_after_crash(self):
        root=new_root(self);c.prepare_fixture(root)
        key=source.example_jobs()[0].key
        with self.assertRaises(c.InjectedCrash):
            c.run_fixture_pipeline(root,apply=True,fault='after:reserve:'+key)
        self.assertEqual(_counts(root),(0,0))
        result=c.run_fixture_pipeline(root,apply=True)
        self.assertEqual(result['state'],'COMPLETE_OFFLINE_FIXTURE_ONLY',result)
        self.assertEqual(result['fake_requests_reserved'],46)
        self.assertEqual(result['fake_requests_this_invocation'],45)

    def test_429_503_timeout_retry_has_native_receipts_and_virtual_advice(self):
        for status in (429,503,None):
            with self.subTest(status=status):
                root=new_root(self);key=source.example_jobs()[0].key
                failures={key:(status,200)}
                c.prepare_fixture(root,http_failures=failures)
                result=c.run_fixture_pipeline(root,apply=True,http_failures=failures)
                self.assertEqual(result['state'],'COMPLETE_OFFLINE_FIXTURE_ONLY',result)
                self.assertEqual(result['fake_requests_reserved'],46)
                body=state.read_state(root)
                advice=[e for e in body['events'] if e.get('state')=='RETRY_ADVISORY_ONLY']
                self.assertEqual(len(advice),1)
                self.assertGreater(advice[0]['virtual_delay_seconds'],0)
                self.assertFalse(advice[0]['clock_wait_executed'])
                self.assertEqual(_counts(root)[1],62)

    def test_exhausted_circuit_survives_new_invocation_and_is_recorded(self):
        root=new_root(self);key=source.example_jobs()[0].key
        failures={key:(429,429,429)}
        c.prepare_fixture(root,http_failures=failures)
        first=c.run_fixture_pipeline(root,apply=True,http_failures=failures)
        self.assertEqual(first['state'],'BLOCKED_OFFLINE_PIPELINE')
        self.assertEqual(first['reason'],'SOURCE_CIRCUIT_OPEN')
        self.assertEqual(first['fake_requests_this_invocation'],3)
        self.assertEqual(state.read_state(root)['status'],'BLOCKED')
        before=_counts(root)
        with patch.object(source,'apply_example_job',side_effect=AssertionError('Circuit reset')):
            again=c.run_fixture_pipeline(root,apply=True,http_failures=failures)
        self.assertEqual(again['state'],'BLOCKED_OFFLINE_PIPELINE')
        self.assertEqual(_counts(root),before)

    def test_completed_report_tampering_never_reuses_pass(self):
        root=new_root(self);c.prepare_fixture(root)
        self.assertEqual(c.run_fixture_pipeline(root,apply=True)['state'],'COMPLETE_OFFLINE_FIXTURE_ONLY')
        report=root/'artifacts/report.html'
        report.write_text(report.read_text().replace('stroke-width="2.6"','stroke-width="9"'))
        result=c.run_fixture_pipeline(root,apply=True)
        self.assertEqual(result['state'],'BLOCKED_OFFLINE_PIPELINE')
        self.assertIn('ARTIFACT',result['reason'])

    def test_forged_archive_shape_with_matching_checksum_is_blocked(self):
        root=new_root(self);c.prepare_fixture(root)
        self.assertEqual(c.run_fixture_pipeline(root,apply=True)['state'],'COMPLETE_OFFLINE_FIXTURE_ONLY')
        (root/'artifacts/archive.json').write_text('[]')
        body=state.read_state(root);body['outputs']['archive.json']=state.artifact_sha(root,'archive.json')
        (root/state.STATE).write_bytes(state.encode({'body':body,'sha256':state.digest(state.encode(body))}))
        result=c.run_fixture_pipeline(root,apply=True)
        self.assertEqual(result['state'],'BLOCKED_OFFLINE_PIPELINE')

    def test_before_stage_and_after_output_crashes_resume_from_last_safe_checkpoint(self):
        for prefix in ('before:','after-output:'):
            for stage in c.STAGES:
                with self.subTest(position=prefix+stage):
                    root=new_root(self);c.prepare_fixture(root)
                    with self.assertRaises(c.InjectedCrash):
                        c.run_fixture_pipeline(root,apply=True,fault=prefix+stage)
                    prior=_counts(root)
                    result=c.run_fixture_pipeline(root,apply=True)
                    self.assertEqual(result['state'],'COMPLETE_OFFLINE_FIXTURE_ONLY',result)
                    if prefix=='before:' and stage=='ingest':
                        self.assertEqual(result['fake_requests_this_invocation'],45)
                    else:
                        self.assertEqual(result['fake_requests_this_invocation'],0)
                        self.assertEqual(_counts(root),prior)

    def test_corrupt_raw_after_commit_blocks_before_any_new_source_call(self):
        root=new_root(self);c.prepare_fixture(root);key=source.example_jobs()[0].key
        with self.assertRaises(c.InjectedCrash):
            c.run_fixture_pipeline(root,apply=True,fault='after:job:'+key)
        with sqlite3.connect(root/'package_a_synthetic.db') as db:
            db.execute("UPDATE package_a_receipts SET raw_payload=?",(b'corrupted',))
        with patch.object(source,'apply_example_job',side_effect=AssertionError('Corruption source call')):
            result=c.run_fixture_pipeline(root,apply=True)
        self.assertEqual(result['state'],'BLOCKED_OFFLINE_PIPELINE')
        self.assertEqual(result['reason'],'RAW_INTEGRITY_BLOCKED')

    def test_permanent_rejected_payload_is_not_retried(self):
        from dataclasses import replace
        jobs=source.example_jobs()
        bad=(replace(jobs[0],raw_bytes=b'{"chart":{"result":null}}'),)+jobs[1:]
        root=new_root(self)
        with patch.object(source,'example_jobs',return_value=bad):
            c.prepare_fixture(root)
            result=c.run_fixture_pipeline(root,apply=True)
        self.assertEqual(result['state'],'BLOCKED_OFFLINE_PIPELINE')
        self.assertEqual(result['reason'],'PERMANENT_SOURCE_FAILURE')
        self.assertEqual(state.read_state(root)['jobs'][bad[0].key]['reserved'],1)
        self.assertEqual(_counts(root),(0,5))

    def test_contender_opens_no_sqlite_or_fake_http_and_changes_no_state(self):
        root=new_root(self);c.prepare_fixture(root)
        before=(root/state.STATE).read_bytes()
        with hold_offline_parent_lease(root/'locks'):
            with patch.object(sqlite3,'connect',side_effect=AssertionError('Contender SQL')):
                with patch.object(source,'apply_example_job',side_effect=AssertionError('Contender HTTP')):
                    result=c.run_fixture_pipeline(root,apply=True)
        self.assertEqual(result['state'],'BLOCKED_WRITER_BUSY')
        self.assertEqual((root/state.STATE).read_bytes(),before)

    @unittest.skipUnless(hasattr(os,'fork'),'POSIX fork required')
    def test_sigkill_releases_lease_and_resumes_committed_native_group(self):
        root=new_root(self);c.prepare_fixture(root)
        context=mp.get_context('fork');ready=context.Queue()
        child=context.Process(target=_crash_child,args=(root,ready));child.start()
        try:
            self.assertEqual(ready.get(timeout=20),'commit-durable')
            os.kill(child.pid,signal.SIGKILL);child.join(timeout=5)
            self.assertEqual(child.exitcode,-signal.SIGKILL)
            result=c.run_fixture_pipeline(root,apply=True)
            self.assertEqual(result['state'],'COMPLETE_OFFLINE_FIXTURE_ONLY',result)
            self.assertEqual(result['fake_requests_this_invocation'],44)
            self.assertEqual(_counts(root)[1],57)
        finally:
            if child.is_alive():child.kill();child.join(timeout=5)
            ready.close()

    def test_untrusted_uppercase_exception_is_redacted(self):
        root=new_root(self);c.prepare_fixture(root)
        secret='API_SECRET_DO_NOT_LOG_ABC123'
        with patch.object(source,'apply_example_job',side_effect=ValueError(secret)):
            result=c.run_fixture_pipeline(root,apply=True)
        self.assertEqual(result['state'],'BLOCKED_OFFLINE_PIPELINE')
        self.assertNotIn(secret,json.dumps(result))
        self.assertNotIn(secret,(root/state.STATE).read_text())

if __name__=='__main__':unittest.main()
