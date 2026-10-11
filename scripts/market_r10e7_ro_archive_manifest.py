#!/usr/bin/env python3
"""R10-E.7 archived historical coverage from explicitly selected SQLite, RO.

Never infer API completeness or entitlement from stored counts. No HTTP,
--apply, production DB default, migration, scheduler or database writes.
"""
import argparse
from contextlib import closing
import datetime as dt
import json
from pathlib import Path
import sqlite3

from ingestion.researchbitcoin_catalog import CATALOG
from ingestion.config import FRED_SERIES

UTC = dt.timezone.utc
SOURCES = (
    ("researchbitcoin", "onchain_external_observations",
     "SELECT metric,COUNT(*),MIN(observed_date),MAX(observed_date) "
     "FROM onchain_external_observations WHERE provider='researchbitcoin' "
     "GROUP BY metric", "date"),
    ("bitview", "daily",
     "SELECT s.name,COUNT(*),MIN(d.ts),MAX(d.ts) FROM daily d "
     "JOIN series s ON s.id=d.series_id WHERE s.idx='1d' GROUP BY s.name", "unix"),
    ("fred", "macro_fred",
     "SELECT series_id,COUNT(*),MIN(date),MAX(date) FROM macro_fred "
     "GROUP BY series_id", "date"),
    ("yahoo", "market_ohlc_history",
     "SELECT symbol,COUNT(*),MIN(ts),MAX(ts) FROM market_ohlc_history "
     "WHERE provider='Yahoo Finance' GROUP BY symbol", "unix"),
)
REQUIRED = {
    "onchain_external_observations": {"provider", "metric", "observed_date"},
    "daily": {"series_id", "ts"}, "series": {"id", "name", "idx"},
    "macro_fred": {"series_id", "date"},
    "market_ohlc_history": {"provider", "symbol", "ts"},
    "archive_sources": {"source_id", "plan_tier", "entitlement_verified"},
    "archive_ingest_windows": {"source_id", "metric", "status", "data_points"},
    "archive_fred_vintages": {"series_id", "observed_date", "realtime_start"},
}


def _stamp(v, kind):
    if v is None:
        return None
    if kind == "date":
        if not isinstance(v, str) or v != dt.date.fromisoformat(v).isoformat():
            raise ValueError("Non-canonical archived date")
        return v
    if type(v) is not int or v < 0:
        raise ValueError("Invalid unix archived timestamp")
    return dt.datetime.fromtimestamp(v, UTC).date().isoformat()


def _read_series(db, query, kind):
    out = {}
    for metric, n, first, last in db.execute(query):
        if not isinstance(metric, str) or not metric or metric in out or type(n) is not int or n < 0:
            raise ValueError("Invalid metric/count in archive")
        out[metric] = {
            "metric": metric, "observations": n,
            "first_archived_utc": _stamp(first, kind),
            "last_archived_utc": _stamp(last, kind),
            "coverage_state": "ARCHIVED_RANGE_ONLY",
            "historical_completeness": "NOT_VERIFIED",
        }
    return out


