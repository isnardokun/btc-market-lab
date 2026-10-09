#!/usr/bin/env python3
"""
export_dashboard.py — Genera dashboard_latest.json con el estado
actual del sistema: precios, macro, predicciones, niveles, hit rates.
Corre diariamente via cron.
"""
import sys, os, json, sqlite3, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ingestion.config import DB_PATH, BASE_DIR

OUT_PATH = BASE_DIR + "/dashboards/dashboard_latest.json"

def last_value(conn, series_id):
    row = conn.execute(
        "SELECT date, value FROM macro_fred WHERE series_id=? ORDER BY date DESC LIMIT 1",
        (series_id,)
    ).fetchone()
    return {"date": row[0], "value": row[1]} if row else None

def last_n_values(conn, series_id, n=30):
    rows = conn.execute(
        "SELECT date, value FROM macro_fred WHERE series_id=? ORDER BY date DESC LIMIT ?",
        (series_id, n)
    ).fetchall()
    return [{"date": r[0], "value": r[1]} for r in reversed(rows)]

def macro_summary(conn):
    """Consistent indicators from real FRED series (never relabel a level as a change)."""
    result = {}
    series_map = {
        "DGS10":    ("10y_yield", "Yield 10Y", "percent"),
        "DGS2":     ("2y_yield", "Yield 2Y", "percent"),
        "DTWEXBGS": ("usd_broad", "USD Broad Index (not DXY)", "index"),
        "VIXCLS":   ("vix", "VIX", "index"),
        "NFCI":     ("nfci", "NFCI", "index"),
        "UNRATE":   ("unemployment", "Desempleo", "percent"),
        "RSXFS":    ("retail_sales", "Retail Sales", "millions_usd"),
        "CBBTCUSD": ("btc_fred", "BTC (FRED)", "usd"),
    }
    for sid, (key, label, unit) in series_map.items():
        row = conn.execute(
            "SELECT date, value FROM macro_fred WHERE series_id=? ORDER BY date DESC LIMIT 1",
            (sid,)
        ).fetchone()
        if row and row[1] is not None:
            result[key] = {
                "label": label, "value": round(row[1], 4),
                "date": row[0], "unit": unit, "source_series": sid,
                "status": "ok",
            }

    # CPIAUCSL is the SA level, not a YoY percent. Compare corresponding months.
    cpi = conn.execute("""
        SELECT a.date, 100.0 * (a.value / b.value - 1.0)
        FROM macro_fred a JOIN macro_fred b
          ON b.series_id='CPIAUCSL' AND b.date=date(a.date, '-12 months')
        WHERE a.series_id='CPIAUCSL' AND b.value > 0
        ORDER BY a.date DESC LIMIT 1
    """).fetchone()
    if cpi:
        result["cpi_yoy"] = {
            "label": "CPI YoY (SA)", "value": round(cpi[1], 3),
            "date": cpi[0], "unit": "percent", "source_series": "CPIAUCSL",
            "status": "ok",
        }

    # PAYEMS is a level in thousands of persons. NFP is its monthly difference.
    nfp = conn.execute("""
        SELECT a.date, a.value - b.value
        FROM macro_fred a JOIN macro_fred b
          ON b.series_id='PAYEMS' AND b.date=date(a.date, '-1 month')
        WHERE a.series_id='PAYEMS'
        ORDER BY a.date DESC LIMIT 1
    """).fetchone()
    if nfp:
        result["nfp_change"] = {
            "label": "NFP variación mensual", "value": round(nfp[1], 2),
            "date": nfp[0], "unit": "thousand_persons", "source_series": "PAYEMS",
            "status": "ok",
        }
    return result


def active_predictions(conn, limit=20):
    rows = conn.execute("""
        SELECT p.id, p.asset, p.pred_type, p.direction, p.price_from,
               p.price_target, p.level_tag, p.expires_at, p.note,
               r.report_date
        FROM predictions p
        JOIN reports r ON r.id = p.report_id
        WHERE p.status = 'active'
        ORDER BY p.expires_at ASC
        LIMIT ?
    """, (limit,)).fetchall()
    result = []
    today = datetime.date.today()
    for r in rows:
        expires = datetime.date.fromisoformat(r[7])
        days_left = (expires - today).days
        result.append({
            "id":           r[0],
            "asset":        r[1],
            "type":         r[2],
            "direction":    r[3],
            "price_from":   r[4],
            "price_target": r[5],
            "level_tag":    r[6],
            "expires_at":   r[7],
            "days_left":    max(days_left, 0),
            "note":         r[8],
            "report_date":  r[9],
        })
    return result

