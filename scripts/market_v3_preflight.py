#!/usr/bin/env python3
"""R10-C read-only SQLite pre/post fingerprint. Does NOT migrate or back up data.

PRE: on the existing v2 DB, capture strict legacy hashes and quality checks.
POST: on a separately authorized v3 migration, compare ALL legacy hashes with
the PRE JSON saved externally by the operator. This tool never executes DDL,
HTTP, backups, scheduler jobs, or SQL writes.

The PRE result does not authorize production migration. Run existing RAW,
TEMPORAL, and LINEAGE read-only auditors separately as independent gates.
"""
import argparse
from contextlib import closing, nullcontext
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ingestion.config import DB_PATH
from scripts.market_readonly_snapshot import verify_read_snapshot

V2_RECEIPT_COLS = (
    "request_id", "run_id", "provider", "stream", "symbol",
    "interval_label", "endpoint_path", "requested_end_ms",
    "requested_limit", "attempted_utc", "ended_utc", "http_attempts",
    "status", "error_class", "raw_sha256", "returned_rows",
    "persisted_rows", "first_observed_utc", "last_observed_utc",
)
# For the v2->v3 transition, legacy logical projections stay identical.
# Migration v3 adds a marker, so explicitly hash only migration rows 1 and 2.
LEGACY_PROJECTIONS = {
    "market_context_migrations": (("version", "applied_at_utc"), "WHERE version IN (1,2)"),
    "market_source_cursors": (None, ""),
    "market_source_runs": (None, ""),
    "market_raw_payloads": (None, ""),
    "market_derivatives": (None, ""),
    "market_etf_flows": (None, ""),
    "market_calendar_events": (None, ""),
    "market_context_revisions": (None, ""),
    "market_request_lineage": (V2_RECEIPT_COLS, ""),
}
REQUIRED = set(LEGACY_PROJECTIONS)
V3_COLUMNS = {"direction", "requested_start_ms"}


def _v3_layout_issues(db):
    """Check *constraints*, not just names, before accepting a v3 POST."""
    issues = []
    columns = {r[1]: r for r in db.execute(
        "PRAGMA table_info(market_request_lineage)")}
    direction = columns.get("direction")
    start_ms = columns.get("requested_start_ms")
    if (direction is None or direction[2].upper() != "TEXT" or
            direction[3] != 1):
        issues.append("v3 lineage direction must be NOT NULL TEXT")
    if start_ms is None or start_ms[2].upper() != "INTEGER":
        issues.append("v3 lineage requested_start_ms must be INTEGER")

    receipt_sql = db.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type='table' AND name='market_request_lineage'"
    ).fetchone()
    normalized = "".join(((receipt_sql or [""])[0] or "").lower().split())
    if "check(directionin('backward','forward'))" not in normalized:
        issues.append("v3 lineage direction CHECK missing")

    acq = {r[1]: r for r in db.execute(
        "PRAGMA table_info(market_request_acquisitions)")}
    required = {"acquired_id", "request_id", "provider", "endpoint_path",
                "raw_sha256", "acquired_utc"}
    if required - acq.keys():
        issues.append("v3 acquisitions required columns missing")
        return issues
    if acq["acquired_id"][5] != 1:
        issues.append("v3 acquisitions acquired_id must be PRIMARY KEY")
    for key in required:
        if acq[key][2].upper() != "TEXT":
            issues.append("v3 acquisitions type mismatch: " + key)
        if key != "acquired_id" and acq[key][3] != 1:
            issues.append("v3 acquisitions NOT NULL missing: " + key)

    foreign = {(x[3], x[2], x[4]) for x in db.execute(
        "PRAGMA foreign_key_list(market_request_acquisitions)")}
    if ("request_id", "market_request_lineage", "request_id") not in foreign:
        issues.append("v3 acquisitions lineage FOREIGN KEY missing")
    if ("raw_sha256", "market_raw_payloads", "sha256") not in foreign:
        issues.append("v3 acquisitions raw FOREIGN KEY missing")

    # PRAGMA index_info on SQLite-managed index names: quote safely.
    unique_request = False
    for idx in db.execute("PRAGMA index_list(market_request_acquisitions)"):
        if not idx[2]:
            continue
        quoted = '"' + idx[1].replace('"', '""') + '"'
        indexed = [r[2] for r in db.execute(
            "PRAGMA index_info(" + quoted + ")")]
        if indexed == ["request_id"]:
            unique_request = True
            break
    if not unique_request:
        issues.append("v3 acquisitions UNIQUE(request_id) missing")
    return issues


