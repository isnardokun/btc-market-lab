#!/usr/bin/env python3
"""
scripts/backup_db.py
====================
Backup de la BD SQLite: copia + VACUUM para compactar.
Se ejecuta manualmente o antes de migraciones.
Guarda backups en: db/backups/
"""
import sys, os, shutil, datetime, sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db" / "btc_research.db"
BACKUP_DIR = BASE_DIR / "db" / "backups"
TODAY = datetime.date.today().strftime("%Y%m%d")

def backup_db():
    """Crea backup de la BD con timestamp."""
    if not DB_PATH.exists():
        print(f"BD no encontrada: {DB_PATH}")
        return

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    # Verificar integridad antes de backup
    print("Verificando integridad de BD...")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("PRAGMA integrity_check")
    result = cur.fetchone()
    conn.close()

    if result[0] != "ok":
        print(f"⚠️  INTEGRITY CHECK FAILED: {result}")
        return

    size_mb = DB_PATH.stat().st_size / 1e6
    print(f"  BD OK: {size_mb:.1f} MB")

    # Backup: copy + vacuum
    backup_path = BACKUP_DIR / f"btc_research_{TODAY}.db"
    print(f"Creando backup: {backup_path}")
    shutil.copy2(DB_PATH, backup_path)

    # Vacuum para compactar
    print("  Vacuum (compactando)...")
    vac_conn = sqlite3.connect(backup_path)
    vac_conn.execute("VACUUM")
    vac_conn.close()

    backup_size_mb = backup_path.stat().st_size / 1e6
    print(f"  Backup: {backup_path} ({backup_size_mb:.1f} MB)")

    # Limpiar backups antiguos (mantener últimos 30)
    backups = sorted(BACKUP_DIR.glob("btc_research_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for old in backups[30:]:
        old.unlink()
        removed += 1
    if removed:
        print(f"  Limpiados {removed} backups antiguos")

    print(f"\n✅ Backup completo: {backup_path}")

if __name__ == "__main__":
    backup_db()
