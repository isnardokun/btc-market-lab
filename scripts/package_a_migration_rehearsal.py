"""Package A legacy-schema coexistence and WAL-safe migration rehearsal.

STRICTLY disposable synthetic databases: marker + filename + TemporaryDirectory
prefix + adjacent locks. This is NOT the production migration CLI and cannot
be used on btc_research.db. No network, subprocess, cron, API or imports of
production configuration. Backup via existing WAL-aware SQLite backup code.
"""
from pathlib import Path
import hashlib
import json
import sqlite3

from ingestion.package_a_contracts import utc
from scripts.backup_db import create_backup
from scripts.market_r10e11_writer_exclusion import hold_writer_lock, WriterLockBusy
from storage import package_a_ledger

LOCK_NAMESPACE = "btc-research-global-writer-v1"
MARKER = ".package-a-migration-only"
MARKER_TEXT = "DISPOSABLE PACKAGE A LEGACY SQLITE REHEARSAL\n"
LEGACY_TABLES = (
    "series", "daily", "price_btc", "macro_fred",
    "market_derivatives", "market_raw_payloads", "market_request_lineage",
)
PACKAGE_TABLES = (
    "package_a_schema_migrations", "package_a_receipts",
    "package_a_observations", "package_a_cursors", "package_a_coverage",
    "package_a_request_provenance",
)

EXPECTED_KEYS = {
    "package_a_schema_migrations": ("version",),
    "package_a_receipts": ("receipt_id",),
    "package_a_observations": ("provider", "metric", "symbol", "frequency",
                               "observed_utc", "dimension_key"),
    "package_a_cursors": ("provider", "metric", "symbol", "frequency"),
    "package_a_coverage": ("provider", "metric", "symbol", "frequency"),
    "package_a_request_provenance": ("receipt_id",),
}
EXPECTED_FOREIGN_KEYS = {
    "package_a_observations": {("receipt_id", "package_a_receipts", "receipt_id")},
    "package_a_cursors": {("advanced_by_receipt", "package_a_receipts", "receipt_id")},
    "package_a_request_provenance": {("receipt_id", "package_a_receipts", "receipt_id")},
}


def _fixture_paths(db_path, lock_dir):
    if not isinstance(db_path, (str, Path)) or not isinstance(lock_dir, (str, Path)):
        raise ValueError("Explicit synthetic fixture paths required")
    db = Path(db_path)
    root = db.parent
    locks = Path(lock_dir)
    mark = root / MARKER
    if (db.name != "package_a_synthetic.db"
            or not root.name.startswith("package-a-fixture-")
            or any(p.is_symlink() for p in (root, db, locks, mark))
            or not db.is_file() or not locks.is_dir()
            or locks.resolve() != root.resolve() / "locks"
            or not mark.is_file() or mark.read_text(encoding="utf-8") != MARKER_TEXT):
        raise ValueError("Refusing non-synthetic database or lock path")
    return db, root


def _digest_legacy(conn):
    """Stable row-level digest of named legacy tables, without modifying them."""
    out = {}
    for table in LEGACY_TABLES:
        present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not present:
            raise ValueError("Legacy fixture missing expected table: " + table)
        # Identifiers are restricted to literal known constants above.
        column_names = [x[1] for x in conn.execute("PRAGMA table_info(" + table + ")")]
        if not column_names:
            raise ValueError("Legacy table has no columns")
        rows = conn.execute("SELECT * FROM " + table).fetchall()
        # Sorting by Python repr avoids relying on ROWID (WITHOUT ROWID tables).
        values = [repr(tuple(row)) for row in rows]
        raw = json.dumps(sorted(values), ensure_ascii=True).encode()
        out[table] = {"rows": len(rows), "sha256": hashlib.sha256(raw).hexdigest()}
    return out


def _layout(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'package_a_%'"
    )}