def _record_value(value):
    if isinstance(value, bytes):
        return ["blob", len(value), hashlib.sha256(value).hexdigest()]
    if isinstance(value, float):
        return ["real", repr(value)]
    if isinstance(value, int):
        return ["int", str(value)]
    if value is None:
        return ["null"]
    return ["text", str(value)]


def _fingerprint(db, table, columns, condition):
    meta = db.execute(f"PRAGMA table_info({table})").fetchall()
    available = {r[1] for r in meta}
    select_cols = tuple(columns or (r[1] for r in meta))
    if not set(select_cols) <= available or not meta:
        raise ValueError(f"Missing legacy columns in {table}")
    pk = tuple(r[1] for r in sorted(meta, key=lambda r: r[5]) if r[5])
    if not pk:
        raise ValueError(f"Cannot fingerprint {table} without a stable primary key")
    # The explicit projection excludes new v3 columns. ORDER BY the original
    # natural primary key, not rowid, to avoid false drift after ALTER TABLE.
    sql = (f"SELECT {','.join(select_cols)} FROM {table} {condition} "
           f"ORDER BY {','.join(pk)}")
    h = hashlib.sha256()
    count = 0
    for row in db.execute(sql):
        payload = json.dumps([_record_value(x) for x in row],
                             separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        h.update(str(len(payload)).encode("ascii") + b":" + payload)
        count += 1
    return {"rows": count, "sha256": h.hexdigest()}


def inspect(path, *, phase="pre", expected_backward=None, baseline=None, connection=None):
    """Return evidence; fail closed on defects. No DB/file/network writes."""
    p = Path(path)
    if phase not in ("pre", "post"):
        raise ValueError("Phase must be pre or post")
    if not p.is_file():
        raise FileNotFoundError("Existing SQLite database required; never create one")
    if expected_backward is not None and expected_backward < 0:
        raise ValueError("Expected backward count must be non-negative")
    if phase == "post" and baseline is None:
        raise ValueError("POST requires an independently captured PRE baseline")
    if phase == "pre" and baseline is not None:
        raise ValueError("PRE cannot compare against another baseline")

    report = {
        "contract": "R10-C-READONLY-PREFLIGHT-v1",
        "phase": phase,
        "quality_state": "BLOCKED",
        "authorization": "EVIDENCE_ONLY_NO_MIGRATION_NO_HTTP",
        "historical_completeness": "NOT_VERIFIED",
        "checks": {},
        "snapshot": {},
        "issues": [],
    }
    owned = connection is None
    handle = (closing(sqlite3.connect(p.resolve().as_uri() + "?mode=ro",
                                      uri=True, timeout=30)) if owned
              else nullcontext(connection))
    with handle as db:
        if owned:
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA foreign_keys=ON")
            # Keep a coherent read transaction for standalone PRE/POST too.
            db.execute("BEGIN")
        else:
            verify_read_snapshot(db, p)
        try:
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            missing = REQUIRED - tables
            if missing:
                report["issues"].append("Missing tables: " + ",".join(sorted(missing)))
                return report

            integrity = db.execute("PRAGMA integrity_check").fetchone()
            fk = db.execute("PRAGMA foreign_key_check").fetchone()
            report["checks"]["integrity_check"] = integrity is not None and integrity[0] == "ok"
            report["checks"]["foreign_key_check"] = fk is None
            if not report["checks"]["integrity_check"]:
                report["issues"].append("SQLite integrity_check failed")
            if not report["checks"]["foreign_key_check"]:
                report["issues"].append("SQLite foreign_key_check failed")

            versions = {r[0] for r in db.execute(
                "SELECT version FROM market_context_migrations")}
            report["checks"]["versions"] = sorted(versions)
            if not {1, 2} <= versions:
                report["issues"].append("Missing legacy migration v1/v2 marker")

            cols = {r[1] for r in db.execute(
                "PRAGMA table_info(market_request_lineage)")}
            if not set(V2_RECEIPT_COLS) <= cols:
                report["issues"].append("Incomplete legacy receipt projection")
                return report
            has_v3 = V3_COLUMNS <= cols and "market_request_acquisitions" in tables and 3 in versions
            report["checks"]["v3_schema_present"] = has_v3
            if phase == "pre" and (3 in versions or V3_COLUMNS & cols or
                                    "market_request_acquisitions" in tables):
                report["issues"].append("PRE expects existing v2, NOT migrated v3")
            if phase == "post" and not has_v3:
                report["issues"].append("POST requires fully installed v3")
            if phase == "post" and has_v3:
                report["issues"].extend(_v3_layout_issues(db))

            for table, (projection, condition) in LEGACY_PROJECTIONS.items():
                report["snapshot"][table] = _fingerprint(db, table, projection, condition)

            count = report["snapshot"]["market_request_lineage"]["rows"]
            report["checks"]["backward_receipts"] = count
            if expected_backward is not None and count != expected_backward:
                report["issues"].append("Backward receipts disagree with expected baseline")
            if has_v3:
                altered = db.execute(
                    "SELECT COUNT(*) FROM market_request_lineage "
                    "WHERE direction IS NOT 'backward' OR requested_start_ms IS NOT NULL"
                ).fetchone()[0]
                acquired = db.execute(
                    "SELECT COUNT(*) FROM market_request_acquisitions"
                ).fetchone()[0]
                report["checks"]["changed_backward_receipts"] = altered
                report["checks"]["acquisitions"] = acquired
                if altered or acquired:
                    report["issues"].append("R10-C may not alter legacy receipts or acquire requests")
            forward_cursors = db.execute(
                "SELECT COUNT(*) FROM market_source_cursors WHERE stream LIKE 'forward_v1_%'"
            ).fetchone()[0]
            report["checks"]["forward_cursors"] = forward_cursors
            if forward_cursors:
                report["issues"].append("Forward cursor appeared before R10-D")

            if baseline is not None:
                if not isinstance(baseline, dict) or (
                    baseline.get("contract") != report["contract"] or
                    baseline.get("phase") != "pre" or
                    baseline.get("quality_state") != "PASS_READ_ONLY_PRECHECK" or
                    not isinstance(baseline.get("snapshot"), dict)
                ):
                    report["issues"].append("Invalid or unapproved PRE evidence")
                else:
                    for table in LEGACY_PROJECTIONS:
                        if baseline["snapshot"].get(table) != report["snapshot"].get(table):
                            report["issues"].append(f"Legacy fingerprint changed: {table}")
                    if baseline.get("checks", {}).get("backward_receipts") != count:
                        report["issues"].append("Backward receipt count drifted after PRE")
                    if baseline.get("checks", {}).get("forward_cursors") != 0:
                        report["issues"].append("PRE evidence already had forward cursors")
        finally:
            if owned:
                db.rollback()  # standalone inspector owns only its own transaction
    if not report["issues"]:
        report["quality_state"] = ("PASS_READ_ONLY_PRECHECK" if phase == "pre"
                                   else "PASS_READ_ONLY_POSTCHECK")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=Path(DB_PATH))
    ap.add_argument("--phase", choices=("pre", "post"), default="pre")
    ap.add_argument("--expected-backward", type=int)
    ap.add_argument("--baseline", type=Path,
                    help="PRE JSON file; required for POST, read only")
    args = ap.parse_args(argv)
    try:
        baseline = None
        if args.baseline is not None:
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        result = inspect(args.db, phase=args.phase,
                         expected_backward=args.expected_backward, baseline=baseline)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if result["quality_state"].startswith("PASS_") else 1
    except (OSError, sqlite3.Error, ValueError, TypeError, json.JSONDecodeError) as exc:
        # Avoid printing DB paths, raw provider bodies or credentials.
        print(json.dumps({"quality_state": "PREFLIGHT_UNAVAILABLE",
                          "error_class": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
