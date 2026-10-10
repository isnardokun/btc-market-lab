#!/usr/bin/env python3



"""



Forward-only incremental OI pilot for BTCUSDT — one page, BTC-only.







Direction: forward (startTime >= MAX(observed_utc) + 300000ms)



This is NOT a backward historical collector — it extends from the newest



stored bar toward the present, respecting closed-bar cutoff.







PLAN mode (default): validates inputs and prints plan without network or writes.



--apply: executes one page and persists everything transactionally.







Requires v3 schema (direction column in market_request_lineage).



Requires market_request_acquisitions table for SHA collision safety.







Usage:



  python3 scripts/market_history_forward.py --provider bybit --metric open_interest



  python3 scripts/market_history_forward.py --provider bybit --metric open_interest --apply



"""



import argparse



import datetime as dt



import json



import math



import sqlite3



import sys



import uuid



from pathlib import Path







ROOT = Path(__file__).resolve().parents[1]



sys.path.insert(0, str(ROOT))



from ingestion.market_network_errors import error_category



from ingestion.config import DB_PATH



from ingestion.market_context_sources import download, SOURCE_URLS, parse_api_json



from storage.market_context import (



    installed, now_utc, raw_payload, store_derivative, get_cursor, save_cursor,



    request_lineage_installed, write_request_lineage



)







# ── Forward-only configuration ────────────────────────────────────────────────







CONFIG = {



    ("binance",  "open_interest"):  ("/futures/data/openInterestHist",  500, "5m"),



    ("binance",  "funding_settled"): ("/fapi/v1/fundingRate",             1000, "settlement"),



    ("bybit",    "open_interest"):  ("/v5/market/open-interest",         200, "5m"),



    ("bybit",    "funding_settled"):("/v5/market/funding/history",         200, "settlement"),



}



CURSOR_PREFIX = "forward_v1_"   # separate namespace from backward cursors



CUTOFF_MARGIN_MS = 10 * 60 * 1000  # 10-minute safety margin for closed bars



INTERVAL_5M_MS  = 5 * 60 * 1000    # 300_000 ms











def ms_from_utc(value: str) -> int:



    t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))



    if t.tzinfo is None:



        raise ValueError("UTC-aware datetime required")



    return int(t.astimezone(dt.timezone.utc).timestamp() * 1000)











def utc_from_ms(ms: int) -> str:



    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat()











def _parse_rows(provider: str, metric: str, body: bytes, interval_ms: int):



    """Parse API response into list of (timestamp_ms, value, quote_usd)."""



    payload = parse_api_json(body)



    if provider == "binance":



        if not isinstance(payload, list):



            raise ValueError("Binance response must be a list")



        observations = payload



    else:



        if not isinstance(payload, dict) or payload.get("retCode") != 0:



            raise ValueError(f"Bybit retCode {payload.get('retCode')}")



        info = payload.get("result", {})



        if info.get("category") not in (None, "linear"):



            raise ValueError("Bybit category mismatch")



        if info.get("symbol") not in (None, "BTCUSDT"):



            raise ValueError("Bybit symbol mismatch")



        observations = info.get("list", [])



        if not isinstance(observations, list):



            raise ValueError("Bybit list missing")







    parsed = []



    seen = set()



    for item in observations:



        if not isinstance(item, dict):



            continue



        if item.get("symbol") not in (None, "BTCUSDT"):



            raise ValueError("Unexpected symbol in response")







        if provider == "binance":



            key = "timestamp" if metric == "open_interest" else "fundingTime"



            value_key = "sumOpenInterest" if metric == "open_interest" else "fundingRate"



            quote = (item.get("sumOpenInterestValue")



                     if metric == "open_interest" else None)



        else:



            key = ("timestamp" if metric == "open_interest"



                   else "fundingRateTimestamp")



            value_key = "openInterest" if metric == "open_interest" else "fundingRate"



            quote = None







        try:
            stamp = int(item[key])
        except KeyError:
            raise ValueError(f"Required field '{key}' missing from item: {list(item.keys())}")



        if stamp <= 0:



            raise ValueError(f"Non-positive timestamp {stamp}")



        if stamp in seen:



            raise ValueError(f"Duplicate timestamp {stamp} in response")



        seen.add(stamp)



        parsed.append((stamp, float(item[value_key]), quote))







    return sorted(parsed, key=lambda x: x[0])











