#!/usr/bin/env python3
"""Read-only, SQLite-backed evidence for Hermes' ETF/derivatives/calendar rollout.

Never queries networks, creates a database, writes a JSON file or prints raw
API bodies, tokens, personal paths or private logs. Only aggregate counters,
UTC coverage, units, selected public market values and error class names.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ingestion.config import DB_PATH

SOURCES = ("binance", "bybit", "farside", "bls", "fred")
METRICS = ("open_interest", "funding_settled")

def _schema(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

def _rows(db, sql, params=()):
    return [tuple(r) for r in db.execute(sql,params).fetchall()]

def collect(db_path, *, full_check=False, now=None):
    """Summarize coverage and proof of raw lineage from the ONE production DB."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Expected timezone-aware UTC now")
    source = Path(db_path)
    if not source.is_file():
        raise FileNotFoundError("SQLite database not found (never create a new one)")
    report = {
        "collected_at_utc": now.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
        "database": "existing btc_research.db (read-only)",
        "data_types": ["ETF USD millions", "derivatives raw BTC/fraction",
                       "BLS/FRED calendar", "raw bytes and revisions"],
        "provider_status": {}, "missing_or_incomplete": [],
        "operational_state": "INCOMPLETE_OR_FAILED",
        "historical_completeness_claim": "NOT ASSESSED: no claim of complete histories without provider-window audit",
    }
    with sqlite3.connect(source.resolve().as_uri()+"?mode=ro",uri=True,timeout=20) as db:
        db.execute("PRAGMA query_only=ON")
        tables=_schema(db)
        report["sqlite"]={
            "quick_check":db.execute("PRAGMA quick_check(1)").fetchone()[0],
            "foreign_key_violations":None,
            "market_schema_installed":"market_context_migrations" in tables,
            "archive_schema_installed":"archive_schema_migrations" in tables,
        }
        if full_check:
            report["sqlite"]["integrity_check"]=db.execute("PRAGMA integrity_check").fetchone()[0]
        required = {
            "market_context_migrations","market_raw_payloads","market_etf_flows",
            "market_derivatives","market_calendar_events","market_source_runs",
            "market_context_revisions","market_source_cursors",
        }
        absent=sorted(required-tables)
        report["sqlite"]["market_tables_missing"]=absent
        if absent:
            report["missing_or_incomplete"].append("missing market_* migration/tables")
            report["sqlite"]["foreign_key_violations"] = len(
                db.execute("PRAGMA foreign_key_check").fetchall()
            )
            report["provider_status"] = {
                provider: {"status": "NOT_INSTALLED", "runs": 0, "last": None}
                for provider in SOURCES
            }
            return report
        report["sqlite"]["foreign_key_violations"]=len(db.execute("PRAGMA foreign_key_check").fetchall())
        migration=_rows(db,"SELECT version,applied_at_utc FROM market_context_migrations ORDER BY version")
        report["sqlite"]["market_migrations"]=[dict(version=x[0],applied_at_utc=x[1]) for x in migration]
        all_runs=_rows(db,"SELECT provider,status,requests,points,started_utc,ended_utc,error_code "
                          "FROM market_source_runs ORDER BY started_utc DESC")
        for provider in SOURCES:
            runs=[r for r in all_runs if r[0]==provider]
            report["provider_status"][provider]={
                "runs":len(runs),
                "last":({"status":runs[0][1],"requests":runs[0][2],
                         "points_changed":runs[0][3],"started_utc":runs[0][4],
                         "ended_utc":runs[0][5],"error_class":runs[0][6]}
                        if runs else None),
                "run_status_counts":{status:sum(x[1]==status for x in runs)
                                     for status in ("success","empty","failed","running")},
            }
            if not runs:
                report["missing_or_incomplete"].append(provider+": no collection attempt recorded")
            elif runs[0][1]!="success":
                report["missing_or_incomplete"].append(provider+": latest attempt not success")
        report["raw_payloads"]=[
            {"provider":r[0],"payloads":r[1],"total_bytes":r[2],"first_capture_utc":r[3],
             "last_capture_utc":r[4]}
            for r in _rows(db,"SELECT provider,COUNT(*),SUM(LENGTH(body)),MIN(fetched_utc),"
                           "MAX(fetched_utc) FROM market_raw_payloads GROUP BY provider ORDER BY provider")
        ]
        report["raw_payloads_sha256_sample_verified"]=True
        report["raw_payloads_sample_checked"]=0
        for digest,body in _rows(db,"SELECT sha256,body FROM market_raw_payloads ORDER BY fetched_utc DESC LIMIT 5"):
            report["raw_payloads_sample_checked"]+=1
            if hashlib.sha256(body).hexdigest()!=digest:
                report["raw_payloads_sha256_sample_verified"]=False
                report["missing_or_incomplete"].append("raw payload digest mismatch")
        report["etf"]=[
            {"provider":r[0],"ticker":r[1],"rows":r[2],"first_date":r[3],"last_date":r[4]}
            for r in _rows(db,"SELECT provider,ticker,COUNT(*),MIN(trade_date),MAX(trade_date) "
                           "FROM market_etf_flows GROUP BY provider,ticker ORDER BY provider,ticker")
        ]
        report["etf_recent"]=[
            {"trade_date":r[0],"ticker":r[1],"net_flow_usd_m":r[2],"state":r[3],
             "payload_sha256_prefix":r[4][:12]}
            for r in _rows(db,"SELECT trade_date,ticker,net_flow_usd_m,state,raw_sha256 "
                           "FROM market_etf_flows ORDER BY trade_date DESC,ticker LIMIT 12")
        ]
        report["derivatives"]=[
            {"provider":r[0],"symbol":r[1],"metric":r[2],"raw_unit":r[3],
             "interval":r[4],"observations":r[5],"first_utc":r[6],"last_utc":r[7]}
            for r in _rows(db,"SELECT provider,symbol,metric,raw_unit,interval_label,"
                           "COUNT(*),MIN(observed_utc),MAX(observed_utc) "
                           "FROM market_derivatives GROUP BY provider,symbol,metric,raw_unit,"
                           "interval_label ORDER BY provider,metric")
        ]
        report["derivatives_recent"]=[
            {"provider":r[0],"metric":r[1],"observed_utc":r[2],
             "raw_value":r[3],"raw_unit":r[4],"quote_usd":r[5],
             "payload_sha256_prefix":r[6][:12]}
            for r in _rows(db,"SELECT provider,metric,observed_utc,raw_value,raw_unit,"
                           "quote_usd,raw_sha256 FROM market_derivatives "
                           "ORDER BY observed_utc DESC LIMIT 8")
        ]
        report["calendar"]=[
            {"provider":r[0],"events":r[1],"first_date":r[2],"last_date":r[3],
             "utc_timed_events":r[4],"date_only_events":r[5]}
            for r in _rows(db,"SELECT provider,COUNT(*),MIN(event_date),MAX(event_date),"
                           "SUM(CASE WHEN time_precision='utc' THEN 1 ELSE 0 END),"
                           "SUM(CASE WHEN time_precision='date_only' THEN 1 ELSE 0 END) "
                           "FROM market_calendar_events GROUP BY provider")
        ]
        report["calendar_upcoming"]=[
            {"provider":r[0],"title":r[1][:90],"event_date":r[2],
             "scheduled_utc":r[3],"precision":r[4]}
            for r in _rows(db,"SELECT provider,title,event_date,scheduled_utc,time_precision "
                           "FROM market_calendar_events WHERE state='scheduled' AND event_date>=? "
                           "ORDER BY event_date LIMIT 6",(now.date().isoformat(),))
        ]
        report["revisions"]=[
            {"table":r[0],"changes":r[1]}
            for r in _rows(db,"SELECT table_name,COUNT(*) FROM market_context_revisions "
                           "GROUP BY table_name")
        ]
        report["history_cursors"]=[
            {"provider":r[0],"stream":r[1],"cursor":r[2][:60],
             "updated_utc":r[3]}
            for r in _rows(db,"SELECT provider,stream,cursor,updated_utc "
                           "FROM market_source_cursors ORDER BY provider,stream")
        ]
        # FRED series are a separate legacy macro table, not identical to the
        # newly ingested FRED release calendar.
        if "macro_fred" in tables:
            report["fred_macro_series"]=[
                {"series":r[0],"rows":r[1],"first_period":r[2],"last_period":r[3]}
                for r in _rows(db,"SELECT series_id,COUNT(*),MIN(date),MAX(date) "
                               "FROM macro_fred GROUP BY series_id ORDER BY series_id")
            ]
        else:
            report["missing_or_incomplete"].append("legacy FRED macro_fred absent")
        for provider,metric in (("binance","open_interest"),("binance","funding_settled"),
                                ("bybit","open_interest"),("bybit","funding_settled")):
            if not any(x["provider"]==provider and x["metric"]==metric and x["observations"]>0
                       for x in report["derivatives"]):
                report["missing_or_incomplete"].append(provider+"/"+metric+": no SQLite observations")
        if not report["etf"]:
            report["missing_or_incomplete"].append("ETF: no flow observations")
        if not report["calendar"]:
            report["missing_or_incomplete"].append("calendar: no events")
        if report["sqlite"]["foreign_key_violations"]:
            report["missing_or_incomplete"].append("SQLite foreign key violations")
        if report["sqlite"]["quick_check"]!="ok":
            report["missing_or_incomplete"].append("SQLite quick_check failed")
    report["operational_state"]=("PASS_EVIDENCE_PRESENT" if not report["missing_or_incomplete"]
                                 else "INCOMPLETE_OR_FAILED")
    report["historical_completeness_claim"]="NOT ASSESSED: provider time windows and missing days require independent audit"
    return report

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",type=Path,default=Path(DB_PATH))
    parser.add_argument("--full-check",action="store_true",help="Run PRAGMA integrity_check, potentially expensive")
    parser.add_argument("--strict",action="store_true",
                        help="Nonzero if any provider, table or observed metric lacks evidence")
    args=parser.parse_args(argv)
    try:
        report=collect(args.db,full_check=args.full_check)
        print(json.dumps(report,indent=2,ensure_ascii=False,sort_keys=True))
        return int(args.strict and report["operational_state"]!="PASS_EVIDENCE_PRESENT")
    except (OSError,sqlite3.Error,ValueError) as exc:
        print(json.dumps({"operational_state":"AUDIT_UNAVAILABLE",
                          "error_class":type(exc).__name__},sort_keys=True))
        return 2

if __name__=="__main__":
    raise SystemExit(main())
