#!/usr/bin/env python3
"""Independent read-only reconciliation of derivatives against archived API bytes.

Every stored derivative row is compared to its own SHA256-verified original
response BLOB in the SAME existing SQLite. No network, file writes, new DB,
or trust in the provider-run status. This proves storage/transformation
fidelity, NOT that an external provider's market estimate was accurate.
"""
import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ingestion.config import DB_PATH

STREAMS = {
 ("binance","open_interest"): "/futures/data/openInterestHist",
 ("binance","funding_settled"): "/fapi/v1/fundingRate",
 ("bybit","open_interest"): "/v5/market/open-interest",
 ("bybit","funding_settled"): "/v5/market/funding/history",
}

def stamp_ms(value):
    return dt.datetime.fromtimestamp(
        int(value)/1000, dt.timezone.utc
    ).isoformat(timespec="seconds")

def equivalent(a,b):
    try:
        a=Decimal(str(a))
        b=Decimal(str(b))
        return a.is_finite() and b.is_finite() and abs(a-b)<=max(
            Decimal("0.000000000001"),abs(a)*Decimal("0.0000000001"))
    except (InvalidOperation,TypeError,ValueError):
        return False

def expected_rows(provider,endpoint,body):
    """Index exact native fields by metric, UTC timestamp and interval."""
    data=json.loads(body)
    if provider=="binance":
        if not isinstance(data,list):
            raise ValueError("Binance body not list")
        metric = ("open_interest" if endpoint.endswith(STREAMS[(provider,"open_interest")])
                  else "funding_settled" if endpoint.endswith(STREAMS[(provider,"funding_settled")])
                  else None)
        if metric is None:raise ValueError("Unexpected Binance endpoint")
        result=[]
        for item in data:
            if item.get("symbol") not in (None,"BTCUSDT"):
                raise ValueError("Binance response symbol mismatch")
            if metric=="open_interest":
                t=stamp_ms(item["timestamp"])
                result.append((metric,t,"5m",item["sumOpenInterest"],"BTC",
                               item.get("sumOpenInterestValue")))
            else:
                if item.get("symbol")!="BTCUSDT":
                    raise ValueError("Binance funding symbol mismatch")
                t=stamp_ms(item["fundingTime"])
                result.append((metric,t,"settlement",item["fundingRate"],"fraction",None))
    else:
        if provider!="bybit" or not isinstance(data,dict) or data.get("retCode")!=0:
            raise ValueError("Bybit malformed provider response")
        payload=data.get("result")
        if not isinstance(payload,dict) or payload.get("symbol") not in (None,"BTCUSDT") or payload.get("category") not in (None,"linear"):
            raise ValueError("Bybit category or symbol mismatch")
        rows=payload.get("list")
        if not isinstance(rows,list):
            raise ValueError("Bybit list missing")
        metric=("open_interest" if endpoint.endswith(STREAMS[(provider,"open_interest")])
                else "funding_settled" if endpoint.endswith(STREAMS[(provider,"funding_settled")])
                else None)
        if metric is None:raise ValueError("Unexpected Bybit endpoint")
        result=[]
        for item in rows:
            if item.get("symbol") not in (None,"BTCUSDT"):
                raise ValueError("Bybit row symbol mismatch")
            if metric=="open_interest":
                t=stamp_ms(item["timestamp"])
                result.append((metric,t,"5m",item["openInterest"],"BTC",None))
            else:
                t=stamp_ms(item["fundingRateTimestamp"])
                result.append((metric,t,"settlement",item["fundingRate"],"fraction",None))
    indexed={}
    for metric,t,interval,value,unit,quote in result:
        key=(metric,t,interval)
        if key in indexed:
            raise ValueError("Duplicate timestamp within single API body")
        indexed[key]=(value,unit,quote)
    return indexed

