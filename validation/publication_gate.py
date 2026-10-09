#!/usr/bin/env python3
"""
publication_gate.py — Auditor de calidad antes de publicar.
Ejecutar DESPUES de daily_report_v2.py en el pipeline.

Score: exactitud 35%, consistencia 25%, trazabilidad 20%,
       análisis 15%, presentación 5%.
Bloquea si hay errores críticos. Min 95/100 para auto-publicar.
"""
import os, sys, sqlite3, re, datetime, hashlib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from validation.content_checks import audit_report_html, extract_cpi_yoy_from_macro_strip
DB_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/db/btc_research.db"

TODAY = datetime.date.today()
TODAY_STR = TODAY.strftime("%Y-%m-%d")
REPORT_PATH = Path(__file__).parent.parent / "reports" / f"daily_report_{TODAY_STR}.html"
ts_52w = int((datetime.datetime.combine(TODAY, datetime.time(0,0)).replace(tzinfo=datetime.timezone.utc) - datetime.timedelta(days=365)).timestamp())

# ─── Pesos ────────────────────────────────────────────────────────────────
WEIGHTS = {
    "exactitud":    0.35,
    "consistencia": 0.25,
    "trazabilidad": 0.20,
    "analisis":     0.15,
    "presentacion": 0.05,
}
PASS_SCORE = 95
CRITICAL_ERRORS = []

# ─── Resultados por categoría ────────────────────────────────────────────
results = {
    "exactitud":    {"score": 100, "issues": []},
    "consistencia": {"score": 100, "issues": []},
    "trazabilidad": {"score": 100, "issues": []},
    "analisis":     {"score": 100, "issues": []},
    "presentacion": {"score": 100, "issues": []},
}

def issue(cat, severity, msg):
    """Registra un problema. severity: 'critical', 'warning', 'info'."""
    score_map = {"critical": 0, "warning": 5, "info": 0}
    results[cat]["score"] = max(0, results[cat]["score"] - score_map.get(severity, 5))
    tag = {"critical": "🔴", "warning": "🟡", "info": "🔵"}[severity]
    results[cat]["issues"].append(f"  {tag} {msg}")

