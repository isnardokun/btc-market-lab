#!/usr/bin/env python3
"""Exactly one optional Binance historical-OI API probe, never an ingestion.

PLAN by default; --probe reads existing BTC market SQLite in query-only mode,
derives the immediately preceding 5m timestamp, and performs at most one
GET /futures/data/openInterestHist with limit=1. No HTTP retries,
redirections, bypass, SQLite writes, second data store or raw body logging.
This checks endpoint access and one timestamp; NOT full coverage.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ingestion.config import DB_PATH
from ingestion.market_network_errors import error_category
from scripts.binance_https_probe import transport
from storage.market_context import get_cursor

HOST="fapi.binance.com"
PATH="/futures/data/openInterestHist"
URL="https://"+HOST+PATH
CADENCE_MS=300_000
CURSOR_STREAM="history_backward_v1_open_interest"
MAX_BODY=16384


def _ms(value):
    t=dt.datetime.fromisoformat(str(value).replace("Z","+00:00"))
    if t.tzinfo is None:
        raise ValueError("Source time missing timezone")
    return int(t.astimezone(dt.timezone.utc).timestamp()*1000)


def read_boundary(path):
    """Fail closed if the existing history checkpoint and oldest OI differ."""
    p=Path(path)
    if not p.is_file():
        raise FileNotFoundError("Existing SQLite required")
    with sqlite3.connect(p.resolve().as_uri()+"?mode=ro",uri=True,timeout=15) as db:
        db.execute("PRAGMA query_only=ON")
        oldest=db.execute(
            "SELECT MIN(observed_utc) FROM market_derivatives "
            "WHERE provider='binance' AND symbol='BTCUSDT' "
            "AND metric='open_interest'").fetchone()[0]
        if oldest is None:
            raise ValueError("No existing Binance OI observations")
        end_ms=_ms(oldest)-1
        cursor=get_cursor(db,"binance",CURSOR_STREAM)
        if cursor is None or int(cursor)!=end_ms:
            raise ValueError("Historical cursor mismatches earliest SQLite observation")
        return oldest,end_ms


def parse_one(body,end_ms):
    if not isinstance(body,bytes) or not 0<len(body)<=MAX_BODY:
        return "INVALID_BODY",None
    try:
        data=json.loads(body)
    except (UnicodeError,ValueError):
        return "MALFORMED_JSON",None
    if not isinstance(data,list) or len(data)>1:
        return "UNEXPECTED_LIST",None
    if not data:
        return "EMPTY_HISTORY",None
    row=data[0]
    if not isinstance(row,dict) or row.get("symbol") not in ("BTCUSDT",None):
        return "SYMBOL_MISMATCH",None
    try:
        ts=int(row["timestamp"])
        from decimal import Decimal
        quantity=Decimal(str(row["sumOpenInterest"]))
        if not quantity.is_finite() or quantity<0:
            return "INVALID_VALUE",None
    except (KeyError,TypeError,ValueError,ArithmeticError):
        return "INVALID_RECORD",None
    if ts != end_ms+1-CADENCE_MS:
        return "BOUNDARY_MISMATCH",ts
    return "MATCH_PREVIOUS_5M",ts


def check(db_path, *,open_request=transport):
    report={
        "utc":dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "host":HOST,"endpoint_path":PATH,
        "request_type":"ONE_DIAGNOSTIC_HISTORIC_OI_GET",
        "symbol":"BTCUSDT","period":"5m","limit":1,
        "http_attempts":0,"redirects_followed":0,"sqlite_writes":0,
        "observations_persisted":0,"raw_payload_persisted":False,
        "http_status":None,"error_class":None,"outcome":"BLOCKED",
        "historical_completeness":"NOT_VERIFIED",
    }
    try:
        oldest,end_ms=read_boundary(db_path)
    except (OSError,sqlite3.Error,ValueError,TypeError) as exc:
        report["error_class"]=type(exc).__name__
        return report
    report["oldest_existing_utc"]=oldest
    report["requested_endTime_ms"]=end_ms
    report["expected_previous_utc"]=dt.datetime.fromtimestamp(
        (end_ms+1-CADENCE_MS)/1000,dt.timezone.utc).isoformat(timespec="seconds")
    params={"symbol":"BTCUSDT","period":"5m","endTime":end_ms,"limit":1}
    request=Request(
        URL+"?"+urlencode(params),
        headers={"User-Agent":"BTCMarketLab/2.0 single OI diagnostic",
                 "Accept":"application/json","Connection":"close"},
        method="GET")
    began=time.monotonic()
    report["http_attempts"]=1
    try:
        # Reuse strict no-redirect, standard system-CA transport from ping.
        with open_request(request,timeout=8) as response:
            status=response.getcode()
            report["http_status"]=int(status)
            if status!=200:
                report["error_class"]="UnexpectedHTTPStatus"
                report["outcome"]="FAILED"
            else:
                body=response.read(MAX_BODY+1)
                result,stamp=parse_one(body,end_ms)
                report["response_contract"]=result
                report["received_records"]=1 if stamp is not None else 0
                report["raw_body_sha256_prefix"]=hashlib.sha256(body).hexdigest()[:16]
                report["outcome"]="PASS_OI_ENDPOINT_AND_BOUNDARY" if result=="MATCH_PREVIOUS_5M" else "FAILED"
                if stamp is not None:
                    report["response_observed_utc"]=dt.datetime.fromtimestamp(
                        stamp/1000,dt.timezone.utc).isoformat(timespec="seconds")
    except HTTPError as exc:
        report["http_status"]=int(exc.code)
        report["error_class"]=error_category(exc)
        report["outcome"]="FAILED"
    except (URLError,OSError,ValueError) as exc:
        report["error_class"]=error_category(exc)
        report["outcome"]="FAILED"
    finally:
        report["elapsed_ms"]=int((time.monotonic()-began)*1000)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db",type=Path,default=Path(DB_PATH))
    p.add_argument("--probe",action="store_true",
                   help="Authorize exactly one Binance historical OI GET")
    args=p.parse_args(argv)
    if not args.probe:
        print(json.dumps({"outcome":"PLAN_ONLY","host":HOST,"endpoint_path":PATH,
                          "period":"5m","limit":1,"http_attempts":0,
                          "sqlite_writes":0,"requires":"--probe"},sort_keys=True))
        return 0
    report=check(args.db)
    print(json.dumps(report,indent=2,sort_keys=True))
    return 0 if report["outcome"]=="PASS_OI_ENDPOINT_AND_BOUNDARY" else 1


if __name__=="__main__":
    raise SystemExit(main())
