"""Rendering and local report gate use a single immutable B input model."""
from dataclasses import replace
import unittest
from html import unescape

from tests.unit.test_package_b_analytics import snapshot_fixture
from analysis.package_b_analytics import analyze_snapshot
from rendering.package_b_report import render_report, audit_candidate_report
from rendering.portable_report import make_portable

class ReportRenderingTests(unittest.TestCase):
    def setUp(self):
        self.model=analyze_snapshot(snapshot_fixture())

    def test_full_report_is_portable_deterministic_and_carries_source_identity(self):
        html=render_report(self.model)
        self.assertEqual(html,render_report(self.model))
        self.assertEqual(html,make_portable(html))
        for text in ('SINTÉTICO','Bitcoin (BTC)','SPDR S&P 500 ETF (SPY)',
                     'Gold Shares (GLD)','Hechos observados','Lectura condicional',
                     'Fuentes y trazabilidad','NO_DATA',self.model.snapshot.sha256,
                     'data-b-report-version','USD'):
            self.assertIn(text,unescape(html))
        self.assertEqual(html.count('<svg '),3)
        self.assertNotIn('GC=F',html)
        gate=audit_candidate_report(self.model,html)
        self.assertEqual(gate['state'],'OFFLINE_REPORT_LOCAL_RECONCILIATION_PASS')
        self.assertFalse(gate['production_publication_authorized'])

    def test_price_and_chart_or_asof_tampering_blocks_gate(self):
        html=render_report(self.model)
        for changed in (html.replace('$80,079.00','$99,999.00'),
                        html.replace('viewBox="0 0 860 260"','viewBox="0 0 860 800"'),
                        html.replace('2026-10-12','2026-10-13')):
            self.assertNotEqual(changed,html)
            self.assertEqual(audit_candidate_report(self.model,changed)['state'],
                             'BLOCKED_OFFLINE_REPORT_EVIDENCE')

    def test_legacy_financial_checks_support_section_attributes_and_escaped_names(self):
        from tests.unit.test_financial_integrity import fixture
        from validation.content_checks import audit_report_html
        import datetime as dt
        document=fixture(False).replace('<section>','<section class="page">').replace('S&P','S&amp;P')
        self.assertEqual(audit_report_html(document,report_day=dt.date(2026,10,8)),[])

    def test_injected_remote_resource_blocks_portability(self):
        html=render_report(self.model).replace('</head>','<script src="https://bad.invalid/a.js"></script></head>')
        self.assertIn('REMOTE_OR_UNSAFE_DEPENDENCY',audit_candidate_report(self.model,html)['failures'])

    def test_missing_critical_price_never_becomes_a_rendered_zero_or_pass(self):
        model=analyze_snapshot(snapshot_fixture(missing=('BTC-USD','close')))
        html=render_report(model)
        gate=audit_candidate_report(model,html)
        self.assertEqual(gate['state'],'BLOCKED_OFFLINE_REPORT_EVIDENCE')
        self.assertIn('CRITICAL_INPUT_DATA',gate['failures'])
        self.assertIn('BLOCKED',html)
        self.assertNotIn('$0.00',html)

    def test_insufficient_history_preserves_missing_sma200(self):
        html=render_report(self.model)
        self.assertIn('SMA 200',html)
        self.assertIn('NO_DATA',html)
        self.assertNotIn('NaN',html)
        self.assertNotIn('None',html)


