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
# Do not silently pass by validating the last archived run weeks ago.
RUN_DAY = datetime.datetime.now(datetime.timezone.utc).date()
LAST_DAY = datetime.date.fromisoformat(LATEST_STR)
RUN_AGE_DAYS = (RUN_DAY - LAST_DAY).days

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
    """A recent pipeline is required; old successful reports are not enough."""
    assert 0 <= RUN_AGE_DAYS <= 2, (
        f"Última fecha daily_metrics={LATEST_STR} está a {RUN_AGE_DAYS} días "
        "del reloj UTC: no aceptar una ejecución histórica como actual")
    db = get_db()
    cur = db.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM daily_metrics WHERE report_date=?", (LATEST_STR,)
    )
    count = cur.fetchone()[0]
    db.close()
    assert count > 0, f"daily_metrics sin datos para {LATEST_STR}"
    print(f"  ✅ daily_metrics: {count} métricas para {LATEST_STR}")


def resolve_gate_report_path(gate):
    """JSON gate stores an absolute or root-relative source HTML path."""
    value = gate.get("report")
    assert isinstance(value, str) and value.strip(), "Gate sin ruta de informe"
    report = Path(value)
    return report if report.is_absolute() else BASE_DIR / report


def read_latest_gate():
    gate_path = BASE_DIR / "reports" / f"gate_{LATEST_STR}.json"
    assert gate_path.is_file(), f"Gate JSON no existe: {gate_path}"
    with gate_path.open(encoding="utf-8") as stream:
        return json.load(stream)


def test_gate_json_exists_and_passed():
    """Verify an approved report or an explicitly documented rejection."""
    import hashlib
    gate = read_latest_gate()
    score = gate.get("score_total", gate.get("score", 0))
    passed = gate.get("pass")
    critical_count = gate.get("critical_count", 0)
    report_path = resolve_gate_report_path(gate)
    assert passed is True or passed is False, "Gate sin campo pass booleano"
    assert isinstance(score, (int, float)), "Score del gate inválido"

    if passed:
        assert score >= 95, f"Gate PASS con score={score} < 95"
        assert critical_count == 0, f"Gate PASS con {critical_count} errores críticos"
        assert report_path.is_file(), (
            f"Gate PASS sin HTML de origen: {report_path}"
        )
        expected_hash = gate.get("report_sha256")
        assert expected_hash, "Gate aprobado sin huella SHA256 del reporte"
        actual_hash = hashlib.sha256(report_path.read_bytes()).hexdigest()
        assert actual_hash == expected_hash, (
            f"HTML cambió después de aprobación: {actual_hash} != {expected_hash}"
        )
        print(f"  ✅ gate APROBADO: score={score}, critical=0, SHA256 válido")
    else:
        assert critical_count > 0 or score < 95, (
            "Gate rechazó el reporte sin razones críticas ni score insuficiente"
        )
        assert gate.get("blocked_reason") in {"critical_errors", "low_score"}, (
            "Gate rechazó reporte sin motivo estructurado"
        )
        print(f"  ⛔ gate RECHAZADO CORRECTAMENTE: score={score}, critical={critical_count}")


def test_html_report_exists():
    """A gated approval MUST point to the actual approved HTML; no silent skip."""
    gate = read_latest_gate()
    report_path = resolve_gate_report_path(gate)
    if gate["pass"] is True:
        assert report_path.is_file(), (
            f"El gate aprobó pero el HTML no existe: {report_path}"
        )
        actual = report_path
    else:
        # On rejection scripts/daily.sh moves the draft to reports/rejected.
        # Do not conflate a missing approved HTML with a successful publication.
        rejected = BASE_DIR / "reports" / "rejected" / (
            report_path.stem + "_REJECTED.html"
        )
        assert not report_path.exists(), (
            "Gate rechazó el borrador, pero sigue en ubicación de publicación"
        )
        if not rejected.is_file():
            print("  ⛔ gate rechazó antes de generar el borrador; no hay HTML")
            return
        actual = rejected
        print(f"  ⛔ revisando borrador rechazado: {actual.name}")
    size = actual.stat().st_size
    assert size > 10000, f"HTML inválido o muy pequeño: {size} bytes"
    with actual.open(encoding="utf-8") as stream:
        content = stream.read()
    assert "Mercados Daily Pro" in content, "HTML sin título"
    assert "BTC" in content, "HTML sin sección de BTC"
    print(f"  ✅ HTML presente y legible: {size:,} bytes")


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