def resolved_predictions(conn, limit=30):
    rows = conn.execute("""
        SELECT p.id, p.asset, p.pred_type, p.direction,
               p.price_target, p.expires_at, p.status, p.accuracy,
               s.overall_score, s.price_achieved, s.reached_at,
               r.report_date
        FROM predictions p
        JOIN reports r ON r.id = p.report_id
        LEFT JOIN prediction_scores s ON s.prediction_id = p.id
        WHERE p.status IN ('achieved','missed','partial')
        ORDER BY p.expires_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    result = []
    for r in rows:
        result.append({
            "id":             r[0],
            "asset":          r[1],
            "type":           r[2],
            "direction":      r[3],
            "price_target":   r[4],
            "expires_at":     r[5],
            "status":         r[6],
            "accuracy":       r[7],
            "score":          round(r[8], 1) if r[8] else None,
            "price_achieved": r[9],
            "reached_at":     r[10],
            "report_date":    r[11],
        })
    return result

def hit_rates(conn, days=30):
    rows = conn.execute("""
        SELECT asset,
               COUNT(*) as total,
               SUM(CASE WHEN status='achieved' THEN 1 ELSE 0 END) as achieved,
               SUM(CASE WHEN status='missed' THEN 1 ELSE 0 END) as missed,
               SUM(CASE WHEN status='partial' THEN 1 ELSE 0 END) as partial
        FROM predictions
        WHERE status IN ('achieved','missed','partial')
          AND created_at >= datetime('now', ?)
        GROUP BY asset
    """, (f"-{days} days",)).fetchall()
    result = {}
    for asset, total, achieved, missed, partial in rows:
        hit = achieved or 0
        result[asset] = {
            "total":    total,
            "achieved": hit,
            "missed":   missed or 0,
            "partial":  partial or 0,
            "hit_rate": round(hit * 100 / total, 1) if total else 0,
        }
    return result

def active_levels(conn, limit=50):
    rows = conn.execute("""
        SELECT asset, level_type, price, level_date, source, id
        FROM market_levels
        WHERE is_active = 1
        ORDER BY level_date DESC
        LIMIT ?
    """, (limit,)).fetchall()
    today = datetime.date.today()
    result = []
    for r in rows:
        level_date = datetime.date.fromisoformat(r[3])
        days_old = (today - level_date).days
        result.append({
            "asset":      r[0],
            "type":      r[1],
            "price":     r[2],
            "date":      r[3],
            "source":    r[4],
            "days_old":  days_old,
            "id":        r[5],
        })
    return result

def recent_reports(conn, limit=10):
    rows = conn.execute("""
        SELECT id, report_date, asset, section, version, filename, created_at
        FROM reports
        ORDER BY report_date DESC, version DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return [
        {
            "id":        r[0],
            "date":      r[1],
            "asset":     r[2],
            "section":   r[3],
            "version":   r[4],
            "filename":  r[5],
            "created_at": r[6],
        }
        for r in rows
    ]

def main():
    conn = sqlite3.connect(DB_PATH)

    # Crear dashboards dir si no existe
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

    dashboard = {
        "generated_at": datetime.datetime.now().isoformat(),
        "macro":       macro_summary(conn),
        "predictions": {
            "active":   active_predictions(conn),
            "resolved": resolved_predictions(conn),
        },
        "hit_rates":  hit_rates(conn),
        "levels":     active_levels(conn),
        "reports":    recent_reports(conn),
    }

    with open(OUT_PATH, "w") as f:
        json.dump(dashboard, f, default=str, ensure_ascii=False, indent=2)

    conn.close()
    print(f"Dashboard exportado: {OUT_PATH}")
    print(f"  Macro series: {len(dashboard['macro'])}")
    print(f"  Predicciones activas: {len(dashboard['predictions']['active'])}")
    print(f"  Predicciones resueltas: {len(dashboard['predictions']['resolved'])}")
    print(f"  Hit rates: {list(dashboard['hit_rates'].keys())}")
    print(f"  Niveles activos: {len(dashboard['levels'])}")
    print(f"  Reportes recientes: {len(dashboard['reports'])}")

if __name__ == "__main__":
    main()
