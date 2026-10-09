#!/usr/bin/env python3
"""Hermes PR #15 regressions: 52-week date boundary and partial-price DB cleanup."""
import datetime as dt
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analysis import daily_report
from ingestion import ingest_price
from validation.content_checks import audit_report_html
from tests.unit.test_financial_integrity import fixture, section


UTC = dt.timezone.utc


def midnight(day):
    return int(dt.datetime.combine(day, dt.time.min, tzinfo=UTC).timestamp())


class Correct52WeekTests(unittest.TestCase):
    def test_52weeks_equals_364_days_not_366(self):
        asof = dt.date(2026, 10, 8)
        self.assertEqual(asof - dt.timedelta(weeks=52), dt.date(2025, 10, 9))
        self.assertEqual((dt.date(2026, 10, 9) - dt.date(2025, 10, 8)).days, 366)

    def test_gate_rejects_old_extreme_per_the_observation_cutoff(self):
        # Report generated 09-Oct-2026 about final 08-Oct-2026 BTC close.
        extra = (
            '<div class="source-freshness" data-btc-asof-utc="2026-10-08 00:00"></div>'
            '<div class="stats-bar">'
            '<div class="stat-item"><div class="slbl">Max 52s</div>'
            '<div class="sval">$123355</div><div class="ssub">08 Oct 2025</div></div>'
            '<div class="stat-item"><div class="slbl">Min 52s</div>'
            '<div class="sval">$58559</div><div class="ssub">30 Jun 2026</div></div>'
            '</div>'
        )
        html = (
            '<p>Generado 2026-10-09 13:08 UTC</p>'
            '<div class="ticker-item"><div class="tk-pair">BTC/USD</div>'
            '<div class="tk-price">$81,676</div></div>'
            + section("BTC", 81676, 82139, 85000, 75613, 65955, extra)
            + section("SPY", 773, 780, 800, 700, 650)
            + section("GOLD", 4157, 4698, 4880, 3992, 3800)
        )
        errors = audit_report_html(html, report_day=dt.date(2026, 10, 9))
        self.assertTrue(any("fecha de Max 52s" in x for x in errors), errors)
        fresh = html.replace("08 Oct 2025", "09 Oct 2025")
        newer = audit_report_html(fresh, report_day=dt.date(2026, 10, 9))
        self.assertFalse(any("fecha de Max 52s" in x for x in newer), newer)

    def test_sql_window_does_not_include_oct8_2025_maximum(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "btc.db"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE price_btc(ts INTEGER PRIMARY KEY, price REAL)")
                for day, price in [
                    (dt.date(2025, 10, 6), 124753),
                    (dt.date(2025, 10, 8), 123355),
                    (dt.date(2025, 10, 9), 121706),
                    (dt.date(2026, 6, 30), 58559),
                    (dt.date(2026, 10, 8), 81676),
                ]:
                    db.execute("INSERT INTO price_btc VALUES (?,?)", (midnight(day), price))
            with patch.object(daily_report, "DB_PATH", str(path)), \
                 patch.object(daily_report, "ts_52w", midnight(dt.date(2025, 10, 9))), \
                 patch.object(daily_report, "ts_today", midnight(dt.date(2026, 10, 8)) + 86399):
                info = daily_report.get_btc_price_data(81676)
            self.assertEqual(info["high52"], 121706)
            self.assertEqual(info["high52_date"], "09 Oct 2025")
            self.assertEqual(info["ath"], 124753)


class PartialPriceArchiveTests(unittest.TestCase):
    def test_previous_partial_price_is_purged_from_sqlite(self):
        today = dt.datetime.now(UTC).date()
        prior = today - dt.timedelta(days=1)
        old = today - dt.timedelta(days=2)
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "btc.db")
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE price_btc(ts INTEGER PRIMARY KEY, price REAL)")
                db.execute("INSERT INTO price_btc VALUES (?,?)", (midnight(today), 90000))
                db.execute("INSERT INTO price_btc VALUES (?,?)", (midnight(old), 80000))
            response = {
                "chart": {"result": [{
                    "timestamp": [midnight(prior), midnight(today)],
                    "indicators": {"quote": [{"close": [81000, 90000]}]}
                }]}
            }
            with patch.object(ingest_price, "DB_PATH", path), \
                 patch.object(ingest_price.urllib.request, "urlopen",
                              return_value=io.BytesIO(json.dumps(response).encode())):
                ingest_price.ingest()
            with sqlite3.connect(path) as db:
                stored = db.execute("SELECT ts, price FROM price_btc ORDER BY ts").fetchall()
            self.assertEqual(stored, [
                (midnight(old), 80000),
                (midnight(prior), 81000)
            ])


if __name__ == "__main__":
    unittest.main()
