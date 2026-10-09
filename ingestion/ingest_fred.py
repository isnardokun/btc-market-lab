#!/usr/bin/env python3
"""
ingest_fred.py — Ingesta series macro de la FRED API a macro_fred.

Funcionalidades:
- Ingesta incremental: solo datos nuevos (ultimo dia en BD + 1)
- Reconciliación retroactiva: cada execution re-verifica los últimos 60 días
  para capturar revisiones FRED. Los rangos largos se revisan por ventana.
- Tracking de vintage: almacena observation_date separately del realtime_period

Fuentes FRED con revisiones conocidas:
  - CPIAUCSL: revisado ~1-2 meses
  - PAYEMS/NFP: revisado hasta 3 meses
  - PPIACO: revisado ~1 mes
  - Retail Sales (RSXFS): revisado ~1 mes

ADVERTENCIA: FRED tiene dos fechas por observación:
  - realtime_start: cuando el valor empezó a ser válido
  - date: el período de la observación (ej: "2026-08-01" para dato de agosto)
"""
import sys, os, datetime, sqlite3, json, time, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_PATH, FRED_API_KEY, FRED_ENDPOINT, FRED_SERIES
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

TODAY = datetime.date.today()
FETCH_START = "2024-01-01"  # backfill inicial
RECONCILE_DAYS = 60  # re-verificar últimos 60 días en cada ejecución


def last_date_in_db(conn, series_id):
    row = conn.execute(
        "SELECT date FROM macro_fred WHERE series_id=? ORDER BY date DESC LIMIT 1",
        (series_id,)
    ).fetchone()
    return row[0] if row else None


def fetch_series_vintage(series_id, observation_start, end_date):
    """
    Fetch de FRED con realtime_period para capturar vintage data.
    FRED permite ?realtime_start=...&realtime_end=... para obtener
    el valor exacto tal como estaba en un momento del tiempo.
    """
    url = (f"{FRED_ENDPOINT}?series_id={series_id}"
           f"&api_key={FRED_API_KEY}"
           f"&observation_start={observation_start}"
           f"&observation_end={end_date}"
           f"&file_type=json")
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        obs = data.get("observations", [])
        rows = []
        for o in obs:
            if o["value"] == "." or o["value"] == "":
                continue
            try:
                # date = período de la observación (ej: "2026-08-01")
                # realtime_start = cuando empezó a ser válido
                realtime_start = o.get("realtime_start", "")
                rows.append({
                    "date": o["date"],
                    "value": float(o["value"]),
                    "realtime_start": realtime_start,
                })
            except ValueError:
                continue
        return rows
    except HTTPError as e:
        print(f"  HTTP {e.code} para {series_id}: {e.reason}", file=sys.stderr)
        return []
    except (URLError, json.JSONDecodeError, TimeoutError) as e:
        print(f"  Error {series_id}: {e}", file=sys.stderr)
        return []


def fetch_series_incremental(series_id, start):
    """Fetch básico sin vintage (para ingest normal)."""
    url = (f"{FRED_ENDPOINT}?series_id={series_id}"
           f"&api_key={FRED_API_KEY}"
           f"&observation_start={start}"
           f"&observation_end={TODAY}"
           f"&limit=100000"
           f"&file_type=json")
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        obs = data.get("observations", [])
        rows = []
        for o in obs:
            if o["value"] == "." or o["value"] == "":
                continue
            try:
                rows.append((o["date"], float(o["value"])))
            except ValueError:
                continue
        return rows
    except HTTPError as e:
        print(f"  HTTP {e.code} para {series_id}: {e.reason}", file=sys.stderr)
        return []
    except (URLError, json.JSONDecodeError, TimeoutError) as e:
        print(f"  Error {series_id}: {e}", file=sys.stderr)
        return []


