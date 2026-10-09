"""Offline checks for specialized news, provenance, and evidence-first local RAG."""
import datetime as dt
import os
import unittest
from unittest.mock import patch

from ingestion.specialized_news import SOURCES, parse_feed, collect_specialized_news, MAX_XML_BYTES
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
            parse_feed(b'X' * (MAX_XML_BYTES + 1), SOURCES[0], now=NOW)

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

    def test_blS_index_is_not_a_dated_press_release(self):
        xml = b"""<rss version="2.0"><channel><item>
        <title>Major Economic Indicators Latest Numbers</title>
        <link>https://www.bls.gov/</link>
        <pubDate>Fri, 09 Oct 2026 13:00:00 GMT</pubDate>
        <description>Consumer Price Index inflation and unemployment latest numbers are presented on this frequently refreshed landing page.</description>
        </item></channel></rss>"""
        self.assertEqual(parse_feed(xml, SOURCES[1], now=NOW), [])

    def test_bitcoin_optech_source_registered(self):
        self.assertTrue(any(src.name == "Bitcoin Optech"
                            and src.asset == "BTC"
                            and src.feed.endswith("/feed.xml") for src in SOURCES))

    def test_undated_and_stale_exa_entries_not_reported(self):
        today = dt.date(2026, 10, 9)
        recent = {
            "title": "Bitcoin liquidity changes following the latest policy announcement",
            "url": "https://example.com/october-news",
            "highlight": "The financial market report covers Bitcoin positioning and the latest recent economic and liquidity trends.",
            "date": "2026-10-08",
            "source_type": "discovery",
        }
        undated = dict(recent, url="https://example.com/undated", date="")
        stale = dict(recent, url="https://example.com/old", date="2026-03-03")
        future = dict(recent, url="https://example.com/future", date="2026-10-10")
        merged = news_pipeline.merge_validated_news(
            [undated, stale, future, recent], [], today=today)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["url"], recent["url"])

    def test_targeted_research_accepts_coinmetrics_substack_recent(self):
        raw = """Title: Bitcoin's changing monetary policy sensitivity discussed in new research
URL: https://coinmetrics.substack.com/p/bitcoin-market-macro-sensitivity
Published: 2026-10-08
Highlights: The State of the Network team discusses the changing sensitivity of Bitcoin markets to macroeconomic news and interest rates."""
        with patch.dict(os.environ, {"NEWS_TARGETED_EXA": "1"}), \\
             patch.object(news_pipeline, "exa_search", return_value=raw):
            result = news_pipeline.targeted_research_news("BTC", today=dt.date(2026, 10, 9))
        self.assertGreaterEqual(len(result), 1)
        self.assertEqual(result[0]["source"], "Coin Metrics State of the Network")
        self.assertEqual(result[0]["source_type"], "research")

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
