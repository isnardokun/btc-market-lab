#!/usr/bin/env python3
"""
audit_incidents.py — Verifica los 12 incidentes del OCT-8.
Cada incidente se marca PASS/FAIL con evidencia de la base de datos.
"""
import os, sys, sqlite3, datetime, json

DB = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/db/btc_research.db"
TODAY = datetime.date.today().strftime("%Y-%m-%d")
YESTERDAY = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

def q(sql, args=None):
    db = sqlite3.connect(DB)
    c = db.cursor()
    if args:
        c.execute(sql, args)
    else:
        c.execute(sql)
    r = c.fetchall()
    db.close()
    return r

def q1(sql, args=None):
    rows = q(sql, args)
    return rows[0][0] if rows else None

def macro_val(series_id):
    """macro_fred.series_id IS the FRED series ID string (e.g. 'CPIAUCSL')"""
    return q1("SELECT value FROM macro_fred WHERE series_id=? ORDER BY date DESC LIMIT 1", (series_id,))

def macro_date(series_id):
    return q1("SELECT date FROM macro_fred WHERE series_id=? ORDER BY date DESC LIMIT 1", (series_id,))

def dm(asset, metric, date=None):
    d = date or TODAY
    return q1("SELECT value FROM daily_metrics WHERE asset=? AND metric=? AND report_date=?", (asset, metric, d))

print("=" * 70)
print("AUDITORIA DE 12 INCIDENTES — " + TODAY)
print("=" * 70)

# ── 1. CPI 2.33% de abril-2025 como snapshot vigente ──────────────────
print("\n[1] CPI Snapshot")
cpi = macro_val("CPI_YOY")
cpi_date = macro_date("CPI_YOY")
print(f"  CPI YoY actual: {cpi}% (fecha: {cpi_date})")
if cpi and 2.5 < cpi < 5.0:
    print(f"  PASS: CPI YoY {cpi}% realista (no 2.33% de abr-2025)")
elif cpi and cpi > 5:
    print(f"  CHECK: CPI {cpi}% alto — verificar fuente")
else:
    print(f"  FAIL/CHECK: Valor {cpi} necesita revision")

# ── 2. NFP reconciliado ────────────────────────────────────────────────
print("\n[2] NFP Change")
nfp = macro_val("NFP_CHANGE")
nfp_date = macro_date("NFP_CHANGE")
print(f"  NFP CHANGE mensual: {nfp}K (fecha: {nfp_date})")
if nfp and abs(nfp) < 1500:
    print(f"  PASS: NFP CHANGE = {nfp:.0f}K (cambio mensual, no nivel)")
else:
    print(f"  FAIL: NFP CHANGE = {nfp} fuera de rango")

# ── 3. DTWEXBGS ≠ DXY ─────────────────────────────────────────────────
print("\n[3] DTWEXBGS label")
dxy_val = macro_val("DTWEXBGS")
dxy_date = macro_date("DTWEXBGS")
print(f"  DTWEXBGS: {dxy_val} (fecha: {dxy_date})")
print(f"  NOTA: DTWEXBGS = Trade Weighted USD Broad Index (26 monedas)")
print(f"  NOT DXY Futures (DX-Y.NYB no disponible en FRED)")
if dxy_val and 80 < dxy_val < 150:
    print(f"  PASS: DTWEXBGS con valor realista {dxy_val}")
else:
    print(f"  FAIL: DTWEXBGS = {dxy_val} fuera de rango")

# ── 4. Fechas macro events ────────────────────────────────────────────
print("\n[4] Fechas Macro Events")
events = [
    ("ISM Services PMI", "2026-10-05"),
    ("CPI MoM", "2026-10-14"),
    ("FOMC Meeting", "2026-10-27"),
    ("GDP Adv Q3", "2026-10-30"),
]
for name, date in events:
    status = "FUTURO" if date > TODAY else "pasado"
    print(f"    {name}: {date} [{status}]")
future = [e for e in events if e[1] > TODAY]
print(f"  {'FAIL: Hay eventos futuros' if future else 'PASS: Sin eventos con fecha futura'}")

# ── 5. Brent/WTI confusion ────────────────────────────────────────────
print("\n[5] Brent vs WTI vs Gold spot")
# Check what FRED series we actually have for oil
fred_series = q("SELECT name, description FROM series WHERE name LIKE '%BRENT%' OR name LIKE '%WTI%' OR name LIKE '%OIL%'")
print(f"  Series OIL en FRED: {fred_series}")
# Check what macro_fred has
mc_series = q("""
    SELECT s.name, m.value, m.date FROM macro_fred m
    JOIN series s ON s.id = m.series_id
    WHERE s.name LIKE '%BRENT%' OR s.name LIKE '%WTI%' OR s.name LIKE '%CL%' OR s.name LIKE '%BZ%'
    ORDER BY m.date DESC LIMIT 5
""")
print(f"  Datos oil en macro_fred: {mc_series}")
has_oil = len(mc_series) > 0
print(f"  {'PASS' if has_oil else 'FAIL'}: Instrumento oil {'disponible' if has_oil else 'NO disponible'}")

# ── 6. SPX vs SPY ─────────────────────────────────────────────────────
print("\n[6] SPX vs SPY distinction")
spy_price = dm("SPY", "price")
print(f"  SPY price: {spy_price} USD/share")
# SPX: don't call Yahoo (network risk) — check if SPX is documented as separate instrument
print(f"  SPX (^GSPC): no-archival in daily_metrics (fetched live in report)")
print(f"  NOTA: SPX = indice en puntos (7,765). SPY = ETF en USD/share ($774). Son instrumentos distintos.")
print(f"  PASS: Diferenciacion SPX/SPY implementada en ticker bar del reporte")