def audit(path, *, max_issues=12):
    p=Path(path)
    if not p.is_file():
        raise FileNotFoundError("Existing SQLite required")
    errors=[]
    counts={p+"_"+m:{"rows":0,"verified":0} for p,m in STREAMS}
    checked_blobs=0
    indexed={}
    with sqlite3.connect(p.resolve().as_uri()+"?mode=ro",uri=True,timeout=30) as db:
        db.execute("PRAGMA query_only=ON")
        tables={r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"market_derivatives","market_raw_payloads"}<=tables:
            return {"state":"INCOMPLETE","reason":"market tables missing","counts":counts}
        payloads=db.execute(
            "SELECT sha256,provider,endpoint,body FROM market_raw_payloads "
            "WHERE provider IN ('binance','bybit')").fetchall()
        for sha,provider,endpoint,body in payloads:
            checked_blobs+=1
            if hashlib.sha256(body).hexdigest()!=sha:
                errors.append("raw sha256 mismatch "+sha[:12])
                continue
            try:
                indexed[sha]=expected_rows(provider,endpoint,body)
            except (ValueError,TypeError,KeyError,OverflowError,IndexError,
                    json.JSONDecodeError) as exc:
                errors.append("invalid source body "+sha[:12]+" "+type(exc).__name__)
        observations=db.execute(
            "SELECT provider,symbol,metric,observed_utc,interval_label,raw_value,"
            "raw_unit,quote_usd,raw_sha256,source_endpoint "
            "FROM market_derivatives WHERE provider IN ('binance','bybit') "
            "ORDER BY provider,metric,observed_utc").fetchall()
        stored_keys = {
            (provider, metric, at, interval)
            for provider, symbol, metric, at, interval, value, unit, quote, sha, endpoint
            in observations if symbol == "BTCUSDT"
        }
        # Reconciliation MUST be bidirectional: checking only SQLite -> RAW
        # incorrectly PASSED if an archived provider observation was deleted.
        # Superseded payloads may reference an existing observation now tied
        # to another SHA; do not misclassify those as missing.
        for sha, provider, endpoint, body in payloads:
            for metric, stamp, interval in indexed.get(sha, {}):
                if (provider, metric, stamp, interval) not in stored_keys:
                    errors.append(
                        "archived source observation missing from SQLite "
                        + provider + "_" + metric + " " + stamp
                    )
        for provider,symbol,metric,at,interval,value,unit,quote,sha,endpoint in observations:
            key=provider+"_"+metric
            if key not in counts:
                errors.append("unknown derivative stream")
                continue
            counts[key]["rows"]+=1
            expected_path=STREAMS.get((provider,metric))
            if symbol!="BTCUSDT" or not endpoint.endswith(expected_path or "#invalid#"):
                errors.append("symbol/endpoint mismatch "+key+" "+str(at))
                continue
            native=indexed.get(sha,{}).get((metric,at,interval))
            if native is None:
                errors.append("missing source row "+key+" "+str(at))
                continue
            original_value,original_unit,original_quote=native
            if unit!=original_unit or not equivalent(value,original_value):
                errors.append("value/unit mismatch "+key+" "+str(at))
                continue
            if original_quote is None and quote is not None:
                errors.append("invented quote "+key+" "+str(at))
                continue
            if original_quote is not None and (
                quote is None or not equivalent(quote,original_quote)):
                errors.append("quote mismatch "+key+" "+str(at))
                continue
            counts[key]["verified"]+=1
    absent=[name for name,v in counts.items() if v["rows"]==0]
    for stream in absent:
        errors.append("no observations "+stream)
    return {
        "state": "REJECTED" if errors else "PASS_SQLITE_TO_RAW",
        "rows":sum(v["rows"] for v in counts.values()),
        "verified":sum(v["verified"] for v in counts.values()),
        "blob_sha256_verified":checked_blobs-len([e for e in errors if e.startswith("raw sha256 mismatch")]),
        "blobs_seen":checked_blobs,
        "streams":counts,
        "issues_count":len(errors),
        "issues":errors[:max_issues],
        "caveat":"Checks archived response fidelity, not external exchange truth or fiat USD quote unit.",
    }

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db",type=Path,default=Path(DB_PATH))
    args=p.parse_args(argv)
    try:
        outcome=audit(args.db)
        print(json.dumps(outcome,ensure_ascii=False,indent=2,sort_keys=True))
        return 0 if outcome["state"]=="PASS_SQLITE_TO_RAW" else 1
    except (OSError,sqlite3.Error,ValueError) as exc:
        print(json.dumps({"state":"AUDIT_UNAVAILABLE","error_class":type(exc).__name__}))
        return 2

if __name__=="__main__":
    raise SystemExit(main())
