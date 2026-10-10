#!/usr/bin/env python3
"""Independent read-only audit of post-v2 request provenance and stored BTC data.

Unlike a basic foreign-key check, this reconciles each request receipt with
run accounting, original raw response bytes, exact timestamp boundaries,
and normalized SQLite rows. Legacy v1 rows correctly have no fabricated
request metadata. No HTTP, SQLite writes or source-body output.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ingestion.config import DB_PATH
from scripts.market_history_pilot import CONFIG
from scripts.market_raw_reconcile import expected_rows

FIELDS=("request_id","run_id","provider","stream","symbol","interval_label",
        "endpoint_path","requested_end_ms","requested_limit","attempted_utc",
        "ended_utc","http_attempts","status","error_class","raw_sha256",
        "returned_rows","persisted_rows","first_observed_utc",
        "last_observed_utc")

def _dt(value):
    t=dt.datetime.fromisoformat(str(value).replace("Z","+00:00"))
    if t.tzinfo is None:
        raise ValueError("Missing UTC offset")
    return t.astimezone(dt.timezone.utc)

def audit(db_path):
    p=Path(db_path)
    if not p.is_file():
        raise FileNotFoundError("Existing SQLite required")
    report={"quality_state":"REVIEW_REQUIRED",
            "historical_completeness":"NOT_VERIFIED",
            "requests_total":0,"requests_verified":0,
            "success":0,"empty":0,"failed":0,
            "source_records_verified":0,"observations_verified":0,
            "by_stream":{},"issues":[]}
    with sqlite3.connect(p.resolve().as_uri()+"?mode=ro",uri=True,timeout=30) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA foreign_keys=ON")
        tables={x[0] for x in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"market_request_lineage","market_source_runs",
                "market_raw_payloads","market_derivatives",
                "market_source_cursors","market_context_migrations"}<=tables:
            report["issues"].append("Required version 2 market tables absent")
            return report
        if not db.execute(
            "SELECT 1 FROM market_context_migrations WHERE version=2").fetchone():
            report["issues"].append("Schema migration version 2 missing")
            return report
        if db.execute("PRAGMA foreign_key_check").fetchone():
            report["issues"].append("Foreign key violation")
        cols=",".join(FIELDS)
        receipts=[dict(zip(FIELDS,r)) for r in db.execute(
            "SELECT "+cols+" FROM market_request_lineage ORDER BY rowid")]
        report["requests_total"]=len(receipts)
        for i,rec in enumerate(receipts):
            problems=[]
            provider=rec["provider"]
            metric=rec["stream"]
            stream=provider+"_"+metric
            tally=report["by_stream"].setdefault(stream,{
                "requests":0,"successful":0,"points":0,
                "last_request_end_ms":None,"oldest_utc":None})
            tally["requests"]+=1
            template=CONFIG.get((provider,metric))
            if template is None:
                problems.append("unrecognized provider or stream")
                interval=None
            else:
                endpoint,limit,interval=template
                if rec["endpoint_path"]!=endpoint or rec["interval_label"]!=interval:
                    problems.append("provider path or interval mismatch")
                if rec["requested_limit"]!=limit:
                    problems.append("unexpected page limit")
            if rec["symbol"]!="BTCUSDT":
                problems.append("source instrument mismatch")
            if rec["requested_end_ms"] is None or not isinstance(rec["requested_end_ms"],int):
                problems.append("request endTime absent or malformed")
            if rec["http_attempts"]!=1:
                problems.append("request must have exactly one attempt")
            if rec["status"] not in ("success","empty","failed"):
                problems.append("unrecognized request state")
            try:
                started=_dt(rec["attempted_utc"])
                ended=_dt(rec["ended_utc"])
                if started>ended:
                    problems.append("request UTC ordering")
            except (ValueError,TypeError):
                problems.append("bad request UTC timestamps")
            run=db.execute(
                "SELECT provider,status,requests,points,error_code "
                "FROM market_source_runs WHERE run_id=?",(rec["run_id"],)).fetchone()
            if run is None:
                problems.append("source-run receipt missing")
            elif (run[0]!=provider or run[1]!=rec["status"]
                  or run[2]!=rec["http_attempts"]
                  or run[3]!=rec["persisted_rows"]):
                problems.append("source-run status or counts disagreement")
            if rec["status"]=="failed":
                report["failed"]+=1
                if (rec["raw_sha256"] is not None or rec["returned_rows"]!=0
                    or rec["persisted_rows"]!=0 or rec["first_observed_utc"] is not None
                    or rec["last_observed_utc"] is not None):
                    problems.append("failed attempt claims source observations")
                if not rec["error_class"] or (run and rec["error_class"]!=run[4]):
                    problems.append("failed attempt error not reconciled")
            else:
                if rec["status"]=="success":
                    report["success"]+=1
                else:
                    report["empty"]+=1
                if rec["error_class"] or (run and run[4]):
                    problems.append("successful response has error category")
                sha=rec["raw_sha256"]
                raw=db.execute(
                    "SELECT provider,endpoint,body FROM market_raw_payloads WHERE sha256=?",
                    (sha,)).fetchone()
                source={}
                if raw is None:
                    problems.append("raw response BLOB missing")
                else:
                    raw_provider,raw_endpoint,body=raw
                    if hashlib.sha256(body).hexdigest()!=sha:
                        problems.append("BLOB SHA256 mismatch")
                    if raw_provider!=provider or not raw_endpoint.endswith(rec["endpoint_path"]):
                        problems.append("raw provider/endpoint mismatch")
                    try:
                        source=expected_rows(provider,raw_endpoint,body)
                    except (ValueError,TypeError,KeyError,OverflowError,IndexError) as exc:
                        problems.append("cannot parse source BLOB: "+type(exc).__name__)
                native=[(k,v) for k,v in source.items() if k[0]==metric]
                if len(native)!=len(source):
                    problems.append("raw source contains other stream")
                if len(native)!=rec["returned_rows"]:
                    problems.append("returned count disagrees with source BLOB")
                stored=db.execute(
                    "SELECT observed_utc,interval_label,raw_sha256 FROM market_derivatives "
                    "WHERE provider=? AND symbol='BTCUSDT' AND metric=? AND raw_sha256=? "
                    "ORDER BY observed_utc",(provider,metric,sha)).fetchall()
                if len(stored)!=rec["persisted_rows"]:
                    problems.append("persisted count disagrees with rows linked to SHA")
                if rec["returned_rows"]!=rec["persisted_rows"]:
                    problems.append("source record count not fully persisted")
                wanted={(key[1],key[2]) for key,value in native}
                observed={(t,inter) for t,inter,stored_sha in stored}
                if wanted!=observed:
                    problems.append("source records and stored UTC keys differ")
                if stored:
                    first,last=stored[0][0],stored[-1][0]
                    if first!=rec["first_observed_utc"] or last!=rec["last_observed_utc"]:
                        problems.append("first/last UTC mismatch")
                    try:
                        newest_ms=int(_dt(last).timestamp()*1000)
                        if newest_ms>rec["requested_end_ms"]:
                            problems.append("source timestamp newer than query boundary")
                        if metric=="open_interest":
                            if newest_ms!=rec["requested_end_ms"]+1-300_000:
                                problems.append("five-minute page boundary mismatch")
                            stamps=[int(_dt(row[0]).timestamp()*1000) for row in stored]
                            if any(b-a!=300_000 for a,b in zip(stamps,stamps[1:])):
                                problems.append("internal 5m gap")
                            next_newer=db.execute(
                                "SELECT observed_utc FROM market_derivatives "
                                "WHERE provider=? AND symbol='BTCUSDT' AND metric=? "
                                "AND observed_utc>? ORDER BY observed_utc ASC LIMIT 1",
                                (provider,metric,last)).fetchone()
                            if next_newer and int(_dt(next_newer[0]).timestamp()*1000)!=newest_ms+300_000:
                                problems.append("gap at source page/SQLite boundary")
                    except (ValueError,TypeError,OverflowError):
                        problems.append("malformed stored page timestamps")
                    tally["oldest_utc"]=min(first,tally["oldest_utc"]) if tally["oldest_utc"] else first
                elif rec["first_observed_utc"] or rec["last_observed_utc"]:
                    problems.append("source-empty request has UTC range")
                if rec["status"]=="empty" and (rec["returned_rows"] or rec["persisted_rows"]):
                    problems.append("empty response unexpectedly contained rows")
                if rec["status"]=="success" and rec["persisted_rows"]<=0:
                    problems.append("success recorded with no persisted rows")
                if not problems:
                    tally["successful"]+=int(rec["status"]=="success")
                    tally["points"]+=rec["persisted_rows"]
                    tally["last_request_end_ms"]=rec["requested_end_ms"]
                    report["source_records_verified"]+=len(native)
                    report["observations_verified"]+=len(stored)
            if problems:
                for problem in problems:
                    report["issues"].append({
                       "request_index":i+1,"stream":stream,"problem":problem})
            else:
                report["requests_verified"]+=1
        # Check the current cursor against the oldest successful persisted point.
        for key,tally in report["by_stream"].items():
            if not tally["successful"]:
                continue
            provider,metric=key.split("_",1)
            oldest=db.execute(
                "SELECT MIN(observed_utc) FROM market_derivatives "
                "WHERE provider=? AND symbol='BTCUSDT' AND metric=?",
                (provider,metric)).fetchone()[0]
            cursor=db.execute(
                "SELECT cursor FROM market_source_cursors "
                "WHERE provider=? AND stream=?",
                (provider,"history_backward_v1_"+metric)).fetchone()
            if not cursor or int(cursor[0])!=int(_dt(oldest).timestamp()*1000)-1:
                report["issues"].append({
                    "stream":key,"problem":"current checkpoint differs from earliest stored UTC"})
    report["quality_state"]=(
        "REVIEW_REQUIRED" if report["issues"] else
        "NO_REQUEST_RECEIPTS_YET" if report["requests_total"]==0 else
        "PASS_REQUEST_LINEAGE")
    report["caveat"]="Only post-v2 requests have exact request-level records; historical completeness remains NOT_VERIFIED."
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db",type=Path,default=Path(DB_PATH))
    args=p.parse_args(argv)
    try:
        r=audit(args.db)
        print(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True))
        return 0 if r["quality_state"] in (
            "PASS_REQUEST_LINEAGE","NO_REQUEST_RECEIPTS_YET") else 1
    except (OSError,ValueError,TypeError,sqlite3.Error) as exc:
        print(json.dumps({"quality_state":"AUDIT_UNAVAILABLE",
                          "error_class":type(exc).__name__}))
        return 2

if __name__=="__main__":
    raise SystemExit(main())
