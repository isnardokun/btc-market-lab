#!/usr/bin/env python3
"""Offline, read-only coverage and forward backlog planner for BTCUSDT OI 5m.

This tool NEVER requests market data or writes SQLite. It is not an apply
or scheduler entry point. Explicit --db is required; no production default.
Historical completeness is never inferred from internal contiguity.
"""

import argparse
import datetime as dt
import json
from pathlib import Path
import sqlite3
from contextlib import closing

SLOT_MS = 300_000
MARGIN_MS = 600_000
PAGE_LIMITS = {"binance": 500, "bybit": 200}
UTC = dt.timezone.utc


def utc_ms(value: str) -> int:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("UTC-aware timestamp required")
    return int(parsed.astimezone(UTC).timestamp() * 1_000)


def iso_ms(value: int) -> str:
    return dt.datetime.fromtimestamp(value / 1_000, UTC).isoformat(timespec="seconds")


def cutoff_ms(now: dt.datetime) -> int:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("UTC-aware clock required")
    return ((int(now.astimezone(UTC).timestamp() * 1_000) - MARGIN_MS) // SLOT_MS) * SLOT_MS


def plan_stream(stamps: list[str], provider: str, *, now: dt.datetime) -> dict:
    """Plan one provider based on all stored stamps; no network or writes."""
    if provider not in PAGE_LIMITS:
        raise ValueError("Unsupported provider")
    cutoff = cutoff_ms(now)
    result = {
        "provider": provider,
        "symbol": "BTCUSDT", "metric": "open_interest", "interval": "5m",
        "page_limit": PAGE_LIMITS[provider],
        "cutoff_utc": iso_ms(cutoff),
        "stored_points": len(stamps),
        "historical_completeness": "NOT_VERIFIED",
        "provider_availability": "NOT_CHECKED",
        "http_requests": 0, "sqlite_writes": 0,
    }
    if not stamps:
        return {**result, "status": "BLOCKED_NO_BASELINE", "internal_gap_count": None,
                "closed_intervals_waiting": None, "pages_needed": None}

    times = sorted(utc_ms(value) for value in stamps)
    if any(value % SLOT_MS for value in times):
        return {**result, "status": "BLOCKED_UNALIGNED_TIMESTAMP",
                "internal_gap_count": None, "closed_intervals_waiting": None,
                "pages_needed": None}
    gaps = 0
    for previous, current in zip(times, times[1:]):
        if current <= previous:
            return {**result, "status": "BLOCKED_DUPLICATE_OR_ORDER",
                    "internal_gap_count": None, "closed_intervals_waiting": None,
                    "pages_needed": None}
        gaps += (current - previous) // SLOT_MS - 1

    last = times[-1]
    next_start = last + SLOT_MS
    # Both endpoints are inclusive; a cutoff equal to next_start is ONE slot.
    waiting = max(0, (cutoff - next_start) // SLOT_MS + 1)
    pages = (waiting + PAGE_LIMITS[provider] - 1) // PAGE_LIMITS[provider]
    status = ("BLOCKED_INTERNAL_GAP" if gaps else
              "BLOCKED_MAX_AFTER_CUTOFF" if last > cutoff else
              "NO_CLOSED_BAR" if waiting == 0 else "PLAN_ONLY")
    return {**result, "status": status, "internal_gap_count": gaps,
            "first_stored_utc": iso_ms(times[0]),
            "max_stored_utc": iso_ms(last),
            "next_start_utc": iso_ms(next_start),
            "closed_intervals_waiting": waiting,
            "pages_needed": pages,
            "first_page_end_utc": iso_ms(min(next_start + (PAGE_LIMITS[provider] - 1) * SLOT_MS, cutoff))
            if waiting and not gaps and last <= cutoff else None,
            "applies_after_revalidation_only": True}


def plan_snapshot(db_path: Path, *, now: dt.datetime) -> dict:
    """Open existing SQLite strictly read-only and plan both OI streams."""
    if not db_path.is_file():
        raise FileNotFoundError("Existing SQLite file required")
    path = db_path.resolve(strict=True)
    uri = path.as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        if db.execute("PRAGMA query_only").fetchone()[0] != 1:
            raise ValueError("Read-only SQLite required")
        databases = db.execute("PRAGMA database_list").fetchall()
        main = [file for _, name, file in databases if name == "main"]
        extra = [(name, file) for _, name, file in databases
                 if name != "main" and not (name == "temp" and not file)]
        if len(main) != 1 or not main[0] or extra or Path(main[0]).resolve() != path:
            raise ValueError("Attached or mismatched SQLite database")
        streams = []
        for provider in PAGE_LIMITS:
            stamps = [row[0] for row in db.execute(
                "SELECT observed_utc FROM market_derivatives "
                "WHERE provider=? AND symbol='BTCUSDT' "
                "AND metric='open_interest' AND interval_label='5m' "
                "ORDER BY observed_utc", (provider,))]
            streams.append(plan_stream(stamps, provider, now=now))
        db.rollback()  # Ends read transaction without changes.
    return {"state": "PLAN_ONLY_READ_ONLY", "historical_completeness": "NOT_VERIFIED",
            "provider_availability": "NOT_CHECKED", "now_utc": now.astimezone(UTC).isoformat(),
            "sources": streams, "http_requests": 0, "sqlite_writes": 0}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="R10-E read-only OI backlog planner; no HTTP, no apply")
    parser.add_argument("--db", type=Path, required=True,
                        help="Existing SQLite snapshot path; NO default production path")
    parser.add_argument("--now-utc", help="Optional UTC-aware time for deterministic offline testing")
    args = parser.parse_args(argv)
    try:
        now = (dt.datetime.fromisoformat(args.now_utc.replace("Z", "+00:00"))
               if args.now_utc else dt.datetime.now(UTC))
        result = plan_snapshot(args.db, now=now)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (ValueError, OSError, sqlite3.Error, OverflowError) as exc:
        print(json.dumps({"state": "BLOCKED", "reason": type(exc).__name__,
                          "http_requests": 0, "sqlite_writes": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
