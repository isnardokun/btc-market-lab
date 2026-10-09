#!/usr/bin/env python3
"""Safe additive SQLite archive schema migration; never edits market observations.

Default --plan reports objects to create without opening the DB for writing.
--apply always creates and checks a WAL-safe SQLite backup first.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingestion.config import DB_PATH
from scripts.backup_db import create_backup
from storage.archive_schema import ensure_archive_schema, SCHEMA_VERSION


def inspect(db_path):
    path=Path(db_path)
    if not path.is_file():
        raise FileNotFoundError("La base SQLite no existe")
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True) as conn:
        names={r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        triggers={r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'")}
        report={"version_target":SCHEMA_VERSION,
                "core_present":sorted(x for x in (
                    "daily","series","price_btc","macro_fred",
                    "onchain_external_observations","market_ohlc_history"
                ) if x in names),
                "archive_tables":sorted(x for x in names if x.startswith("archive_")),
                "archive_triggers":sorted(x for x in triggers if x.startswith("trg_archive_"))}
    return report


def run(db_path, *, apply=False, backup_dir=None):
    before=inspect(db_path)
    if not apply:
        return {"mode":"PLAN","changes":"Sólo DDL aditivo; sin reescribir observaciones",
                "existing":before}
    dest=Path(backup_dir) if backup_dir else Path(db_path).parent/"backups"
    backup=create_backup(db_path,backup_dir=dest)
    with sqlite3.connect(db_path,timeout=30) as conn:
        conn.execute("PRAGMA busy_timeout=30000")
        ensure_archive_schema(conn)
        conn.commit()
        result=conn.execute("PRAGMA quick_check").fetchone()[0]
        if result!="ok":
            raise RuntimeError("SQLite no pasó quick_check después de la migración")
    return {"mode":"APPLY","backup_local":str(backup),
            "integrity":"ok","after":inspect(db_path)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",type=Path,default=Path(DB_PATH))
    parser.add_argument("--apply",action="store_true",
                        help="AUTORIZAR backup WAL-safe más migración aditiva")
    parser.add_argument("--backup-dir",type=Path,help="Backup fuera del repositorio público")
    args=parser.parse_args()
    try:
        print(json.dumps(run(args.db,apply=args.apply,backup_dir=args.backup_dir),
                         indent=2,ensure_ascii=False))
        return 0
    except (OSError,sqlite3.Error,RuntimeError,ValueError) as exc:
        print("SCHEMA STOP: "+type(exc).__name__,file=sys.stderr)
        return 2


if __name__=="__main__":
    sys.exit(main())
