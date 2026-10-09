#!/usr/bin/env python3
"""Read-only history coverage report per provider, dataset and metric.

Counts and observed limits are what is ARCHIVED locally, NOT proof of all
provider history or exact completeness. Never expose DB rows or credentials.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingestion.config import DB_PATH


def coverage(db_path):
    path=Path(db_path)
    if not path.is_file():
        raise FileNotFoundError("SQLite local no existe")
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True) as db:
        tables={row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        result={"generated_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
                "db_path":"LOCAL (omitted)","datasets":[]}
        statements=[
            ("bitview","daily",
             "SELECT s.name,COUNT(*),MIN(d.ts),MAX(d.ts) "
             "FROM daily d JOIN series s ON s.id=d.series_id "
             "GROUP BY s.name ORDER BY s.name", "unix"),
            ("Yahoo Finance","price_btc",
             "SELECT 'BTC-USD',COUNT(*),MIN(ts),MAX(ts) FROM price_btc","unix"),
            ("Yahoo Finance","market_ohlc_history",
             "SELECT symbol,COUNT(*),MIN(ts),MAX(ts) FROM market_ohlc_history "
             "GROUP BY symbol ORDER BY symbol","unix"),
            ("FRED","macro_fred",
             "SELECT series_id,COUNT(*),MIN(date),MAX(date) FROM macro_fred "
             "GROUP BY series_id ORDER BY series_id","date"),
            ("RSS/Exa","research_news_archive",
             "SELECT source || ':' || asset,COUNT(*),MIN(report_date),MAX(report_date) "
             "FROM research_news_archive GROUP BY source,asset ORDER BY source,asset","date"),
            ("ResearchBitcoin","onchain_external_observations",
             "SELECT metric,COUNT(*),MIN(observed_date),MAX(observed_date) "
             "FROM onchain_external_observations "
             "WHERE provider='researchbitcoin' GROUP BY metric ORDER BY metric","date"),
        ]
        for provider,table,query,format_ in statements:
            if table not in tables:
                result["datasets"].append({"provider":provider,"table":table,
                                           "status":"not_initialized","series":[]})
                continue
            items=[]
            for metric,count,first,last in db.execute(query):
                if format_=="unix":
                    first=(dt.datetime.fromtimestamp(first,dt.timezone.utc).date().isoformat()
                           if first is not None else None)
                    last=(dt.datetime.fromtimestamp(last,dt.timezone.utc).date().isoformat()
                          if last is not None else None)
                items.append({"metric":metric,"observations":count,
                              "first_utc":first,"last_utc":last})
            result["datasets"].append({"provider":provider,"table":table,
                                       "status":"archived_range_only","series":items})
        if "historical_fetch_windows" in tables:
            result["bitview_historical_windows"]=[
                {"status":status,"windows":count}
                for status,count in db.execute(
                    "SELECT status,COUNT(*) FROM historical_fetch_windows "
                    "WHERE provider='bitview' GROUP BY status")]
        if "archive_sources" in tables:
            result["archive_source_registry"]=[
                {"provider":p,"display_name":name,"tier_reported":tier,
                 "entitlement_verified":bool(verified)}
                for p,name,tier,verified in db.execute(
                    "SELECT source_id,display_name,plan_tier,entitlement_verified "
                    "FROM archive_sources ORDER BY source_id")]
        if "archive_datasets" in tables:
            result["archive_dataset_count"]=[
                {"provider":provider,"series_registered":count}
                for provider,count in db.execute(
                    "SELECT source_id,COUNT(*) FROM archive_datasets GROUP BY source_id")]
        if "archive_ingest_windows" in tables:
            result["archive_fetch_windows"]=[
                {"provider":provider,"status":status,"windows":cnt,
                 "observed_points":points}
                for provider,status,cnt,points in db.execute(
                    "SELECT source_id,status,COUNT(*),SUM(data_points) "
                    "FROM archive_ingest_windows GROUP BY source_id,status "
                    "ORDER BY source_id,status")]
        if "archive_observation_revisions" in tables:
            result["archive_revisions"]=[
                {"provider":provider,"changes_retained":count}
                for provider,count in db.execute(
                    "SELECT source_id,COUNT(*) FROM archive_observation_revisions "
                    "GROUP BY source_id")]
        if "archive_fred_vintages" in tables:
            result["fred_realtime_vintages_count"]=db.execute(
                "SELECT COUNT(*) FROM archive_fred_vintages").fetchone()[0]
        if "archive_ingest_runs" in tables:
            result["archive_recent_runs"]=[
                {"provider":provider,"operation":operation,"status":status,
                 "requests":req,"points":points,"started_at_utc":started}
                for provider,operation,status,req,points,started in db.execute(
                    "SELECT source_id,operation,status,requests,data_points,"
                    "started_at_utc FROM archive_ingest_runs "
                    "ORDER BY started_at_utc DESC LIMIT 20")]
    result["completeness_rule"]="NO afirmar historia completa sin verificar el límite, cobertura y brechas de cada API"
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",type=Path,default=Path(DB_PATH))
    parser.add_argument("--output",type=Path,
                        help="Archivo JSON local, nunca subir al Git público")
    args=parser.parse_args()
    try:
        result=coverage(args.db)
        encoded=json.dumps(result,indent=2,ensure_ascii=False)
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(encoded+"\n",encoding="utf-8")
            print("Cobertura local archivada; contenido privado no impreso.")
        else:
            print(encoded)
        return 0
    except (OSError,sqlite3.Error,ValueError) as exc:
        print("Coverage STOP: "+type(exc).__name__,file=sys.stderr)
        return 1


if __name__=="__main__":
    sys.exit(main())