# ─── 1. EXACTITUD ─────────────────────────────────────────────────────────
def check_exactitud():
    db = sqlite3.connect(DB_PATH)
    cur = db.cursor()

    # 1.1 Verificar último reporte existe
    if not REPORT_PATH.exists():
        issue("exactitud", "critical", f"Reporte no encontrado: {REPORT_PATH}")
        db.close()
        return

    html = REPORT_PATH.read_text()

    # 1.2 Extraer métricas del HTML para verificar consistencia con BD
    # CPI YoY
    html_cpi = extract_cpi_yoy_from_macro_strip(html)
    if html_cpi is not None:
        cur.execute("""
            SELECT value FROM daily_metrics
            WHERE asset='MACRO' AND metric='cpi_yoy'
            ORDER BY report_date DESC LIMIT 1
        """)
        row = cur.fetchone()
        if row:
            db_cpi = float(row[0])
            if abs(html_cpi - db_cpi) > 0.1:
                issue("exactitud", "critical",
                      f"CPI YoY discrepancy: HTML={html_cpi}%, DB={db_cpi}%")
            # Verificar si es un valor historico mallabelado
            if html_cpi < 1 or html_cpi > 10:
                issue("exactitud", "warning",
                      f"CPI YoY={html_cpi}% fuera de rango razonable (0-10%)")
        else:
            issue("exactitud", "warning", "CPI YoY en reporte pero no en daily_metrics")

    # 1.3 NFP — debe mostrar cambio mensual (K), no nivel
    nfp_match = re.search(r'NFP.*?([+-]?\d+\.?\d*)\s*K', html)
    if nfp_match:
        nfp_val = float(nfp_match.group(1))
        if abs(nfp_val) > 500:
            issue("exactitud", "critical",
                  f"NFP={nfp_val}K — valor muy alto para cambio mensual, posible confusión con nivel PAYEMS")
        # Verificar que el dato existe
        cur.execute("""
            SELECT value FROM daily_metrics
            WHERE asset='MACRO' AND metric='nfp'
            ORDER BY report_date DESC LIMIT 1
        """)
        row = cur.fetchone()
        if not row:
            issue("exactitud", "warning", "NFP mostrado pero no encontrado en daily_metrics")

    # 1.4 DXY — verificar que no sea confundido con "ratio"
    if 'DXY' in html:
        # El reporte no debe mostrar DXY como "ratio" — es un índice
        dxy_section = re.search(r'DXY.*?</div>', html, re.DOTALL)
        if dxy_section and 'ratio' in dxy_section.group(0).lower():
            issue("exactitud", "warning", "DXY mostrado incorrectamente como 'ratio' — es un índice (sin unidad)")

    # 1.5 Verificar precio BTC razonable
    btc_match = re.search(r'BTC.*?\$([0-9,]+)', html)
    if btc_match:
        btc_price = float(btc_match.group(1).replace(",", ""))
        if btc_price < 1000 or btc_price > 500000:
            issue("exactitud", "warning", f"BTC precio ${btc_price:,.0f} fuera de rango razonable")
        # Verificar contra Yahoo
        cur.execute("""
            SELECT price FROM price_btc
            ORDER BY ts DESC LIMIT 1
        """)
        row = cur.fetchone()
        if row:
            db_btc = float(row[0])
            if abs(btc_price - db_btc) / db_btc > 0.02:
                issue("exactitud", "warning",
                      f"BTC precio en reporte vs BD: ${btc_price:,.0f} vs ${db_btc:,.0f} (diff >2%)")

    # 1.6 No rechazar un valor numérico por ser idéntico a una lectura histórica.
    # El valor se comprueba contra la observación archivada del mismo indicador.
    if html_cpi is not None:
        cur.execute(
            "SELECT date FROM macro_fred WHERE series_id='CPI_YOY' "
            "ORDER BY date DESC LIMIT 1"
        )
        cpi_source_date = cur.fetchone()
        if cpi_source_date:
            try:
                dated = datetime.date.fromisoformat(cpi_source_date[0])
                age_days = (TODAY - dated).days
                if age_days > 120 or age_days < -31:
                    issue("exactitud", "warning",
                          f"CPI_YOY de FRED con fecha base {dated}, "
                          f"antigüedad {age_days} días: verificar calendario")
            except ValueError:
                issue("exactitud", "warning", "Fecha de CPI_YOY no parseable")

    # 1.7 Verificar ATH correcto
    cur.execute("SELECT MAX(price), ts FROM price_btc")
    row = cur.fetchone()
    if row:
        ath_price, ath_ts = row
        if ath_price > 0:
            ath_date = datetime.datetime.fromtimestamp(ath_ts).strftime("%d %b %Y")
            # El ATH debe ser ~$124,753 (oct 2025) para BTC
            if ath_price < 100000:
                issue("exactitud", "warning", f"ATH BTC ${ath_price:,.0f} parece bajo — verificar con fuente")

    # 1.8 Verificar que N no haya RSI sin dato (None mostrado como "None")
    if 'None' in html and 'RSI' in html:
        issue("exactitud", "warning", "Reporte contiene 'None' para RSI — posible error de datos")

    db.close()

