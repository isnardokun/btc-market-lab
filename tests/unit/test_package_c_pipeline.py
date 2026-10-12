"""Actual A→B pipeline and read-only gates over marked SQLite fixtures."""
import hashlib
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch
from tests.unit.package_c_test_helpers import new_root
from scripts import package_c_state as state

try:
    from scripts import package_c_pipeline as c
except ImportError:
    c=None


class PipelineContractTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(c,'Package C pipeline missing')

    def test_default_plan_does_not_create_root_open_sqlite_or_call_source(self):
        root=new_root(self)
        with patch.object(sqlite3,'connect',side_effect=AssertionError('Planning SQL')):
            with patch('scripts.package_b_report_fixture.apply_example_job',side_effect=AssertionError('Planning HTTP')):
                plan=c.run_fixture_pipeline(root)
        self.assertEqual(plan['state'],'PLAN_ONLY_NO_IO')
        self.assertEqual(plan['planned_fake_requests'],45)
        self.assertEqual(plan['planned_streams'],57)
        self.assertFalse(plan['production_authorized'])
        self.assertFalse(root.exists())

    def test_apply_requires_explicit_preparation_before_any_sqlite(self):
        root=new_root(self)
        with patch.object(sqlite3,'connect',side_effect=AssertionError('Unprepared SQL')):
            result=c.run_fixture_pipeline(root,apply=True)
        self.assertEqual(result['state'],'BLOCKED_OFFLINE_PIPELINE')
        self.assertFalse(root.exists())

    def test_prepare_and_plan_drift_are_fail_closed(self):
        root=new_root(self)
        c.prepare_fixture(root)
        with patch.object(sqlite3,'connect',side_effect=AssertionError('Drift SQL')):
            result=c.run_fixture_pipeline(root,apply=True,
                policy=c.OrchestrationPolicy(max_fake_requests=127))
        self.assertEqual(result['state'],'BLOCKED_OFFLINE_PIPELINE')
        self.assertIn('PLAN_DRIFT',result['reason'])

    def test_budget_below_native_plan_blocks_before_sql_or_http(self):
        root=new_root(self)
        policy=c.OrchestrationPolicy(max_fake_requests=44)
        with patch.object(sqlite3,'connect',side_effect=AssertionError('Budget SQL')):
            with self.assertRaises(ValueError):c.prepare_fixture(root,policy=policy)
        self.assertFalse(root.exists())
        for invalid in (True,0,129):
            with self.assertRaises(ValueError):c.OrchestrationPolicy(max_fake_requests=invalid)

    def test_complete_pipeline_is_source_audited_archived_and_idempotent(self):
        root=new_root(self);c.prepare_fixture(root)
        result=c.run_fixture_pipeline(root,apply=True)
        self.assertEqual(result['state'],'COMPLETE_OFFLINE_FIXTURE_ONLY',result)
        self.assertEqual(result['fake_requests_this_invocation'],45)
        self.assertEqual(result['fake_requests_reserved'],45)
        self.assertEqual(result['stages_completed'],list(c.STAGES))
        gate=json.loads((root/'artifacts/gate.json').read_text())
        self.assertTrue(gate['publication_gate']['pass'])
        self.assertEqual(gate['publication_gate']['score_total'],100)
        self.assertFalse(gate['production_publication_authorized'])
        model=json.loads((root/'artifacts/evidence.json').read_text())
        self.assertEqual(len(model['snapshot']['series']),57)
        archive=json.loads((root/'artifacts/archive.json').read_text())
        for name,digest in archive['files'].items():
            self.assertEqual(hashlib.sha256((root/'artifacts'/name).read_bytes()).hexdigest(),digest)
        charts=json.loads((root/'artifacts/charts.json').read_text())
        self.assertEqual(len(charts),3)
        db=root/'package_a_synthetic.db';before=db.read_bytes()
        again=c.run_fixture_pipeline(root,apply=True)
        self.assertEqual(again['state'],'COMPLETE_OFFLINE_FIXTURE_ONLY',again)
        self.assertEqual(again['run_id'],result['run_id'])
        self.assertEqual(again['fake_requests_this_invocation'],0)
        self.assertEqual(db.read_bytes(),before)

    def test_manifest_tampering_or_unknown_fields_block_before_sqlite(self):
        root=new_root(self);c.prepare_fixture(root)
        path=root/'run_state.json';payload=json.loads(path.read_text())
        payload['body']['status']='COMPLETE';path.write_text(json.dumps(payload))
        with patch.object(sqlite3,'connect',side_effect=AssertionError('Corrupt state SQL')):
            result=c.run_fixture_pipeline(root,apply=True)
        self.assertEqual(result['state'],'BLOCKED_OFFLINE_PIPELINE')

    def test_symlink_root_and_hardlink_state_cannot_touch_external_files(self):
        root=new_root(self);outside=root.parent/'outside';outside.mkdir()
        root.symlink_to(outside,target_is_directory=True)
        with self.assertRaises(ValueError):c.prepare_fixture(root)
        self.assertEqual(list(outside.iterdir()),[])
        root.unlink();c.prepare_fixture(root)
        copy=root.parent/'linked_state';copy.hardlink_to(root/'run_state.json')
        before=copy.read_bytes()
        with patch.object(sqlite3,'connect',side_effect=AssertionError('Linked SQL')):
            result=c.run_fixture_pipeline(root,apply=True)
        self.assertEqual(result['state'],'BLOCKED_OFFLINE_PIPELINE')
        self.assertEqual(copy.read_bytes(),before)

    def test_boolean_fixture_version_or_forged_complete_blocks_before_sql(self):
        for case in ('version','complete'):
            with self.subTest(case=case):
                root=new_root(self);c.prepare_fixture(root)
                if case=='version':
                    path=root/state.FLAG
                    marker=json.loads(path.read_text());marker['version']=True
                    path.write_text(json.dumps(marker))
                else:
                    body=state.read_state(root);body['completed']=list(c.STAGES);body['status']='COMPLETE'
                    (root/state.STATE).write_bytes(state.encode({'body':body,'sha256':state.digest(state.encode(body))}))
                with patch.object(sqlite3,'connect',side_effect=AssertionError('Invalid state reached SQL')):
                    result=c.run_fixture_pipeline(root,apply=True)
                self.assertEqual(result['state'],'BLOCKED_OFFLINE_PIPELINE')

    def test_unknown_fault_is_rejected_before_sql_or_ingestion(self):
        root=new_root(self);c.prepare_fixture(root)
        with patch.object(sqlite3,'connect',side_effect=AssertionError('Unknown fault SQL')):
            result=c.run_fixture_pipeline(root,apply=True,fault='after:production')
        self.assertEqual(result['state'],'BLOCKED_OFFLINE_PIPELINE')

    def test_state_writer_requires_actual_parent_lease(self):
        root=new_root(self);c.prepare_fixture(root)
        before=(root/state.STATE).read_bytes();body=state.read_state(root);body['status']='RUNNING'
        with self.assertRaises(ValueError):state.save_state(root,body)
        self.assertEqual((root/state.STATE).read_bytes(),before)

    def test_db_replaced_at_lock_acquisition_blocks_before_sql_or_fake_http(self):
        root=new_root(self);c.prepare_fixture(root)
        db=root/'package_a_synthetic.db';replacement=root/'replacement.db'
        replacement.write_bytes(db.read_bytes());before=replacement.read_bytes()
        original=c.hold_offline_parent_lease
        @contextmanager
        def swap_then_lock(locks,**kwargs):
            replacement.replace(db)
            with original(locks,**kwargs):yield
        with patch.object(c,'hold_offline_parent_lease',side_effect=swap_then_lock):
            with patch.object(sqlite3,'connect',side_effect=AssertionError('Replaced DB SQL')):
                with patch.object(c.source,'apply_example_job',side_effect=AssertionError('Replaced DB HTTP')):
                    result=c.run_fixture_pipeline(root,apply=True)
        self.assertEqual(result['reason'],'FIXTURE_IDENTITY_DRIFT',result)
        self.assertEqual(db.read_bytes(),before)

    def test_journal_event_variants_are_typed_and_allowlisted_before_sql(self):
        invalid=[None,{'token':'unvalidated-event'},
            {'stage':'ingest','state':'RESERVED','job':'unknown','attempt':1},
            {'stage':'ingest','state':'RESERVED','job':c.source.example_jobs()[0].key,'attempt':True},
            {'stage':'ingest','state':'RETRY_ADVISORY_ONLY','job':c.source.example_jobs()[0].key,
             'virtual_delay_seconds':5,'clock_wait_executed':True},
            {'stage':'ingest','state':'BLOCKED','reason':'UNVALIDATED_SECRET'},
            {'stage':'production','state':'DONE'}]
        for event in invalid:
            with self.subTest(event=event):
                root=new_root(self);c.prepare_fixture(root);body=state.read_state(root)
                body['events']=[event]
                (root/state.STATE).write_bytes(state.encode({'body':body,'sha256':state.digest(state.encode(body))}))
                before=(root/state.STATE).read_bytes()
                with patch.object(sqlite3,'connect',side_effect=AssertionError('Invalid journal SQL')):
                    with patch.object(c.source,'apply_example_job',side_effect=AssertionError('Invalid journal HTTP')):
                        result=c.run_fixture_pipeline(root,apply=True)
                self.assertEqual(result['reason'],'STATE_EVENT_SCHEMA_INVALID',result)
                self.assertEqual((root/state.STATE).read_bytes(),before)

    def test_owned_sqlite_connections_are_closed_at_stage_boundaries(self):
        root=new_root(self);created=[];original=sqlite3.connect
        def tracked(*args,**kwargs):
            conn=original(*args,**kwargs);created.append(conn);return conn
        try:
            with patch.object(sqlite3,'connect',side_effect=tracked):
                c.prepare_fixture(root)
                c._receipt_groups(root/'package_a_synthetic.db',c.source.example_jobs()[0])
            for conn in created:
                with self.assertRaises(sqlite3.ProgrammingError):conn.execute('SELECT 1')
        finally:
            for conn in created:conn.close()


