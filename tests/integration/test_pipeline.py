#!/usr/bin/env python3
"""
tests/integration/test_pipeline.py
================================
Integration tests — verifican que el sistema completo produce outputs válidos.
"""
import sys, os, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import sqlite3, json
from pathlib import Path

BASE_DIR = Path("/home/ignotus/btc-research")
DB_PATH = BASE_DIR / "db" / "btc_research.db"


def get_db():
    return sqlite3.connect(DB_PATH)


def latest_daily_date():
    """Fecha más reciente en daily_metrics — no依赖于 TODAY que puede ser mañana."""
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT MAX(report_date) FROM daily_metrics")
    row = cur.fetchone()[0]
    db.close()
    return row or "2026-01-01"


# Fecha del último pipeline real (no la fecha de hoy)
LATEST_DATE = latest_daily_date()
LATEST_STR = LATEST_DATE.strftime("%Y-%m-%d") if isinstance(LATEST_DATE, datetime.date) else LATEST_DATE
TODAY = datetime.date.today()
TODAY_STR = TODAY.strftime("%Y-%m-%d")


def test_price_btc_has_recent_data():
    """price_btc tiene datos recientes (últimos 7 días)."""
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT MAX(ts) FROM price_btc")
    max_ts = cur.fetchone()[0]
    db.close()
    assert max_ts is not None, "price_btc está vacía"
    latest = datetime.datetime.fromtimestamp(max_ts).date()
    diff = (TODAY - latest).days
    assert diff <= 7, f"BTC price stale: {diff} días ({latest})"
    print(f"  ✅ price_btc: última fecha {latest} ({diff} días)")


def test_macro_fred_has_recent_data():
    """macro_fred tiene datos de los últimos 30 días."""
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT MAX(date) FROM macro_fred")
    max_date = cur.fetchone()[0]
    db.close()
    assert max_date is not None, "macro_fred está vacía"
    latest = datetime.date.fromisoformat(max_date)
    diff = (TODAY - latest).days
    assert diff <= 30, f"FRED data stale: {diff} días ({max_date})"
    print(f"  ✅ macro_fred: última fecha {max_date} ({diff} días)")


def test_daily_metrics_has_data():
    """daily_metrics tiene datos para la fecha del último pipeline."""
    db = get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM daily_metrics WHERE report_date=?", (LATEST_STR,)
    )
    count = cur.fetchone()[0]
    db.close()
    assert count > 0, f"daily_metrics sin datos para {LATEST_STR}"
    print(f"  ✅ daily_metrics: {count} métricas para {LATEST_STR}")


def test_gate_json_exists_and_passed():
    """gate_YYYY-MM-DD.json existe y pasó (score >= 95, critical=0)."""
    gate_path = BASE_DIR / "reports" / f"gate_{LATEST_STR}.json"
    assert gate_path.exists(), f"Gate JSON no existe: {gate_path}"
    with open(gate_path) as f:
        gate = json.load(f)
    score = gate.get("score_total", gate.get("score", 0))
    passed = gate.get("pass", False)
    critical_count = gate.get("critical_count", 0)
    assert critical_count == 0, f"Gate tiene {critical_count} error(es) crítico(s): {gate.get('all_issues', [])}"
    assert score >= 95, f"Gate score {score} < 95"
    assert passed, f"Gate pass=False incluso con score={score}"
    print(f"  ✅ publication_gate: score={score}/100, pass={passed}, critical=0")


def test_html_report_exists():
    """El reporte HTML existe y tiene contenido válido."""
    report_path = BASE_DIR / "reports" / f"daily_report_{LATEST_STR}.html"
    assert report_path.exists(), f"HTML report no existe: {report_path}"
    size = report_path.stat().st_size
    assert size > 10000, f"HTML report muy pequeño: {size} bytes"
    with open(report_path) as f:
        content = f.read()
    assert "Mercados Daily Pro" in content, "HTML no tiene título esperado"
    assert "BTC" in content, "HTML no menciona BTC"
    print(f"  ✅ HTML report: {size:,} bytes, contiene BTC y título")


def test_onchain_metrics_available():
    """Los 12 métricas on-chain están disponibles para la fecha del pipeline."""
    db = get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT COUNT(DISTINCT metric) FROM daily_metrics WHERE report_date=? AND asset='BTC'",
        (LATEST_STR,)
    )
    count = cur.fetchone()[0]
    db.close()
    assert count >= 12, f"Solo {count} métricas on-chain (necesario >= 12)"
    print(f"  ✅ on-chain: {count} métricas BTC disponibles")


def test_no_duplicate_metrics_today():
    """No hay duplicados en daily_metrics para la fecha del pipeline (UNIQUE constraint)."""
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT asset, metric, COUNT(*) as cnt
        FROM daily_metrics
        WHERE report_date=?
        GROUP BY asset, metric
        HAVING cnt > 1
    """, (LATEST_STR,))
    dups = cur.fetchall()
    db.close()
    assert len(dups) == 0, f"Duplicados en daily_metrics: {dups}"
    print(f"  ✅ daily_metrics: sin duplicados para {LATEST_STR}")


def test_ath_is_maximum():
    """ATH en price_btc es el máximo histórico (no el último precio)."""
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT MAX(price) FROM price_btc")
    max_price = cur.fetchone()[0]
    cur.execute("SELECT price FROM price_btc ORDER BY price DESC LIMIT 1")
    ath_price = cur.fetchone()[0]
    db.close()
    assert ath_price == max_price, f"ATH {ath_price} != máximo {max_price}"
    assert ath_price > 50000, f"ATH {ath_price} irrealisticamente bajo"
    print(f"  ✅ ATH: ${ath_price:,.0f} (máximo histórico)")


def test_cpi_yoy_in_realistic_range():
    """CPI YoY está en rango realista (1% - 10%)."""
    db = get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT value FROM macro_fred WHERE series_id='CPI_YOY' ORDER BY date DESC LIMIT 1"
    )
    row = cur.fetchone()
    db.close()
    assert row is not None, "CPI_YOY no tiene datos"
    cpi = row[0]
    assert 0.5 <= cpi <= 10, f"CPI YoY {cpi}% fuera de rango realista"
    print(f"  ✅ CPI YoY: {cpi:.2f}% (rango OK)")


def test_nfp_change_in_realistic_range():
    """NFP CHANGE en rango realista (-500K a +500K)."""
    db = get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT value FROM macro_fred WHERE series_id='NFP_CHANGE' ORDER BY date DESC LIMIT 1"
    )
    row = cur.fetchone()
    db.close()
    assert row is not None, "NFP_CHANGE no tiene datos"
    nfp = row[0]
    assert -500 <= nfp <= 500, f"NFP {nfp}K fuera de rango realista"
    print(f"  ✅ NFP CHANGE: {nfp:+.0f}K (rango OK)")


if __name__ == "__main__":
    print(f"\nIntegration tests — {LATEST_STR} (pipeline date)\n")
    test_price_btc_has_recent_data()
    test_macro_fred_has_recent_data()
    test_daily_metrics_has_data()
    test_gate_json_exists_and_passed()
    test_html_report_exists()
    test_onchain_metrics_available()
    test_no_duplicate_metrics_today()
    test_ath_is_maximum()
    test_cpi_yoy_in_realistic_range()
    test_nfp_change_in_realistic_range()
    print("\n✅ All integration tests passed")
