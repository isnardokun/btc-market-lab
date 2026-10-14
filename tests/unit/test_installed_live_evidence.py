from contextlib import closing
import importlib.util
import sqlite3
import unittest
from dataclasses import replace
from tests.unit.live_fixture_helpers import new_fixture,grant_for,capability,ASOF
from tests.unit.installed_fixture_helpers import CAPTURE
from ingestion.package_a_contracts import SeriesSpec
from analysis.installed_evidence import LegacySelection,read_installed_snapshot

class LiveEvidenceTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('analysis.installed_live_evidence'),'guarded evidence missing')
        from analysis import installed_live_evidence
        return installed_live_evidence
    def test_sql_window_selects_after_more_than_100000_unrelated_legacy_rows(self):
        m=self.module();root,path,locks=new_fixture(self)
        with closing(sqlite3.connect(path)) as db,db:
            db.executemany('INSERT INTO price_btc(ts,price) VALUES(?,1)',((1000000000+i,) for i in range(100007)))
        sel=LegacySelection(SeriesSpec('yahoo','close','BTC-USD','d1','USD'),'price_btc','BTC-USD')
        from scripts.installed_live_guard import hold_runtime_session
        with hold_runtime_session(capability(grant_for(root,path,locks))) as session:
            snap=m.read_live_snapshot(session,asof=ASOF,snapshot_captured_utc=CAPTURE,selections=(sel,),start_utc='2026-10-09T00:00:00Z',end_exclusive_utc='2026-10-12T00:00:00Z')
        self.assertEqual([p.value for p in snap.series[0].points],[80000,81000,80500])
        from analysis.package_b_analytics import analyze_snapshot
        model=analyze_snapshot(snap);self.assertEqual(model.assets[0].quality,'PARTIAL_CLOSE_ONLY')
        self.assertIsNone(model.assets[0].atr14);self.assertFalse(snap.production_authorized)
    def test_native_six_provider_custody_is_explicit_and_snapshot_hashes_recomputed(self):
        m=self.module()
        from scripts.package_b_report_fixture import example_jobs
        from ingestion.installed_live_sources import request_from_example
        from ingestion.installed_live_transport import FrozenLiveTransport
        from ingestion.installed_live_acquisition import acquire_live_page
        from scripts.installed_live_guard import hold_runtime_session
        root,path,locks=new_fixture(self);jobs=[next(j for j in example_jobs() if j.specs[0].provider==p) for p in ('yahoo','fred','bitview','researchbitcoin','binance','bybit')]
        requests=tuple(map(request_from_example,jobs));client=FrozenLiveTransport({r.sha256:((200,j.raw_bytes),) for r,j in zip(requests,jobs)})
        with hold_runtime_session(capability(grant_for(root,path,locks,requests=requests,max_dp=100000))) as session:
            for r in requests:acquire_live_page(session,r,client,run_id='evidence',now=ASOF)
            snap=m.read_live_snapshot(session,asof=ASOF,snapshot_captured_utc=CAPTURE,selections=(),start_utc='2026-01-01T00:00:00Z',end_exclusive_utc='2026-10-12T00:00:00Z')
        self.assertEqual(len({s.provider for s in snap.series}),6)
        self.assertEqual({p.provenance for s in snap.series for p in s.points},{'SIMULATED_HTTP_REQUEST_VERIFIED'})
        from analysis.package_b_analytics import analyze_snapshot
        from rendering.package_b_report import render_report,audit_candidate_report
        model=analyze_snapshot(snap);html=render_report(model)
        self.assertIn('SIMULATED_HTTP_REQUEST_VERIFIED',html)
        self.assertIn('SINTÉTICO',html);self.assertFalse(audit_candidate_report(model,html)['production_publication_authorized'])
    def seed_link(self,path,acquired):
        with closing(sqlite3.connect(path)) as db,db:
            cols=[r[1] for r in db.execute('PRAGMA table_info(market_request_lineage)')]
            values={'request_id':'synthetic-link','run_id':'SYNTHETIC','provider':'binance','stream':'funding_settled','symbol':'BTCUSDT','interval_label':'event','endpoint_path':'/fapi/v1/fundingRate','status':'success','attempted_utc':'2026-10-12T00:00:00Z','ended_utc':'2026-10-12T00:01:00Z','first_observed_utc':'2026-10-11T00:00:00Z','last_observed_utc':'2026-10-12T00:00:00Z','raw_sha256':db.execute('SELECT sha256 FROM market_raw_payloads').fetchone()[0]}
            db.execute('INSERT INTO market_request_lineage('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')',tuple(values.get(c,0) for c in cols))
            cols=[r[1] for r in db.execute('PRAGMA table_info(market_request_acquisitions)')];values['acquired_utc']=acquired
            db.execute('INSERT INTO market_request_acquisitions('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')',tuple(values.get(c,0) for c in cols))
    def test_future_acquired_utc_cannot_credit_unreconciled_link(self):
        root,path,locks=new_fixture(self);self.seed_link(path,'2026-10-13T00:00:00Z')
        sel=LegacySelection(SeriesSpec('binance','funding_settled','BTCUSDT','event','fraction'),'market_derivatives','BTCUSDT')
        snap=read_installed_snapshot(path,locks,asof=ASOF,snapshot_captured_utc=CAPTURE,selections=(sel,))
        self.assertIsNone(snap.series[0].points[0].receipt_id)
    def test_naive_acquired_utc_blocks_link(self):
        root,path,locks=new_fixture(self);self.seed_link(path,'2026-10-12T00:02:00')
        sel=LegacySelection(SeriesSpec('binance','funding_settled','BTCUSDT','event','fraction'),'market_derivatives','BTCUSDT')
        with self.assertRaises(ValueError):read_installed_snapshot(path,locks,asof=ASOF,snapshot_captured_utc=CAPTURE,selections=(sel,))
    def test_real_mode_rejects_simulated_points_and_labels_private_data_honestly(self):
        from analysis.package_b_evidence import ReportSnapshot,EvidenceSeries,EvidencePoint,snapshot_digest
        from analysis.package_b_analytics import analyze_snapshot
        from rendering.package_b_report import render_report
        point=EvidencePoint('2026-10-11T00:00:00Z','',1,'a'*64,'r','b'*64,'c'*64,CAPTURE,'SIMULATED_HTTP_REQUEST_VERIFIED')
        series=EvidenceSeries('binance','funding_settled','BTCUSDT','event','fraction','native',(point,),False)
        snap=ReportSnapshot(ASOF,CAPTURE,(series,),mode='PRIVATE_LIVE_EVIDENCE',dataset_origin='LIVE_PRIVATE')
        snap=replace(snap,sha256=snapshot_digest(snap))
        with self.assertRaises(ValueError):analyze_snapshot(snap)
        point=replace(point,provenance='HTTP_REQUEST_VERIFIED');snap=replace(snap,series=(replace(series,points=(point,)),));snap=replace(snap,sha256=snapshot_digest(snap))
        html=render_report(analyze_snapshot(snap));self.assertIn('DATOS REALES · DIAGNÓSTICO PRIVADO',html)
        self.assertNotIn('EJEMPLO SINTÉTICO',html);self.assertNotIn('ejemplo sintético',html)

    def test_acquisition_can_use_existing_schema_without_migration_permission(self):
        from scripts.installed_live_guard import hold_runtime_session
        from storage.installed_live_ledger import initialize_live
        from ingestion.installed_live_sources import request_from_example
        from ingestion.installed_live_transport import FrozenLiveTransport
        from ingestion.installed_live_acquisition import acquire_live_page
        from scripts.package_b_report_fixture import example_jobs
        root,path,locks=new_fixture(self);job=example_jobs()[0];req=request_from_example(job)
        g=grant_for(root,path,locks,requests=(req,))
        with hold_runtime_session(capability(g)) as session:
            with session.connect(readonly=False) as (db,check):
                db.execute('BEGIN IMMEDIATE');initialize_live(db,session=session,now=ASOF);check();db.commit()
        g=replace(g,allowed_operations=('read','ingest','report_private'),approval_artifact_sha='');g=replace(g,approval_artifact_sha=g.artifact_sha)
        with hold_runtime_session(capability(g)) as session:
            got=acquire_live_page(session,req,FrozenLiveTransport({req.sha256:((200,job.raw_bytes),)}),run_id='no-migrate',now=ASOF)
        self.assertEqual(got.status,'ok')

    def test_pathological_selected_text_is_rejected_before_batch_materialization(self):
        m=self.module();root,path,locks=new_fixture(self)
        with closing(sqlite3.connect(path)) as db,db:
            db.execute("UPDATE macro_fred SET quality_status=?",('x'*1000000,))
        sel=LegacySelection(SeriesSpec('fred','DGS10','USA','d1','percent'),'macro_fred','DGS10')
        from scripts.installed_live_guard import hold_runtime_session
        import tracemalloc
        with hold_runtime_session(capability(grant_for(root,path,locks))) as session:
            tracemalloc.start()
            try:
                with self.assertRaisesRegex(ValueError,'SCALAR_FIELD'):m.read_live_snapshot(session,asof=ASOF,snapshot_captured_utc=CAPTURE,selections=(sel,),start_utc='2026-10-01T00:00:00Z',end_exclusive_utc=CAPTURE)
                _,peak=tracemalloc.get_traced_memory()
            finally:tracemalloc.stop()
        self.assertLess(peak,500000)

if __name__=='__main__':unittest.main()