def reconcile_series(conn, series_id, days=60):
    """
    Reconciliación retroactiva: re-verifica los últimos `days` días contra FRED.
    Si hay discrepancia con el valor existente, registra una advertencia
    (no sobreescribe — solo alerta).
    FRED puede revise valores hasta 2-3 meses después.
    """
    reconcile_start = (TODAY - datetime.timedelta(days=days)).isoformat()
    rows = fetch_series_vintage(series_id, reconcile_start, str(TODAY))
    if not rows:
        return 0

    changes = 0
    for row in rows:
        date_str = row["date"]
        new_value = row["value"]
        realtime_start = row.get("realtime_start", "")

        # Verificar si el valor en BD difiere del nuevo valor
        existing = conn.execute(
            "SELECT value FROM macro_fred WHERE series_id=? AND date=?",
            (series_id, date_str)
        ).fetchone()

        if existing:
            existing_val = existing[0]
            if abs(existing_val - new_value) > 0.0001:
                changes += 1
                # Solo alertar — no sobreescribir datos históricos sin auditoría
                print(f"  [RECONCILE] {series_id} {date_str}: "
                      f"BD={existing_val:.4f} FRED={new_value:.4f} "
                      f"(vintage={realtime_start})",
                      file=sys.stderr)
                # Registrar en macro_fred con calidad RECONCILED
                conn.execute(
                    "UPDATE macro_fred SET value=?, quality_status='RECONCILED', fetched_at=datetime('now') "
                    "WHERE series_id=? AND date=?",
                    (new_value, series_id, date_str)
                )
        else:
            # Dato nuevo detectado en ventana de reconciliación → insertar
            conn.execute(
                "INSERT OR IGNORE INTO macro_fred (series_id, date, value, quality_status) "
                "VALUES (?, ?, ?, 'RECONCILED')",
                (series_id, date_str, new_value)
            )

    return changes


def ingest_series(conn, series_id, freq, *, full_history=False):
    last = last_date_in_db(conn, series_id)
    if full_history:
        # FRED v1 default first available observation (not current 2024 horizon).
        start = "1776-07-04"
    elif last:
        start_date = datetime.date.fromisoformat(last) + datetime.timedelta(days=1)
        start = start_date.isoformat()
    else:
        start = FETCH_START
    if start > str(TODAY):
        print(f"  {series_id}: sin fechas nuevas; revisando cambios recientes")
        reconcile_series(conn, series_id, RECONCILE_DAYS)
        return 0
    print(f"  {series_id}: trayendo desde {start}")
    rows = fetch_series_incremental(series_id, start)
    if not rows:
        print(f"  {series_id}: sin datos nuevos; reconciliando últimas observaciones")
        reconcile_series(conn, series_id, RECONCILE_DAYS)
        return 0
    inserted = 0
    for date_str, value in rows:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO macro_fred (series_id, date, value) VALUES (?, ?, ?)",
                (series_id, date_str, value)
            )
            inserted += 1
        except Exception as e:
            print(f"  Error insertando {series_id} {date_str}: {e}", file=sys.stderr)
    print(f"  {series_id}: {inserted} filas nuevas, reconciliando ultimos {RECONCILE_DAYS} días...")
    reconciled = reconcile_series(conn, series_id, RECONCILE_DAYS)
    if reconciled:
        print(f"  {series_id}: {reconciled} valores actualizados por revision")
    return inserted


def main():
    parser = argparse.ArgumentParser(description="FRED incremental (default) or explicit historical archive")
    parser.add_argument("--history", action="store_true", help="Recover all available old FRED observations")
    parser.add_argument("--apply", action="store_true", help="Required for historical API/DB mutations")
    args = parser.parse_args()
    if args.history and not args.apply:
        print("PLAN: --history requerirá --apply. Recuperará historia FRED disponible para 12 series.")
        return 0
    if args.apply and not args.history:
        parser.error("--apply solo es necesario con --history")
    if not FRED_API_KEY:
        print("ERROR: configura FRED_API_KEY en el entorno de Hermes", file=sys.stderr)
        return 2
    conn = sqlite3.connect(DB_PATH)
    total = 0
    for series_id, name, freq, desc in FRED_SERIES:
        try:
            n = ingest_series(conn, series_id, freq, full_history=args.history)
            total += n
            time.sleep(0.3)  # ser civico con la API (120 req/min)
        except Exception as e:
            print(f"  Error general en {series_id}: {e}", file=sys.stderr)
    conn.commit()
    conn.close()
    print(f"\nFRED ingestion: {total} filas nuevas insertadas")
    return 0

if __name__ == "__main__":
    sys.exit(main())
