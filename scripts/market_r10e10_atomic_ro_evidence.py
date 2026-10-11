"""R10-E.10: one SQLite read transaction for E7/E8/E9 evidence.

A single mode=ro, query_only=ON connection with BEGIN reads each archived
row ONCE to construct both the range manifest and the internal gap analysis.
Unlike independently produced E7/E8 reports, this bundle can ground a claim
that its aggregates came from one consistent SQLite snapshot. It still NEVER
proves API availability, provider entitlements or historical completeness.

No CLI, no API calls, no scheduler, no default DB, no SQL modifications.
"""
from contextlib import closing
import datetime as dt
from pathlib import Path
import sqlite3

from ingestion.researchbitcoin_catalog import CATALOG
from ingestion.config import FRED_SERIES
from scripts import market_r10e7_ro_archive_manifest as e7
from scripts import market_r10e8_ro_internal_gap_audit as e8
from scripts.market_r10e9_evidence_review_queue import reconcile_review_queue

UTC = dt.timezone.utc


def _catalog(provider):
    if provider == "researchbitcoin":
        return set(CATALOG)
    if provider == "fred":
        return {slug for slug, _, _, _ in FRED_SERIES}
    return set()


def _inspect_one_snapshot(db, *, now, max_observations):
    # No reconnection or nested read transactions after this point.
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for table in tables & e7.REQUIRED.keys():
        cols = {r[1] for r in db.execute("PRAGMA table_info(" + table + ")")}
        if not e7.REQUIRED[table].issubset(cols):
            raise ValueError("Incomplete E7 archive schema: " + table)
    for table in tables & e8.REQUIRED.keys():
        cols = {r[1] for r in db.execute("PRAGMA table_info(" + table + ")")}
        if not e8.REQUIRED[table].issubset(cols):
            raise ValueError("Incomplete E8 gap schema: " + table)

    archive_sources = []
    gap_sources = []
    remaining = max_observations
    for provider, (table, query) in e8.SQL.items():
        dependencies = ("daily", "series") if provider == "bitview" else (table,)
        present = all(name in tables for name in dependencies)
        grouped = {}
        if present:
            for metric, raw in db.execute(query):
                remaining -= 1
                if remaining < 0:
                    raise ValueError("Refusing incomplete snapshot due to row budget")
                if not isinstance(metric, str) or not metric:
                    raise ValueError("Empty archived metric name")
                date = e8._calendar_day(raw, provider in ("bitview", "yahoo"))
                grouped.setdefault(metric, []).append(date)

        archived = []
        for metric in sorted(_catalog(provider) | set(grouped)):
            dates = grouped.get(metric, [])
            row = {
                "metric": metric,
                "observations": len(dates),
                "first_archived_utc": min(dates).isoformat() if dates else None,
                "last_archived_utc": max(dates).isoformat() if dates else None,
                "coverage_state": (
                    "ARCHIVED_RANGE_ONLY" if dates else
                    "NO_OBSERVATIONS" if present else "SCHEMA_NOT_INITIALIZED"),
                "historical_completeness": "NOT_VERIFIED",
            }
            if provider == "researchbitcoin":
                declared = CATALOG.get(metric)
                row["in_scalar_catalog"] = declared is not None
                if declared:
                    row.update(raw_unit=declared.unit, raw_scale=declared.raw_scale,
                               shape="scalar")
                else:
                    row["shape"] = "UNSUPPORTED_UNCATALOGUED"
            archived.append(row)

        archive_sources.append({
            "provider": provider, "table": table,
            "schema_state": "PRESENT" if present else "NOT_INITIALIZED",
            "historical_completeness": "NOT_VERIFIED",
            "series": archived,
        })
        gap_sources.append({
            "provider": provider,
            "schema_state": "PRESENT" if present else "NOT_INITIALIZED",
            "historical_completeness": "NOT_VERIFIED",
            "series": [e8._classify(provider, metric, grouped[metric])
                       for metric in sorted(grouped)],
        })

    rbn = archive_sources[0]
    tier = (db.execute("SELECT plan_tier,entitlement_verified FROM archive_sources "
                       "WHERE source_id='researchbitcoin'").fetchone()
            if "archive_sources" in tables else None)
    rbn["declared_tier_db"] = tier[0] if tier else None
    rbn["entitlement_flag_db"] = bool(tier[1]) if tier else False
    rbn["independent_tier2_access_proved"] = False

    windows = (
        [{"provider": p, "metric": m, "status": s, "windows": n,
          "reported_points": points}
         for p, m, s, n, points in db.execute(
            "SELECT source_id,metric,status,COUNT(*),SUM(data_points) "
            "FROM archive_ingest_windows GROUP BY source_id,metric,status "
            "ORDER BY source_id,metric,status")]
        if "archive_ingest_windows" in tables else []
    )
    vintages = (db.execute("SELECT COUNT(*) FROM archive_fred_vintages").fetchone()[0]
                if "archive_fred_vintages" in tables else None)

    manifest = {
        "state": "ARCHIVED_RANGES_READ_ONLY",
        "generated_utc": now.isoformat(),
        "db_location": "REDACTED",
        "execution_authorized": False,
        "real_tier2_entitlement_verified": False,
        "provider_availability": "NOT_CHECKED",
        "historical_completeness": "NOT_VERIFIED",
        "http_requests": 0, "sqlite_writes": 0,
        "sources": archive_sources,
        "window_summaries": windows,
        "fred_vintage_count": vintages,
    }
    gaps = {
        "state": "INTERNAL_GAP_CANDIDATES_READ_ONLY",
        "generated_utc": now.isoformat(),
        "db_location": "REDACTED",
        "execution_authorized": False,
        "real_provider_availability_verified": False,
        "historical_completeness": "NOT_VERIFIED",
        "http_requests": 0, "sqlite_writes": 0,
        "calendar_policy": "SOURCE_AWARE_NO_FAKE_TRADING_OR_PUBLICATION_DAYS",
        "sources": gap_sources,
    }
    # E9's input validation and source-calendar controls remain mandatory.
    reviewed = reconcile_review_queue(manifest, gaps)
    if reviewed["atomic_cross_report_snapshot_verified"] is not False:
        raise AssertionError("Standalone E9 must not assert atomicity")
    reviewed = {
        **reviewed,
        "atomic_cross_report_snapshot_verified": True,
        "independent_reports_consistent_on_aggregates_only": False,
        "snapshot_evidence": "ONE_SQLITE_READ_TRANSACTION_AND_ROW_SCAN",
        "caveat": (
            "Both E7-compatible and E8-compatible reports were created from "
            "the same SQLite read transaction, but local history is not "
            "provider availability and no network entitlement is verified"
        ),
    }
    return manifest, gaps, reviewed


