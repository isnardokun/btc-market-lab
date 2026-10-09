#!/usr/bin/env python3
"""Online SQLite WAL-safe, atomic database backup for historical research.

Uses sqlite3.Connection.backup() instead of shutil.copy2(): copying only the
SQLite main file is unsafe while WAL transactions are uncheckpointed.
Never mutates or vacuums the live database.
"""
import datetime as dt
import os
from pathlib import Path
import sqlite3
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "db" / "btc_research.db"
BACKUP_DIR = ROOT / "db" / "backups"


def create_backup(db_path=DB_PATH, backup_dir=BACKUP_DIR, *, stamp=None,
                  retain=30):
    source = Path(db_path)
    dest_dir = Path(backup_dir)
    if not source.is_file():
        raise FileNotFoundError("SQLite fuente no encontrada")
    if not 1 <= retain <= 100:
        raise ValueError("retain debe estar entre 1 y 100")
    dest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest_dir.chmod(0o700)
    now = stamp or dt.datetime.now(dt.timezone.utc)
    label = now.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = dest_dir / ("btc_research_" + label + ".db")
    fd, temp_name = tempfile.mkstemp(prefix=".backup-", suffix=".db",
                                    dir=dest_dir)
    os.close(fd)
    tmp = Path(temp_name)
    try:
        with sqlite3.connect(source.resolve().as_uri() + "?mode=ro",
                             uri=True, timeout=30) as active:
            if active.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("La SQLite fuente no pasó integrity_check")
            with sqlite3.connect(tmp) as snapshot:
                active.backup(snapshot, pages=256, sleep=0.1)
                if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup SQLite no pasó integrity_check")
        os.chmod(tmp, 0o600)
        if destination.exists():
            raise FileExistsError("El backup con mismo timestamp ya existe")
        os.replace(tmp, destination)
        old = sorted(dest_dir.glob("btc_research_*.db"),
                     key=lambda p:p.stat().st_mtime, reverse=True)
        for backup in old[retain:]:
            backup.unlink()
        return destination
    finally:
        if tmp.exists():
            tmp.unlink()


def backup_db():
    try:
        path = create_backup()
        print("Backup SQLite WAL-safe confirmado: " + str(path))
        print("integrity_check original: OK; copia: OK")
        return 0
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        print("BACKUP FALLÓ; no iniciar backfill. Causa: "
              + type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(backup_db())