class PipelineCliTests(unittest.TestCase):
    def setUp(self):
        self.cli=Path(__file__).resolve().parents[2]/'scripts/package_c_pipeline_fixture.py'
        self.assertTrue(self.cli.is_file(),'Offline plan/prepare/apply CLI is missing')

    def invoke(self,*args):
        return subprocess.run([sys.executable,str(self.cli),*args],capture_output=True,text=True)

    def test_cli_defaults_to_plan_and_has_no_implicit_database(self):
        root=new_root(self)
        result=self.invoke('--root',str(root))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['state'],'PLAN_ONLY_NO_IO')
        self.assertFalse(root.exists())

    def test_cli_prepare_and_apply_are_separate_explicit_actions(self):
        root=new_root(self)
        missing=self.invoke('--root',str(root),'--apply')
        self.assertEqual(missing.returncode,3)
        self.assertFalse(root.exists())
        prepared=self.invoke('--root',str(root),'--prepare')
        self.assertEqual(prepared.returncode,0,prepared.stdout)
        applied=self.invoke('--root',str(root),'--apply')
        self.assertEqual(applied.returncode,0,applied.stdout)
        self.assertEqual(json.loads(applied.stdout)['state'],'COMPLETE_OFFLINE_FIXTURE_ONLY')

    def test_cli_rejects_live_or_credential_flags_without_echoing_values(self):
        secret='DO_NOT_ECHO_FAKE_CREDENTIAL_ABC'
        result=self.invoke('--live','--token',secret)
        self.assertEqual(result.returncode,3)
        self.assertNotIn(secret,result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['reason'],'INVALID_CLI_ARGUMENTS')

if __name__=='__main__':unittest.main()
