#!/usr/bin/env python3
"""Deterministic regressions taken from Hermes diagnostics PR #4."""
import datetime
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from quant_engine.indicators import price_relative_levels, compute_scenarios
from validation.content_checks import audit_report_html
from analysis import daily_report


def section(asset, spot, bull_trigger, bull_target, bear_trigger, bear_target, extra=""):
    heading = {"BTC": "Bitcoin (BTC) — Analisis de Precio",
               "SPY": "S&P 500 — SPDR S&P 500 ETF (SPY)",
               "GOLD": "Oro Futuro (GC=F) — Analisis de Precio"}[asset]
    return (f'<section><div class="section-title">{heading}</div>'
            f'<div class="price-row"><span class="big-price">${spot:,.2f}</span></div>'
            f'<div class="bias-box"><div class="scenario s-bull">'
            f'Escenario alcista. Activación: supera ${bull_trigger:,.2f}. '
            f'Objetivo: ${bull_target:,.2f}.'
            f'</div><div class="scenario s-bear">Escenario bajista. '
            f'Activación: pierde ${bear_trigger:,.2f}. '
            f'Objetivo: ${bear_target:,.2f}.'
            f'</div></div>{extra}</section>')


def fixture(bad=False):
    ticker = "---" if bad else "$81,900"
    btc_extra = (
        '<div class="stats-bar"><div class="stat-item"><div class="slbl">Max 52s</div>'
        '<div class="sval">$123355</div><div class="ssub">05 Oct 2025</div></div>'
        '<div class="stat-item"><div class="slbl">Min 52s</div>'
        '<div class="sval">$58559</div><div class="ssub">29 Jun 2026</div></div></div>'
        '<p>BTC cayo -34.2% desde el ATH de $124753 (05 Oct 2025) '
        'hasta el minimo de 52 semanas en $58559.</p>'
        '<p>El ratio actual 1.52xx; {usd(spy_price)}; 1.5%% annual</p>'
    ) if bad else ''
    return (
        f'<div class="ticker-item"><div class="tk-pair">BTC/USD</div>'
        f'<div class="tk-price">{ticker}</div></div>'
        + section("BTC", 81900, 82139, 74861 if bad else 85000,
                  75613, 65955, btc_extra)
        + section("SPY", 773.93, 777.88, 759.57 if bad else 790.00,
                  729.46, 725.43)
        + section("GOLD", 4202, 5318, 5312 if bad else 5410,
                  4000, 3900)
    )


class FinancialIntegrityTests(unittest.TestCase):
    def test_historical_pr4_rejects_all_p0_contradictions(self):
        reasons = audit_report_html(fixture(True), report_day=datetime.date(2026, 10, 8))
        for part in ("Precio principal BTC", "escenario alcista incoherente",
                     "fecha de Max 52s", "ATH", "plantilla", "duplicadas"):
            with self.subTest(part=part):
                self.assertTrue(any(part.lower() in reason.lower() for reason in reasons), reasons)
        self.assertTrue(any("SPY" in reason for reason in reasons))
        self.assertTrue(any("GOLD" in reason for reason in reasons))

    def test_consistent_report_passes(self):
        reasons = audit_report_html(fixture(False), report_day=datetime.date(2026, 10, 8))
        self.assertEqual(reasons, [])

    def test_correct_price_relative_pivots(self):
        below, above = price_relative_levels(81900, [58559, 65955, 75613, 90000],
                                             [66505, 74861, 82139, 85000])
        self.assertEqual(below, [75613, 65955, 58559])
        self.assertEqual(above, [82139, 85000])
        with self.assertRaises(ValueError):
            price_relative_levels(None, [], [])

    def test_scenario_targets_directional_for_all_assets(self):
        for asset, price, supports, resistances in [
            ("BTC", 81900, [58559, 65955, 75613], [66505, 74861, 82139, 85000]),
            ("SPY", 773.93, [652.53, 725.43, 729.46], [695.49, 759.57, 777.88, 794.50]),
            ("GOLD", 4202, [3992, 4376], [4880, 5312, 5318]),
        ]:
            with self.subTest(asset=asset):
                bull, base, bear = compute_scenarios(
                    price, [], [], [], supports, resistances,
                    48, 100, -12, asset,
                )
                self.assertIn("Objetivo condicional:", bull)
                self.assertIn("Objetivo condicional:", bear)
                self.assertIn("spot", base)

    def test_missing_upper_target_is_explicit_not_fabricated(self):
        bull, base, bear = compute_scenarios(
            81900, [], [], [], [75613, 65955], [82139], 48, None, -12, "BTC",
        )
        self.assertIn("Objetivo no estimable", bull)
        self.assertNotIn("Objetivo condicional:", bull)
        self.assertIn("Objetivo condicional:", bear)

    def test_ath_and_52w_dates_came_from_rows_not_labels(self):
        with tempfile.TemporaryDirectory() as scratch:
            dbpath = Path(scratch) / "test.db"
            con = sqlite3.connect(dbpath)
            con.execute("CREATE TABLE price_btc(ts INTEGER, price REAL)")
            def ts(day):
                return int(datetime.datetime.combine(day, datetime.time(12),
                    tzinfo=datetime.timezone.utc).timestamp())
            for date, val in [
                (datetime.date(2025, 10, 5), 124753),
                (datetime.date(2025, 10, 8), 100000),
                (datetime.date(2026, 6, 29), 58559),
                (datetime.date(2026, 10, 7), 81900),
            ]:
                con.execute("INSERT INTO price_btc VALUES (?,?)", (ts(date), val))
            con.commit()
            con.close()
            with patch.object(daily_report, "DB_PATH", str(dbpath)), \
                 patch.object(daily_report, "ts_52w", ts(datetime.date(2025, 10, 7))), \
                 patch.object(daily_report, "ts_today", ts(datetime.date(2026, 10, 8))):
                data = daily_report.get_btc_price_data(current_price=81936)
            self.assertEqual(data["high52"], 100000)
            self.assertEqual(data["high52_date"], "08 Oct 2025")
            self.assertEqual(data["low52_date"], "29 Jun 2026")
            self.assertEqual(data["from_ath"], round((81936 / 124753 - 1)*100, 1))
            self.assertEqual(data["drawdown_to_low52"], round((58559 / 124753 - 1)*100, 1))


if __name__ == "__main__":
    unittest.main()