# ─── 2. CONSISTENCIA ──────────────────────────────────────────────────────
def check_consistencia():
    db = sqlite3.connect(DB_PATH)
    cur = db.cursor()

    if not REPORT_PATH.exists():
        db.close()
        return

    html = REPORT_PATH.read_text()

    # 2.1 RSI en tablas debe coincidir con RSI en narrativa
    rsi_table = re.search(r'RSI.*?(\d+)', html)
    if rsi_table:
        rsi_val = rsi_table.group(1)
        # Verificar que la narrativa también menciona RSI
        if 'RSI' not in html:
            issue("consistencia", "warning", "RSI encontrado en tabla pero no en narrativa")

    # 2.2 Soportes/Resistencias deben estar correctamente categorizados
    # Extraer S/R del HTML
    sr_matches = re.findall(r'Soporte|Resistencia|soporte|resistencia', html, re.IGNORECASE)
    if sr_matches:
        # Verificar que hay precio actual contra el cual evaluar S/R
        btc_match = re.search(r'BTC.*?\$([0-9,]+)', html)
        if not btc_match:
            issue("consistencia", "warning", "S/R mencionados pero precio BTC no encontrado")

    # 2.3 MVRV en narrativa vs tablas — deben ser coherentes
    mvrv_narr = re.search(r'MVRV.*?(\d+\.?\d*)', html)
    if mvrv_narr:
        mvrv_val = float(mvrv_narr.group(1))
        cur.execute("""
            SELECT value FROM daily_metrics WHERE asset='BTC' AND metric='mvrv'
            ORDER BY report_date DESC LIMIT 1
        """)
        row = cur.fetchone()
        if row:
            db_mvrv = float(row[0])
            if abs(mvrv_val - db_mvrv) > 0.2:
                issue("consistencia", "warning",
                      f"MVRV narrativa vs BD: {mvrv_val} vs {db_mvrv}")

    # 2.4 NPF-change vs NPF-level — no deben estar mezclados
    # Si la narrativa dice "NFP en 159K" está mal (level, no change)
    if re.search(r'NFP\s+en\s+159', html):
        issue("consistencia", "critical", "NFP mostrado como nivel (159K) en lugar de cambio mensual (+29K)")

    # 2.5 Verify yield values are consistent
    y10_match = re.search(r'10Y.*?(\d+\.?\d*)%', html)
    if y10_match:
        y10 = float(y10_match.group(1))
        cur.execute("""
            SELECT value FROM daily_metrics WHERE asset='MACRO' AND metric='yield_10y'
            ORDER BY report_date DESC LIMIT 1
        """)
        row = cur.fetchone()
        if row:
            db_y10 = float(row[0])
            if abs(y10 - db_y10) > 0.1:
                issue("consistencia", "warning",
                      f"10Y en reporte vs BD: {y10}% vs {db_y10}%")

    # 2.6 Verificar que DXY no se confunda con DTWEXBGS en narrativa
    # Si hay texto sobre "DXY" debe indicar que es "Trade Weighted USD Broad Index"
    if 'DXY' in html and 'Trade Weighted' not in html and 'índice del dólar' not in html.lower():
        issue("consistencia", "info", "DXY mencionado sin clarificar que es el Trade Weighted USD Broad Index")

    # 2.7 ATH >= 52W High — permite igualdad (tolerancia 0.5% por ajustes de fuente)
    cur.execute("SELECT price FROM price_btc ORDER BY price DESC LIMIT 1")
    ath_row = cur.fetchone()
    if ath_row:
        ath_val = ath_row[0]
        cur.execute("SELECT MAX(price) FROM price_btc WHERE ts >= ?", (ts_52w,))
        high52_row = cur.fetchone()
        if high52_row and high52_row[0]:
            high52_val = high52_row[0]
            if ath_val < high52_val * 0.995:  # tolerancia 0.5%
                issue("consistencia", "critical",
                      f"ATH ${ath_val:,.0f} < 52W High ${high52_val:,.0f} — inconsistencia en jerarquía")

    db.close()

# ─── 3. TRAZABILIDAD ──────────────────────────────────────────────────────
def check_trazabilidad():
    db = sqlite3.connect(DB_PATH)
    cur = db.cursor()

    if not REPORT_PATH.exists():
        db.close()
        return

    html = REPORT_PATH.read_text()

    # 3.1 Verificar que TODAS las métricas en el reporte estén en daily_metrics
    # Extraer nombres de métricas del HTML
    metric_patterns = [
        (r'RSI.*?(\d+)', 'rsi_14'),
        (r'MVRV.*?(\d+\.?\d*)', 'mvrv'),
        (r'Hash Rate.*?([0-9,]+)', 'hash_rate'),
        (r'Dificultad.*?([0-9,]+)', 'difficulty'),
        (r'SMA 20.*?\$([0-9,]+)', 'sma_20'),
        (r'SMA 50.*?\$([0-9,]+)', 'sma_50'),
        (r'SMA 200.*?\$([0-9,]+)', 'sma_200'),
        (r'CPI YoY.*?(\d+\.?\d*)%', 'cpi_yoy'),
        (r'NFP.*?([+-]?\d+\.?\d*)\s*K', 'nfp'),
        (r'10Y.*?(\d+\.?\d*)%', 'yield_10y'),
        (r'VIX.*?(\d+\.?\d*)', 'vix'),
        (r'DXY.*?(\d+\.?\d*)', 'dxy'),
    ]

    found_metrics = set()
    for pattern, metric in metric_patterns:
        if re.search(pattern, html):
            found_metrics.add(metric)

    # 3.2 Verificar que los datos en daily_metrics fueron archivados HOY
    cur.execute("""
        SELECT COUNT(DISTINCT asset || ':' || metric) FROM daily_metrics
        WHERE report_date = ?
    """, (TODAY_STR,))
    row = cur.fetchone()
    archived = row[0] if row else 0

    if archived < 30:
        issue("trazabilidad", "critical",
              f"Solo {archived} métricas archivadas hoy — esperado >30. Pipeline incompleto.")
    elif archived < 50:
        issue("trazabilidad", "warning",
              f"Solo {archived} métricas archivadas hoy — verificar completitud.")

    # 3.3 Verificar que todas las métricas archiveadas tienen source definido
    cur.execute("""
        SELECT metric, source FROM daily_metrics
        WHERE report_date = ? AND (source IS NULL OR source = '')
    """, (TODAY_STR,))
    null_source = cur.fetchall()
    if null_source:
        issue("trazabilidad", "warning",
              f"{len(null_source)} métricas sin source definido: {[r[0] for r in null_source]}")

    # 3.4 Verificar calidad_status de macro_fred
    cur.execute("""
        SELECT COUNT(*) FROM macro_fred
        WHERE date >= ? AND quality_status != 'VERIFIED'
    """, (TODAY_STR,))
    row = cur.fetchone()
    if row and row[0] > 0:
        issue("trazabilidad", "warning",
              f"{row[0]} observaciones macro sin verificar en fecha {TODAY_STR}")

    # 3.5 News URLs — verificar que no haya URLs vacías
    url_pattern = re.compile(r'href=["\'](https?://[^"\']*)["\']')
    urls = url_pattern.findall(html)
    for url in urls:
        if not url or url in ["http://", "https://", ""]:
            issue("trazabilidad", "critical", f"URL vacía o inválida en reporte")

    # 3.6 Verificar que las noticias tienen texto (no son solo título)
    news_blocks = re.findall(r'<p.*?noticia.*?</p>', html, re.DOTALL | re.IGNORECASE)
    for block in news_blocks[:5]:  # check first 5
        if len(block) < 50:
            issue("trazabilidad", "warning", "Bloque de noticia con texto insuficiente")

    db.close()