def _validate_forward_response(rows, start_ms: int, end_ms: int,



                               interval_ms: int, metric: str):



    """Validate forward response temporal semantics strictly."""



    if not rows:



        return  # empty is handled separately







    times = [r[0] for r in rows]







    # All timestamps must be >= start_ms and <= end_ms



    for t in times:



        if t < start_ms or t > end_ms:



            raise ValueError(f"Timestamp {t} outside requested range [{start_ms}, {end_ms}]")







    # Must start exactly at start_ms



    if times[0] != start_ms:



        raise ValueError(f"First timestamp {times[0]} != expected start {start_ms}")







    # All must be 5m apart



    for a, b in zip(times, times[1:]):



        if b - a != interval_ms:



            raise ValueError(f"Gap detected: {a} -> {b} ({b-a}ms, expected {interval_ms}ms)")







    # No duplicates



    if len(set(times)) != len(times):



        raise ValueError("Duplicate timestamps after sort")











def _cutoff_now(margin_ms: int = CUTOFF_MARGIN_MS) -> int:



    """Return UTC now minus margin, as ms epoch."""



    return ms_from_utc(now_utc()) - margin_ms











def collect_one_page(db, provider: str, metric: str, *, fetch=download):



    """Collect exactly one forward page from MAX(observed_utc) + interval."""



    if (provider, metric) not in CONFIG:



        raise ValueError(f"Unknown provider/metric: {provider}/{metric}")



    if not installed(db):



        raise ValueError("Market schema not installed")



    if not request_lineage_installed(db):



        raise ValueError("Market request lineage v2 not installed")







    suffix, page_limit, interval_label = CONFIG[(provider, metric)]



    stream = CURSOR_PREFIX + metric



    interval_ms = INTERVAL_5M_MS if metric == "open_interest" else (8 * 3600 * 1000)







    # Read current MAX



    max_row = db.execute(



        "SELECT MAX(observed_utc) FROM market_derivatives "



        "WHERE provider=? AND symbol='BTCUSDT' AND metric=?",



        (provider, metric)).fetchone()[0]



    if max_row is None:



        raise ValueError(f"No existing {provider}/{metric} data — forward pilot needs existing snapshot")







    max_ms = ms_from_utc(max_row)



    next_start_ms = max_ms + interval_ms







    # Compute cutoff: closed bars only



    cutoff_ms = _cutoff_now()



    if next_start_ms > cutoff_ms:



        return {



            "provider": provider, "metric": metric,



            "status": "stale_cutoff",



            "requests": 0, "points": 0,



            "start_ms": next_start_ms, "cutoff_ms": cutoff_ms,



            "cursor_advanced": False,



        }







    # Build page: startTime..endTime within cutoff



    # limit pages by bytes, not just count



    end_page_ms = min(next_start_ms + (page_limit - 1) * interval_ms, cutoff_ms)



    if end_page_ms < next_start_ms:



        return {



            "provider": provider, "metric": metric,



            "status": "empty",



            "requests": 0, "points": 0,



            "start_ms": next_start_ms, "cutoff_ms": cutoff_ms,



            "cursor_advanced": False,



        }







    endpoint = SOURCE_URLS[provider] + suffix



    params = {"symbol": "BTCUSDT", "limit": page_limit,



              "startTime": next_start_ms, "endTime": end_page_ms}



    if provider == "binance" and metric == "open_interest":



        params["period"] = "5m"



    elif provider == "bybit":



        params["category"] = "linear"



        if metric == "open_interest":



            params["intervalTime"] = "5min"







    attempt_stamp = now_utc()



    body = fetch(endpoint, params)



    rows = _parse_rows(provider, metric, body, interval_ms)







    # Validate temporal semantics



    if rows:



        _validate_forward_response(rows, next_start_ms, end_page_ms, interval_ms, metric)







    # Store



    digest = raw_payload(db, provider, endpoint, body, "application/json")



    acquired_utc = now_utc()



    acquired_id = f"acq-{uuid.uuid4().hex[:12]}"







    added = 0



    for stamp, value, quote in rows:



        observed = utc_from_ms(stamp)



        added += int(store_derivative(



            db, provider=provider, symbol="BTCUSDT", metric=metric,



            observed_utc=observed, interval_label=interval_label,



            value=value, unit="BTC" if metric == "open_interest" else "fraction",



            endpoint=endpoint, sha=digest, quote_usd=quote))







    if rows:



        new_max_ms = max(r[0] for r in rows)



        save_cursor(db, provider, stream, str(new_max_ms))







    return {



        "provider": provider, "metric": metric,



        "status": "success" if rows else "empty",



        "requests": 1, "points": added,



        "source_sha256": digest[:16],



        "_full_source_sha256": digest,



        "acquired_id": acquired_id,



        "start_ms": next_start_ms, "end_ms": end_page_ms,



        "cutoff_ms": cutoff_ms,



        "first_utc": utc_from_ms(rows[0][0]) if rows else None,



        "last_utc": utc_from_ms(rows[-1][0]) if rows else None,



        "cursor_advanced": bool(rows),



    }











