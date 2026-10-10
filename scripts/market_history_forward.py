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






    ("bybit",    "open_interest"):  ("/v5/market/open-interest",         200, "5m"),






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



            raise ValueError("Malformed provider row: expected JSON object")



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
    """One OI page with atomic writes and a separate failed-attempt receipt.

    No HTTP occurs under a SQLite write lock. BEGIN IMMEDIATE plus re-reading
    MAX before INSERT protects against another writer extending the same series.
    Caller must not supply an open transaction.
    """
    if (provider, metric) not in CONFIG or metric != "open_interest":
        return {"status": "failed", "error_code": "UnsupportedStream",
                "requests": 0, "points": 0}
    if db.in_transaction:
        return {"status": "failed", "error_code": "ActiveTransaction",
                "requests": 0, "points": 0}
    if not installed(db) or not request_lineage_installed(db):
        return {"status": "failed", "error_code": "SchemaV3Required",
                "requests": 0, "points": 0}
    cols = {r[1] for r in db.execute("PRAGMA table_info(market_request_lineage)")}
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"direction", "requested_start_ms"} <= cols or (
            "market_request_acquisitions" not in tables):
        return {"status": "failed", "error_code": "SchemaV3Required",
                "requests": 0, "points": 0}

    endpoint_path, page_limit, interval_label = CONFIG[(provider, metric)]
    endpoint = SOURCE_URLS[provider] + endpoint_path
    interval_ms = INTERVAL_5M_MS
    uid = uuid.uuid4().hex
    request_id = f"req-{uid}"
    attempt_stamp = now_utc()

    def latest_ms():
        row = db.execute(
            "SELECT MAX(observed_utc) FROM market_derivatives "
            "WHERE provider=? AND symbol='BTCUSDT' AND metric=? "
            "AND interval_label='5m'", (provider, metric)).fetchone()[0]
        if row is None:
            raise ValueError("No existing 5m OI snapshot")
        return ms_from_utc(row)

    def failure(reason, start_ms, end_ms):
        """Record the actual failed HTTP attempt AFTER rollback, atomically."""
        if db.in_transaction:
            db.rollback()
        logged = False
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO market_source_runs "
                "(run_id,provider,started_utc,ended_utc,status,requests,points,error_code) "
                "VALUES(?,?,?,?,'failed',1,0,?)",
                (uid, provider, attempt_stamp, now_utc(), reason))
            write_request_lineage(
                db, request_id=request_id, run_id=uid,
                provider=provider, metric=metric, interval_label=interval_label,
                endpoint_path=endpoint_path, requested_start_ms=start_ms,
                requested_end_ms=end_ms, requested_limit=page_limit,
                attempted_utc=attempt_stamp, ended_utc=now_utc(),
                http_attempts=1, status="failed", error_class=reason,
                direction="forward")
            db.commit()
            logged = True
        except (sqlite3.Error, ValueError):
            db.rollback()
        return {"provider": provider, "metric": metric, "status": "failed",
                "requests": 1, "points": 0, "error_code": reason,
                "request_lineage_logged": logged, "cursor_advanced": False}

    try:
        initial_max_ms = latest_ms()
    except (ValueError, TypeError, sqlite3.Error):
        return {"provider": provider, "metric": metric, "status": "failed",
                "requests": 0, "points": 0, "error_code": "SnapshotUnavailable",
                "request_lineage_logged": False, "cursor_advanced": False}

    start_ms = initial_max_ms + interval_ms
    cutoff_ms = (_cutoff_now() // interval_ms) * interval_ms
    if start_ms > cutoff_ms:
        return {"provider": provider, "metric": metric, "status": "stale_cutoff",
                "requests": 0, "points": 0, "request_lineage_logged": False,
                "cursor_advanced": False}
    end_ms = min(start_ms + (page_limit - 1) * interval_ms, cutoff_ms)
    params = {"symbol": "BTCUSDT", "limit": page_limit,
              "startTime": start_ms, "endTime": end_ms}
    if provider == "binance":
        params["period"] = "5m"
    else:
        params.update({"category": "linear", "intervalTime": "5min"})

    try:
        body = fetch(endpoint, params)
        rows = _parse_rows(provider, metric, body, interval_ms)
        if len(rows) > page_limit:
            raise ValueError("API response exceeds requested page limit")
        if rows:
            _validate_forward_response(rows, start_ms, end_ms, interval_ms, metric)
            if any(stamp % interval_ms for stamp, _value, _quote in rows):
                raise ValueError("OI timestamp is not aligned to five-minute UTC boundary")
    except Exception as exc:
        return failure(error_category(exc), start_ms, end_ms)

    try:
        db.execute("BEGIN IMMEDIATE")
        # Concurrent writers may have advanced MAX while the HTTP was in flight.
        if latest_ms() != initial_max_ms:
            raise ValueError("ConcurrentWriterAdvancedMAX")
        if rows:
            # UPSERT is not a safe means of detecting overlap: reject it explicitly.
            for stamp, _value, _quote in rows:
                observed = utc_from_ms(stamp)
                exists = db.execute(
                    "SELECT 1 FROM market_derivatives WHERE provider=? "
                    "AND symbol='BTCUSDT' AND metric='open_interest' "
                    "AND interval_label='5m' AND observed_utc=?",
                    (provider, observed)).fetchone()
                if exists:
                    raise ValueError("ForwardOverlapDetected")

        digest = raw_payload(db, provider, endpoint, body, "application/json")
        db.execute(
            "INSERT INTO market_source_runs(run_id,provider,started_utc,status) "
            "VALUES(?,?,?,'running')",
            (uid, provider, attempt_stamp))
        inserted = 0
        for stamp, value, quote in rows:
            inserted += int(store_derivative(
                db, provider=provider, symbol="BTCUSDT", metric=metric,
                observed_utc=utc_from_ms(stamp), interval_label="5m",
                value=value, unit="BTC", endpoint=endpoint, sha=digest,
                quote_usd=quote))
        if inserted != len(rows):
            raise ValueError("ForwardRowCountMismatch")

        status = "success" if rows else "empty"
        if rows:
            save_cursor(db, provider, CURSOR_PREFIX + metric, str(rows[-1][0]))
        write_request_lineage(
            db, request_id=request_id, run_id=uid, provider=provider,
            metric=metric, interval_label=interval_label,
            endpoint_path=endpoint_path, requested_start_ms=start_ms,
            requested_end_ms=end_ms, requested_limit=page_limit,
            attempted_utc=attempt_stamp, ended_utc=now_utc(),
            http_attempts=1, status=status, raw_sha256=digest,
            returned_rows=len(rows), persisted_rows=inserted,
            first_observed_utc=utc_from_ms(rows[0][0]) if rows else None,
            last_observed_utc=utc_from_ms(rows[-1][0]) if rows else None,
            direction="forward")
        db.execute(
            "INSERT INTO market_request_acquisitions "
            "(acquired_id,request_id,provider,endpoint_path,raw_sha256,acquired_utc) "
            "VALUES(?,?,?,?,?,?)",
            (f"acq-{uuid.uuid4().hex}", request_id,
             provider, endpoint_path, digest, now_utc()))
        db.execute(
            "UPDATE market_source_runs SET ended_utc=?,status=?,"
            "requests=1,points=? WHERE run_id=?",
            (now_utc(), status, inserted, uid))
        db.commit()
        return {"provider": provider, "metric": metric, "status": status,
                "requests": 1, "points": inserted,
                "request_lineage_logged": True, "cursor_advanced": bool(rows)}
    except Exception as exc:
        return failure(error_category(exc), start_ms, end_ms)


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
    """Fail-closed CLI: PLAN is read-only; APPLY requires an existing SQLite."""
    p = argparse.ArgumentParser(description="Forward incremental OI pilot")
    p.add_argument("--provider", required=True, choices=["binance", "bybit"])
    p.add_argument("--metric", required=True, choices=["open_interest"])
    p.add_argument("--apply", action="store_true",
                   help="Execute exactly one forward page (default: PLAN_ONLY)")
    p.add_argument("--db", type=Path, default=Path(DB_PATH))
    args = p.parse_args(argv)
    if _db is None and not args.db.is_file():
        print(json.dumps({"status": "failed", "error_code": "DatabaseNotFound"}))
        return 2
    own_db = _db is None
    try:
        db = _db if _db is not None else sqlite3.connect(
            args.db.resolve().as_uri() + ("?mode=rw" if args.apply else "?mode=ro"),
            uri=True, timeout=30)
    except (sqlite3.Error, OSError) as exc:
        print(json.dumps({"status": "failed", "error_code": type(exc).__name__}))
        return 2
    try:
        db.execute("PRAGMA foreign_keys=ON")
        if not args.apply:
            db.execute("PRAGMA query_only=ON")
            result = plan(db, args.provider, args.metric)
            print(json.dumps(result, indent=2, default=str))
            return 2 if "error" in result else 0

        result = execute(db, args.provider, args.metric)
        db.commit()
        print(json.dumps(result, indent=2, default=str))
        if result.get("status") == "failed":
            return 1
        if result.get("status") == "stale_cutoff":
            return 0

        # Independent post-page gates. An audit failure stops the caller.
        # Do not overwrite or roll back committed source evidence here.
        if own_db:
            from scripts.market_raw_reconcile import audit as raw_audit
            from scripts.market_temporal_quality import inspect as temporal_inspect
            from scripts.market_request_lineage_audit import audit as lineage_audit
            raw = raw_audit(args.db)
            temporal = temporal_inspect(args.db)
            lineage = lineage_audit(args.db)
            gates = {
                "RAW": raw.get("state"),
                "TEMPORAL": temporal.get("quality_state"),
                "LINEAGE": lineage.get("quality_state"),
            }
            print(json.dumps({"post_page_quality_gates": gates}))
            if (gates["RAW"] != "PASS_SQLITE_TO_RAW"
                    or gates["TEMPORAL"] != "SNAPSHOT_INTERNAL_QA_OK"
                    or gates["LINEAGE"] != "PASS_REQUEST_LINEAGE"):
                return 3
        return 0
    except (sqlite3.Error, ValueError, OSError) as exc:
        if db.in_transaction:
            db.rollback()
        print(json.dumps({"status": "failed", "error_code": type(exc).__name__}))
        return 2
    finally:
        if own_db:
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())
