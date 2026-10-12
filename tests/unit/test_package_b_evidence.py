"""B snapshot contracts on real A fake-HTTP receipts, never live resources."""
import datetime as dt
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.package_a_ingest_fixture import MARKER, MARKER_VALUE
from scripts.package_a_frozen_legacy_transport import run_mocked_legacy_page
from ingestion.package_a_contracts import SeriesSpec
from analysis.package_b_evidence import read_report_snapshot, snapshot_digest

ASOF = '2026-10-12T12:00:00Z'

class ReportEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='package-a-fixture-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = self.root/'package_a_synthetic.db'
        self.lock = self.root/'locks'
        self.lock.mkdir()
        (self.root/MARKER).write_text(MARKER_VALUE)
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE legacy_keep(value TEXT)')
            db.execute("INSERT INTO legacy_keep VALUES ('SAFE')")
        self.spec = SeriesSpec('fred','CPIAUCSL','USA','monthly','index')
        self.store([
            {'date':'2026-09-01','value':'322','realtime_start':'2026-09-10'},
            {'date':'2026-09-01','value':'324','realtime_start':'2026-10-13'},
        ])

    def store(self, rows, captured='2026-10-10T00:00:00Z'):
        import json
        return run_mocked_legacy_page(self.db,self.lock,self.spec,
            '2026-09-01T00:00:00Z','2026-10-14T00:00:00Z',
            raw_bytes=json.dumps({'observations':rows}).encode(),now=captured)

    def test_latest_available_vintage_only_and_immutable_series(self):
        before=self.db.read_bytes()
        snapshot=read_report_snapshot(self.db,self.lock,asof=ASOF)
        series=snapshot.series[0]
        self.assertEqual(len(series.points),1)
        self.assertEqual(series.points[0].value,322.)
        self.assertEqual(series.points[0].dimension,'2026-09-10')
        self.assertEqual(snapshot.real_history_verified,False)
        self.assertEqual(len(series.points[0].raw_sha256),64)
        self.assertEqual(len(series.points[0].request_sha256),64)
        self.assertEqual(snapshot_digest(snapshot),snapshot.sha256)
        self.assertEqual(before,self.db.read_bytes())
        with self.assertRaises((AttributeError,TypeError)):
            series.points[0].value=999

    def test_later_asof_can_observe_revised_vintage_without_changing_prior_view(self):
        first=read_report_snapshot(self.db,self.lock,asof=ASOF)
        later=read_report_snapshot(self.db,self.lock,asof='2026-10-14T12:00:00Z')
        self.assertEqual(first.series[0].points[0].value,322)
        self.assertEqual(later.series[0].points[0].value,324)
        self.assertNotEqual(first.sha256,later.sha256)

    def test_future_capture_is_not_available_in_historical_snapshot(self):
        self.store([{'date':'2026-09-02','value':'320','realtime_start':'2026-09-10'}],
                   captured='2026-10-14T00:00:00Z')
        snapshot=read_report_snapshot(self.db,self.lock,asof=ASOF)
        self.assertEqual([p.value for p in snapshot.series[0].points],[322])

    def test_future_observations_and_open_daily_bar_are_excluded(self):
        self.spec=SeriesSpec('yahoo','close','BTC-USD','d1','USD')
        import json
        result=run_mocked_legacy_page(self.db,self.lock,self.spec,
            '2026-10-11T00:00:00Z','2026-10-14T00:00:00Z',
            raw_bytes=json.dumps({'chart':{'result':[{'timestamp':[1791676800,1791763200,1791849600],
                'indicators':{'quote':[{'close':[81000,82000,83000]}]}}]}}).encode(),
            now='2026-10-14T00:00:00Z')
        # Captured in the future: the earlier report cannot know any point.
        snapshot=read_report_snapshot(self.db,self.lock,asof=ASOF)
        self.assertFalse(any(s.provider=='yahoo' for s in snapshot.series))
        later=read_report_snapshot(self.db,self.lock,asof='2026-10-14T00:00:00Z',
                                    cutoff='2026-10-12T00:00:00Z')
        rows=next(s for s in later.series if s.provider=='yahoo').points
        self.assertEqual([p.observed_utc for p in rows],['2026-10-11T00:00:00Z'])

    def test_corrupt_raw_digest_blocks_snapshot(self):
        with sqlite3.connect(self.db) as db:
            db.execute('UPDATE package_a_receipts SET raw_sha256=?',('a'*64,))
        with self.assertRaises(ValueError):
            read_report_snapshot(self.db,self.lock,asof=ASOF)

    def test_missing_request_provenance_is_not_a_legacy_report_fallback(self):
        with sqlite3.connect(self.db) as db:
            db.execute('DELETE FROM package_a_request_provenance')
        with self.assertRaises(ValueError):
            read_report_snapshot(self.db,self.lock,asof=ASOF)

    def test_reader_rejects_unmarked_paths_and_later_cutoff(self):
        with self.assertRaises(ValueError):
            read_report_snapshot(self.db,self.lock,asof=ASOF,
                                  cutoff='2026-10-13T00:00:00Z')
        (self.root/MARKER).unlink()
        with self.assertRaises(ValueError):
            read_report_snapshot(self.db,self.lock,asof=ASOF)

    def test_readonly_connection_cannot_write(self):
        real=sqlite3.connect
        connections=[]
        def connect(path,*args,**kw):
            self.assertTrue(kw.get('uri'))
            self.assertTrue(path.endswith('?mode=ro'))
            db=real(path,*args,**kw)
            connections.append(db)
            return db
        with patch('analysis.package_b_evidence.sqlite3.connect',side_effect=connect):
            read_report_snapshot(self.db,self.lock,asof=ASOF)
        self.assertEqual(len(connections),1)

if __name__=='__main__': unittest.main()