def execute(db, provider: str, metric: str, *, fetch=download):
    """Execute one forward page with full v3 lineage.

    Returns a dict with:
      - status: 'success' | 'empty' | 'stale_cutoff' | 'failed'
      - requests, points
      - request_lineage_logged: bool
      - cursor_advanced: bool

    All writes are atomic via the caller's transaction context.
    """
    if not request_lineage_installed(db):
        return {"provider": provider, "metric": metric, "status": "failed",
                "requests": 0, "points": 0,
                "error_code": "SchemaV2Required"}

    cols = {r[1] for r in db.execute("PRAGMA table_info(market_request_lineage)")}
    if "direction" not in cols or "requested_start_ms" not in cols:
        return {"provider": provider, "metric": metric, "status": "failed",
                "requests": 0, "points": 0,
                "error_code": "SchemaV3Required"}

    uid = uuid.uuid4().hex
    db.execute(
        "INSERT INTO market_source_runs(run_id,provider,started_utc,status) "
        "VALUES(?,?,?,'running')",
        (uid, provider, now_utc()))

    attempt_stamp = now_utc()
    endpoint_path = CONFIG[(provider, metric)][0]
    page_limit = CONFIG[(provider, metric)][1]
    interval_label = CONFIG[(provider, metric)][2]

    # ── Phase 1: Determine boundaries (no transaction needed) ───────────────────
    try:
        max_row = db.execute(
            "SELECT MAX(observed_utc) FROM market_derivatives "
            "WHERE provider=? AND symbol='BTCUSDT' AND metric=?",
            (provider, metric)).fetchone()[0]
        if max_row is None:
            raise ValueError(f"No existing {provider}/{metric} data")

        interval_ms = INTERVAL_5M_MS if metric == "open_interest" else (8 * 3600 * 1000)
        next_start_ms = ms_from_utc(max_row) + interval_ms
        cutoff_ms = _cutoff_now()

        if next_start_ms > cutoff_ms:
            db.execute(
                "UPDATE market_source_runs SET ended_utc=?,status='success',"
                "requests=0,points=0 WHERE run_id=?",
                (now_utc(), uid))
            return {
                "provider": provider, "metric": metric,
                "status": "stale_cutoff", "requests": 0, "points": 0,
                "request_lineage_logged": False, "cursor_advanced": False,
            }

        end_page_ms = min(next_start_ms + (page_limit - 1) * interval_ms, cutoff_ms)
        if end_page_ms < next_start_ms:
            db.execute(
                "UPDATE market_source_runs SET ended_utc=?,status='empty',"
                "requests=0,points=0 WHERE run_id=?",
                (now_utc(), uid))
            return {
                "provider": provider, "metric": metric,
                "status": "empty", "requests": 0, "points": 0,
                "request_lineage_logged": False, "cursor_advanced": False,
            }

        # ── Phase 2: HTTP request ──────────────────────────────────────────────
        suffix = CONFIG[(provider, metric)][0]
        endpoint = SOURCE_URLS[provider] + suffix
        params = {"symbol": "BTCUSDT", "limit": page_limit,
                  "startTime": next_start_ms, "endTime": end_page_ms}
        if provider == "binance" and metric == "open_interest":
            params["period"] = "5m"
        elif provider == "bybit":
            params["category"] = "linear"
            if metric == "open_interest":
                params["intervalTime"] = "5min"

        try:
            body = fetch(endpoint, params)
        except Exception as fetch_exc:
            # Network error: mark run failed, write lineage record, return
            reason = error_category(fetch_exc)
            db.execute(
                "UPDATE market_source_runs SET ended_utc=?,status='failed',"
                "requests=1,error_code=? WHERE run_id=?",
                (now_utc(), reason, uid))
            db.execute(
                "INSERT INTO market_request_lineage"
                "(request_id,run_id,provider,stream,symbol,interval_label,endpoint_path,"
                "requested_start_ms,requested_end_ms,requested_limit,"
                "attempted_utc,ended_utc,http_attempts,"
                "status,error_class,raw_sha256,returned_rows,persisted_rows,"
                "first_observed_utc,last_observed_utc,direction)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"req-{uid}", uid, provider, metric, "BTCUSDT", interval_label,
                 endpoint_path,
                 next_start_ms, end_page_ms, page_limit,
                 attempt_stamp, now_utc(), 1,
                 "failed", reason, None, 0, 0,
                 None, None, "forward"))
            return {
                "provider": provider, "metric": metric,
                "status": "failed", "requests": 1, "points": 0,
                "error_code": reason,
                "request_lineage_logged": True,
                "cursor_advanced": False,
            }

        # ── Phase 3: Parse and store ─────────────────────────────────────────
        rows = _parse_rows(provider, metric, body, interval_ms)
        if rows:
            _validate_forward_response(rows, next_start_ms, end_page_ms, interval_ms, metric)

        digest = raw_payload(db, provider, endpoint, body, "application/json")
        acquired_id = f"acq-{uuid.uuid4().hex[:12]}"
        acquired_utc = now_utc()

        added = 0
        for stamp, value, quote in rows:
            observed = utc_from_ms(stamp)
            added += int(store_derivative(
                db, provider=provider, symbol="BTCUSDT", metric=metric,
                observed_utc=observed, interval_label=interval_label,
                value=value, unit="BTC" if metric == "open_interest" else "fraction",
                endpoint=endpoint, sha=digest, quote_usd=quote))

        if rows:
            new_max_ms = max(r[0] for r in rows)
            stream = CURSOR_PREFIX + metric
            save_cursor(db, provider, stream, str(new_max_ms))

        lineage_status = "empty" if not rows else "success"
        request_id = f"req-{uid}"
        write_request_lineage(
            db, request_id=request_id, run_id=uid,
            provider=provider, metric=metric,
            interval_label=interval_label,
            endpoint_path=endpoint_path,
            requested_start_ms=next_start_ms,
            requested_end_ms=end_page_ms,
            requested_limit=page_limit,
            attempted_utc=attempt_stamp, ended_utc=now_utc(),
            http_attempts=1, status=lineage_status,
            raw_sha256=digest,
            returned_rows=added,
            persisted_rows=added,
            first_observed_utc=utc_from_ms(rows[0][0]) if rows else None,
            last_observed_utc=utc_from_ms(rows[-1][0]) if rows else None,
            direction="forward")

        db.execute(
            "INSERT INTO market_request_acquisitions "
            "(acquired_id,request_id,provider,endpoint_path,raw_sha256,acquired_utc) "
            "VALUES(?,?,?,?,?,?)",
            (acquired_id, request_id, provider, endpoint_path, digest, acquired_utc))

        db.execute(
            "UPDATE market_source_runs SET ended_utc=?,status=?,"
            "requests=1,points=? WHERE run_id=?",
            (now_utc(), lineage_status, added, uid))

        return {
            "provider": provider, "metric": metric,
            "status": lineage_status, "requests": 1, "points": added,
            "request_lineage_logged": True,
            "cursor_advanced": bool(rows),
        }

    except Exception as exc:
        db.rollback()
        reason = error_category(exc)
        db.execute(
            "UPDATE market_source_runs SET ended_utc=?,status='failed',"
            "requests=1,error_code=? WHERE run_id=?",
            (now_utc(), reason, uid))
        return {
            "provider": provider, "metric": metric,
            "status": "failed", "requests": 1, "points": 0,
            "error_code": reason,
            "request_lineage_logged": False,
            "cursor_advanced": False,
        }


