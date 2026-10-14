from contextlib import closing
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from tests.unit.live_fixture_helpers import new_fixture,grant_for,ASOF,CODE_SHA
from tests.unit.installed_fixture_helpers import CAPTURE

class LivePipelineTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('scripts.installed_live_pipeline'),'native pipeline missing')
        from scripts import installed_live_pipeline as m
        return m
    def fixture(self,full=False):
        m=self.module()
        from scripts.package_b_report_fixture import example_jobs
        from ingestion.installed_live_sources import request_from_example
        from ingestion.installed_live_transport import FrozenLiveTransport
        from scripts.installed_live_policy import validate_runtime_grant
        root,path,locks=new_fixture(self);jobs=example_jobs() if full else (next(j for j in example_jobs() if j.specs[0].provider=='binance'),)
        requests=tuple(map(request_from_example,jobs))
        plan=m.LivePlan('native-test',requests,'2025-01-01T00:00:00Z','2026-10-12T00:00:00Z',ASOF,CAPTURE)
        g=grant_for(root,path,locks,requests=requests,max_requests=128,max_dp=1000000)
        g=replace(g,plan_sha=plan.sha256,approval_artifact_sha='');g=replace(g,approval_artifact_sha=g.artifact_sha)
        cap=validate_runtime_grant(g,code_sha=CODE_SHA,plan_sha=plan.sha256,now=ASOF,operation='read',approved_artifact_sha=g.artifact_sha)
        client=FrozenLiveTransport({r.sha256:((200,j.raw_bytes),) for r,j in zip(requests,jobs)})
        return m,root,path,plan,cap,client
    def test_default_plan_reads_no_env_db_or_transport(self):
        m=self.module()
        with patch('os.getenv',side_effect=AssertionError('env')),patch('sqlite3.connect',side_effect=AssertionError('DB')),patch('urllib.request.urlopen',side_effect=AssertionError('HTTP')):
            r=m.run_live_pipeline(m.LivePlan('plan-only',(), '2026-10-01T00:00:00Z','2026-10-12T00:00:00Z',ASOF,CAPTURE))
        self.assertEqual(r['state'],'PLAN_ONLY_NO_IO')
    def test_six_source_complete_pipeline_and_completed_resume_refetch_zero(self):
        m,root,path,plan,cap,client=self.fixture(True)
        result=m.run_live_pipeline(plan,capability=cap,apply=True,transport=client)
        self.assertEqual(result['completed'],list(m.STAGES));self.assertEqual(result['gate']['state'],'OFFLINE_LIVE_BINDING_LOCAL_PASS')
        self.assertGreaterEqual(result['gate']['score'],95);self.assertEqual(result['gate']['series_count'],57)
        self.assertFalse(result['gate']['production_publication_authorized'])
        self.assertEqual(client.calls,45);before=client.calls
        again=m.run_live_pipeline(plan,capability=cap,apply=True,transport=client)
        self.assertEqual(again,result);self.assertEqual(client.calls,before)
        for p in (root/'live-run-native-test').iterdir():self.assertEqual(p.stat().st_mode&0o777,0o600)
        probe=subprocess.run(['node','scripts/package_b_visual_qa.cjs','--html',str(root/'live-run-native-test'/'report.html'),'--gate',str(root/'live-run-native-test'/'gate.json'),'--output-dir',str(root/'visual-probe'),'--playwright-module',str(root/'absent-local-playwright')],capture_output=True,text=True)
        self.assertNotIn('Unapproved or mismatched',probe.stderr)
        self.assertIn('Cannot find module',probe.stderr)

    def test_incomplete_pilot_remains_private_diagnosis(self):
        m,root,path,plan,cap,client=self.fixture()
        result=m.run_live_pipeline(plan,capability=cap,apply=True,transport=client)
        self.assertEqual(result['gate']['state'],'PRIVATE_DIAGNOSTIC_INCOMPLETE')
        self.assertFalse(result['gate']['full_report_ready']);self.assertFalse(result['gate']['production_publication_authorized'])
    def test_all_twenty_one_stage_faults_resume_without_hidden_refetch(self):
        m=self.module()
        for stage in m.STAGES:
            for prefix in ('before:','after-output:','after:'):
                with self.subTest(fault=prefix+stage):
                    m,root,path,plan,cap,client=self.fixture()
                    with self.assertRaises(m.PipelineCrash):m.run_live_pipeline(plan,capability=cap,apply=True,transport=client,fault=prefix+stage)
                    result=m.run_live_pipeline(plan,capability=cap,apply=True,transport=client)
                    self.assertEqual(result['completed'],list(m.STAGES));self.assertEqual(client.calls,1)
    def test_committed_ingest_before_checkpoint_replays_zero_http(self):
        m,root,path,plan,cap,client=self.fixture()
        with self.assertRaises(BaseException):m.run_live_pipeline(plan,capability=cap,apply=True,transport=client,fault='ingest:after_commit')
        self.assertEqual(client.calls,1)
        m.run_live_pipeline(plan,capability=cap,apply=True,transport=client);self.assertEqual(client.calls,1)
    def test_checkpoint_raw_output_plan_and_grant_corruption_block(self):
        for kind in ('checkpoint','raw','output','plan','grant'):
            with self.subTest(kind=kind):
                m,root,path,plan,cap,client=self.fixture()
                m.run_live_pipeline(plan,capability=cap,apply=True,transport=client);before=client.calls
                if kind=='checkpoint':(root/'live-run-native-test'/'checkpoint.json').write_text('{}')
                elif kind=='output':(root/'live-run-native-test'/'report.html').write_text('corrupt')
                elif kind=='raw':
                    with closing(sqlite3.connect(path)) as db,db:db.execute("UPDATE live_raw_payloads SET body=X'00'")
                elif kind=='plan':plan=replace(plan,snapshot_captured_utc='2026-10-11T00:00:00Z')
                else:cap=replace(cap,grant=replace(cap.grant,max_requests=1))
                with self.assertRaises(ValueError):m.run_live_pipeline(plan,capability=cap,apply=True,transport=client)
                self.assertEqual(client.calls,before)
    def test_expiry_during_stage_does_not_complete(self):
        m,root,path,plan,cap,client=self.fixture()
        from scripts.installed_live_policy import ValidatedGrant
        real=ValidatedGrant.verify;calls=[0]
        def verify(self,operation,**kw):
            calls[0]+=1
            if calls[0]>45:raise ValueError('RUNTIME_GRANT_EXPIRED')
            return real(self,operation,**kw)
        with patch.object(ValidatedGrant,'verify',verify):
            with self.assertRaisesRegex(ValueError,'EXPIRED'):m.run_live_pipeline(plan,capability=cap,apply=True,transport=client)
        self.assertFalse((root/'live-run-native-test'/'gate.json').exists())
    def test_writer_readiness_requires_observed_complete_manifest(self):
        self.assertIsNotNone(importlib.util.find_spec('scripts.installed_live_readiness'),'readiness missing')
        from scripts.installed_live_readiness import assess_writer_readiness
        manifest={'namespace':'btc-research-global-writer-v1','lock':'/explicit/locks','code_sha':'a'*40,'writers':[{'id':'daily','sha256':'b'*64}],'window_start':'2026-10-12T00:00:00Z','window_end':'2026-10-12T00:10:00Z'}
        unknown={'observed_utc':ASOF,'inventory_complete':False,'processes':[]}
        self.assertEqual(assess_writer_readiness(manifest,unknown)['state'],'BLOCKED')
        good={'observed_utc':ASOF,'inventory_complete':True,'processes':[{'id':'daily','sha256':'b'*64,'state':'STOPPED_OBSERVED','namespace':'btc-research-global-writer-v1','lock':'/explicit/locks','code_sha':'a'*40}]}
        self.assertEqual(assess_writer_readiness(manifest,good)['state'],'CONTROLLED_WRITER_WINDOW_OBSERVED')
        good['processes'].append({'id':'rogue','state':'RUNNING_UNCOOPERATIVE'})
        self.assertEqual(assess_writer_readiness(manifest,good)['state'],'BLOCKED')

    def test_sigkill_at_real_committed_boundary_leaves_replayable_custody(self):
        m,root,path,plan,cap,client=self.fixture()
        script="""
import json,os,signal
from pathlib import Path
from unittest.mock import patch
from scripts.installed_live_policy import grant_from_dict,validate_runtime_grant
from scripts.installed_live_pipeline import LivePlan,run_live_pipeline
from ingestion.installed_live_sources import request_from_dict
from ingestion.installed_live_transport import FrozenLiveTransport
from ingestion.installed_live_acquisition import InjectedCrash
v=json.loads(Path(__import__('sys').argv[1]).read_text());g=grant_from_dict(v['grant']);p=v['plan'];p['requests']=tuple(request_from_dict(r) for r in p['requests']);p['selections']=();plan=LivePlan(**p)
cap=validate_runtime_grant(g,code_sha=g.code_sha,plan_sha=plan.sha256,now=v['asof'],operation='read',approved_artifact_sha=g.artifact_sha)
client=FrozenLiveTransport({k:((200,bytes.fromhex(b)),) for k,b in v['raw'].items()})
try:run_live_pipeline(plan,capability=cap,apply=True,transport=client,fault='ingest:after_commit')
except InjectedCrash:os.kill(os.getpid(),signal.SIGKILL)
"""
        from dataclasses import asdict
        config=root/'child.json';config.write_text(json.dumps({'grant':asdict(cap.grant),'plan':asdict(plan),'asof':ASOF,'raw':{k:v[0][1].hex() for k,v in client.outcomes.items()}}));config.chmod(0o600)
        child=subprocess.run([sys.executable,'-c',script,str(config)],cwd=Path(__file__).absolute().parents[2],capture_output=True,timeout=10)
        self.assertEqual(child.returncode,-signal.SIGKILL,child.stderr.decode())
        result=m.run_live_pipeline(plan,capability=cap,apply=True,transport=client)
        self.assertEqual(client.calls,0);self.assertEqual(len(result['completed']),7)
    def test_contender_starts_zero_sql_or_http(self):
        m,root,path,plan,cap,client=self.fixture()
        from scripts.market_r10e11_writer_exclusion import hold_writer_lock
        # flock in an independently opened descriptor is an actual contender.
        with hold_writer_lock(cap.grant.lock,namespace='btc-research-global-writer-v1'):
            with patch('sqlite3.connect',side_effect=AssertionError('contender opened SQL')):
                with self.assertRaises(Exception):m.run_live_pipeline(plan,capability=cap,apply=True,transport=client)
        self.assertEqual(client.calls,0)
    def test_uncheckpointed_corrupt_output_is_not_silently_repaired(self):
        m,root,path,plan,cap,client=self.fixture()
        with self.assertRaises(m.PipelineCrash):m.run_live_pipeline(plan,capability=cap,apply=True,transport=client,fault='after-output:report')
        (root/'live-run-native-test'/'report.html').write_bytes(b'corrupt')
        with self.assertRaises(ValueError):m.run_live_pipeline(plan,capability=cap,apply=True,transport=client)
        self.assertEqual((root/'live-run-native-test'/'report.html').read_bytes(),b'corrupt')

if __name__=='__main__':unittest.main()
