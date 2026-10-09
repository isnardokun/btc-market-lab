#!/usr/bin/env python3
"""Offline regressions for completed UTC daily ingestion and source news quality."""
import datetime as dt
import unittest
from unittest.mock import patch

from ingestion import ingest, news_pipeline


UTC = dt.timezone.utc
def stamped(day):
    return int(dt.datetime.combine(day, dt.time(0), tzinfo=UTC).timestamp())


class CompleteUtcIngestionTests(unittest.TestCase):
    def test_up_to_date_series_never_queries_still_open_day(self):
        now = dt.datetime(2026, 10, 9, 12, 40, tzinfo=UTC)
        self.assertIsNone(ingest.complete_utc_window(
            stamped(dt.date(2026, 10, 8)), now))
        self.assertIsNone(ingest.complete_utc_window(
            stamped(dt.date(2026, 10, 9)), now))

    def test_complete_day_missing_is_requested_and_retried(self):
        now = dt.datetime(2026, 10, 9, 12, 40, tzinfo=UTC)
        self.assertEqual(
            ingest.complete_utc_window(stamped(dt.date(2026, 10, 6)), now),
            ("2026-10-07", "2026-10-08")
        )

    def test_timezone_offset_does_not_shift_cutoff(self):
        minus5 = dt.timezone(dt.timedelta(hours=-5))
        now_colombia = dt.datetime(2026, 10, 8, 21, 10, tzinfo=minus5)
        self.assertEqual(
            ingest.complete_utc_window(stamped(dt.date(2026, 10, 7)), now_colombia),
            ("2026-10-08", "2026-10-08")
        )

    def test_naive_clock_rejected(self):
        with self.assertRaises(ValueError):
            ingest.complete_utc_window(None, dt.datetime(2026, 10, 9, 7, 0))

    def test_utc_roundtrip_is_host_timezone_independent(self):
        date = "2026-10-08"
        self.assertEqual(ingest.ts_to_date(ingest.date_to_ts(date)), date)
        self.assertEqual(ingest.date_to_ts(date), stamped(dt.date(2026, 10, 8)))

    def test_no_network_request_when_last_completed_day_exists(self):
        # freeze only the function receiving the clock, not the host system
        prev_day = dt.datetime.now(UTC).date() - dt.timedelta(days=1)
        with patch.object(ingest, "get_last_ts", return_value=stamped(prev_day)), \
             patch.object(ingest, "api_fetch", side_effect=AssertionError("network called")):
            result = ingest.ingest_series(None, "mvrv", "mvrv", "1d")
        self.assertEqual(result, 0)

    def test_explicit_historical_fetch_preserves_requested_range(self):
        with patch.object(ingest, "get_series_id", return_value=1), \
             patch.object(ingest, "api_fetch", return_value={"data": []}) as api:
            result = ingest.ingest_series(
                object(), "mvrv", "mvrv", "1d",
                force_start="2026-09-01", force_end="2026-09-03"
            )
        self.assertEqual(result, 0)
        self.assertIn("2026-09-01&end=2026-09-03", api.call_args.args[0])


class SourceExcerptTests(unittest.TestCase):
    def test_duplicate_leading_headline_removed(self):
        title = "Gold prices rise as yields fall"
        highlight = (
            title + " # " + title + " Gold rose after yields eased across "
            "government bonds, according to the original research briefing."
        )
        item = news_pipeline.normalize_for_report([{
            "title": title, "highlight": highlight,
            "date": "2026-10-09", "url": "https://example.com"
        }])[0]
        self.assertEqual(item["title"], title)
        self.assertEqual(item["summary"].count(title), 0)
        self.assertTrue(item["summary"].startswith("Gold rose after"))

    def test_excerpt_does_not_cut_word_midway(self):
        title = "Market briefing"
        highlight = ("Market liquidity conditions remained uncertain "
                     "and volatility continued to increase sharply. ") * 8
        excerpt = news_pipeline.clean_source_excerpt(title, highlight, max_chars=90)
        self.assertLessEqual(len(excerpt), 91)
        self.assertTrue(excerpt.endswith("…"))
        self.assertFalse(excerpt.endswith("sharp…"))

    def test_utc_news_query_follows_run_month(self):
        march = news_pipeline.default_news_queries(dt.date(2027, 3, 11))
        self.assertIn("March 2027", march["BTC"])
        self.assertIn("March 2027", march["SPY"])
        self.assertNotIn("October 2026", march["GOLD"])


if __name__ == "__main__":
    unittest.main()