def plan(db, provider: str, metric: str) -> dict:



    """Print forward plan without network or writes."""



    suffix, page_limit, interval_label = CONFIG.get((provider, metric))



    if suffix is None:



        return {"error": f"Unknown {provider}/{metric}"}







    if not installed(db):



        return {"error": "Market schema not installed"}



    if not request_lineage_installed(db):



        return {"error": "Market request lineage v2 not installed"}







    cols = {r[1] for r in db.execute("PRAGMA table_info(market_request_lineage)")}



    if "direction" not in cols:



        return {"error": "Schema v3 not installed (direction column missing)"}







    max_row = db.execute(



        "SELECT MAX(observed_utc) FROM market_derivatives "



        "WHERE provider=? AND symbol='BTCUSDT' AND metric=?",



        (provider, metric)).fetchone()[0]



    if max_row is None:



        return {"error": f"No existing {provider}/{metric} snapshot"}







    max_ms = ms_from_utc(max_row)



    next_start_ms = max_ms + (INTERVAL_5M_MS if metric == "open_interest"



                                else 8 * 3600 * 1000)



    cutoff_ms = _cutoff_now()



    end_page_ms = min(next_start_ms + (page_limit - 1) * 300_000, cutoff_ms)







    return {



        "status": "PLAN_ONLY",



        "provider": provider, "metric": metric,



        "page_limit": page_limit,



        "interval_label": interval_label,



        "current_MAX_ms": max_ms,



        "current_MAX_utc": max_row,



        "next_start_ms": next_start_ms,



        "next_start_utc": utc_from_ms(next_start_ms),



        "cutoff_ms": cutoff_ms,



        "cutoff_utc": utc_from_ms(cutoff_ms),



        "end_page_ms": end_page_ms,



        "end_page_utc": utc_from_ms(end_page_ms),



        "closed_intervals_waiting": max(0, math.floor((cutoff_ms - max_ms) / 300_000) - 1),



        "network_requests": 0,



        "sqlite_writes": 0,



        "caveat": "Requires independently checked fresh WAL-safe backup and v3 migration",



    }











def main(argv=None, _db=None):
    """Entry point. _db overrides the DB connection (for test injection)."""
    import argparse

    p = argparse.ArgumentParser(description="Forward incremental OI pilot")
    p.add_argument("--provider", required=True, choices=["binance", "bybit"])
    p.add_argument("--metric", required=True, choices=["open_interest", "funding_settled"])
    p.add_argument("--apply", action="store_true", help="Execute one forward page (default: PLAN_ONLY)")
    p.add_argument("--db", type=Path, default=Path(DB_PATH))
    args = p.parse_args(argv)

    if _db is not None:
        db = _db
    else:
        db = sqlite3.connect(args.db)
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA foreign_keys=ON")

    if not args.apply:
        result = plan(db, args.provider, args.metric)
        print(json.dumps(result, indent=2, default=str))
        return

    data = execute(db, args.provider, args.metric)
    print(json.dumps(data, indent=2, default=str))


if __name__ == "__main__":
    main()
