"""C composes the real B provider fixtures with A's single parent lease."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import package_b_report_fixture as source
from scripts.package_a_ingest_fixture import MARKER,MARKER_VALUE,inspect_fixture
from scripts.package_a_parent_lock import hold_offline_parent_lease


class SourceJobContracts(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(source,'example_jobs'),'Pure native request job planner is missing')

    def fixture(self):
        temp=tempfile.TemporaryDirectory(prefix='package-a-fixture-')
        self.addCleanup(temp.cleanup)
        root=Path(temp.name);locks=root/'locks';locks.mkdir(mode=0o700)
        (root/MARKER).write_text(MARKER_VALUE)
        db=root/'package_a_synthetic.db'
        with sqlite3.connect(db) as conn:conn.execute('CREATE TABLE retained(id INTEGER)')
        return db,locks

    def test_native_plan_has_45_requests_and_57_streams_without_io(self):
        with patch.object(sqlite3,'connect',side_effect=AssertionError('No planning SQL')):
            jobs=source.example_jobs()
        self.assertEqual(len(jobs),45)
        self.assertEqual(sum(len(j.specs) for j in jobs),57)
        self.assertEqual(len({j.key for j in jobs}),45)
        self.assertEqual({j.specs[0].provider for j in jobs},
            {'yahoo','fred','researchbitcoin','bitview','binance','bybit'})
        self.assertEqual(jobs,source.example_jobs())

    def test_boolean_without_parent_lease_cannot_fetch_or_open_sqlite(self):
        db,locks=self.fixture();job=source.example_jobs()[0]
        with patch('scripts.package_a_yahoo_fanout._legacy_fake_fetch',side_effect=AssertionError('HTTP called')):
            with patch.object(sqlite3,'connect',side_effect=AssertionError('SQL opened')):
                with self.assertRaises(ValueError):
                    source.apply_example_job(db,locks,job,now=source.ASOF,_parent_lock_held=True)

    def test_yahoo_fanout_and_legacy_page_reuse_same_parent_flock(self):
        db,locks=self.fixture();jobs=source.example_jobs()
        with hold_offline_parent_lease(locks):
            yahoo=source.apply_example_job(db,locks,jobs[0],now=source.ASOF,_parent_lock_held=True)
            legacy=source.apply_example_job(db,locks,jobs[3],now=source.ASOF,_parent_lock_held=True)
        self.assertEqual(yahoo['status'],'ok')
        self.assertEqual(yahoo['frozen_http_requests'],1)
        self.assertEqual(yahoo['receipts_persisted'],5)
        self.assertEqual(legacy['status'],'ok')
        self.assertEqual(legacy['frozen_http_requests'],1)
        self.assertEqual(inspect_fixture(db,locks)['state'],'LOCAL_FIXTURE_INTEGRITY_PASS')

    def test_retryable_status_preserves_failed_native_receipts_and_no_cursor(self):
        db,locks=self.fixture();job=source.example_jobs()[0]
        result=source.apply_example_job(db,locks,job,now=source.ASOF,status_code=429)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['frozen_http_requests'],1)
        with sqlite3.connect(db) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM package_a_observations').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT count(*) FROM package_a_cursors').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT count(*) FROM package_a_receipts WHERE http_status=429').fetchone()[0],5)


if __name__=='__main__':unittest.main()
