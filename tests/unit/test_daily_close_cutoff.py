#!/usr/bin/env python3
"""Regression: daily market report is about the LAST COMPLETE UTC DAY."""
import datetime as dt
import unittest

from validation.content_checks import audit_report_html

from ingestion.daily_cutoff import (
    previous_completed_utc_day, closed_daily_bars, last_complete_day_end_timestamp
)

UTC = dt.timezone.utc


def candle(day, close=83000):
    stamp = int(dt.datetime.combine(day, dt.time(0), tzinfo=UTC).timestamp())
    return {"ts": stamp, "open": close, "high": close + 10,
            "low": close - 10, "close": close, "volume": 1000}


class CompletedUtcDayTests(unittest.TestCase):
    def test_current_day_price_never_used_for_reporting(self):
        now = dt.datetime(2026, 10, 9, 12, 40, tzinfo=UTC)
        rows = [candle(dt.date(2026, 10, 7)),
                candle(dt.date(2026, 10, 8), 81234),
                candle(dt.date(2026, 10, 9), 83790)]
        output = closed_daily_bars(rows, now_utc=now)
        self.assertEqual([x["close"] for x in output], [83000, 81234])
        self.assertEqual(output[-1]["close"], 81234)

    def test_current_day_utc_boundary_in_bogota_timezone(self):
        minus5 = dt.timezone(dt.timedelta(hours=-5))
        local_clock = dt.datetime(2026, 10, 8, 22, 10, tzinfo=minus5)
        self.assertEqual(previous_completed_utc_day(local_clock),
                         dt.date(2026, 10, 8))
        output = closed_daily_bars([candle(dt.date(2026, 10, 8)),
                                    candle(dt.date(2026, 10, 9))],
                                   now_utc=local_clock)
        self.assertEqual(len(output), 1)

    def test_monday_allows_last_trading_session_friday(self):
        now = dt.datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
        output = closed_daily_bars([candle(dt.date(2026, 10, 9), 772),
                                    candle(dt.date(2026, 10, 12), 799)],
                                   now_utc=now)
        self.assertEqual(output[-1]["close"], 772)

    def test_prior_day_utc_cutoff_before_latest_candle(self):
        now = dt.datetime(2026, 10, 9, 12, 40, tzinfo=UTC)
        self.assertEqual(
            last_complete_day_end_timestamp(now),
            int(dt.datetime(2026, 10, 8, 23, 59, 59, tzinfo=UTC).timestamp())
        )

    def test_no_previous_close_must_remain_unavailable(self):
        now = dt.datetime(2026, 10, 9, 12, 40, tzinfo=UTC)
        self.assertEqual(closed_daily_bars([candle(dt.date(2026, 10, 9))],
                                           now_utc=now), [])

    def test_incomplete_or_nonfinite_bar_excluded(self):
        now = dt.datetime(2026, 10, 9, 12, 40, tzinfo=UTC)
        bad = candle(dt.date(2026, 10, 8))
        bad["high"] = float("nan")
        good = candle(dt.date(2026, 10, 7))
        self.assertEqual(closed_daily_bars([bad, good], now_utc=now), [good])

    def test_publication_gate_rejects_open_utc_day_price_even_if_recent(self):
        html = ('<p>Generado 2026-10-09 12:40 UTC</p>'
                '<div data-btc-asof-utc="2026-10-09 00:00" '
                'data-onchain-oldest-utc="2026-10-08 00:00"></div>')
        errors = audit_report_html(
            html, report_day=dt.date(2026, 10, 9), strict_asof=True
        )
        self.assertTrue(any("vela del día UTC" in x for x in errors), errors)

    def test_naive_datetime_not_accepted(self):
        with self.assertRaises(ValueError):
            previous_completed_utc_day(dt.datetime(2026, 10, 9, 12, 40))


if __name__ == "__main__":
    unittest.main()