class ReportEndToEndTests(unittest.TestCase):
    def test_fake_provider_pages_to_sqlite_to_report_are_reconciled(self):
        import tempfile,sqlite3
        from pathlib import Path
        from scripts.package_b_report_fixture import create_example_fixture
        from analysis.package_b_evidence import read_report_snapshot
        from scripts.package_a_ingest_fixture import MARKER,MARKER_VALUE
        with tempfile.TemporaryDirectory(prefix='package-a-fixture-') as directory:
            root=Path(directory);lock=root/'locks';lock.mkdir()
            (root/MARKER).write_text(MARKER_VALUE)
            db=root/'package_a_synthetic.db'
            with sqlite3.connect(db) as conn:conn.execute('CREATE TABLE legacy_keep(value TEXT)')
            create_example_fixture(db,lock)
            snapshot=read_report_snapshot(db,lock,asof='2026-10-12T12:00:00Z')
            self.assertEqual(set(s.provider for s in snapshot.series),
                             {'yahoo','fred','researchbitcoin','bitview','binance','bybit'})
            self.assertEqual(len(snapshot.series),57)
            before=db.read_bytes()
            model=analyze_snapshot(snapshot)
            report=render_report(model)
            gate=audit_candidate_report(model,report)
            self.assertFalse(model.critical_errors,model.critical_errors)
            self.assertEqual(gate['state'],'OFFLINE_REPORT_LOCAL_RECONCILIATION_PASS',gate)
            self.assertEqual(db.read_bytes(),before)
            self.assertIsNotNone(model.assets[0].sma200)
            self.assertIsNotNone(model.assets[1].sma200)
            self.assertIn('fracción RAW 0..1',report)
            self.assertIn('researchbitcoin',report)


class ReportSourceSafetyTests(unittest.TestCase):
    def test_old_optional_macro_and_derivatives_are_marked_stale(self):
        from analysis.package_b_evidence import EvidencePoint,EvidenceSeries,snapshot_digest
        snapshot=snapshot_fixture()
        old=(EvidenceSeries('fred','DGS10','USA','d1','percent','native',(
            EvidencePoint('2020-10-09T00:00:00Z','2020-10-10',4.1,'a'*64,
                          'old','b'*64,'c'*64,'2026-10-12T00:00:00Z'),),False),
             EvidenceSeries('bybit','open_interest','BTCUSDT','5m','BTC','native',(
            EvidencePoint('2020-10-09T23:55:00Z','',40000,'a'*64,
                          'old','b'*64,'c'*64,'2026-10-12T00:00:00Z'),),False))
        snapshot=replace(snapshot,series=snapshot.series+old,sha256='')
        snapshot=replace(snapshot,sha256=snapshot_digest(snapshot))
        model=analyze_snapshot(snapshot)
        document=render_report(model)
        self.assertIn('STALE',document)
        self.assertIn('2020-10-09 23:55 UTC',document)
        gate=audit_candidate_report(model,document)
        self.assertIn('STALE_SOURCE_SERIES',gate['warnings'])
        self.assertFalse(gate['production_publication_authorized'])

    def test_example_cli_rejects_existing_output_symlinks_before_fixture_work(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from contextlib import redirect_stderr
        from io import StringIO
        from scripts.package_b_report_fixture import main
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);out=root/'output';out.mkdir()
            outside=root/'keep.txt';outside.write_text('KEEP')
            (out/'package_b_synthetic_report.html').symlink_to(outside)
            with patch('scripts.package_b_report_fixture.create_example_fixture',
                       side_effect=AssertionError('Work must not begin')):
                with redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit) as result:
                        main(['--output-dir',str(out)])
            self.assertEqual(result.exception.code,2)
            self.assertEqual(outside.read_text(),'KEEP')


class FundingDisplayTests(unittest.TestCase):
    def test_small_native_funding_is_not_rendered_as_zero(self):
        from analysis.package_b_evidence import EvidencePoint,EvidenceSeries,snapshot_digest
        snapshot=snapshot_fixture()
        funding=EvidenceSeries('binance','funding_settled','BTCUSDT','event',
            'fraction','native',(EvidencePoint('2026-10-11T16:00:00Z','',1e-8,
            'a'*64,'tiny','b'*64,'c'*64,'2026-10-12T00:00:00Z'),),False)
        snapshot=replace(snapshot,series=snapshot.series+(funding,),sha256='')
        snapshot=replace(snapshot,sha256=snapshot_digest(snapshot))
        document=render_report(analyze_snapshot(snapshot))
        self.assertIn('1e-08',document)


