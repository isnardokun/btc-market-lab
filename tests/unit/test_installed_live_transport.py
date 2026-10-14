from dataclasses import replace
from io import BytesIO
import importlib.util
import json
import time
import unittest
from urllib.error import HTTPError

from tests.unit.live_fixture_helpers import new_fixture,grant_for,capability,ASOF
from scripts.package_b_report_fixture import example_jobs

class Response(BytesIO):
    def __init__(self,body,url,status=200):super().__init__(body);self.url=url;self.status=status;self.headers={'Content-Type':'application/json'}

class LiveTransportTests(unittest.TestCase):
    def modules(self):
        self.assertIsNotNone(importlib.util.find_spec('ingestion.installed_live_sources'),'Native request missing')
        from ingestion import installed_live_sources as s,installed_live_transport as t
        return s,t
    def test_six_sources_keep_native_units_and_windows(self):
        s,t=self.modules()
        for provider in ('yahoo','fred','bitview','researchbitcoin','binance','bybit'):
            job=next(j for j in example_jobs() if j.specs[0].provider==provider)
            r=s.request_from_example(job)
            points=s.normalize_live_page(r,job.raw_bytes,captured_utc=ASOF)
            self.assertTrue(points);self.assertEqual(r.specs,job.specs)
            self.assertNotIn('.invalid',r.native_origin)
    def test_constructor_has_no_environment_or_network_io(self):
        s,t=self.modules()
        from unittest.mock import patch
        with patch('os.getenv',side_effect=AssertionError('env read')):
            t.BoundedHttpTransport()
    def test_mocked_http_is_always_simulated_custody_and_closes(self):
        s,t=self.modules();root,path,locks=new_fixture(self);job=example_jobs()[0];r=s.request_from_example(job)
        cap=capability(grant_for(root,path,locks,requests=(r,)))
        response=Response(job.raw_bytes,r.native_origin+r.path)
        from scripts.installed_live_guard import hold_runtime_session
        def opener(req,timeout):response.url=req.full_url;return response
        with hold_runtime_session(cap):
            result=t.BoundedHttpTransport(opener=opener).fetch(r,cap,deadline=time.monotonic()+10)
        self.assertEqual(result.custody_mode,'SIMULATED_HTTP');self.assertTrue(response.closed)
    def test_foreign_redirect_is_rejected_and_closed(self):
        s,t=self.modules();root,path,locks=new_fixture(self);r=s.request_from_example(example_jobs()[0])
        cap=capability(grant_for(root,path,locks,requests=(r,)));response=Response(b'[]','https://evil.invalid/a')
        from scripts.installed_live_guard import hold_runtime_session
        with hold_runtime_session(cap):
            with self.assertRaisesRegex(ValueError,'REDIRECT'):
                t.BoundedHttpTransport(opener=lambda req,timeout:response).fetch(r,cap,deadline=time.monotonic()+10)
        self.assertTrue(response.closed)
    def test_oversized_response_is_rejected(self):
        s,t=self.modules();root,path,locks=new_fixture(self);r=s.request_from_example(example_jobs()[0])
        g=grant_for(root,path,locks,requests=(r,));g=replace(g,max_raw_bytes=8,approval_artifact_sha='');g=replace(g,approval_artifact_sha=g.artifact_sha)
        cap=capability(g);response=Response(b'123456789',r.native_origin+r.path)
        from scripts.installed_live_guard import hold_runtime_session
        def opener(req,timeout):response.url=req.full_url;return response
        with hold_runtime_session(cap):
            with self.assertRaisesRegex(ValueError,'PAYLOAD'):
                t.BoundedHttpTransport(opener=opener).fetch(r,cap,deadline=time.monotonic()+10)
        self.assertTrue(response.closed)
    def test_http_error_body_is_retained_not_lost(self):
        s,t=self.modules();root,path,locks=new_fixture(self);r=s.request_from_example(example_jobs()[0]);cap=capability(grant_for(root,path,locks,requests=(r,)))
        response=BytesIO(b'{"error":"SYNTHETIC_429"}')
        def opener(req,timeout):raise HTTPError(req.full_url,429,'limited',{'Retry-After':'2'},response)
        from scripts.installed_live_guard import hold_runtime_session
        with hold_runtime_session(cap):result=t.BoundedHttpTransport(opener=opener).fetch(r,cap,deadline=time.monotonic()+10)
        self.assertEqual(result.http_status,429);self.assertEqual(result.body,b'{"error":"SYNTHETIC_429"}');self.assertTrue(response.closed)
    def test_unapproved_request_and_deadline_start_zero_transport(self):
        s,t=self.modules();root,path,locks=new_fixture(self);r=s.request_from_example(example_jobs()[0]);cap=capability(grant_for(root,path,locks))
        calls=[];client=t.BoundedHttpTransport(opener=lambda *a,**k:calls.append(a))
        with self.assertRaises(ValueError):client.fetch(r,cap,deadline=time.monotonic()-1)
        self.assertEqual(calls,[])
    def test_grant_needs_actual_parent_lease_before_http(self):
        s,t=self.modules();root,path,locks=new_fixture(self);r=s.request_from_example(example_jobs()[0]);cap=capability(grant_for(root,path,locks,requests=(r,)))
        with self.assertRaises(ValueError):t.BoundedHttpTransport(opener=lambda *a,**k:self.fail('HTTP started')).fetch(r,cap,deadline=time.monotonic()+10)
    def test_native_params_cannot_be_changed_independently_of_window(self):
        s,t=self.modules();r=s.request_from_example(example_jobs()[0])
        with self.assertRaises(ValueError):replace(r,params=r.params+(('api_key','secret'),))
        with self.assertRaises(ValueError):replace(r,path='/account/orders')
    def test_fred_future_vintage_and_native_daily_gap_are_rejected(self):
        s,t=self.modules()
        job=next(j for j in example_jobs() if j.specs[0].provider=='fred');r=s.request_from_example(job)
        payload=json.loads(job.raw_bytes);payload['observations'][0]['realtime_start']='2027-01-01'
        with self.assertRaises(ValueError):s.normalize_live_page(r,json.dumps(payload).encode(),captured_utc=ASOF)
        job=next(j for j in example_jobs() if j.specs[0].provider=='researchbitcoin');r=s.request_from_example(job)
        payload=json.loads(job.raw_bytes);payload['data']=payload['data'][1:]
        with self.assertRaises(ValueError):s.normalize_live_page(r,json.dumps(payload).encode(),captured_utc=ASOF)
    def test_rbn_dp_upper_bound_rejects_unknown_large_shape(self):
        s,t=self.modules();job=next(j for j in example_jobs() if j.specs[0].provider=='researchbitcoin');r=s.request_from_example(job)
        r=replace(r,max_dp=1)
        with self.assertRaisesRegex(ValueError,'DP'):s.normalize_live_page(r,job.raw_bytes,captured_utc=ASOF)
    def test_private_pilot_native_limit_is_bounded_and_part_of_identity(self):
        s,t=self.modules();job=next(j for j in example_jobs() if j.specs[0].provider=='binance' and j.specs[0].metric=='funding_settled')
        r=s.native_request(job.specs,job.start,job.end,page_limit=100)
        self.assertEqual(dict(r.params)['limit'],100)
        with self.assertRaises(ValueError):s.native_request(job.specs,job.start,job.end,page_limit=1001)

