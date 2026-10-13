from contextlib import closing
from dataclasses import replace
import importlib.util
import sqlite3
import unittest
from unittest.mock import patch

from ingestion.package_a_contracts import SeriesSpec
from tests.unit.installed_fixture_helpers import new_fixture, ASOF, CAPTURE
from analysis.package_b_analytics import analyze_snapshot
from rendering.package_b_report import render_report, audit_candidate_report


class InstalledEvidenceTests(unittest.TestCase):
    def setUp(self): self.root,self.path,self.locks=new_fixture(self)

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('analysis.installed_evidence'),'Installed evidence adapter missing')
        from analysis import installed_evidence
        return installed_evidence

    def selection(self,provider,metric,symbol,freq,unit,table,lookup):
        return self.module().LegacySelection(SeriesSpec(provider,metric,symbol,freq,unit),table,lookup)

    def read(self,selections,**kwargs):
        return self.module().read_installed_snapshot(self.path,self.locks,asof=kwargs.get('asof',ASOF),
            snapshot_captured_utc=CAPTURE,selections=tuple(selections))

    def close(self): return self.selection('yahoo','close','BTC-USD','d1','USD','price_btc','BTC-USD')

    def test_close_only_has_no_fabricated_ohlcv_or_receipts(self):
        snapshot=self.read([self.close()]); point=snapshot.series[0].points[-1]
        self.assertEqual(snapshot.mode,'OFFLINE_INSTALLED_SCHEMA')
        self.assertEqual(point.value,80500)
        self.assertIsNone(point.receipt_id)
        self.assertIsNone(point.raw_sha256)
        self.assertIsNone(point.request_sha256)
        self.assertEqual(point.provenance,'LEGACY_NO_REQUEST')
        self.assertEqual(point.availability,'SNAPSHOT_UPPER_BOUND')
        model=analyze_snapshot(snapshot)
        self.assertEqual(model.assets[0].close,80500)
        self.assertIsNone(model.assets[0].atr14)
        self.assertFalse(model.assets[0].scenarios)
        document=render_report(model)
        self.assertIn('LEGACY_NO_REQUEST',document)
        self.assertIn('NO_DATA',document)
        self.assertIn('SINTÉTICO',document)
        self.assertIn('cierres',document)
        self.assertIn('CRITICAL_INPUT_DATA',audit_candidate_report(model,document)['failures'])

    def test_explicit_bitview_fred_rbn_and_market_selection(self):
        selections=[self.selection('bitview','mvrv','BTC','d1','ratio','daily','mvrv'),
            self.selection('fred','DGS10','USA','d1','percent','archive_fred_vintages','DGS10'),
            self.selection('researchbitcoin','sopr_sth','BTC','d1','ratio','onchain_external_observations','sopr_sth'),
            self.selection('binance','funding_settled','BTCUSDT','event','fraction','market_derivatives','BTCUSDT')]
        snapshot=self.read(selections)
        self.assertEqual(len(snapshot.series),4)
        fred=next(s for s in snapshot.series if s.provider=='fred')
        self.assertEqual(fred.points[0].value,4.2)
        self.assertEqual(fred.points[0].dimension,'2026-10-11')
        derivative=next(s for s in snapshot.series if s.provider=='binance').points[0]
        self.assertEqual(derivative.provenance,'LEGACY_RAW_WITHOUT_ACQUISITION')
        self.assertIsNone(derivative.receipt_id)
        self.assertIsNotNone(derivative.raw_sha256)

    def test_missing_native_raw_blocks_read(self):
        selection=self.selection('binance','funding_settled','BTCUSDT','event','fraction','market_derivatives','BTCUSDT')
        with closing(sqlite3.connect(self.path)) as db, db: db.execute("UPDATE market_raw_payloads SET body=X'00'")
        with self.assertRaisesRegex(ValueError,'RAW'): self.read([selection])

    def test_future_fetch_and_snapshot_upper_bound_no_lookahead(self):
        snapshot=self.read([self.close()],asof='2026-10-11T12:00:00Z')
        self.assertFalse(snapshot.series)
        selection=self.selection('researchbitcoin','sopr_sth','BTC','d1','ratio','onchain_external_observations','sopr_sth')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE onchain_external_observations SET fetched_at_utc='2026-10-13T00:00:00Z'")
        self.assertFalse(self.read([selection]).series)

    def test_mixed_units_or_implicit_mapping_rejected(self):
        selection=self.selection('researchbitcoin','sopr_sth','BTC','d1','ratio','onchain_external_observations','sopr_sth')
        with closing(sqlite3.connect(self.path)) as db, db: db.execute("UPDATE onchain_external_observations SET unit='USD'")
        with self.assertRaisesRegex(ValueError,'UNIT'): self.read([selection])
        with self.assertRaises(ValueError): self.selection('yahoo','high','BTC-USD','d1','USD','price_btc','BTC-USD')

    def test_query_only_read_preserves_every_legacy_table(self):
        before=self.path.read_bytes()
        snapshot=self.read([self.close()])
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(len(snapshot.series),1)
        self.assertFalse(snapshot.production_authorized)
        self.assertFalse(snapshot.real_history_verified)

    def test_unknown_fred_vintage_not_claimed_known(self):
        selection=self.selection('fred','DGS10','USA','d1','percent','macro_fred','DGS10')
        document=render_report(analyze_snapshot(self.read([selection])))
        self.assertIn('VINTAGE_UNKNOWN',document)

    def test_unmarked_database_and_replaced_inode_are_rejected_before_connect(self):
        selection=self.close()
        original=self.root/'.package-a-offline-fixture'
        from scripts.package_a_ingest_fixture import MARKER
        original=self.root/MARKER; original.unlink()
        with patch('analysis.installed_evidence.sqlite3.connect',side_effect=AssertionError('SQL must not start')):
            with self.assertRaises(ValueError): self.read([selection])

    def test_legacy_latest_price_and_ohlcv_contract(self):
        from storage import repositories
        with patch.object(repositories,'_get_db',side_effect=lambda:sqlite3.connect(self.path)):
            stamp,value=repositories.get_btc_latest_price()
            self.assertEqual(value,80500)
            with self.assertRaisesRegex(ValueError,'OHLCV_UNAVAILABLE'): repositories.get_btc_ohlc()

    def test_nonzero_offset_future_capture_is_not_replaced_by_snapshot_bound(self):
        selection=self.selection('researchbitcoin','sopr_sth','BTC','d1','ratio','onchain_external_observations','sopr_sth')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE onchain_external_observations SET fetched_at_utc='2026-10-13T01:00:00+01:00'")
        self.assertFalse(self.read([selection]).series)

    def test_future_naive_and_date_capture_fail_closed(self):
        selection=self.selection('researchbitcoin','sopr_sth','BTC','d1','ratio','onchain_external_observations','sopr_sth')
        for capture in ('2026-10-13T00:00:00','2026-10-13'):
            with self.subTest(capture=capture):
                with closing(sqlite3.connect(self.path)) as db, db: db.execute('UPDATE onchain_external_observations SET fetched_at_utc=?',(capture,))
                try: snapshot=self.read([selection])
                except ValueError: continue
                self.assertFalse(snapshot.series,'Ambiguous future capture was credited')

    def test_actual_sqlite_descriptor_cannot_read_swapped_foreign_inode(self):
        selection=self.close(); other,foreign,_=new_fixture(self)
        with closing(sqlite3.connect(foreign)) as db, db: db.execute('UPDATE price_btc SET price=999999')
        real=sqlite3.connect
        def racing_connect(*args,**kwargs):
            if args[0]==':memory:': return real(*args,**kwargs)
            held=self.root/'held.db';self.path.rename(held);foreign.rename(self.path)
            try: return real(*args,**kwargs)
            finally: self.path.rename(foreign);held.rename(self.path)
        with patch('analysis.installed_evidence.sqlite3.connect',side_effect=racing_connect):
            try: snapshot=self.read([selection])
            except ValueError: return
        self.assertEqual([p.value for p in snapshot.series[0].points],[80000,81000,80500])

    def test_returned_foreign_connection_is_rejected_even_if_path_restored(self):
        selection=self.close(); other,foreign,_=new_fixture(self)
        real=sqlite3.connect
        with patch('analysis.installed_evidence.sqlite3.connect',side_effect=lambda *a,**kw:real(foreign.resolve().as_uri()+'?mode=ro',uri=True)):
            with self.assertRaisesRegex(ValueError,'SQLITE.*IDENTITY'): self.read([selection])


if __name__=='__main__': unittest.main()
