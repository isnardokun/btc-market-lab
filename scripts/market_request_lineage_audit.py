#!/usr/bin/env python3
"""Independent read-only audit of post-v2 request provenance and stored BTC data.

Supports BOTH backward and forward request receipts (v3 schema).
- Backward: endTime = MIN(observed_utc) - 1ms, one-way to older data
- Forward:  startTime = MAX(observed_utc) + 5min, one-way to newer data

Legacy v1 rows correctly have no fabricated request metadata.
No HTTP, SQLite writes or source-body output.
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
        "endpoint_path","requested_start_ms","requested_end_ms","requested_limit",
        "attempted_utc","ended_utc","http_attempts","status","error_class",
        "raw_sha256","returned_rows","persisted_rows","first_observed_utc",
        "last_observed_utc","direction")

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
            "requests_total":0,"requests_verified":0,
            "success":0,"empty":0,"failed":0,
            "backward_requests":0,"forward_requests":0,
            "source_records_verified":0,"observations_verified":0,
            "by_stream":{},"issues":[]}
    with sqlite3.connect(p.resolve().as_uri()+"?mode=ro",uri=True,timeout=30) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA foreign_keys=ON")
        tables={x[0] for x in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        required_tables={
            "market_request_lineage","market_source_runs",
            "market_raw_payloads","market_derivatives",
            "market_source_cursors","market_context_migrations"}
        if not required_tables <= tables:
            missing = required_tables - tables
            report["issues"].append(f"Required tables absent: {missing}")
            return report
        if not db.execute(
            "SELECT 1 FROM market_context_migrations WHERE version=2").fetchone():
            report["issues"].append("Schema migration version 2 missing")
            return report

        # Check v3 columns
        cols = {r[1] for r in db.execute("PRAGMA table_info(market_request_lineage)")}
        has_direction = "direction" in cols
        has_start_ms  = "requested_start_ms" in cols
        has_acquisitions = "market_request_acquisitions" in tables

        if db.execute("PRAGMA foreign_key_check").fetchone():
            report["issues"].append("Foreign key violation")

        col_list = ",".join(f for f in FIELDS if f in cols)
        receipts=[dict(zip([f for f in FIELDS if f in cols],r))
                  for r in db.execute(f"SELECT {col_list} FROM market_request_lineage ORDER BY rowid")]

        report["requests_total"]=len(receipts)

        for i,rec in enumerate(receipts):
            problems=[]
            provider=rec["provider"]
            metric=rec["stream"]
            stream=provider+"_"+metric
            direction = rec.get("direction", "backward")  # default for legacy v2 receipts
            tally=report["by_stream"].setdefault(stream,{
                "requests":0,"successful":0,"points":0,
                "backward_requests":0,"forward_requests":0,
                "oldest_utc":None,"newest_utc":None,
                "backward_cursor":None,"forward_cursor":None})
            tally["requests"]+=1
            if direction == "forward":
                report["forward_requests"]+=1
                tally["forward_requests"]+=1
            else:
                report["backward_requests"]+=1
                tally["backward_requests"]+=1

            # Direction check
            if has_direction and direction not in ("backward","forward"):
                problems.append("invalid direction value")

            # Basic field validations
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

            # start_ms only required for forward
            if direction == "forward":
                if rec.get("requested_start_ms") is None:
                    problems.append("forward request missing requested_start_ms")
                elif not isinstance(rec["requested_start_ms"], int):
                    problems.append("requested_start_ms must be integer")
            else:
                # backward: end_ms is the boundary
                if rec.get("requested_end_ms") is None or not isinstance(rec["requested_end_ms"], int):
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

            # Run reconciliation
            run=db.execute(
                "SELECT provider,status,requests,points,error_code "
                "FROM market_source_runs WHERE run_id=?",(rec["run_id"],)).fetchone()
            if run is None:
                problems.append("source-run receipt missing")
            elif (run[0]!=provider or run[1]!=rec["status"]
                  or run[2]!=rec["http_attempts"]
                  or run[3]!=rec["persisted_rows"]):
                problems.append("source-run status or counts disagreement")

            # Acquisition record check (v3 forward only — not required for legacy v2)
            if rec["status"] in ("success","empty") and has_acquisitions and direction == "forward":
                acq=db.execute(
                    "SELECT request_id,raw_sha256,provider,endpoint_path,acquired_utc "
                    "FROM market_request_acquisitions WHERE request_id=?",
                    (rec["request_id"],)).fetchone()
                if acq is None:
                    problems.append("acquisition record missing for successful/empty request")
                else:
                    acq_req_id,acq_sha,acq_prov,acq_path,acq_ts = acq
                    if acq_sha != rec["raw_sha256"]:
                        problems.append("acquisition SHA mismatch")
                    if acq_prov != provider:
                        problems.append("acquisition provider mismatch")
                    if not acq_path.endswith(rec["endpoint_path"]):
                        problems.append("acquisition endpoint path mismatch")

            # Failed request checks
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
                    if not raw_endpoint.endswith(rec["endpoint_path"]):
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

                    # Direction-specific temporal validation
                    try:
                        first_ms=int(_dt(first).timestamp()*1000)
                        last_ms=int(_dt(last).timestamp()*1000)

                        if direction == "forward":
                            # Forward: first timestamp must match requested_start_ms
                            start_ms = rec.get("requested_start_ms")
                            if start_ms is not None and first_ms != start_ms:
                                problems.append(f"forward first timestamp {first_ms} != requested_start_ms {start_ms}")
                            # All must be 5min apart ascending
                            stamps=[int(_dt(row[0]).timestamp()*1000) for row in stored]
                            for a,b in zip(stamps,stamps[1:]):
                                if b-a != 300_000:
                                    problems.append(f"forward internal gap: {a}->{b}")
                            # last must be <= requested_end_ms
                            if rec.get("requested_end_ms") is not None:
                                if last_ms > rec["requested_end_ms"]:
                                    problems.append("forward last timestamp exceeds requested_end_ms")
                            # Newest must be >= oldest
                            if first_ms > last_ms:
                                problems.append("forward timestamps not ascending")
                        else:
                            # Backward: newest_ms must be == requested_end_ms + 1 - 300_000
                            newest_ms = last_ms  # for backward, last=most-recent
                            end_ms = rec.get("requested_end_ms")
                            if end_ms is not None:
                                if newest_ms != end_ms + 1 - 300_000:
                                    problems.append(f"backward newest {newest_ms} != end_ms+1-300000 ({end_ms+1-300_000})")
                            stamps=[int(_dt(row[0]).timestamp()*1000) for row in stored]
                            if any(b-a!=300_000 for a,b in zip(stamps,stamps[1:])):
                                problems.append("backward internal 5m gap")
                            next_newer=db.execute(
                                "SELECT observed_utc FROM market_derivatives "
                                "WHERE provider=? AND symbol='BTCUSDT' AND metric=? "
                                "AND observed_utc>? ORDER BY observed_utc ASC LIMIT 1",
                                (provider,metric,last)).fetchone()
                            if next_newer and int(_dt(next_newer[0]).timestamp()*1000)!=last_ms+300_000:
                                problems.append("gap at backward page/SQLite boundary")

                        tally["oldest_utc"]=min(first,tally["oldest_utc"]) if tally["oldest_utc"] else first
                        tally["newest_utc"]=max(last,tally["newest_utc"]) if tally["newest_utc"] else last

                    except (ValueError,TypeError,OverflowError):
                        problems.append("malformed stored page timestamps")

                elif rec["first_observed_utc"] or rec["last_observed_utc"]:
                    problems.append("source-empty request has UTC range")

                if rec["status"]=="empty" and (rec["returned_rows"] or rec["persisted_rows"]):
                    problems.append("empty response unexpectedly contained rows")
                if rec["status"]=="success" and rec["persisted_rows"]<=0:
                    problems.append("success recorded with no persisted rows")

                if not problems:
                    tally["successful"]+=int(rec["status"]=="success")
                    tally["points"]+=rec["persisted_rows"]
                    report["source_records_verified"]+=len(native)
                    report["observations_verified"]+=len(stored)

            if problems:
                for problem in problems:
                    report["issues"].append({
                       "request_index":i+1,"stream":stream,"direction":direction,"problem":problem})
            else:
                report["requests_verified"]+=1

        # Cursor checks (both namespaces)
        for key,tally in report["by_stream"].items():
            provider,metric=key.split("_",1)
            # Backward cursor
            bw_cursor=db.execute(
                "SELECT cursor FROM market_source_cursors "
                "WHERE provider=? AND stream=?",
                (provider,"history_backward_v1_"+metric)).fetchone()
            if bw_cursor:
                tally["backward_cursor"]=bw_cursor[0]
            # Forward cursor
            fw_cursor=db.execute(
                "SELECT cursor FROM market_source_cursors "
                "WHERE provider=? AND stream=?",
                (provider,"forward_v1_"+metric)).fetchone()
            if fw_cursor:
                tally["forward_cursor"]=fw_cursor[0]

            # Verify backward cursor = MIN(observed_utc) - 1ms
            if tally.get("oldest_utc") and bw_cursor:
                expected=int(_dt(tally["oldest_utc"]).timestamp()*1000)-1
                if int(bw_cursor[0])!=expected:
                    report["issues"].append({
                        "stream":key,"problem":f"backward cursor {bw_cursor[0]} != MIN(utc)-1ms ({expected})"})

            # Verify forward cursor = MAX(observed_utc) (for forward stream)
            if tally.get("newest_utc") and fw_cursor:
                expected=int(_dt(tally["newest_utc"]).timestamp()*1000)
                if int(fw_cursor[0])!=expected:
                    report["issues"].append({
                        "stream":key,"problem":f"forward cursor {fw_cursor[0]} != MAX(utc) ({expected})"})

    report["quality_state"]=(
        "REVIEW_REQUIRED" if report["issues"] else
        "NO_REQUEST_RECEIPTS_YET" if report["requests_total"]==0 else
        "PASS_REQUEST_LINEAGE")
    report["caveat"]=(
        "Backward receipts: newest_ms == requested_end_ms+1-300_000. "
        "Forward receipts: first_ms == requested_start_ms, all ascending 5min. "
        "Acquisition records verified for v3 successful/empty requests.")
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