class PublicationGateFixtureTests(unittest.TestCase):
    def test_existing_gate_evaluates_fixture_and_blocks_html_tampering(self):
        import tempfile,sqlite3
        from pathlib import Path
        from scripts.package_b_report_fixture import create_example_fixture
        from scripts.package_a_ingest_fixture import MARKER,MARKER_VALUE
        from analysis.package_b_evidence import read_report_snapshot
        from validation.publication_gate import evaluate_package_b_fixture,WEIGHTS,PASS_SCORE
        with tempfile.TemporaryDirectory(prefix='package-a-fixture-') as directory:
            root=Path(directory);locks=root/'locks';locks.mkdir()
            (root/MARKER).write_text(MARKER_VALUE)
            path=root/'package_a_synthetic.db'
            with sqlite3.connect(path) as db:db.execute('CREATE TABLE keep(id INTEGER)')
            create_example_fixture(path,locks)
            asof='2026-10-12T00:05:00Z'
            model=analyze_snapshot(read_report_snapshot(path,locks,asof=asof))
            document=render_report(model)
            before=path.read_bytes()
            gate=evaluate_package_b_fixture(document,db_path=path,lock_dir=locks,asof=asof)
            self.assertTrue(gate['pass'],gate)
            self.assertEqual(set(gate['scores']),set(WEIGHTS))
            self.assertGreaterEqual(gate['score_total'],PASS_SCORE)
            self.assertEqual(gate['state'],'B_OFFLINE_REPORT_GATE_PASS')
            self.assertFalse(gate['production_publication_authorized'])
            bad=evaluate_package_b_fixture(document.replace('stroke-width="2.6"','stroke-width="9"'),
                db_path=path,lock_dir=locks,asof=asof)
            self.assertFalse(bad['pass'])
            self.assertGreater(bad['critical_count'],0)
            self.assertEqual(path.read_bytes(),before)

    def test_unmarked_database_is_blocked_without_touching_legacy_defaults(self):
        import tempfile,sqlite3
        from pathlib import Path
        from unittest.mock import patch
        from validation import publication_gate
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'unmarked.db'
            with sqlite3.connect(path) as db:db.execute('CREATE TABLE keep(id INTEGER)')
            before=path.read_bytes()
            with patch.object(publication_gate,'check_exactitud',side_effect=AssertionError('legacy IO')):
                gate=publication_gate.evaluate_package_b_fixture('<html></html>',
                    db_path=path,lock_dir=root,asof='2026-10-12T00:05:00Z')
            self.assertFalse(gate['pass'])
            self.assertEqual(gate['state'],'BLOCKED_B_OFFLINE_REPORT_GATE')
            self.assertEqual(path.read_bytes(),before)


class VisualGateInputTests(unittest.TestCase):
    def test_visual_runner_rejects_nonboolean_pass_or_missing_nonfinite_score_before_browser(self):
        import hashlib,json,shutil,subprocess,tempfile
        from pathlib import Path
        node=shutil.which('node')
        if node is None:self.skipTest('Node required for the separate visual CI job')
        script=Path(__file__).resolve().parents[2]/'scripts/package_b_visual_qa.cjs'
        document=render_report(analyze_snapshot(snapshot_fixture())).encode()
        sha=hashlib.sha256(document).hexdigest()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);html=root/'report.html';html.write_bytes(document)
            for invalid in ({'pass':'false','score_total':100},
                            {'pass':True}, {'pass':True,'score_total':'not-a-number'},
                            {'pass':True,'score_total':'100'}):
                gate={'html_sha256':sha,'production_publication_authorized':False,
                      'publication_gate':{'report_sha256':sha,'critical_count':0,**invalid}}
                gate_path=root/'gate.json';gate_path.write_text(json.dumps(gate))
                result=subprocess.run([node,str(script),'--html',str(html),'--gate',str(gate_path),
                    '--output-dir',str(root/'new-output'),'--playwright-module',str(root/'no-browser')],
                    text=True,capture_output=True)
                self.assertEqual(result.returncode,1)
                self.assertIn('Unapproved or mismatched synthetic HTML/gate',result.stderr)
                self.assertFalse((root/'new-output').exists())

if __name__=='__main__': unittest.main()
