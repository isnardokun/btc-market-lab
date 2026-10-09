"""Offline checks for specialized news, provenance, and evidence-first local RAG."""
import datetime as dt
import os
import unittest
from unittest.mock import patch

from ingestion.specialized_news import SOURCES, parse_feed, collect_specialized_news
from ingestion import news_pipeline
from analysis.knowledge_rag import retrieve, explain_snapshot, render_research_note

NOW = dt.datetime(2026, 10, 9, 14, 0, tzinfo=dt.timezone.utc)
RSS = b'''<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Federal Reserve releases new monetary policy decision and rationale</title>
<link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20261008a.htm</link>
<pubDate>Thu, 08 Oct 2026 18:00:00 GMT</pubDate>
<description>The central bank published a primary statement about its policy decisions and subsequent communications.</description></item>
<item><title>Future spoofed central bank release with no confirmed date</title>
<link>https://www.federalreserve.gov/future</link>
<pubDate>Sat, 10 Oct 2026 18:00:00 GMT</pubDate>
<description>This future dated entry has long enough text to test future event filtering without web.</description></item>
<item><title>Offsite content masquerading as Federal Reserve authority</title>
<link>https://example.com/phishing</link>
<pubDate>Thu, 08 Oct 2026 18:00:00 GMT</pubDate>
<description>This text is reasonably long but belongs to an unauthorized publisher domain.</description></item>
</channel></rss>'''


class SpecializedNewsTests(unittest.TestCase):
    def test_official_feed_filters_future_and_offsite(self):
        items = parse_feed(RSS, SOURCES[0], now=NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "Federal Reserve")
        self.assertEqual(items[0]["source_type"], "primary")
        self.assertEqual(items[0]["date"], "2026-10-08")

    def test_xml_rejects_doctype_and_unbounded_payload(self):
        with self.assertRaises(ValueError):
            parse_feed(b'<!DOCTYPE rss><rss></rss>', SOURCES[0], now=NOW)
        with self.assertRaises(ValueError):
            parse_feed(b'X' * 600_000, SOURCES[0], now=NOW)

    def test_opt_out_does_not_call_fetcher(self):
        with patch.dict(os.environ, {"NEWS_RSS_ENABLED": "0"}):
            results = collect_specialized_news(
                ["BTC", "SPY"], fetcher=lambda *a, **kw: self.fail("network call")
            )
        self.assertEqual(results, {"BTC": [], "SPY": []})

    def test_dedupe_tracking_and_source_priority(self):
        a = {"title": "A significant Bitcoin market event was announced today",
             "url": "https://coinmetrics.substack.com/p/report?utm_source=a",
             "highlight": "This source offered a valid market news summary of sufficient length.",
             "date": "2026-10-08"}
        b = dict(a, url="https://coinmetrics.substack.com/p/report?utm_source=b", source="Coin Metrics")
        merged = news_pipeline.merge_validated_news([a], [b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source"], "Coin Metrics")

    def test_legitimate_error_term_no_longer_blocks_headline(self):
        ok = {"title": "Bitcoin network errors decrease after software updates",
              "url": "https://bitcoinops.org/en/newsletters/",
              "highlight": "Developers summarized network bug fixes and technical changes in a review.",
              "date": "2026-10-08"}
        self.assertTrue(news_pipeline.validate_entry(ok, 0)[0])
        bad = dict(ok, title="Error: API request failed on source fetch")
        self.assertFalse(news_pipeline.validate_entry(bad, 0)[0])

    def test_fail_open_on_publisher_failure(self):
        with patch.dict(os.environ, {"NEWS_RSS_ENABLED": "1"}):
            result = collect_specialized_news(
                ["SPY"], fetcher=lambda *a, **kw: (_ for _ in ()).throw(OSError("disabled"))
            )
        self.assertEqual(result["SPY"], [])


class GroundedRagTests(unittest.TestCase):
    def test_fts_retrieves_actual_methodology_card(self):
        m = retrieve("mvrv", limit=3)
        self.assertTrue(m)
        self.assertEqual(m[0].metric, "mvrv")
        self.assertIn("https://", m[0].url)

    def test_evidence_does_not_invent_future_probability(self):
        facts = explain_snapshot({"mvrv": 1.52, "asopr": 1.0165, "nupl": 0.34},
                                 {"mvrv": "2026-10-08 00:00"})
        self.assertEqual({f["metric"] for f in facts}, {"mvrv", "asopr", "nupl"})
        self.assertIn("superior a la realizada", facts[0]["observation"])
        self.assertEqual(facts[0]["asof"], "2026-10-08 00:00")
        html = render_research_note({"mvrv": 1.52, "asopr": 1.0165, "nupl": 0.34})
        self.assertIn("RAG local", html)
        self.assertIn("Consultar metodología", html)
        self.assertNotIn("90% de probabilidad", html)

    def test_missing_and_nonfinite_metrics_cannot_generate_claims(self):
        self.assertEqual(explain_snapshot({"mvrv": float("nan"), "asopr": None}), [])
        self.assertEqual(render_research_note({}), "")

    def test_no_macro_or_news_headline_fabricates_numeric_knowledge(self):
        html = render_research_note({"mvrv": 0.9})
        self.assertIn("inferior a la realizada", html)
        self.assertNotIn("precio objetivo", html)


if __name__ == "__main__":
    unittest.main()
