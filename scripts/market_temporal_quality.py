#!/usr/bin/env python3
"""Read-only diagnostics for coverage, temporal cadence and original raw payloads.

Measures the extent of *observed* coverage, without claiming that a short
provider snapshot is a complete history. Funding cadence is exchange-specific,
so gaps are descriptive rather than blindly enforcing an 8h schedule.
Only the existing SQLite is read; no HTTP, writes, CSV/JSON sidecars or keys.
"""
import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ingestion.config import DB_PATH
from scripts.market_raw_reconcile import STREAMS, expected_rows

def _utc(text):
    instant=dt.datetime.fromisoformat(str(text).replace("Z","+00:00"))
    if instant.tzinfo is None:
        raise ValueError("timestamp lacks timezone")
    return instant.astimezone(dt.timezone.utc)

def inspect(db_path, *, now=None):
    p=Path(db_path)
    if not p.is_file():
        raise FileNotFoundError("Existing SQLite required; never create a new DB")
    now=now or dt.datetime.now(dt.timezone.utc)
    now=_utc(now.isoformat())
    report={
        "audit_utc":now.isoformat(timespec="seconds"),
        "classification":"INITIAL_SNAPSHOT_NOT_COMPLETE_HISTORY",
        "streams":{},
        "original_payloads":{},
        "source_rows_unstored":[],
        "issues":[],
        "limits":{
            "binance_oi":"Provider limits statistics to approximately one month",
            "funding":"Settlement interval may vary by exchange and contract",
            "bybit_oi":"Must explicitly traverse API cursor for historical coverage",
            "etf_calendar":"Not checked here; requires independent provider ingest",
        },
    }
    with sqlite3.connect(p.resolve().as_uri()+"?mode=ro",uri=True,timeout=30) as db:
        db.execute("PRAGMA query_only=ON")
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"market_derivatives","market_raw_payloads","market_source_runs"}<=tables:
            report["issues"].append("Required market tables missing")
            report["classification"]="NOT_DEPLOYED"
            return report
        original={}
        raw_counts=Counter()
        row_keys={}
        for sha,provider,endpoint,body in db.execute(
            "SELECT sha256,provider,endpoint,body FROM market_raw_payloads "
            "WHERE provider IN ('binance','bybit')"):
            if hashlib.sha256(body).hexdigest()!=sha:
                report["issues"].append("SHA256 source body mismatch "+sha[:12])
                continue
            try:
                expected=expected_rows(provider,endpoint,body)
            except (ValueError,KeyError,TypeError,OverflowError,IndexError) as exc:
                report["issues"].append("Bad source body "+sha[:12]+":"+type(exc).__name__)
                continue
            original[sha]=(provider,endpoint,expected)
            for metric,stamp,interval in expected:
                raw_counts[provider+"_"+metric]+=1
            report["original_payloads"][sha[:16]]={
                "provider":provider,
                "endpoint_path":endpoint.split("/",3)[-1],
                "source_observations":len(expected),
                "bytes":len(body),
                "checksum":"OK",
            }
        for (provider,metric),endpoint_suffix in STREAMS.items():
            key=provider+"_"+metric
            points=db.execute(
                "SELECT observed_utc,raw_value,raw_unit,raw_sha256,interval_label "
                "FROM market_derivatives WHERE provider=? AND symbol='BTCUSDT' AND metric=? "
                "ORDER BY observed_utc",(provider,metric)).fetchall()
            observed=[]
            units=set()
            for stamp,value,unit,sha,interval in points:
                try:timestamp=_utc(stamp)
                except (TypeError,ValueError):
                    report["issues"].append(key+": malformed observation UTC")
                    continue
                units.add(unit)
                observed.append(timestamp)
                row_keys[(provider,metric,stamp,interval)]=sha
            intervals=[int((b-a).total_seconds()/60)
                       for a,b in zip(observed,observed[1:])]
            frequencies=Counter(intervals)
            duplicate_count=sum(v>1 for v in Counter(observed).values())
            if duplicate_count:
                report["issues"].append(key+": duplicate UTC timestamps")
            if any(v<=0 for v in intervals):
                report["issues"].append(key+": non-increasing chronology")
            if any(t>now+dt.timedelta(minutes=10) for t in observed):
                report["issues"].append(key+": observation dated beyond 10m into future")
            gap_ranges=[]
            missing_5m=0
            if metric=="open_interest":
                for a,b,delta in zip(observed,observed[1:],intervals):
                    if delta>5:
                        missed=max(0,delta//5-1)
                        missing_5m+=missed
                        if len(gap_ranges)<8:
                            gap_ranges.append({"after_utc":a.isoformat(timespec="seconds"),
                                               "before_utc":b.isoformat(timespec="seconds"),
                                               "minutes":delta,"missing_slots_at_5m":missed})
                    if delta!=5 and delta>0 and delta<5:
                        report["issues"].append(key+": unexpected sub-5min spacing")
                if observed and not all(t.minute%5==0 and t.second==0 for t in observed):
                    report["issues"].append(key+": timestamps not aligned to 5m boundaries")
            report["streams"][key]={
                "rows":len(points),"valid_timestamps":len(observed),
                "first_utc":observed[0].isoformat(timespec="seconds") if observed else None,
                "last_utc":observed[-1].isoformat(timespec="seconds") if observed else None,
                "span_hours":round((observed[-1]-observed[0]).total_seconds()/3600,2) if len(observed)>1 else 0,
                "age_of_last_minutes":round((now-observed[-1]).total_seconds()/60,1) if observed else None,
                "raw_units":sorted(units),
                "delta_minutes_histogram":dict(sorted(frequencies.items(),key=lambda p:-p[1])[:10]),
                "five_minute_gaps_inside_observed_window":missing_5m if metric=="open_interest" else None,
                "first_eight_gaps":gap_ranges,
                "duplicate_timestamps":duplicate_count,
                "raw_rows_across_archived_payloads":raw_counts[key],
                "funding_interval_policy":"DESCRIPTIVE_ONLY_CHECK_INSTRUMENTS_INFO" if metric=="funding_settled" else None,
            }
            if not observed:report["issues"].append(key+": no data")
        missing=Counter()
        replaced=Counter()
        for sha,(provider,endpoint,expected) in original.items():
            for metric,stamp,interval in expected:
                key=(provider,metric,stamp,interval)
                linked=row_keys.get(key)
                if linked is None:
                    missing[provider+"_"+metric]+=1
                elif linked!=sha:
                    replaced[provider+"_"+metric]+=1
        report["source_rows_unstored"]=[{"stream":key,"count":val}
                                       for key,val in sorted(missing.items())]
        report["source_rows_superseded_sha"]=[{"stream":key,"count":val}
                                            for key,val in sorted(replaced.items())]
        if missing:
            report["issues"].append("Archived source includes native rows without stored observation")
        report["source_run_status"]=[
            {"provider":p,"status":status,"runs":n}
            for p,status,n in db.execute("SELECT provider,status,COUNT(*) "
               "FROM market_source_runs GROUP BY provider,status ORDER BY provider,status")
        ]
    report["quality_state"]="REVIEW_REQUIRED" if report["issues"] else "SNAPSHOT_INTERNAL_QA_OK"
    report["historical_completeness"]="NOT_VERIFIED"
    report["caveat"]="Internal consistency only; missing API coverage outside captured period is not measurable from these snapshots."
    return report

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db",type=Path,default=Path(DB_PATH))
    args=p.parse_args(argv)
    try:
        result=inspect(args.db)
        print(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True))
        return 0 if result.get("quality_state")=="SNAPSHOT_INTERNAL_QA_OK" else 1
    except (OSError,ValueError,sqlite3.Error) as exc:
        print(json.dumps({"quality_state":"AUDIT_UNAVAILABLE","error_class":type(exc).__name__}))
        return 2

if __name__=="__main__":
    raise SystemExit(main())
