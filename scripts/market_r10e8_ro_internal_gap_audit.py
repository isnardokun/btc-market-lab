#!/usr/bin/env python3
"""R10-E.8: classify INTERNAL archived gaps without confusing provider calendars.

Explicit RO SQLite only; no HTTP, no default prod DB, no --apply or scheduler.
Missing time slots are CANDIDATES, never a verified recoverable API window.
"""
import argparse
from contextlib import closing
import datetime as dt
import json
from pathlib import Path
import sqlite3

from ingestion.config import FRED_SERIES

UTC = dt.timezone.utc
FRED_FREQUENCY = {s[0]: s[2] for s in FRED_SERIES}
MAX_OBSERVATIONS = 1_000_000
MAX_GAPS_DISPLAYED = 40
SQL = {
    "researchbitcoin": ("onchain_external_observations",
                        "SELECT metric,observed_date FROM onchain_external_observations "
                        "WHERE provider='researchbitcoin' ORDER BY metric,observed_date"),
    "bitview": ("daily",
                "SELECT s.name,d.ts FROM daily d JOIN series s ON d.series_id=s.id "
                "WHERE s.idx='1d' ORDER BY s.name,d.ts"),
    "fred": ("macro_fred",
             "SELECT series_id,date FROM macro_fred ORDER BY series_id,date"),
    "yahoo": ("market_ohlc_history",
              "SELECT symbol,ts FROM market_ohlc_history "
              "WHERE provider='Yahoo Finance' ORDER BY symbol,ts"),
}
REQUIRED = {
    "onchain_external_observations": {"provider", "metric", "observed_date"},
    "series": {"id", "name", "idx"},
    "daily": {"series_id", "ts"},
    "macro_fred": {"series_id", "date"},
    "market_ohlc_history": {"provider", "symbol", "ts"},
}


def _calendar_day(value, epoch):
    if epoch:
        if type(value) is not int or value < 0:
            raise ValueError("Invalid archive unix timestamp")
        return dt.datetime.fromtimestamp(value, UTC).date()
    if not isinstance(value, str):
        raise ValueError("Archive ISO date must be string")
    result = dt.date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError("Noncanonical date")
    return result


def _month(d):
    return d.year * 12 + d.month - 1


def _month_string(month_index):
    y, m = divmod(month_index, 12)
    if y < 1 or y > 9999:
        raise ValueError("Bad month index")
    return f"{y:04d}-{m+1:02d}"


def _classify(provider, series, points):
    if not points:
        raise ValueError("Cannot classify empty series")
    unique_days = sorted(set(points))
    first, last = unique_days[0], unique_days[-1]
    if provider in ("researchbitcoin", "bitview"):
        mode = "UTC_CALENDAR_DAY"
        observed = set(unique_days)
        missing_count = (last - first).days + 1 - len(observed)
        missing = []
        for offset in range((last - first).days + 1):
            day = first + dt.timedelta(days=offset)
            if day not in observed and len(missing) < MAX_GAPS_DISPLAYED:
                missing.append(day.isoformat())
        note = "Internal daily gaps are candidates, not proof of provider availability"
    elif provider == "fred" and FRED_FREQUENCY.get(series) == "monthly":
        mode = "MONTHLY_OBSERVATION"
        observed = {_month(d) for d in unique_days}
        low, high = min(observed), max(observed)
        missing_count = high - low + 1 - len(observed)
        missing = [_month_string(i) for i in range(low, high + 1)
                   if i not in observed][:MAX_GAPS_DISPLAYED]
        note = "Missing calendar months are review candidates, not confirmed provider errors"
    else:
        mode = "EXTERNAL_PUBLICATION_CALENDAR_REQUIRED"
        missing_count = None
        missing = []
        note = ("Cannot infer missing observations from days: "
                "FRED business/weekly schedules or Yahoo trading sessions "
                "require verified provider calendars")
    return {
        "provider": provider, "metric": series, "observations": len(points),
        "distinct_utc_dates": len(unique_days),
        "first_archived_utc": first.isoformat(),
        "last_archived_utc": last.isoformat(),
        "gap_method": mode,
        "internal_gap_candidates": missing_count,
        "sample_missing_slots": missing, "sample_truncated": (
            missing_count is not None and missing_count > len(missing)),
        "interpretation": note,
        "historical_completeness": "NOT_VERIFIED",
        "recoverable_window_verified": False,
    }


def audit_internal_gaps(db_path, *, now=None):
    """A single read transaction; no edits and no external source assertions."""
    if not isinstance(db_path, (str, Path)):
        raise ValueError("Explicit SQLite path required")
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError("SQLite snapshot not found")
    path = path.resolve(strict=True)
    clock = now if now is not None else dt.datetime.now(UTC)
    if (not isinstance(clock, dt.datetime) or clock.tzinfo is None
            or clock.utcoffset() != dt.timedelta(0)):
        raise ValueError("UTC aware clock required")

    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        attached = db.execute("PRAGMA database_list").fetchall()
        if (db.execute("PRAGMA query_only").fetchone()[0] != 1
                or len(attached) != 1 or attached[0][1] != "main"
                or Path(attached[0][2]).resolve() != path):
            raise ValueError("Attached/untrusted SQLite connection")
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table in tables & REQUIRED.keys():
            cols = {r[1] for r in db.execute("PRAGMA table_info(" + table + ")")}
            if not REQUIRED[table].issubset(cols):
                raise ValueError("Incorrect existing schema: " + table)

        sources = []
        remaining = MAX_OBSERVATIONS
        for provider, (table, query) in SQL.items():
            deps = ("series", "daily") if provider == "bitview" else (table,)
            if not all(d in tables for d in deps):
                sources.append({"provider":provider, "schema_state":"NOT_INITIALIZED",
                                "series": [], "historical_completeness":"NOT_VERIFIED"})
                continue
            grouped = {}
            # Bounded reads; exceeding the limit is an explicit BLOCKED, never partial success.
            for metric, raw in db.execute(query):
                remaining -= 1
                if remaining < 0:
                    raise ValueError("Observation budget exceeded; refusing partial audit")
                if not isinstance(metric, str) or not metric:
                    raise ValueError("Invalid archive series name")
                day = _calendar_day(raw, provider in ("bitview", "yahoo"))
                grouped.setdefault(metric, []).append(day)
            sources.append({
                "provider":provider, "schema_state":"PRESENT",
                "series": [_classify(provider, metric, grouped[metric])
                           for metric in sorted(grouped)],
                "historical_completeness":"NOT_VERIFIED",
            })
        db.rollback()
        if db.total_changes:
            raise AssertionError("Unexpected SQLite changes")
    return {
        "state":"INTERNAL_GAP_CANDIDATES_READ_ONLY",
        "generated_utc":clock.isoformat(),
        "db_location":"REDACTED",
        "http_requests":0, "sqlite_writes":0,
        "execution_authorized":False,
        "real_provider_availability_verified":False,
        "historical_completeness":"NOT_VERIFIED",
        "calendar_policy":"SOURCE_AWARE_NO_FAKE_TRADING_OR_PUBLICATION_DAYS",
        "sources":sources,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="R10-E.8 RO internal gap candidates; no apply")
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(audit_internal_gaps(args.db), indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, sqlite3.Error, OverflowError) as exc:
        print(json.dumps({"state":"BLOCKED", "reason":type(exc).__name__,
                          "http_requests":0, "sqlite_writes":0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
