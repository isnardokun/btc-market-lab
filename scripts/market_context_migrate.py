#!/usr/bin/env python3
"""Opt-in migration of market-context tables after verified WAL-safe backup."""
import argparse
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ingestion.config import DB_PATH
from scripts.backup_db import create_backup
from storage.market_context import install,installed,request_lineage_installed,install_request_lineage

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
        with sqlite3.connect(args.db) as db:
            if installed(db) and request_lineage_installed(db):
                print("Market context SQLite v2 schema already installed; no migration needed.")
                return 0
        backup=create_backup(args.db,args.db.parent/"backups")
        with sqlite3.connect(args.db,timeout=30) as db:
            db.execute("PRAGMA foreign_keys=ON")
            if not installed(db):
                install(db)
            with db:
                if not request_lineage_installed(db):
                    install_request_lineage(db)
                status=db.execute("PRAGMA integrity_check").fetchone()[0]
                if status!="ok":
                    raise RuntimeError("SQLite post-migration integrity_check failed")
                if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise RuntimeError("SQLite post-migration foreign_key_check failed")
                if db.execute(
                    "SELECT COUNT(*) FROM market_context_migrations WHERE version=2"
                ).fetchone()[0]!=1:
                    raise RuntimeError("Market request lineage v2 migration version absent")
        print("Market context SQLite v2 installed; WAL-safe checked backup retained.")
        print("Backup location: "+str(backup))
        return 0
    except (OSError,ValueError,sqlite3.Error,RuntimeError) as exc:
        print("STOP: migration failed: "+type(exc).__name__,file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
