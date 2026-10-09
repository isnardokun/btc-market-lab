#!/usr/bin/env python3
"""
ingestion/snapshot.py
====================
Genera un snapshot inmutable por cada ejecución del pipeline.
Contiene:
  - Hash SHA256 del estado de la BD (precios, macro, on-chain)
  - Timestamps de frescura de cada fuente
  - Versión del código (git commit o timestamp de archivo)
  - Parámetros usados (series, umbrales)

Se guarda en: reports/snapshots/snapshot_YYYY-MM-DD_HHMMSS.json
"""
import sys, os, datetime, json, hashlib, sqlite3, subprocess
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db" / "btc_research.db"
SNAP_DIR = BASE_DIR / "reports" / "snapshots"
TODAY = datetime.date.today()
TODAY_STR = TODAY.strftime("%Y-%m-%d")
NOW = datetime.datetime.now().strftime("%H%M%S")


def get_data_hash():
    """Hash SHA256 del estado actual de las tablas principales."""
    db = sqlite3.connect(DB_PATH)
    cur = db.cursor()
    hashes = {}

    # Tablas a hashear: price_btc, macro_fred, daily (latest 1000 rows)
    tables = [
        ("price_btc", "SELECT ts, price FROM price_btc ORDER BY ts DESC LIMIT 100"),
        ("macro_fred", "SELECT date, series_id, value FROM macro_fred ORDER BY date DESC LIMIT 200"),
        ("daily_metrics_latest", "SELECT report_date, asset, metric, value FROM daily_metrics ORDER BY report_date DESC LIMIT 200"),
    ]

    for name, sql in tables:
        cur.execute(sql)
        rows = cur.fetchall()
        # Serialize small sample for hashing (full table would be slow)
        data_str = str(rows)
        h = hashlib.sha256(data_str.encode()).hexdigest()[:16]
        hashes[name] = h

    db.close()
    return hashes


def get_freshness():
    """Timestamp de última actualización por fuente."""
    db = sqlite3.connect(DB_PATH)
    cur = db.cursor()
    freshness = {}

    cur.execute("SELECT MAX(ts) FROM price_btc")
    r = cur.fetchone()
    freshness["price_btc"] = datetime.datetime.fromtimestamp(r[0]).isoformat() if r and r[0] else None

    cur.execute("SELECT MAX(ts) FROM daily")
    r = cur.fetchone()
    freshness["daily_onchain"] = datetime.datetime.fromtimestamp(r[0]).isoformat() if r and r[0] else None

    cur.execute("SELECT MAX(date) FROM macro_fred")
    r = cur.fetchone()
    freshness["macro_fred"] = r[0] if r else None

    cur.execute("SELECT MAX(report_date) FROM daily_metrics")
    r = cur.fetchone()
    freshness["daily_metrics"] = r[0] if r else None

    db.close()
    return freshness


def get_code_version():
    """Versión del código: git commit o timestamp del archivo principal."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=BASE_DIR,
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass

    # Fallback: timestamp del daily_report.py
    dr_path = BASE_DIR / "analysis" / "daily_report.py"
    if dr_path.exists():
        mtime = datetime.datetime.fromtimestamp(dr_path.stat().st_mtime).isoformat()
        return f"file:{mtime}"
    return "unknown"


def get_pipeline_params():
    """Parámetros del pipeline: series IDs, umbrales, thresholds."""
    try:
        sys.path.insert(0, str(BASE_DIR))
        from ingestion.config import FRED_SERIES
        fred_series = [s[0] for s in FRED_SERIES]
    except Exception:
        fred_series = []

    return {
        "fred_series_count": len(fred_series),
        "fred_series": fred_series,
        " SMA200_window": 252,
        "rsi_period": 14,
        "atr_period": 14,
        "lookback_supp_res": 20,
        "news_per_asset": 5,
    }


def create_snapshot():
    """Genera y guarda el snapshot."""
    SNAP_DIR.mkdir(parents=True, exist_ok=True)

    snapshot_id = f"snapshot_{TODAY_STR}_{NOW}"
    snapshot = {
        "snapshot_id": snapshot_id,
        "created_at": datetime.datetime.now().isoformat(),
        "report_date": TODAY_STR,
        "data_hash": get_data_hash(),
        "freshness": get_freshness(),
        "code_version": get_code_version(),
        "params": get_pipeline_params(),
    }

    path = SNAP_DIR / f"{snapshot_id}.json"
    with open(path, "w") as f:
        json.dump(snapshot, f, indent=2)

    print(f"Snapshot creado: {path}")
    print(f"  data_hash: {snapshot['data_hash']}")
    print(f"  freshness: {snapshot['freshness']}")
    print(f"  code_version: {snapshot['code_version']}")
    return snapshot


if __name__ == "__main__":
    create_snapshot()
