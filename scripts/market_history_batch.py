#!/usr/bin/env python3
"""Bounded, sequential historical backfill using verified one-page collector.

Default is PLAN ONLY. --apply is an operator action; NEVER runs from daily
automation. Enforces per-call page ceiling, cooldown, fail-closed stop,
progress checkpoints, bounded row count and read-only quality check on exit.
All market observations and raw API bodies remain in the existing SQLite.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ingestion.config import DB_PATH
from scripts.market_history_pilot import execute
from scripts.market_raw_reconcile import audit as reconcile
from scripts.market_temporal_quality import inspect as temporal
from scripts.market_request_lineage_audit import audit as lineage

def process(path,provider,metric,*,max_pages,pause_seconds,fetch=None,sleep=time.sleep):
    from ingestion.market_context_sources import download
    caller=fetch if fetch is not None else download
    if not 1<=max_pages<=100:
        raise ValueError("Maximum pages must be between 1 and 100")
    if not 1<=pause_seconds<=60:
        raise ValueError("Cooldown must be between 1 and 60 seconds")
    if not Path(path).is_file():
        raise FileNotFoundError("Existing production SQLite required")
    if os.getenv("MARKET_CONTEXT_DAILY_ENABLED")=="1":
        raise ValueError("Batch must not run with daily market refresh enabled")
    if os.getenv("REPORT_SEND_APPROVED_SHA256"):
        raise ValueError("Telegram delivery approval must be absent during backfill")
    summary={
        "provider":provider,"metric":metric,"max_pages":max_pages,
        "pages_attempted":0,"pages_successful":0,"rows_added":0,
        "stopped_because":None,"page_evidence":[],
        "historical_completeness":"NOT_VERIFIED",
        "warnings":[],"page_qa":[],
    }
    with sqlite3.connect(path,timeout=30) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        if db.execute("PRAGMA quick_check(1)").fetchone()[0]!="ok":
            raise ValueError("Preflight SQLite quick_check failed")
        for page in range(max_pages):
            result=execute(db,provider,metric,fetch=caller)
            summary["pages_attempted"]+=1
            summary["page_evidence"].append(result)
            status=result["status"]
            if status!="success":
                summary["stopped_because"]=status
                break
            if result.get("points",0)<=0 or not result.get("cursor_advanced"):
                summary["stopped_because"]="invalid_progress"
                summary["warnings"].append("Success without cursor advancement")
                break
            summary["pages_successful"]+=1
            summary["rows_added"]+=result["points"]
            # Mandatory independent read-only audit after EACH committed page,
            # before ANY further API request. Database write transaction has
            # already committed the BLOB, rows, cursor and request receipt.
            checked_raw=reconcile(path)
            checked_temporal=temporal(path)
            checked_lineage=lineage(path)
            qa={
                "page_number":page+1,
                "raw_state":checked_raw.get("state"),
                "temporal_state":checked_temporal.get("quality_state"),
                "lineage_state":checked_lineage.get("quality_state"),
                "lineage_requests_verified":checked_lineage.get("requests_verified"),
                "lineage_requests_total":checked_lineage.get("requests_total"),
            }
            qa["ok"]=(
                qa["raw_state"]=="PASS_SQLITE_TO_RAW"
                and qa["temporal_state"]=="SNAPSHOT_INTERNAL_QA_OK"
                and qa["lineage_state"]=="PASS_REQUEST_LINEAGE"
                and checked_lineage.get("requests_verified",0)==checked_lineage.get("requests_total",-1))
            summary["page_qa"].append(qa)
            if not qa["ok"]:
                summary["stopped_because"]="per_page_provenance_audit_failed"
                summary["warnings"].append("Stopped before next page; source/temporal/request audit mismatch")
                break
            if (page+1)%5==0:
                if db.execute("PRAGMA quick_check(1)").fetchone()[0]!="ok":
                    summary["stopped_because"]="sqlite_quick_check"
                    break
                if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    summary["stopped_because"]="sqlite_fk_violation"
                    break
            if page+1<max_pages:
                sleep(pause_seconds)
        if summary["stopped_because"] is None:
            summary["stopped_because"]="page_budget_exhausted"
    # Read-only independent auditors run after all DB writers are closed.
    raw=reconcile(path)
    chrono=temporal(path)
    final_lineage=lineage(path)
    summary["request_lineage_audit"]={
        "quality_state":final_lineage.get("quality_state"),
        "requests_total":final_lineage.get("requests_total"),
        "requests_verified":final_lineage.get("requests_verified"),
        "success":final_lineage.get("success"),
        "failed":final_lineage.get("failed"),
        "issues_count":len(final_lineage.get("issues",[]))}
    summary["raw_reconciliation"]={
        "state":raw.get("state"),"rows":raw.get("rows"),
        "verified":raw.get("verified"),"issues_count":raw.get("issues_count")}
    summary["temporal_audit"]={
        "quality_state":chrono.get("quality_state"),
        "issues":chrono.get("issues",[])[:8],
        "historical_completeness":chrono.get("historical_completeness")}
    summary["post_audit_ok"]=(
        raw.get("state")=="PASS_SQLITE_TO_RAW"
        and chrono.get("quality_state")=="SNAPSHOT_INTERNAL_QA_OK"
        and final_lineage.get("quality_state")=="PASS_REQUEST_LINEAGE"
        and final_lineage.get("requests_total",0)>0
        and final_lineage.get("requests_total")==final_lineage.get("requests_verified"))
    # These are different assertions: post_audit_ok means existing
    # observations are still intact, NOT that the backfill succeeded.
    if not summary["post_audit_ok"]:
        summary["batch_outcome"]="STOPPED_POST_AUDIT_FAILED"
    elif summary["stopped_because"]=="page_budget_exhausted":
        summary["batch_outcome"]="BUDGET_COMPLETED"
    elif summary["stopped_because"]=="empty":
        summary["batch_outcome"]="SOURCE_RETURNED_EMPTY"
    else:
        summary["batch_outcome"]="STOPPED_WITHOUT_SUCCESS"
    # A source can legitimately return an empty page at a provider limit;
    # it is NOT proof that all-time history exists or that pagination was exhaustive.
    return summary

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--provider",choices=("binance","bybit"),required=True)
    p.add_argument("--metric",choices=("open_interest","funding_settled"),required=True)
    p.add_argument("--max-pages",type=int,default=5)
    p.add_argument("--pause-seconds",type=int,default=2)
    p.add_argument("--db",type=Path,default=Path(DB_PATH))
    p.add_argument("--apply",action="store_true")
    args=p.parse_args(argv)
    if not 1<=args.max_pages<=100 or not 1<=args.pause_seconds<=60:
        p.error("max-pages must be 1-100 and pause-seconds must be 1-60")
    if not args.apply:
        print(json.dumps({"status":"PLAN_ONLY","provider":args.provider,"metric":args.metric,
                          "max_pages":args.max_pages,"pause_seconds":args.pause_seconds,
                          "network_requests":0,"sqlite_writes":0,
                          "caveat":"Requires independently checked fresh WAL-safe backup"}))
        return 0
    try:
        result=process(args.db,args.provider,args.metric,
                       max_pages=args.max_pages,pause_seconds=args.pause_seconds)
        print(json.dumps(result,indent=2,sort_keys=True))
        return 0 if (result["post_audit_ok"] and result["stopped_because"] in (
                     "page_budget_exhausted","empty")) else 1
    except (OSError,sqlite3.Error,ValueError) as exc:
        print(json.dumps({"status":"STOP","error_class":type(exc).__name__}))
        return 2

if __name__=="__main__":
    raise SystemExit(main())
