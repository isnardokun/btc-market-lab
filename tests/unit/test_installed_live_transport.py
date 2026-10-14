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

if __name__=='__main__':unittest.main()
