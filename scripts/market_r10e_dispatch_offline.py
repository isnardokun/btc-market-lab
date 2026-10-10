#!/usr/bin/env python3
"""R10-E.1 deterministic offline dispatcher: proposes, never executes, one OI page.

No network client, writes, --apply, API key, scheduler, or production DB default.
The exact provider request is still subject to separate owner authorization.
"""

import argparse
import datetime as dt
import json
from pathlib import Path
import sqlite3

from scripts.market_r10e_plan_only import PAGE_LIMITS, UTC, plan_snapshot

EXPECTED_SOURCE_STATUS = frozenset({
    "PLAN_ONLY", "NO_CLOSED_BAR", "BLOCKED_NO_BASELINE",
    "BLOCKED_UNALIGNED_TIMESTAMP", "BLOCKED_DUPLICATE_OR_ORDER",
    "BLOCKED_INTERNAL_GAP", "BLOCKED_MAX_AFTER_CUTOFF",
})


def dispatch_offline(coverage: dict) -> dict:
    """One bounded scheduling proposal from an immutable coverage report.

    Fail closed when any OI stream is corrupt/missing, or when the coverage
    report has the wrong shape, stale provider set or unexpected status.
    """
    if (coverage.get("state") != "PLAN_ONLY_READ_ONLY"
            or coverage.get("historical_completeness") != "NOT_VERIFIED"
            or coverage.get("http_requests") != 0
            or coverage.get("sqlite_writes") != 0):
        raise ValueError("Coverage report must be read-only and unverified")

    streams = coverage.get("sources")
    if not isinstance(streams, list) or len(streams) != len(PAGE_LIMITS):
        raise ValueError("Both OI streams are required")
    by_provider = {}
    for stream in streams:
        if not isinstance(stream, dict):
            raise ValueError("Invalid stream evidence")
        provider = stream.get("provider")
        if provider not in PAGE_LIMITS or provider in by_provider:
            raise ValueError("Unknown or duplicate provider")
        if (stream.get("symbol") != "BTCUSDT"
                or stream.get("metric") != "open_interest"
                or stream.get("interval") != "5m"
                or stream.get("page_limit") != PAGE_LIMITS[provider]
                or stream.get("historical_completeness") != "NOT_VERIFIED"
                or stream.get("provider_availability") != "NOT_CHECKED"
                or stream.get("http_requests") != 0
                or stream.get("sqlite_writes") != 0):
            raise ValueError("Source parameters not supported for dispatch")
        status = stream.get("status")
        if status not in EXPECTED_SOURCE_STATUS:
            raise ValueError("Unknown stream status")
        if status == "PLAN_ONLY":
            pending = stream.get("closed_intervals_waiting")
            pages = stream.get("pages_needed")
            if (type(pending) is not int or pending < 1
                    or type(pages) is not int or pages < 1
                    or not stream.get("next_start_utc")
                    or not stream.get("first_page_end_utc")):
                raise ValueError("Incomplete eligible stream plan")
        by_provider[provider] = stream

    result = {
        "state": "PLAN_ONLY_DISPATCH",
        "execution_authorized": False,
        "live_apply_available": False,
        "historical_completeness": "NOT_VERIFIED",
        "provider_availability": "NOT_CHECKED",
        "http_requests": 0,
        "sqlite_writes": 0,
        "maximum_future_requests_if_authorized": 1,
        "cutoff_utc": coverage.get("now_utc"),
        "candidates": [],
        "selected": None,
    }
    blocked = [name for name, stream in by_provider.items()
               if stream["status"].startswith("BLOCKED_")]
    for name in sorted(by_provider):
        stream = by_provider[name]
        result["candidates"].append({
            "provider": name,
            "status": stream["status"],
            "closed_intervals_waiting": stream.get("closed_intervals_waiting"),
            "pages_needed": stream.get("pages_needed"),
            "internal_gap_count": stream.get("internal_gap_count"),
        })
    if blocked:
        return {**result, "decision": "BLOCKED_SOURCE_QA",
                "blocked_providers": sorted(blocked),
                "reason": "A missing or inconsistent source blocks the whole cycle"}

    available = [s for s in by_provider.values() if s["status"] == "PLAN_ONLY"]
    if not available:
        return {**result, "decision": "NO_CLOSED_BAR", "reason": "All sources caught up"}

    # Highest number of pending *time intervals* first, provider name as
    # stable tie-breaker. Do not infer API availability from this estimate.
    chosen = sorted(available, key=lambda s: (-s["closed_intervals_waiting"], s["provider"]))[0]
    slots = min(chosen["closed_intervals_waiting"], chosen["page_limit"])
    result["selected"] = {
        "provider": chosen["provider"],
        "symbol": "BTCUSDT",
        "metric": "open_interest",
        "interval": "5m",
        "direction": "forward",
        "next_start_utc": chosen["next_start_utc"],
        "first_page_end_utc": chosen["first_page_end_utc"],
        "estimated_slots_this_page": slots,
        "estimated_pages_outstanding": chosen["pages_needed"],
        "provider_history_window_verified": False,
    }
    return {**result, "decision": "PROPOSE_ONE_PAGE_ONLY",
            "reason": "Advisory plan; NOT cleared for execution"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="R10-E.1 offline OI dispatcher (never --apply)")
    p.add_argument("--db", required=True, type=Path, help="Explicit SQLite snapshot path")
    p.add_argument("--now-utc", help="UTC-aware deterministic test clock")
    args = p.parse_args(argv)
    try:
        now = (dt.datetime.fromisoformat(args.now_utc.replace("Z", "+00:00"))
               if args.now_utc else dt.datetime.now(UTC))
        report = dispatch_offline(plan_snapshot(args.db, now=now))
        print(json.dumps(report, sort_keys=True, indent=2))
        return 0 if report["decision"] != "BLOCKED_SOURCE_QA" else 2
    except (ValueError, TypeError, OSError, sqlite3.Error, OverflowError) as exc:
        print(json.dumps({"decision": "BLOCKED", "reason": type(exc).__name__,
                          "execution_authorized": False,
                          "http_requests": 0, "sqlite_writes": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