def inspect_atomic_fixture(db_path, *, now, max_observations=1_000_000):
    """Read one explicitly supplied database; never mutate or access others.

    The caller must authorize any nonfixture path in a separate workflow.
    Hermes QA is strictly confined to TemporaryDirectory synthetic SQLite.
    """
    if not isinstance(db_path, (str, Path)):
        raise ValueError("Explicit DB path required")
    if (not isinstance(now, dt.datetime) or now.tzinfo is None
            or now.utcoffset() != dt.timedelta(0)):
        raise ValueError("Explicit UTC-aware clock required")
    if type(max_observations) is not int or not 1 <= max_observations <= 1_000_000:
        raise ValueError("Invalid bounded observation budget")
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError("Existing SQLite fixture required")
    path = path.resolve(strict=True)

    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        attached = db.execute("PRAGMA database_list").fetchall()
        if (db.execute("PRAGMA query_only").fetchone()[0] != 1
                or len(attached) != 1 or attached[0][1] != "main"
                or Path(attached[0][2]).resolve() != path):
            raise ValueError("Unexpected attached or non-read-only connection")
        manifest, gaps, reviewed = _inspect_one_snapshot(
            db, now=now, max_observations=max_observations)
        db.rollback()
        if db.total_changes:
            raise AssertionError("No SQL changes allowed")

    return {
        "state": "ATOMIC_LOCAL_ARCHIVE_REVIEW_ONLY",
        "snapshot_scope": "ONE_READ_TRANSACTION",
        "source_connection_count": 1,
        "db_location": "REDACTED",
        "archived_range_manifest": manifest,
        "gap_classification": gaps,
        "review": reviewed,
        "historical_completeness": "NOT_VERIFIED",
        "real_provider_availability_verified": False,
        "real_tier2_entitlement_verified": False,
        "execution_authorized": False,
        "request_ready": False,
        "http_requests": 0,
        "sqlite_writes": 0,
    }
