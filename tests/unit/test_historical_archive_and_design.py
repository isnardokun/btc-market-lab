"""Offline regression tests: historical API planners and dedicated report design."""
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from ingestion.researchbitcoin_v2 import query_params_window
from ingestion.researchbitcoin_archive import (
    allowed_earliest, group_missing_dates, plan_metric, run as run_rbn,
)
from ingestion.bitview_history import (
    EPOCH, windows as bitview_windows, decode_day1_response, run as run_bitview,
)
from ingestion.yahoo_history import extract_daily, symbols_in_catalog, archive
from ingestion.news_archive import archive_news
from scripts.history_coverage import coverage
from scripts.history_query import query_history
from rendering.market_design import RESEARCH_CSS, STYLE_VERSION


NOW = dt.datetime(2026, 10, 9, 14, tzinfo=dt.timezone.utc)
LAST = dt.date(2026, 10, 8)
ROOT = Path(__file__).resolve().parents[2]


class HistoryAndResearchDesignTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "archive.sqlite"
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE series(id INTEGER PRIMARY KEY, name TEXT UNIQUE,
                                    idx TEXT, description TEXT);
                CREATE TABLE daily(series_id INTEGER, ts INTEGER,
                                   block_height INTEGER, value REAL,
                                   UNIQUE(series_id,ts));
                CREATE TABLE price_btc(ts INTEGER, price REAL);
                CREATE TABLE macro_fred(series_id TEXT, date TEXT, value REAL);
                CREATE TABLE ingest_instruments(
                    provider TEXT, yahoo_symbol TEXT, metric_id TEXT
                );
                INSERT INTO series(id,name,idx) VALUES (7,'mvrv','1d');
                INSERT INTO ingest_instruments
                    (provider,yahoo_symbol,metric_id)
                    VALUES ('Yahoo Finance','BTC-USD','btc_price'),
                           ('Yahoo Finance','SPY','spy_price'),
                           ('bitview',NULL,'mvrv');
            """)

    def test_rbn_explicit_window_bounds_and_tier_history_limit(self):
        self.assertEqual(query_params_window("2026-10-01", "2026-10-09", now=NOW),
                         {"from_time":"2026-10-01", "to_time":"2026-10-09",
                          "resolution":"d1","output_format":"json"})
        for first, last in [("2026-09-01","2026-10-09"),
                            ("2026-10-08","2026-10-10")]:
            with self.assertRaises(ValueError):
                query_params_window(first,last,now=NOW)
        self.assertEqual(allowed_earliest(LAST,0,None),LAST-dt.timedelta(days=364))
        with self.assertRaises(ValueError):
            allowed_earliest(LAST,0,"2020-01-01")
        with self.assertRaises(ValueError):
            allowed_earliest(LAST,1,None)
        self.assertEqual(allowed_earliest(LAST,1,"2009-01-01"),
                         dt.date(2009,1,1))

    def test_rbn_missing_windows_are_contiguous_bounded_and_resumable(self):
        a=dt.date(2026,10,1)
        dates=[a+dt.timedelta(days=i) for i in (0,1,3,17,18)]
        ranges=group_missing_dates(dates,max_days=14)
        self.assertEqual(ranges[0],(a,a+dt.timedelta(days=2)))
        self.assertEqual(ranges[1],(a+dt.timedelta(days=3),
                                    a+dt.timedelta(days=4)))
        self.assertEqual(ranges[2],(a+dt.timedelta(days=17),
                                    a+dt.timedelta(days=19)))
        self.assertEqual(plan_metric("mvrv_sth",
                         {LAST},mode="incremental",cutoff=LAST),[])
        self.assertEqual(plan_metric("mvrv_sth",
                         set(),mode="incremental",cutoff=LAST),
                         [(LAST-dt.timedelta(days=6),LAST+dt.timedelta(days=1))])

    def test_rbn_archive_dry_run_has_no_network_and_apply_persists(self):
        def client(slug, *, start_day, end_day):
            return {"data":[{"time":start_day.isoformat()+"T00:00:00Z",
                             slug: 1.05}]}
        slug="mvrv_sth"
        plan=run_rbn(self.db,mode="incremental",slugs=[slug],
                     max_requests=1,today=NOW)
        self.assertFalse(plan["executed"])
        with sqlite3.connect(self.db) as conn:
            self.assertNotIn("onchain_external_observations",
                             {r[0] for r in conn.execute(
                                 "SELECT name FROM sqlite_master WHERE type='table'")})
        result=run_rbn(self.db,mode="incremental",slugs=[slug],
                       max_requests=1,apply=True,client=client,today=NOW)
        self.assertEqual(result["saved_observations"],1)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute(
                "SELECT value FROM onchain_external_observations").fetchone()[0],1.05)
        self.assertEqual(run_rbn(self.db,mode="history",slugs=[slug],
                                 max_requests=2,today=NOW)["selected_windows"],2)

    def test_bitview_day1_uses_real_utc_index_and_noninvented_values(self):
        first=EPOCH
        self.assertEqual(decode_day1_response(
            {"index":"1d","start":0,"data":[1.2,None,2.3]},first,
            first+dt.timedelta(days=2))[0][1],1.2)
        with self.assertRaises(ValueError):
            decode_day1_response({"index":"height","start":0,
                                 "data":[3]},first,first)
        self.assertEqual(bitview_windows(dt.date(2026,1,1),
                                         dt.date(2026,1,5),width=2),
                         [(dt.date(2026,1,1),dt.date(2026,1,2)),
                          (dt.date(2026,1,3),dt.date(2026,1,4)),
                          (dt.date(2026,1,5),dt.date(2026,1,5))])

    def test_bitview_archive_plan_and_checkpoint_survive_retries(self):
        def client(name,index,*,start,end):
            start_day=dt.date.fromisoformat(start)
            index_start=(start_day-EPOCH).days
            return {"index":"1d","start":index_start,"data":[3.4]}
        opts=dict(db_path=self.db,slug="mvrv",
                  first=dt.date(2026,10,7),cutoff=LAST,
                  chunk_days=2,max_requests=1)
        plan=run_bitview(**opts)
        self.assertFalse(plan["executed"])
        result=run_bitview(**opts,apply=True,client=client)
        self.assertEqual(result["rows_saved"],1)
        self.assertEqual(run_bitview(**opts)["remaining_windows_before"],0)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM daily WHERE series_id=7").fetchone()[0],1)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM historical_fetch_windows").fetchone()[0],1)

    def test_yahoo_history_catalog_and_completed_utc_filter(self):
        self.assertEqual(symbols_in_catalog(self.db),["BTC-USD","SPY"])
        stamp=lambda day: int(dt.datetime.combine(
            dt.date(2026,10,day),dt.time(),dt.timezone.utc).timestamp())
        data={"chart":{"result":[{
            "timestamp":[stamp(8),stamp(9)],
            "indicators":{"quote":[{
                "open":[10.0,11.0],"high":[13.0,15.0],
                "low":[9.0,10.0],"close":[12.0,14.0],
                "volume":[100.0,101.0]}]}}]}}
        rows=extract_daily(data,now=NOW)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0][4],12.0)
        archive(self.db,"BTC-USD",rows)
        report=coverage(self.db)
        match=[t for t in report["datasets"]
               if t["table"]=="market_ohlc_history"][0]
        self.assertEqual(match["series"][0]["observations"],1)
        self.assertEqual(match["series"][0]["first_utc"],"2026-10-08")

    def test_news_metadata_keeps_url_provenance_without_article_body(self):
        items = {"BTC":[{"title":"Bitcoin network research update",
                         "url":"https://example.org/research/btc",
                         "summary":"Full proprietary copyrighted article body",
                         "date":"2026-10-08", "source":"Research desk",
                         "source_type":"research"},
                        {"title":"Unsafe URL",
                         "url":"javascript:alert(1)", "source":"Unknown"}]}
        saved=archive_news(self.db,"2026-10-09",items,at=NOW)
        self.assertEqual(saved,1)
        self.assertEqual(archive_news(self.db,"2026-10-09",items,at=NOW),0)
        with sqlite3.connect(self.db) as conn:
            rows=conn.execute("SELECT title,url,source_type FROM research_news_archive").fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0][2],"research")
        self.assertNotIn("Full proprietary",str(rows))
        data=coverage(self.db)
        entry=next(x for x in data["datasets"] if x["table"]=="research_news_archive")
        self.assertEqual(entry["series"][0]["observations"],1)

    def test_local_historical_queries_preserve_source_and_utc(self):
        day=dt.date(2026,10,8)
        unix=int(dt.datetime.combine(day,dt.time(),dt.timezone.utc).timestamp())
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT INTO daily(series_id,ts,value) VALUES (7,?,?)",
                       (unix,1.25))
            db.execute("INSERT INTO macro_fred(series_id,date,value) "
                       "VALUES('DGS10','2026-10-08',4.1)")
        archive(self.db,"BTC-USD",[(unix,10.0,12.0,9.0,11.0,100.0)])
        archive_news(self.db,"2026-10-09",{"BTC":[{
            "title":"Research note","url":"https://example.org/note",
            "date":"2026-10-08","source":"Publisher"}]},at=NOW)
        for provider,metric,target in [
            ("bitview","mvrv","value"),
            ("fred","DGS10","value"),
            ("yahoo","BTC-USD","close"),
            ("news","BTC","url"),
        ]:
            with self.subTest(provider=provider):
                dates=("2026-10-09","2026-10-09") if provider=="news" else (
                       "2026-10-08","2026-10-08")
                response=query_history(self.db,provider,metric,*dates)
                self.assertEqual(response["count"],1)
                self.assertIn(target,response["rows"][0])
                self.assertEqual(response["provider"],provider)
        with self.assertRaises(ValueError):
            query_history(self.db,"bogus","x","2026-10-08","2026-10-08")

    def test_private_research_studio_visual_contract(self):
        self.assertEqual(STYLE_VERSION,"mercados-research-studio-v1")
        for part in ["@media print","@media(max-width:760px)","tabular-nums",
                     "focus-visible","--deep:#12243A","#onchain-complement"]:
            self.assertIn(part,RESEARCH_CSS)
        self.assertNotIn("@import",RESEARCH_CSS)
        self.assertNotIn("url(http",RESEARCH_CSS)
        source=(ROOT/"analysis"/"daily_report.py").read_text(encoding="utf-8")
        self.assertIn("CSS+RESEARCH_CSS",source)
        self.assertIn("market-design-system",source)
        skill=(ROOT/"skills"/"mercados-research-design"/"SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: mercados-research-design",skill)
        self.assertIn("Research Studio",skill)
        self.assertIn("publication_gate",skill)


if __name__=="__main__":
    unittest.main()
