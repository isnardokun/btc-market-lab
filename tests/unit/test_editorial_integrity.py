#!/usr/bin/env python3
"""Hermes PR #6 editorial/security regressions, independent of market APIs."""
import datetime
from pathlib import Path
import unittest
from analysis import daily_report
from ingestion import news_pipeline
from validation.content_checks import audit_report_html, extract_cpi_yoy_from_macro_strip


class EditorialIntegrityTests(unittest.TestCase):
    def test_double_dollar_is_critical_even_if_other_values_are_valid(self):
        src = '<div class="ticker-bar">BTC/USD $82,165</div><p>BTC opera en $$82165.</p>'
        problems = audit_report_html(src, report_day=datetime.date(2026, 10, 8))
        self.assertTrue(any("monetario duplicado" in msg for msg in problems), problems)

    def test_news_future_date_cannot_pass_publication_gate(self):
        source = (
            '<div class="hdr-meta">Generado 2026-10-09 03:34 UTC</div>'
            '<div class="news-date">2026-10-10</div>'
        )
        problems = audit_report_html(source, report_day=datetime.date(2026, 10, 8))
        self.assertTrue(any("posterior" in msg for msg in problems), problems)

    def test_news_same_utc_day_as_generation_is_allowed(self):
        source = '<p>Generado 2026-10-09 03:34 UTC</p><div class="news-date">2026-10-09</div>'
        problems = audit_report_html(source, report_day=datetime.date(2026, 10, 8))
        self.assertFalse(any("posterior" in msg for msg in problems), problems)

    def test_original_news_not_pseudo_translated_or_called_forecast(self):
        title = "Gold Should Benefit from Higher Yields"
        original = "Treasury support and inflation have not supported gold this week; research only. " * 2
        items = news_pipeline.normalize_for_report([{
            "title": title, "highlight": original,
            "date": "2026-10-08", "url": "https://example.com/article",
        }])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], title)
        self.assertEqual(items[0]["summary"], original[:280])
        self.assertEqual(items[0]["bias"], "Sin evaluar")
        self.assertNotIn("rendimiento", items[0]["summary"])

    def test_source_html_and_url_are_escaped(self):
        news = {
            "date": "2026-10-08", "bias": "Sin evaluar", "impact": "Tasas",
            "title": '<script>alert("x")</script>',
            "summary": "<img src=x onerror=alert(1)>",
            "url": 'https://example.org/?a=" onmouseover="evil()',
        }
        result = daily_report.news_span(news)
        self.assertNotIn("<script>", result)
        self.assertNotIn("<img", result)
        self.assertIn("&lt;script&gt;", result)
        self.assertNotIn('onmouseover="evil()', result)
        self.assertIn('rel="noopener noreferrer"', result)

    def test_validator_rejects_future_iso_news_time(self):
        content = "2026-10-08 market analysis from valid source"
        item = {"title": "Valid coverage of commodity market",
                "highlight": content * 3, "url": "https://example.org/a",
                "published": "2999-01-01T09:00:00Z"}
        ok, message = news_pipeline.validate_entry(item, 1)
        self.assertFalse(ok)
        self.assertIn("futura", message)

    def test_asof_gate_accepts_observation_fresh_with_utc_timestamps(self):
        html = (
            '<p>Generado 2026-10-09 12:07 UTC</p>'
            '<div class="source-freshness" '
            'data-btc-asof-utc="2026-10-08 19:00" '
            'data-onchain-oldest-utc="2026-10-07 19:00"></div>'
        )
        errors = audit_report_html(
            html, report_day=datetime.date(2026, 10, 9), strict_asof=True
        )
        self.assertFalse(any("observaci" in x or "timestamp" in x for x in errors), errors)

    def test_asof_gate_rejects_old_or_missing_observation_metadata(self):
        html = (
            '<p>Generado 2026-10-09 12:07 UTC</p>'
            '<div class="source-freshness" '
            'data-btc-asof-utc="2026-09-29 19:00" '
            'data-onchain-oldest-utc=""></div>'
        )
        errors = audit_report_html(html, strict_asof=True)
        self.assertTrue(any("desactualizada" in x for x in errors), errors)
        self.assertTrue(any("On-chain" in x and "no disponible" in x for x in errors), errors)

    def test_asof_gate_rejects_future_observation(self):
        html = (
            '<p>Generado 2026-10-09 12:07 UTC</p>'
            '<div data-btc-asof-utc="2026-10-09 21:00" '
            'data-onchain-oldest-utc="2026-10-07 19:00"></div>'
        )
        errors = audit_report_html(html, strict_asof=True)
        self.assertTrue(any("futura" in x for x in errors), errors)

    def test_cpi_tile_is_not_confused_with_macd_minus_532_33(self):
        report = (
            '<div class="mc"><div class="mc-name">CPI YoY</div>'
            '<div class="mc-val">3.35%</div><div class="mc-date">2026-08-01</div></div>'
            '<span class="o-name">MACD</span><span class="o-val">-532.33</span>'
        )
        self.assertAlmostEqual(extract_cpi_yoy_from_macro_strip(report), 3.35)

    def test_cpi_tile_parser_fails_closed_without_real_percent_value(self):
        report = ('<div class="mc-name">CPI YoY</div><div class="mc-val">--</div>'
                  '<span>MACD -532.33</span>')
        self.assertIsNone(extract_cpi_yoy_from_macro_strip(report))

    def test_cpi_2_33_is_not_forbidden_when_actual_value_is_2_33(self):
        report = '<div class="mc-name">CPI YoY</div><div class="mc-val">2.33%</div>'
        self.assertEqual(extract_cpi_yoy_from_macro_strip(report), 2.33)

    def test_snapshot_source_is_timezone_aware_and_param_labels_are_valid(self):
        from ingestion import snapshot
        source = (Path(__file__).resolve().parents[2] /
                  "ingestion/snapshot.py").read_text(encoding="utf-8")
        self.assertIn("datetime.timezone.utc", source)
        self.assertNotIn('" SMA200_window"', source)
        params = snapshot.get_pipeline_params()
        self.assertEqual(params["SMA200_window"], 200)
        self.assertEqual(params["historical_price_lookback"], 252)

    def test_no_static_directional_forecast_in_report_generator(self):
        source = (Path(__file__).resolve().parents[2] / "analysis/daily_report.py").read_text()
        self.assertNotIn("rally hacia $87-92K", source)
        self.assertNotIn("risk-off: BTC", source)
        self.assertNotIn("Soporte critico en zona $4,100-$4,000", source)
        self.assertIn("Resumen de datos observados", source)


if __name__ == "__main__":
    unittest.main()