def read_manifest(db_path, *, now=None):
    """Inspect four archive families in one coherent read-only SQLite snapshot."""
    if not isinstance(db_path, (str, Path)):
        raise ValueError("Explicit DB path required")
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError("Existing DB path required")
    path = path.resolve(strict=True)
    clock = now or dt.datetime.now(UTC)
    if not isinstance(clock, dt.datetime) or clock.tzinfo is None or clock.utcoffset() != dt.timedelta(0):
        raise ValueError("Aware UTC timestamp required")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        attached = db.execute("PRAGMA database_list").fetchall()
        if (db.execute("PRAGMA query_only").fetchone()[0] != 1
                or len(attached) != 1 or attached[0][1] != "main"
                or Path(attached[0][2]).resolve() != path):
            raise ValueError("Non-readonly or attached SQLite")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in tables & REQUIRED.keys():
            columns = {r[1] for r in db.execute("PRAGMA table_info(" + table + ")")}
            if not REQUIRED[table].issubset(columns):
                raise ValueError("Incomplete table schema: " + table)

        families = []
        for source, table, query, kind in SOURCES:
            dependencies = ("daily", "series") if source == "bitview" else (table,)
            initialized = all(dep in tables for dep in dependencies)
            found = _read_series(db, query, kind) if initialized else {}
            catalog = (set(CATALOG) if source == "researchbitcoin"
                       else {s[0] for s in FRED_SERIES} if source == "fred"
                       else set())
            entries = []
            for metric in sorted(set(found) | catalog):
                row = found.get(metric, {
                    "metric": metric, "observations": 0, "first_archived_utc": None,
                    "last_archived_utc": None, "historical_completeness": "NOT_VERIFIED",
                    "coverage_state": "NO_OBSERVATIONS" if initialized else "SCHEMA_NOT_INITIALIZED",
                })
                item = dict(row)
                if source == "researchbitcoin":
                    item["in_scalar_catalog"] = metric in CATALOG
                    declared = CATALOG.get(metric)
                    if declared:
                        item["raw_unit"] = declared.unit
                        item["raw_scale"] = declared.raw_scale
                        item["shape"] = "scalar"
                    else:
                        item["shape"] = "UNSUPPORTED_UNCATALOGUED"
                entries.append(item)
            families.append({
                "provider": source, "table": table, "schema_state": (
                    "PRESENT" if initialized else "NOT_INITIALIZED"),
                "historical_completeness": "NOT_VERIFIED",
                "series": entries,
            })
        rbn = families[0]
        tier = (db.execute("SELECT plan_tier,entitlement_verified FROM archive_sources "
                           "WHERE source_id='researchbitcoin'").fetchone()
                if "archive_sources" in tables else None)
        rbn["declared_tier_db"] = tier[0] if tier else None
        rbn["entitlement_flag_db"] = bool(tier[1]) if tier else False
        rbn["independent_tier2_access_proved"] = False

        windows = ([{"provider":p,"metric":m,"status":s,"windows":n,"reported_points":v}
                    for p,m,s,n,v in db.execute(
                        "SELECT source_id,metric,status,COUNT(*),SUM(data_points) "
                        "FROM archive_ingest_windows GROUP BY source_id,metric,status "
                        "ORDER BY source_id,metric,status")]
                   if "archive_ingest_windows" in tables else [])
        vintage_count = (db.execute("SELECT COUNT(*) FROM archive_fred_vintages").fetchone()[0]
                         if "archive_fred_vintages" in tables else None)
        db.rollback()
        if db.total_changes:
            raise AssertionError("Unexpected SQLite writes")
        return {
            "state": "ARCHIVED_RANGES_READ_ONLY", "generated_utc": clock.isoformat(),
            "db_location": "REDACTED", "execution_authorized": False,
            "real_tier2_entitlement_verified": False,
            "provider_availability": "NOT_CHECKED",
            "historical_completeness": "NOT_VERIFIED",
            "http_requests": 0, "sqlite_writes": 0,
            "sources": families, "window_summaries": windows,
            "fred_vintage_count": vintage_count,
            "limits": ["Stored min/max are not provider historical limits",
                       "Do not infer FRED monthly/daily gaps or Yahoo holidays from calendar days",
                       "ResearchBitcoin declared tier and DB flag are not independent token proof"],
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only historical archive coverage; no apply")
    parser.add_argument("--db", type=Path, required=True, help="Existing SQLite fixture/snapshot")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(read_manifest(args.db), indent=2, sort_keys=True))
        return 0
    except (ValueError, OSError, sqlite3.Error, OverflowError) as exc:
        print(json.dumps({"state":"BLOCKED","reason":type(exc).__name__,
                          "http_requests":0,"sqlite_writes":0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