# ─── 4. ANÁLISIS ─────────────────────────────────────────────────────────
def check_analisis():
    db = sqlite3.connect(DB_PATH)
    cur = db.cursor()

    if not REPORT_PATH.exists():
        db.close()
        return

    html = REPORT_PATH.read_text()

    # 4.1 Verificar que el RSI no se usa como señal única de compra/venta
    # Buscar frases que indiquen "compra" por solo RSI < 40
    if re.search(r'RSI.*?(?:sobreventa|oversold|compra).{0,30}(?:solo|solamente|únicamente)', html, re.IGNORECASE):
        issue("analisis", "warning", "RSI usado como única señal de compra — se necesitan confirmación")

    # 4.2 Verificar que no hay predicciones de precio sin metodología
    pred_patterns = [
        r'BTC.*?llegar[áa].*?\$(\d+)',
        r'BTC.*?objetivo.*?\$(\d+)',
        r'pronóstico',
        r'forecast',
    ]
    for pat in pred_patterns:
        if re.search(pat, html, re.IGNORECASE):
            # Debe haber metodología cerca
            if not re.search(r'metodología|modelo|escenario', html, re.IGNORECASE):
                issue("analisis", "warning",
                      f"Predicción/pronóstico encontrado sin metodología documentada")

    # 4.3 Verificar que los escenarios tienen catalizadores explícitos
    if 'escenario' in html.lower():
        # Debe haber mention de catalizadores
        if not re.search(r'catalizador|invalida|objetivo|horizonte', html, re.IGNORECASE):
            issue("analisis", "warning", "Escenario mencionado sin catalizadores o horizonte")

    # 4.4 Verificar que no se confunden horizontes (intradía vs semanal vs estructural)
    horiz_mix = [
        ("intradía", "semanal"),
        ("semanal", "estructural"),
        ("diario", "mensual"),
    ]
    for h1, h2 in horiz_mix:
        if h1 in html.lower() and h2 in html.lower():
            if not re.search(r'intradía.*semanal|semanal.*estructural', html, re.IGNORECASE | re.DOTALL):
                issue("analisis", "info",
                      f"Mezcla de horizontes ({h1} y {h2}) sin clarificar — puede confundir al lector")

    # 4.5 Verificar que las fuentes están citadas
    source_citations = re.findall(r'(?:Fuente:|Source:|Datos:)\s*\w+', html, re.IGNORECASE)
    if len(source_citations) < 2:
        issue("analisis", "info", "Pocas citas de fuentes en el reporte — verificar trazabilidad")

    db.close()

