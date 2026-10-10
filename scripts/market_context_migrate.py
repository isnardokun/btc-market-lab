#!/usr/bin/env python3
"""Opt-in migration of market-context tables after verified WAL-safe backup."""
import argparse
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ingestion.config import DB_PATH
from scripts.backup_db import create_backup
from scripts.market_v3_preflight import _v3_layout_issues
from storage.market_context import install, installed, request_lineage_installed, install_request_lineage

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply",action="store_true",help="Backup original and install additive SQLite tables")
    p.add_argument("--db",type=Path,default=Path(DB_PATH))
    args=p.parse_args(argv)
    if not args.apply:
        print("PLAN: create WAL-safe checked backup, then add market_* SQLite tables; no rows modified.")
        return 0
    if not args.db.is_file():
        print("STOP: original SQLite not found",file=sys.stderr);return 2
    try:
        with sqlite3.connect(args.db,timeout=30) as db:
            db.execute("PRAGMA foreign_keys=ON")
            if installed(db) and request_lineage_installed(db):
                v2 = db.execute(
                    "SELECT COUNT(*) FROM market_context_migrations WHERE version=2"
                ).fetchone()[0]
                v3 = db.execute(
                    "SELECT COUNT(*) FROM market_context_migrations WHERE version=3"
                ).fetchone()[0]
                if v2 and v3:
                    # Never mistake a version marker for a healthy schema.
                    _integrity_check_connection(db)
                    print("Market context SQLite v1+v2+v3 already fully installed.")
                    return 0
                if v2 and not v3:
                    # v1+v2 present, v3 absent — upgrade in-place (ALTER, no re-create)
                    print("Market context SQLite v1+v2 present; adding v3 (direction + acquisitions)...")
                    backup=create_backup(args.db,args.db.parent/"backups")
                    _upgrade_v2_to_v3(args.db)
                    _integrity_check(args.db)
                    print("Market context SQLite v3 added. Backup retained.")
                    print("Backup location: "+str(backup))
                    return 0
            # No lineage, or v1 without lineage: full install with backup
            backup=create_backup(args.db,args.db.parent/"backups")
        with sqlite3.connect(args.db,timeout=30) as db:
            db.execute("PRAGMA foreign_keys=ON")
            if not installed(db):
                install(db)
            # Fresh installs must create v3 lineage too; older v1 installs
            # must receive the same additive upgrades before declaring success.
            install_request_lineage(db)
        _integrity_check(args.db)
        print("Market context SQLite v1+v2+v3 installed; WAL-safe checked backup retained.")
        print("Backup location: "+str(backup))
        return 0
    except (OSError,ValueError,sqlite3.Error,RuntimeError) as exc:
        print("STOP: migration failed: "+type(exc).__name__+": "+str(exc),file=sys.stderr)
        return 1

def _upgrade_v2_to_v3(db_path):
    """ALTER v2 lineage table to v3 + create acquisitions. Called after WAL-safe backup."""
    with sqlite3.connect(db_path,timeout=30) as db:
        db.execute("PRAGMA foreign_keys=ON")
        existing_cols = {r[1] for r in db.execute("PRAGMA table_info(market_request_lineage)")}
        # Python sqlite3 legacy mode does not implicitly BEGIN for DDL.
        # An explicit write transaction makes ALTER TABLE and marker atomic.
        with db:
            db.execute("BEGIN IMMEDIATE")
            if "direction" not in existing_cols:
                db.execute(
                    "ALTER TABLE market_request_lineage "
                    "ADD COLUMN direction TEXT NOT NULL DEFAULT 'backward' "
                    "CHECK(direction IN ('backward','forward'))"
                )
            if "requested_start_ms" not in existing_cols:
                db.execute(
                    "ALTER TABLE market_request_lineage "
                    "ADD COLUMN requested_start_ms INTEGER"
                )
            db.execute("""
                CREATE TABLE IF NOT EXISTS market_request_acquisitions(
                 acquired_id TEXT PRIMARY KEY,
                 request_id TEXT NOT NULL REFERENCES market_request_lineage(request_id),
                 provider TEXT NOT NULL,
                 endpoint_path TEXT NOT NULL,
                 raw_sha256 TEXT NOT NULL REFERENCES market_raw_payloads(sha256),
                 acquired_utc TEXT NOT NULL,
                 UNIQUE(request_id)
                )
            """)
            db.execute("INSERT OR IGNORE INTO market_context_migrations(version,applied_at_utc)"
                       " VALUES(3,datetime('now'))")
            # Check the uncommitted DDL before exiting `with db`. A failure
            # rolls back ALTER/TABLE/marker together, not after commit.
            _integrity_check_connection(db)

def _integrity_check_connection(db):
    """Validate real v3 constraints and integrity on the current transaction.

    Called BEFORE v2->v3 DDL commits, as well as for existing v3 databases.
    A version marker alone is not proof that v3 is installed correctly.
    """
    status = db.execute("PRAGMA integrity_check").fetchone()[0]
    if status != "ok":
        raise RuntimeError(f"PRAGMA integrity_check failed: {status}")
    fk_failures = db.execute("PRAGMA foreign_key_check").fetchone()
    if fk_failures is not None:
        raise RuntimeError(f"PRAGMA foreign_key_check failed: {fk_failures}")
    lineage_cols = {r[1] for r in db.execute("PRAGMA table_info(market_request_lineage)")}
    required = {"direction", "requested_start_ms", "request_id", "provider",
                "stream", "requested_end_ms", "status", "raw_sha256"}
    missing = required - lineage_cols
    if missing:
        raise RuntimeError(f"market_request_lineage missing columns: {missing}")
    acq_cols = {r[1] for r in db.execute("PRAGMA table_info(market_request_acquisitions)")}
    acq_required = {"acquired_id", "request_id", "raw_sha256", "provider",
                    "endpoint_path", "acquired_utc"}
    missing_acq = acq_required - acq_cols
    if missing_acq:
        raise RuntimeError(f"market_request_acquisitions missing columns: {missing_acq}")
    # Do not silently accept a forged acquisitions table with missing
    # UNIQUE(request_id), FK integrity, column types or lineage CHECK.
    shape_issues = _v3_layout_issues(db)
    if shape_issues:
        raise RuntimeError("Schema v3 constraints invalid: " + "; ".join(shape_issues))


def _integrity_check(db_path):
    with sqlite3.connect(db_path, timeout=30) as db:
        db.execute("PRAGMA foreign_keys=ON")
        _integrity_check_connection(db)

if __name__=="__main__":
    raise SystemExit(main())