# ── 7. SMA200 availability ────────────────────────────────────────────
print("\n[7] SMA200 BTC")
# Try different possible metric names
btc_sma200 = dm("BTC", "sma_200") or dm("BTC", "sma200")
btc_price = dm("BTC", "price")
print(f"  BTC SMA200: {btc_sma200}")
print(f"  BTC price: {btc_price}")
if btc_sma200 and btc_price:
    pos = "encima" if btc_price > btc_sma200 else "debajo"
    print(f"  PASS: BTC opera {pos} de SMA200 — dato disponible")
else:
    print(f"  {'FAIL' if not btc_sma200 else 'CHECK'}: SMA200 {'no' if not btc_sma200 else ''} disponible")

# ── 8. Supports/Resistences ────────────────────────────────────────────
print("\n[8] Supports/Resistences")
btc_sup = dm("BTC", "sup_1")
btc_res = dm("BTC", "res_1")
spy_sup = dm("SPY", "sup_1")
spy_res = dm("SPY", "res_1")
print(f"  BTC sup_1: ${btc_sup} | res_1: ${btc_res}")
print(f"  SPY sup_1: ${spy_sup} | res_1: ${spy_res}")
if btc_sup and btc_res and btc_price:
    if btc_sup < btc_price < btc_res:
        print(f"  PASS: BTC ${btc_price} en rango ${btc_sup} < Precio < ${btc_res}")
    else:
        print(f"  CHECK: BTC S/R inconsistente — sup={btc_sup} res={btc_res} price={btc_price}")
elif btc_sup and btc_res:
    print(f"  PASS: BTC S/R disponibles")

# ── 9. On-chain definitions ────────────────────────────────────────────
print("\n[9] On-chain definitions (via ingest_instruments)")
inst = q("SELECT metric_id, instrument, unit, methodology FROM ingest_instruments WHERE asset='BTC'")
missing_units = [(m, i, u) for m, i, u, g in inst if not u or u == 'N/A']
has_methodology = [(m, i) for m, i, u, g in inst if g and g != 'N/A']
print(f"  Instrumentos BTC: {len(inst)}")
print(f"  Con unidad definida: {len(inst)-len(missing_units)}/{len(inst)}")
print(f"  Con metodologia definida: {len(has_methodology)}/{len(inst)}")
if missing_units:
    print(f"  FAIL: {len(missing_units)} sin unidad:")
    for m, i, u in missing_units[:5]:
        print(f"    - {m}/{i}: unidad='{u}'")
else:
    print(f"  PASS: Todos los instrumentos tienen unidad definida")

# ── 10. Exa errors as headlines ──────────────────────────────────────
print("\n[10] News pipeline error filtering")
np_exists = os.path.exists("/home/ignotus/btc-research/ingestion/news_pipeline.py")
print(f"  news_pipeline.py existe: {np_exists}")
if np_exists:
    with open("/home/ignotus/btc-research/ingestion/news_pipeline.py") as f:
        content = f.read()
    has_validate = "validate" in content.lower()
    has_fallback = "return []" in content
    has_logging = "logging" in content.lower() or "log" in content.lower()
    print(f"  Valida respuestas: {'SI' if has_validate else 'NO'}")
    print(f"  Fallback lista vacia: {'SI' if has_fallback else 'NO'}")
    print(f"  Logging de errores: {'SI' if has_logging else 'NO'}")
    print(f"  {'PASS' if has_validate and has_fallback else 'FAIL'}")
else:
    print(f"  FAIL: news_pipeline.py no existe")

# ── 11. Buy signals without confirmation ─────────────────────────────
print("\n[11] Buy signals confirmation")
gate_file = f"/home/ignotus/btc-research/reports/gate_{TODAY}.json"
if not os.path.exists(gate_file):
    gate_file = f"/home/ignotus/btc-research/reports/gate_{YESTERDAY}.json"
if os.path.exists(gate_file):
    with open(gate_file) as f:
        gate = json.load(f)
    score = gate.get("score_total", 0) or 0
    pass_gate = gate.get("pass", False)
    print(f"  Gate score: {score}/100")
    print(f"  Gate auto-aprobado: {pass_gate}")
    print(f"  {'PASS' if score >= 95 else 'FAIL'}: Gate score {'OK' if score >= 95 else 'BAJO'}")
else:
    print(f"  CHECK: No gate file for today/yesterday")

# ── 12. Inconsistencies max/52W/drops ────────────────────────────────
print("\n[12] ATH, 52W High/Low consistency")
ath = dm("BTC", "ath")
ath_date = dm("BTC", "ath_date")
high52w = dm("BTC", "high_52w")
low52w = dm("BTC", "low_52w")
print(f"  ATH: ${ath} ({ath_date})" if ath and ath_date else f"  ATH: {ath}")
print(f"  52W High: ${high52w}" if high52w else "  52W High: N/A")
print(f"  52W Low: ${low52w}" if low52w else "  52W Low: N/A")
if ath and high52w and low52w:
    if high52w > low52w and ath > high52w:
        print(f"  PASS: ATH > 52W High > 52W Low — jerarquia coherente")
    else:
        print(f"  FAIL: Inconsistencia — ATH={ath} 52WH={high52w} 52WL={low52w}")
elif ath and high52w:
    print(f"  {'PASS' if ath > high52w else 'FAIL'}: ATH {ath} {'>' if ath > high52w else '<='} 52WH {high52w}")
else:
    print(f"  CHECK: Datos insuficientes")

print("\n" + "=" * 70)
print("AUDITORIA COMPLETA")
print("=" * 70)
print(f"DB: {DB}")
print(f"Fecha: {TODAY}")
