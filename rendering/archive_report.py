#!/usr/bin/env python3
"""
archive_report.py — Versiona el HTML generado en la tabla `reports`
y guarda niveles de mercado en `market_levels`.
Despues de generar el HTML con daily_report.py, corre este script.
"""
import sys, os, json, sqlite3, datetime, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ingestion.config import DB_PATH, REPORTS

def archive_report(html_path, snapshot_data, levels, section="daily"):
    """
    snapshot_data: dict con keys:
        report_date, asset, section, price, chg_24h, rsi, macd_hist,
        atr, mvrv, bias, scenario_bull, scenario_base, scenario_bear,
        news_count, macro (dict de valores FRED)
    levels: list of dicts [{type, price, source}]
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    report_date = snapshot_data["report_date"]
    asset       = snapshot_data["asset"]
    section     = snapshot_data.get("section", section)

    # Calcular siguiente version
    cur.execute("""
        SELECT COALESCE(MAX(version), 0) FROM reports
        WHERE report_date=? AND asset=? AND section=?
    """, (report_date, asset, section))
    next_version = cur.fetchone()[0] + 1

    # Guardar snapshot JSON
    snapshot_json = json.dumps(snapshot_data, default=str, ensure_ascii=False)

    # Insertar reporte
    cur.execute("""
        INSERT INTO reports (report_date, version, asset, section, filename, snapshot)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (report_date, next_version, asset, section, html_path, snapshot_json))
    report_id = cur.lastrowid

    # Guardar niveles
    for lv in levels:
        cur.execute("""
            INSERT OR IGNORE INTO market_levels
            (asset, level_date, level_type, price, source, report_id, is_active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (asset, report_date, lv["type"], lv["price"], lv["source"], report_id))

    conn.commit()
    conn.close()
    print(f"  Archived: {asset} {section} v{next_version} (id={report_id}) — {html_path}")
    return report_id


def load_latest_macro():
    """Carga los ultimos valores de macro_fred para el snapshot."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    result = {}
    for series_id, name, freq, desc in [
        ("DGS10",    "10y_yield",   "daily"),
        ("DGS2",     "2y_yield",    "daily"),
        ("DTWEXBGS", "dxy",         "daily"),
        ("VIXCLS",   "vix",         "daily"),
        ("NFCI",     "nfci",        "daily"),
        ("CPALTT01USM661S", "cpi_yoy",  "monthly"),
        ("UNRATE",   "unemployment", "monthly"),
        ("PAYEMS",   "payrolls",     "monthly"),
        ("RSXFS",    "retail_sales", "monthly"),
        ("CBBTCUSD", "btc_fred",     "daily"),
    ]:
        row = cur.execute(
            "SELECT value FROM macro_fred WHERE series_id=? ORDER BY date DESC LIMIT 1",
            (series_id,)
        ).fetchone()
        result[series_id] = row[0] if row else None
    conn.close()
    return result


def archive_from_html(html_path, report_date, asset, section="daily",
                       price=None, chg_24h=None, rsi=None, macd_hist=None,
                       atr=None, mvrv=None, bias=None,
                       scenario_bull=None, scenario_base=None, scenario_bear=None,
                       news_count=0, levels=None):
    """Wrapper para archivar desde daily_report.py con datos inline."""
    snapshot = {
        "report_date": report_date,
        "asset":       asset,
        "section":     section,
        "price":       price,
        "chg_24h":     chg_24h,
        "rsi":         rsi,
        "macd_hist":   macd_hist,
        "atr":         atr,
        "mvrv":        mvrv,
        "bias":        bias,
        "scenario_bull": scenario_bull,
        "scenario_base": scenario_base,
        "scenario_bear": scenario_bear,
        "news_count":  news_count,
        "macro":       load_latest_macro(),
    }
    return archive_report(html_path, snapshot, levels or [], section)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--html")
    ap.add_argument("--date")
    ap.add_argument("--asset", default="ALL")
    ap.add_argument("--section", default="daily")
    ap.add_argument("--snapshot-json")
    args = ap.parse_args()

    if args.html and os.path.exists(args.html):
        with open(args.html) as f:
            html = f.read()
        # Extraer datos basicos del HTML (fallback minimo)
        snapshot = {
            "report_date": args.date or datetime.date.today().isoformat(),
            "asset": args.asset,
            "section": args.section,
            "html_size": len(html),
        }
        archive_report(args.html, snapshot, [])
        print(f"  HTML archivado: {args.html}")
    else:
        print("  Uso: python archive_report.py --html <path> --date YYYY-MM-DD --asset BTC")
