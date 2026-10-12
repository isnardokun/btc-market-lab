"""Independent arithmetic regression vectors for report calculations."""
import unittest
from quant_engine.indicators import compute_rsi

# A fixed close sequence; reference recurrence seeded with first 14 changes.
CLOSES=[44.34,44.09,44.15,43.61,44.33,44.83,45.10,45.42,45.84,
        46.08,45.89,46.03,45.61,46.28,46.28,46.00,46.03,46.41,
        46.22,45.64,46.21,46.25,45.71,46.45,45.78,45.35,44.03,
        44.18,44.22,44.57,43.42,42.66,43.13]

class ReportMathTests(unittest.TestCase):
    def test_wilder_seed_is_first_period_not_trailing_window(self):
        gain=sum(max(CLOSES[i]-CLOSES[i-1],0) for i in range(1,15))/14
        loss=sum(max(CLOSES[i-1]-CLOSES[i],0) for i in range(1,15))/14
        for i in range(15,len(CLOSES)):
            delta=CLOSES[i]-CLOSES[i-1]
            gain=(13*gain+max(delta,0))/14
            loss=(13*loss+max(-delta,0))/14
        expected=100-100/(1+gain/loss)
        self.assertAlmostEqual(compute_rsi(CLOSES,14),expected,places=10)

    def test_wilder_known_initial_value_and_next_close(self):
        self.assertAlmostEqual(compute_rsi(CLOSES[:15],14),70.4641350211,places=8)
        self.assertAlmostEqual(compute_rsi(CLOSES[:16],14),66.2496185536,places=8)


from dataclasses import replace
from analysis.package_b_evidence import (EvidencePoint,EvidenceSeries,ReportSnapshot,
                                       snapshot_digest)
from analysis.package_b_analytics import analyze_snapshot

def snapshot_fixture(*, missing=None, gap=False, stale=False, bad_unit=False):
    import datetime as dt
    series=[]
    for ticker,base in [('BTC-USD',80000),('SPY',600),('GLD',400)]:
        for metric in ('open','high','low','close','volume'):
            if (ticker,metric)==missing: continue
            points=[]
            for i in range(40):
                day=(dt.date(2026,9,2)+dt.timedelta(days=i)).isoformat()+'T00:00:00Z'
                if stale: day=day.replace('2026-09','2025-09').replace('2026-10','2025-10')
                if gap and ticker=='SPY' and metric=='high' and i==4: continue
                value=base+i*2+{'open':0,'high':10,'low':-10,'close':1,'volume':1000}[metric]
                points.append(EvidencePoint(day,'',float(value),'a'*64,'fixture','b'*64,'c'*64,
                                             '2026-10-12T00:00:00Z'))
            unit='native_volume' if metric=='volume' else 'USD'
            if bad_unit and ticker=='SPY': unit='BTC'
            series.append(EvidenceSeries('yahoo',metric,ticker,'d1',unit,'native',tuple(points),False))
    snapshot=ReportSnapshot('2026-10-12T12:00:00Z','2026-10-12T00:00:00Z',tuple(series))
    return replace(snapshot,sha256=snapshot_digest(snapshot))

class ReportAssetContracts(unittest.TestCase):
    def test_three_assets_arithmetic_and_symbol_identity_are_explicit(self):
        model=analyze_snapshot(snapshot_fixture())
        self.assertFalse(model.critical_errors)
        self.assertEqual([a.symbol for a in model.assets],['BTC-USD','SPY','GLD'])
        btc=model.assets[0]
        self.assertEqual(btc.close,80079)
        self.assertAlmostEqual(btc.change_pct,(80079/80077-1)*100)
        self.assertAlmostEqual(btc.sma20,sum(80000+i*2+1 for i in range(20,40))/20)
        self.assertIsNone(btc.sma200)
        self.assertIn('GLD',model.assets[2].instrument)
        self.assertNotIn('GC=F',model.assets[2].instrument)
        self.assertEqual(btc.input_sha256,model.snapshot.sha256)
        self.assertIn('Wilder',btc.rsi_method)

    def test_ohlcv_missing_and_discontinuous_alignment_block_asset(self):
        for snapshot in (snapshot_fixture(missing=('BTC-USD','close')),
                         snapshot_fixture(gap=True)):
            model=analyze_snapshot(snapshot)
            self.assertTrue(model.critical_errors)

    def test_stale_and_incompatible_units_block_critical_report(self):
        for snapshot in (snapshot_fixture(stale=True),snapshot_fixture(bad_unit=True)):
            self.assertTrue(analyze_snapshot(snapshot).critical_errors)

    def test_snapshot_hash_mutation_is_rejected(self):
        snapshot=snapshot_fixture()
        corrupted=replace(snapshot,sha256='d'*64)
        with self.assertRaises(ValueError): analyze_snapshot(corrupted)


class EtfScenarioPrecisionTests(unittest.TestCase):
    def test_gld_conditional_levels_preserve_cents_and_direction(self):
        from quant_engine.indicators import compute_scenarios
        bull,base,bear=compute_scenarios(400.1,[],[],[],[399.9,399.8],
                                        [400.25,400.40],50,1,0,'GLD')
        self.assertIn('$400.25',bull)
        self.assertIn('$400.40',bull)
        self.assertIn('$399.90',bear)
        self.assertIn('$399.80',bear)
        self.assertIn('$400.10',base)

if __name__=='__main__': unittest.main()