class HardDeadlineTests(unittest.TestCase):
    def test_stalled_child_is_killed_and_reaped_at_total_deadline(self):
        from ingestion import installed_live_transport as t
        self.assertTrue(hasattr(t,'bounded_child'), 'hard process supervisor missing')
        import os
        started=time.monotonic()
        with self.assertRaisesRegex(TimeoutError,'TOTAL_DEADLINE'):
            t.bounded_child(lambda: (time.sleep(5),b'late')[1],deadline=started+.12,max_bytes=1024)
        self.assertLess(time.monotonic()-started,1)
        with self.assertRaises(ChildProcessError):os.waitpid(-1,os.WNOHANG)
    def test_large_ipc_is_drained_and_bounded(self):
        from ingestion import installed_live_transport as t
        self.assertTrue(hasattr(t,'bounded_child'), 'hard process supervisor missing')
        data=b'x'*1000000
        self.assertEqual(t.bounded_child(lambda:data,deadline=time.monotonic()+3,max_bytes=len(data)),data)
        with self.assertRaisesRegex(ValueError,'CHILD_RESULT'):
            t.bounded_child(lambda:data,deadline=time.monotonic()+3,max_bytes=8)
    def test_retry_outcomes_are_per_request_not_global_call_index(self):
        from ingestion.installed_live_transport import FrozenLiveTransport
        from ingestion.installed_live_sources import request_from_example
        from scripts.installed_live_guard import hold_runtime_session
        root,path,locks=new_fixture(self);a,b=map(request_from_example,example_jobs()[:2])
        cap=capability(grant_for(root,path,locks,requests=(a,b)))
        client=FrozenLiveTransport({a.sha256:((503,b'bad'),(200,b'good')),b.sha256:((503,b'first'),(200,b'second'))})
        with hold_runtime_session(cap):
            client.fetch(a,cap,deadline=time.monotonic()+2)
            got=client.fetch(b,cap,deadline=time.monotonic()+2)
        self.assertEqual(got.body,b'first')

    def test_real_transport_uses_bounded_child_without_real_network(self):
        from ingestion import installed_live_transport as t
        from ingestion.installed_live_sources import request_from_example
        from scripts.installed_live_policy import validate_runtime_grant
        from scripts.package_a_parent_lock import hold_offline_parent_lease
        from unittest.mock import patch
        import datetime as dt,base64
        root,path,locks=new_fixture(self);r=request_from_example(next(j for j in example_jobs() if j.specs[0].provider=='binance'))
        now=dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        g=grant_for(root,path,locks,requests=(r,));g=replace(g,target_kind='INSTALLED_DB',approval_reference='https://github.com/isnardokun/btc-market-lab/issues/60#issuecomment-1',issued_utc=(now-dt.timedelta(seconds=30)).isoformat(),expires_utc=(now+dt.timedelta(minutes=10)).isoformat(),installed_schema_sha='c'*64,writer_window_sha='d'*64,backup_manifest_sha='e'*64,approval_artifact_sha='');g=replace(g,approval_artifact_sha=g.artifact_sha)
        cap=validate_runtime_grant(g,code_sha=g.code_sha,plan_sha=g.plan_sha,now=now.isoformat(),operation='read',approved_artifact_sha=g.artifact_sha)
        data=json.dumps({'body':base64.b64encode(b'[]').decode(),'status':200,'attempted':now.isoformat(),'received':now.isoformat(),'metadata':[]}).encode()
        with hold_offline_parent_lease(locks),patch.object(t,'bounded_child',return_value=data) as supervisor,patch.object(t.BoundedHttpTransport,'_direct',side_effect=AssertionError('must not call real HTTP in test')):
            result=t.BoundedHttpTransport().fetch(r,cap,deadline=time.monotonic()+5)
        self.assertEqual(supervisor.call_count,1);self.assertEqual(result.custody_mode,'HTTP');self.assertEqual(result.body,b'[]')

    def test_http_child_dies_when_supervisor_is_sigkilled(self):
        import os,signal,subprocess,sys,tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            marker=Path(temp)/'child.pid'
            script="""
import os,time,sys
from pathlib import Path
from ingestion.installed_live_transport import bounded_child
def operation():
    Path(sys.argv[1]).write_text(str(os.getpid()))
    time.sleep(10)
    return b'late'
bounded_child(operation,deadline=time.monotonic()+15,max_bytes=1024)
"""
            parent=subprocess.Popen([sys.executable,'-c',script,str(marker)],cwd=Path(__file__).absolute().parents[2])
            try:
                until=time.monotonic()+3
                while not marker.exists() and time.monotonic()<until:time.sleep(.01)
                self.assertTrue(marker.exists());child=int(marker.read_text())
                parent.kill();parent.wait(timeout=3)
                until=time.monotonic()+3
                while time.monotonic()<until:
                    proc=Path('/proc')/str(child)/'stat'
                    if not proc.exists() or proc.read_text().split(') ')[1].startswith('Z'):break
                    time.sleep(.01)
                else:self.fail('HTTP child survived dead supervisor')
            finally:
                if parent.poll() is None:parent.kill();parent.wait(timeout=3)

if __name__=='__main__':unittest.main()