# ─── 5. PRESENTACIÓN ─────────────────────────────────────────────────────
def check_presentacion():
    if not REPORT_PATH.exists():
        return

    html = REPORT_PATH.read_text()

    # 5.1 Verificar que el reporte tiene fecha de generación
    if TODAY_STR not in html and TODAY.strftime("%d %b") not in html:
        issue("presentacion", "warning", "Fecha del reporte no encontrada en contenido")

    # 5.2 No debe haber texto cortado (última palabra incompleta al final)
    if re.search(r'\w$', html) and len(html) > 1000:
        # Simple check: last 20 chars shouldn't be a partial word
        last_chunk = html[-50:].strip()
        if last_chunk and not last_chunk.endswith(('.', '>', '"', "'", ')', '!', '?')):
            issue("presentacion", "info", "Reporte puede terminar con texto cortado")

    # 5.3 Gráficos con labels legibles (verificar que no haya texto superpuesto)
    # Solo verificamos que los SVG existan
    svg_count = len(re.findall(r'<svg', html))
    if svg_count == 0:
        issue("presentacion", "warning", "No se encontraron gráficos SVG en el reporte")

    # 5.4 Tablas con headers (verificar que no haya tablas sin estructura)
    table_count = len(re.findall(r'<table', html))
    if table_count > 0:
        th_count = len(re.findall(r'<th', html))
        if table_count > 0 and th_count == 0:
            issue("presentacion", "warning", "Hay tablas sin headers (<th>) — estructura incorrecta")

    # 5.5 CSS/estilos aplicados (verificar que el reporte tiene estilos)
    if '<style' not in html and 'class=' not in html:
        issue("presentacion", "warning", "Reporte sin estilos CSS aplicados")

# ─── EJECUCIÓN PRINCIPAL ──────────────────────────────────────────────────
def run():
    print(f"\n{'='*65}")
    print(f"PUBLICATION GATE — {TODAY_STR}")
    print(f"{'='*65}\n")

    # Hard gates: these cannot be compensated by a weighted score.
    if REPORT_PATH.is_file():
        for message in audit_report_html(REPORT_PATH.read_text(encoding="utf-8"), report_day=TODAY, strict_asof=True):
            issue("consistencia", "critical", message)
    else:
        issue("exactitud", "critical", "Reporte ausente, no se puede publicar")

    check_exactitud()
    check_consistencia()
    check_trazabilidad()
    check_analisis()
    check_presentacion()

    # ── Calcular score final ────────────────────────────────────────────────
    total_score = sum(results[cat]["score"] * WEIGHTS[cat] for cat in WEIGHTS)

    print("RESULTADOS POR CATEGORÍA:")
    for cat in WEIGHTS:
        score = results[cat]["score"]
        issues = results[cat]["issues"]
        bar = "█" * int(score / 10) + "░" * (10 - int(score / 10))
        status = "PASS" if score >= 70 else "FAIL"
        print(f"  {cat.upper():15s} [{bar}] {score:.0f}/100  {status}")
        for iss in issues:
            print(iss)
        print()

    print(f"{'─'*65}")
    print(f"SCORE PONDERADO: {total_score:.1f}/100")
    print(f"UMBRAL: {PASS_SCORE}/100")
    print(f"{'─'*65}")

    # ── Decisión ────────────────────────────────────────────────────────────
    critical_count = sum(1 for cat in results for iss in results[cat]["issues"]
                         if "🔴" in iss)
    blocked = critical_count > 0 or total_score < PASS_SCORE

    if blocked:
        print(f"⛔ PUBLICACIÓN BLOQUEADA")
        if critical_count > 0:
            print(f"   {critical_count} error(es) crítico(s) detectado(s)")
        if total_score < PASS_SCORE:
            print(f"   Score {total_score:.1f} < {PASS_SCORE}")
    else:
        print(f"✅ PUBLICACIÓN AUTO-APROBADA")

    # ── Log estructurado ────────────────────────────────────────────────────
    log_path = REPORT_PATH.parent / f"gate_{TODAY_STR}.json"
    import json
    log = {
        "date": TODAY_STR,
        "report": str(REPORT_PATH),
        "report_sha256": hashlib.sha256(REPORT_PATH.read_bytes()).hexdigest() if REPORT_PATH.is_file() else None,
        "score_total": round(total_score, 2),
        "pass": not blocked,
        "blocked_reason": "critical_errors" if critical_count > 0 else ("low_score" if total_score < PASS_SCORE else None),
        "scores": {cat: results[cat]["score"] for cat in WEIGHTS},
        "critical_count": critical_count,
        "all_issues": [iss for cat in results for iss in results[cat]["issues"]],
    }
    log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False))
    print(f"\nLog: {log_path}")

    return 0 if not blocked else 1

if __name__ == "__main__":
    sys.exit(run())
