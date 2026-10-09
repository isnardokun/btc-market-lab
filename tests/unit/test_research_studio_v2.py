"""Regression tests for source-grounded Research Studio v2 (offline)."""
import datetime as dt
import unittest
from analysis.research_studio import rsi_zone, render_executive_brief, rbn_diagnostic
from validation.content_checks import audit_report_html


class ResearchStudioV2Tests(unittest.TestCase):
    def test_rsi_bands_are_descriptive(self):
        self.assertIn("Neutral", rsi_zone(36))
        self.assertIn("Sobreventa", rsi_zone(29))
        self.assertIn("Sobrecompra", rsi_zone(71))
        self.assertIn("Sin dato", rsi_zone(None))

    def test_executive_brief_has_three_evidence_layers(self):
        html = render_executive_brief(
            btc=81676, btc_change=-1.92, btc_rsi=36, btc_macd_hist=-10,
            btc_sma20=84219, btc_asof="2026-10-08 00:00",
            spy=773.93, spy_change=-.42, gold=4157,
            gold_change=.39, yield10=5.28, yield10_date="2026-10-07",
            rbn_available=True)
        for item in ("id=\"executive-brief\"", "Hechos observados",
                     "Lectura condicional", "Riesgos y datos pendientes",
                     "ResearchBitcoin", "2026-10-08 00:00", "Neutral"):
            self.assertIn(item, html)
        self.assertNotIn("CPI 14-oct", html)
        self.assertNotIn("<script", html)

    def test_rbn_nets_are_not_silently_reconciled(self):
        class Series:
            def __init__(self, slug):
                self.slug = slug
        day = dt.date(2026, 10, 8)
        rows = [
            (Series("realized_price_sth"), day, 74322.),
            (Series("realized_profit_sth"), day, 294211856.),
            (Series("realized_loss_sth"), day, 472771459.),
            (Series("net_realized_profit_loss_sth"), day, -180285307.),
            (Series("sopr_sth"), day, .995),
        ]
        html = rbn_diagnostic(rows, btc_price=81676, cutoff=day)
        self.assertIn("Conciliación pendiente", html)
        self.assertIn("1,725,704", html)
        self.assertIn("SOPR STH", html)
        self.assertNotIn("datos corregidos", html)
        self.assertEqual(rbn_diagnostic(rows, btc_price=81676,
                                       cutoff=dt.date(2026,10,7)), "")

    def test_v2_contract_blocks_absent_executive_summary(self):
        html = '<meta name="market-design-system" content="mercados-research-studio-v2">'
        problems = audit_report_html(html, report_day=dt.date(2026, 10, 9))
        self.assertTrue(any("síntesis ejecutiva" in p for p in problems), problems)

    def test_false_rsi_oversold_at_36_is_blocked(self):
        html = ('<div class="o-name">RSI(14)</div><div class="o-val">36</div>'
                '<div class="o-signal">Sobreventa</div>')
        problems = audit_report_html(html, report_day=dt.date(2026, 10, 9))
        self.assertTrue(any("RSI(14): banda" in p for p in problems), problems)

    def test_legacy_v1_is_not_retroactively_rejected(self):
        html = '<meta name="market-design-system" content="mercados-research-studio-v1">'
        problems = audit_report_html(html, report_day=dt.date(2026, 10, 9))
        self.assertFalse(any("síntesis ejecutiva" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