def _assert_new_schema(conn):
    present = _layout(conn)
    if present != set(PACKAGE_TABLES):
        raise ValueError("New schema tables missing/unexpected")
    for table in PACKAGE_TABLES:
        rows = list(conn.execute("PRAGMA table_info(" + table + ")"))
        have = {r[1] for r in rows}
        required = package_a_ledger.REQUIRED.get(table, set())
        if not required.issubset(have):
            raise ValueError("Package schema missing expected columns: " + table)
        primary_key = tuple(r[1] for r in sorted(rows, key=lambda x:x[5]) if r[5] > 0)
        if primary_key != EXPECTED_KEYS[table]:
            raise ValueError("Package schema primary key incompatible: " + table)
        fks = {(r[3],r[2],r[4]) for r in
               conn.execute("PRAGMA foreign_key_list(" + table + ")")}
        if fks != EXPECTED_FOREIGN_KEYS.get(table, set()):
            raise ValueError("Package schema foreign key incompatible: " + table)
    # A forged table with all column names but no PK/FK is NOT a valid v1.
    # Also validate that the expected performance indexes were really created.
    for index in ("idx_package_a_receipts_window",
                  "idx_package_a_observations_dates"):
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (index,)
        ).fetchone():
            raise ValueError("Missing Package A index: " + index)
    if conn.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("SQLite foreign key integrity check failed")
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("SQLite integrity check failed")


def rehearse_additive_migration(db_path, lock_dir, *, now, crash_after=None):
    """Back up and migrate ONLY a disposable fixture under the SAME global lock.

    At crash_after='after_first_ddl', a synthetic crash occurs after the
    first CREATE TABLE, proving the explicit BEGIN IMMEDIATE rolls back DDL.
    Backup is retained even on failure. Never drops/replaces legacy tables.
    """
    db_path, root = _fixture_paths(db_path, lock_dir)
    if crash_after not in (None, "after_first_ddl", "before_commit"):
        raise ValueError("Unrecognized synthetic crash point")
    stamp = utc(now).isoformat().replace("+00:00", "Z")
    try:
        with hold_writer_lock(lock_dir, namespace=LOCK_NAMESPACE):
            # Only a disposable database path can reach this existing WAL-
            # safe backup implementation. Retain all snapshots for QA.
            destination = create_backup(db_path, backup_dir=root/"backup-only", retain=100)
            with sqlite3.connect(db_path, isolation_level=None) as conn:
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA busy_timeout=30000")
                legacy_before = _digest_legacy(conn)
                existing_schema = _layout(conn)
                conn.execute("BEGIN IMMEDIATE")
                try:
                    if crash_after == "after_first_ddl":
                        conn.execute(package_a_ledger.DDL[0])
                        raise package_a_ledger.SimulatedCrash("after_first_ddl")
                    package_a_ledger.initialize(conn, now=stamp)
                    _assert_new_schema(conn)
                    if _digest_legacy(conn) != legacy_before:
                        raise ValueError("Legacy rows changed during migration")
                    if crash_after == "before_commit":
                        raise package_a_ledger.SimulatedCrash("before_commit")
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
                legacy_after = _digest_legacy(conn)
                if legacy_before != legacy_after:
                    raise ValueError("Legacy rows changed after commit")
                _assert_new_schema(conn)
                return {
                    "state": "PACKAGE_A_MIGRATION_REHEARSAL_PASS",
                    "fixture_only": True,
                    "migrated_schema_version": package_a_ledger.MIGRATION_VERSION,
                    "previous_package_tables": sorted(existing_schema),
                    "package_tables": sorted(_layout(conn)),
                    "legacy_digest_before": legacy_before,
                    "legacy_digest_after": legacy_after,
                    "backup": str(destination),
                    "backup_integrity": "ok",
                    "sql_integrity": "ok",
                    "foreign_key_errors": 0,
                    "real_provider_entitlement": "NOT_VERIFIED",
                    "historical_completeness": "NOT_VERIFIED",
                    "production_authorized": False,
                    "real_api_requests": 0,
                }
    except WriterLockBusy:
        return {
            "state": "BLOCKED_WRITER_BUSY",
            "sqlite_connections_started": 0,
            "backup_started": False,
            "production_authorized": False,
            "real_api_requests": 0,
        }
