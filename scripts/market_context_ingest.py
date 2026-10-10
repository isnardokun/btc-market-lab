#!/usr/bin/env python3
"""Persist bounded third-party BTC/ETF/macro data into ONE existing SQLite.

Dry-run by default; --apply writes ONLY to btc_research.db. Zero external
network traffic without --apply. A provider failure never disguises missing
market data and does not erase previously verified observations.
"""
import argparse
import datetime as dt
from pathlib import Path
import os
import sqlite3
import sys
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ingestion.config import DB_PATH
from ingestion.market_context_sources import (
    download,fetch_binance,fetch_bybit,fetch_farside,fetch_bls,fetch_fred)
from urllib.error import HTTPError
from storage.market_context import installed,now_utc

SOURCES=("binance","bybit","farside","bls","fred")

def run(db,source,*,history=False,max_pages=2,fred_api_key=None):
    if source not in SOURCES:raise ValueError("Unknown market source")
    uid=uuid.uuid4().hex
    db.execute("INSERT INTO market_source_runs(run_id,provider,started_utc,status)"
               " VALUES(?,?,?,'running')",(uid,source,now_utc()))
    db.commit()
    attempts = [0]
    def observed_fetch(url,params=None):
        # Count outbound attempts even if HTTP fails before delivering a body.
        # No URL, API keys or response bodies are logged here.
        attempts[0] += 1
        return download(url,params)
    calls={
      "binance":lambda:fetch_binance(db,history=history,max_pages=max_pages,fetch=observed_fetch),
      "bybit":lambda:fetch_bybit(db,max_pages=max_pages,history=history,fetch=observed_fetch),
      "farside":lambda:fetch_farside(db,fetch=observed_fetch),
      "bls":lambda:fetch_bls(db,fetch=observed_fetch),
      "fred":lambda:fetch_fred(db,api_key=fred_api_key,fetch=observed_fetch),
    }
    try:
        with db:
            count,points=calls[source]()
            if count != attempts[0]:
                raise RuntimeError("Provider request accounting mismatch")
            status="success" if count else "empty"
            db.execute("UPDATE market_source_runs SET ended_utc=?,status=?,requests=?,"
                       "points=? WHERE run_id=?",
                       (now_utc(),status,count,points,uid))
        return status,count,points
    except Exception as exc:
        db.rollback()
        # Persist only safe HTTP status / exception class; do not expose
        # response bodies, request URLs (FRED token), host secrets or traces.
        safe_error = ("HTTPError_" + str(exc.code) if isinstance(exc, HTTPError)
                      else type(exc).__name__)
        with db:
            db.execute("UPDATE market_source_runs SET ended_utc=?,status='failed',"
                       "requests=?,error_code=? WHERE run_id=?",
                       (now_utc(),attempts[0],safe_error,uid))
        return "failed",attempts[0],0

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply",action="store_true",help="Explicitly fetch and persist into SQLite")
    p.add_argument("--sources",default=",".join(SOURCES),
                   help="Comma-separated sources: "+",".join(SOURCES))
    p.add_argument("--max-pages",type=int,default=2,help="Maximum pages per derivatives endpoint")
    p.add_argument("--history",action="store_true",
                   help="Start Binance funding at earliest configured year; capped by --max-pages")
    p.add_argument("--db",type=Path,default=Path(DB_PATH))
    args=p.parse_args(argv)
    providers=[x.strip() for x in args.sources.split(",")]
    if not providers or len(set(providers))!=len(providers) or any(x not in SOURCES for x in providers):
        p.error("Invalid provider selection")
    if not 1<=args.max_pages<=10:p.error("--max-pages outside [1,10]")
    if not args.apply:
        print("PLAN only: no HTTP, no SQLite writes. Sources="+",".join(providers))
        print("Run SQLite migration with backup first; then --apply.")
        return 0
    if not args.db.is_file():
        print("STOP: existing btc_research.db required",file=sys.stderr);return 2
    try:
        with sqlite3.connect(args.db,timeout=30) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=30000")
            if not installed(db):
                print("STOP: market context schema absent; run scripts/market_context_migrate.py --apply",file=sys.stderr)
                return 2
            failures=0
            for src in providers:
                state,requests,points=run(db,src,history=args.history,
                             max_pages=args.max_pages,fred_api_key=os.environ.get("FRED_API_KEY"))
                print(src+": "+state+"; requests="+str(requests)+"; changed="+str(points))
                failures+=state=="failed"
            return 1 if failures else 0
    except (OSError,sqlite3.Error) as exc:
        print("STOP: SQLite error: "+type(exc).__name__,file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
