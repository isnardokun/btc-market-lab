#!/usr/bin/env python3
"""One-page, backward-only history pilot for BTCUSDT derivatives.

This does NOT promise complete history. Each explicit invocation requests
exactly ONE page, constrained to precede the oldest stored UTC observation.
Raw response, normalized rows, cursor and provider-run receipt are persisted
transactionally in the SAME existing SQLite. No network in PLAN mode.

Use only after a verified WAL-safe backup and independent operator approval.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import sqlite3
import sys
import uuid
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ingestion.config import DB_PATH
from ingestion.market_context_sources import download, SOURCE_URLS, parse_api_json
from storage.market_context import (
    installed, now_utc, raw_payload, store_derivative, get_cursor, save_cursor
)

CONFIG={
    ("binance","open_interest"):("/futures/data/openInterestHist",500,"5m"),
    ("binance","funding_settled"):("/fapi/v1/fundingRate",1000,"settlement"),
    ("bybit","open_interest"):("/v5/market/open-interest",200,"5m"),
    ("bybit","funding_settled"):("/v5/market/funding/history",200,"settlement"),
}
CURSOR_PREFIX="history_backward_v1_"

def ms_utc(value):
    t=dt.datetime.fromisoformat(str(value).replace("Z","+00:00"))
    if t.tzinfo is None:raise ValueError("UTC-aware source required")
    return int(t.astimezone(dt.timezone.utc).timestamp()*1000)

def _rows(provider,metric,body):
    payload=parse_api_json(body)
    if provider=="binance":
        if not isinstance(payload,list):
            raise ValueError("Binance history must be a list")
        observations=payload
    else:
        if not isinstance(payload,dict) or payload.get("retCode")!=0:
            raise ValueError("Bybit history nonzero retCode")
        info=payload.get("result")
        if not isinstance(info,dict) or info.get("category") not in (None,"linear") or info.get("symbol") not in (None,"BTCUSDT"):
            raise ValueError("Bybit history product mismatch")
        observations=info.get("list")
        if not isinstance(observations,list):
            raise ValueError("Bybit history list missing")
    parsed=[]
    seen=set()
    for item in observations:
        if not isinstance(item,dict) or item.get("symbol") not in (None,"BTCUSDT"):
            raise ValueError("Unexpected source instrument")
        if provider=="binance" and item.get("symbol")!="BTCUSDT" and metric=="funding_settled":
            raise ValueError("Binance funding source symbol absent")
        if provider=="binance":
            key="timestamp" if metric=="open_interest" else "fundingTime"
            value_key="sumOpenInterest" if metric=="open_interest" else "fundingRate"
        else:
            key="timestamp" if metric=="open_interest" else "fundingRateTimestamp"
            value_key="openInterest" if metric=="open_interest" else "fundingRate"
        stamp=int(item[key])
        if stamp in seen:
            raise ValueError("Duplicate source timestamp on one history page")
        seen.add(stamp)
        parsed.append((stamp,item[value_key],
                       item.get("sumOpenInterestValue") if provider=="binance" and metric=="open_interest" else None))
    return parsed

def collect_one_page(db,provider,metric,*,fetch=download):
    if (provider,metric) not in CONFIG:
        raise ValueError("Unknown history stream")
    if not installed(db):
        raise ValueError("Market schema not installed")
    earliest=db.execute(
        "SELECT MIN(observed_utc) FROM market_derivatives "
        "WHERE provider=? AND symbol='BTCUSDT' AND metric=?",
        (provider,metric)).fetchone()[0]
    if earliest is None:
        raise ValueError("No initial snapshot: pilot only extends an existing stream")
    initial_end=ms_utc(earliest)-1
    stream=CURSOR_PREFIX+metric
    old_cursor=get_cursor(db,provider,stream)
    if old_cursor is not None and int(old_cursor)!=initial_end:
        raise ValueError("Stored history checkpoint differs from oldest persisted observation")
    suffix,page_limit,interval=CONFIG[(provider,metric)]
    endpoint=SOURCE_URLS[provider]+suffix
    params={"symbol":"BTCUSDT","endTime":initial_end,"limit":page_limit}
    if provider=="binance" and metric=="open_interest":
        params["period"]="5m"
    elif provider=="bybit":
        params["category"]="linear"
        if metric=="open_interest":params["intervalTime"]="5min"
    body=fetch(endpoint,params)
    rows=_rows(provider,metric,body)
    if len(rows)>page_limit:
        raise ValueError("Provider returned more records than the approved single page limit")
    if any(stamp>initial_end or stamp<=0 for stamp,_,_ in rows):
        raise ValueError("Provider returned observation outside requested historical boundary")
    digest=raw_payload(db,provider,endpoint,body,"application/json")
    added=0
    for stamp,value,quote in rows:
        observed=dt.datetime.fromtimestamp(stamp/1000,dt.timezone.utc).isoformat()
        added+=int(store_derivative(
            db,provider=provider,symbol="BTCUSDT",metric=metric,
            observed_utc=observed,interval_label=interval,
            value=value,unit="BTC" if metric=="open_interest" else "fraction",
            endpoint=endpoint,sha=digest,quote_usd=quote))
    if added!=len(rows):
        raise ValueError("Unexpected row collision in backward-only history page")
    if rows:
        newest_checkpoint=min(stamp for stamp,_,_ in rows)-1
        save_cursor(db,provider,stream,str(newest_checkpoint))
    return {"provider":provider,"metric":metric,"status":"success" if rows else "empty",
            "requests":1,"points":added,"source_sha256":digest[:16],
            "first_utc":dt.datetime.fromtimestamp(min(stamp for stamp,_,_ in rows)/1000,dt.timezone.utc).isoformat() if rows else None,
            "last_utc":dt.datetime.fromtimestamp(max(stamp for stamp,_,_ in rows)/1000,dt.timezone.utc).isoformat() if rows else None,
            "cursor_advanced":bool(rows),
            "provider_limit_caveat":"Binance OI only latest about one month" if provider=="binance" and metric=="open_interest" else "Provider availability must be independently assessed"}

def execute(db,provider,metric,*,fetch=download):
    uid=uuid.uuid4().hex
    db.execute("INSERT INTO market_source_runs(run_id,provider,started_utc,status)"
               " VALUES(?,?,?,'running')",(uid,provider,now_utc()))
    db.commit()
    attempts=0
    def observed_fetch(url,params):
        nonlocal attempts
        attempts+=1
        return fetch(url,params)
    try:
        with db:
            data=collect_one_page(db,provider,metric,fetch=observed_fetch)
            db.execute(
                "UPDATE market_source_runs SET ended_utc=?,status=?,requests=?,points=? "
                "WHERE run_id=?",
                (now_utc(),data["status"],attempts,data["points"],uid))
        return data
    except Exception as exc:
        db.rollback()
        reason="HTTPError_"+str(exc.code) if isinstance(exc,HTTPError) else type(exc).__name__
        with db:
            db.execute(
                "UPDATE market_source_runs SET ended_utc=?,status='failed',requests=?,error_code=? "
                "WHERE run_id=?",(now_utc(),attempts,reason,uid))
        return {"provider":provider,"metric":metric,"status":"failed",
                "requests":attempts,"points":0,"error_code":reason}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--provider",choices=("binance","bybit"),required=True)
    p.add_argument("--metric",choices=("open_interest","funding_settled"),required=True)
    p.add_argument("--apply",action="store_true",help="Explicit one-page network+SQLite write")
    p.add_argument("--db",type=Path,default=Path(DB_PATH))
    args=p.parse_args(argv)
    if not args.apply:
        print("PLAN: one backward history page; no HTTP or SQLite writes")
        print("Source: "+args.provider+"/"+args.metric+"; requires approved backup")
        return 0
    if not args.db.is_file():
        print(json.dumps({"status":"blocked","error_class":"MissingProductionSQLite"}))
        return 2
    try:
        with sqlite3.connect(args.db,timeout=30) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=30000")
            result=execute(db,args.provider,args.metric)
            print(json.dumps(result,indent=2,sort_keys=True))
            return 0 if result["status"] in ("success","empty") else 1
    except (OSError,sqlite3.Error,ValueError) as exc:
        print(json.dumps({"status":"blocked","error_class":type(exc).__name__}))
        return 2

if __name__=="__main__":
    raise SystemExit(main())
