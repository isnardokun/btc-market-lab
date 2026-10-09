# ASTRA — Pendientes Mercados Daily Pro
## Brief para revisión y ajuste

**Proyecto:** Mercados Daily Pro — Generador automatizado de reportes financieros BTC/macro.
**Ubicación:** `/home/ignotus/btc-research/`
**BD:** `/home/ignotus/btc-research/db/btc_research.db` (SQLite)
**Cron:** 06:00 UTC daily — `scripts/daily.sh`
**Publication gate:** score ≥ 95 para publicar. Gate corre ANTES de archive/export (P0 resuelto).
**Auditoría vigente:** RED TEAM V2 — "APROBACIÓN CONDICIONADA" (6.3/10), 10/12 PASS + 2 INFO.

---

## Estado actual verificado (2026-10-08)

Pipeline completo funciona con EXIT:0, gate 99/100, 22 tests passing.

**Flujo:** `ingest_price → ingest → ingest_fred → charts → daily_report → archive_metrics → publication_gate → [archive_report → export_dashboard → snapshot]`

**Lo que YA está bien:**
- Unit tests para indicadores y metric_registry ✅
- Integration tests para el pipeline completo ✅
- Publication gate en posición correcta (antes de archive) ✅
- metric_registry con conversiones explícitas (no por magnitud) ✅
- FRED retroactive reconciliation (60 días) ✅
- ATH >= 52W High check ✅
- SMA200 = 252 días ✅
- compute_scenarios sin horizontes ni confidence scores ✅
- `storage/repositories.py` creado con todos los data access functions ✅

---

## 5 Pendientes — Código relevante y preguntas

---

### PEN-1: Refactoring `analysis/daily_report.py` (~1,339 líneas)

**Por qué importa:** El archivo mezcla 3 capas en `main()` — data fetching (280+ líneas), cómputo de indicadores, y generación HTML. El audit V2 recomendó separación de concerns.

**Estado de `storage/repositories.py`:** Ya existe en `/home/ignotus/btc-research/storage/repositories.py` con todas las funciones de acceso a datos:
- `get_btc_price_data()`, `get_spy_price_data()`, `get_gold_price_data()`
- `get_btc_onchain()`, `get_macro_fred()`, `get_ath()`, `get_52w_range()`
- `get_daily_metrics_latest()`, `get_report_archive()`

**Lo que NO funciona:** `main()` en `daily_report.py` aún hace fetch inline en lugar de llamar repositories. El trabajo pendiente es reescribir `main()` para usar `storage/repositories.py` como única fuente de datos.

**Código relevante — `main()` actual (secciones clave):**

```python
# analysis/daily_report.py — línea ~793 a ~1070 (resumen)
def main():
    # ── BTC ──
    btc_h, btc_l, btc_o, btc_c = yahoo_ohlc("BTC-USD", 252)
    btc_sma20  = compute_ma(btc_c, 20)
    btc_sma50  = compute_ma(btc_c, 50)
    btc_sma200 = compute_ma(btc_c, 200)
    btc_rsi    = compute_rsi(btc_c, 14)
    btc_macd, btc_sig, btc_hist = compute_macd(btc_c)
    btc_atr    = atr(btc_h, btc_l, btc_c, 14)
    btc_cur    = btc_c[-1]

    # Soportes y resistencias
    btc_sup, btc_res = find_supp_res(btc_c, btc_h, btc_l, lookback=20)

    # BTC on-chain (ACTUALMENTE inline, debería usar repositories.get_btc_onchain)
    db = sqlite3.connect(DB_PATH)
    oc = {}
    # ... 70 líneas de SQL con JOIN y conversiones ...
    db.close()

    # ── SPY ──
    spy_h, spy_l, spy_o, spy_c = yahoo_ohlc("SPY", 252)
    spy_sma20  = compute_ma(spy_c, 20)
    spy_sma200 = compute_ma(spy_c, 200)
    spy_rsi    = compute_rsi(spy_c, 14)
    spy_macd, spy_sig, spy_hist = compute_macd(spy_c)
    spy_atr    = atr(spy_h, spy_l, spy_c, 14)
    spy_cur    = spy_c[-1]

    # ── GOLD ──
    gold_h, gold_l, gold_o, gold_c = yahoo_ohlc("GC=F", 252)
    gold_sma20  = compute_ma(gold_c, 20)
    gold_sma200 = compute_ma(gold_c, 200)
    gold_rsi    = compute_rsi(gold_c, 14)
    gold_cur    = gold_c[-1]

    # Macro FRED (ACTUALMENTE inline, debería usar repositories.get_macro_fred)
    db = sqlite3.connect(DB_PATH)
    # ... 60 líneas de SQL para 14 series ...
    db.close()

    # Genera HTML usando todos los datos recolectados
    html = render_html({...bunch of variables...})
    with open(REPORT_HTML, "w") as f:
        f.write(html)
```

**Pregunta para ASTRA:** ¿Cuál es el criterio de aceptación exacto para la separación de capas?
1. `main()` solo orquesta: llama repositories → llama indicadores → llama render
2. Indicadores se computan en funciones separadas por activo (no inline en main)
3. HTML generation en `rendering/` layer (ya existe `rendering/charts.py` pero no `rendering/report.py`)

**Restricciones:**
- NO cambiar el output HTML (mismas variables, mismo formato, mismo template)
- NO cambiar la lógica de indicadores (ya están testeados)
- NO cambiar `storage/repositories.py` (ya funciona)
- El gate score debe mantenerse en 99/100 post-refactor

---

### PEN-2: Telegram

**Por qué importa:** El audit V2 no evaluó notificaciones porque no existen. Para un sistema de publicación profesional, alertas automáticas son estándar.

**Lo que existe:** Nada. Sin bot, sin webhook, sin código.

**Lo que se necesita:**
1. Bot token de Telegram (el usuario debe proporcionarlo)
2. Decide qué eventos disparan mensajes:
   - Gate FAIL (reporte bloqueado)
   - Gate PASS + publicación exitosa
   - Anomalías (precio rompe ATH, RSI extremos, CPI fuera de rango)
   - Notificaciones diarias automáticas con resumen

**Código base — `publication_gate.py` produce este JSON:**

```json
// gate_YYYY-MM-DD.json
{
  "score_total": 99.0,
  "pass": true,
  "checks": {
    "1_btc_ath":  {"pass": true,  "score": 10, "detail": "ATH $124,753"},
    "2_macro_data": {"pass": true, "score": 20, "detail": "14 series OK"},
    "3_onchain":  {"pass": true,  "score": 30, "detail": "12/12 series"},
    "4_exa":      {"pass": true,  "score": 20, "detail": "0 errores"},
    "5_signals":  {"pass": true,  "score": 19, "detail": "99 score"}
  },
  "report_date": "2026-10-08"
}
```

**Pregunta para ASTRA:** Diseña el módulo de notificaciones:
1. ¿Qué formato de mensajes? (texto, markdown, screenshot del gate)
2. ¿Qué eventos? (gate fail, gate pass, anomalías, diario automático)
3. ¿Cómo se activa el bot? (polling vs webhook — polling es más simple para cron)
4. ¿Dónde se guarda el token? (variable de ambiente, no hardcodear)

---

### PEN-3: Dashboard dinámico

**Por qué importa:** `dashboards/dashboard.html` es actualmente estático. El audit V2 mencionó "breaking news mode".

**Lo que existe:**
- `rendering/export_dashboard.py` genera `dashboard_latest.json` con 10 series macro semaforizadas
- `dashboards/dashboard.html` (508 líneas) lee `dashboard_latest.json` al cargar

**Problema:** `dashboard_latest.json` solo se actualiza cuando corre el pipeline (06:00 UTC). No hay modo "live" ni actualización automática.

**Pregunta para ASTRA:** Diseña el dashboard dinámico:
1. ¿"Breaking news mode" = polling del `dashboard_latest.json` cada N minutos?
2. ¿O es un flag `breaking=true` en `dashboard_latest.json` que activa una clase CSS diferente en el HTML?
3. ¿O es un segundo dashboard (`dashboard_live.html`) con datos en tiempo real desde APIs?
4. ¿Qué datos adicionales necesita el modo live? (precio en tiempo real, Funding Rate, orderbook?)

---

### PEN-4: News classification

**Por qué importa:** Audit V2 P1-7: keyword-based classification tiene sesgos documentados.

**Lo que existe:** `ingestion/news_pipeline.py` (367 líneas) usa keywords estáticos para clasificar:
```python
POSITIVE = ["adoption", "etf", "approval", "bullish", "institutional", "rally"]
NEGATIVE = ["ban", "crackdown", "regulation", "hack", "bearish", "crime"]
```

**Problema:** No hay modelo de bias detection. Clasifica por presencia de keywords, no por significado real.

**Pregunta para ASTRA:** Propón una solución factible:
1. ¿Modelo local con Ollama para bias detection? (requiere setup `llm-wiki` skill)
2. ¿API externa (NewsAPI, GDELT)?
3. ¿Clasificación keyword + validación humana antes de publicación?
4. ¿Umbral mínimo de confianza (e.g., solo publicar si confidence > 0.7)?

---

### PEN-5: Day-over-day history

**Por qué importa:** El informe actual solo muestra valores del día. No hay comparación DoD.

**Lo que existe:** `daily_metrics` tiene ~35,000 filas con métricas archivadas por fecha. Se puede hacer:
```sql
SELECT metric, value FROM daily_metrics
WHERE report_date = '2026-10-07' AND asset = 'BTC' AND metric = 'close'
UNION ALL
SELECT metric, value FROM daily_metrics
WHERE report_date = '2026-10-08' AND asset = 'BTC' AND metric = 'close'
```

**Pregunta para ASTRA:** Diseña el historial DoD:
1. ¿Qué métricas tienen comparación DoD? (precio close, RSI, MVRV, funding rate, volume)
2. ¿Presentación: tabla, sparklines, flechas direccionales?
3. ¿Cuántos días de histórico muestra el HTML? (7, 14, 30?)
4. ¿Cómo archivamos el histórico? (ya existe `daily_metrics` — solo consultar)

---

## Lo que NO se debe tocar

| Archivo | Por qué |
|---|---|
| `validation/publication_gate.py` | Funcional, score 99/100 |
| `quant_engine/indicators.py` | Testeado, pasaría tests |
| `ingestion/metric_registry.py` | Testeado, conversiones verificadas |
| `scripts/daily.sh` | Flujo correcto, cron schedule OK |
| `storage/repositories.py` | Funciona, 256 líneas, no requiere cambios |

---

## Flags已知问题

1. `analysis/daily_report.py` línea ~65: `oc["sma200"] = btc_sma200` añadido manualmente — fue necesario porque `get_btc_onchain()` no retornaba SMA200 desde Yahoo. Si reescribes main() para usar repositories, asegúrate que SMA200 esté disponible.

2. `quant_engine/indicators.py` tiene `find_supp_res()` — usa `lookback=20` por defecto. No cambiar este default.

3. Publication gate check 2.7 (ATH >= 52W High) usa columna `price` en `price_btc`, NO `value`. SQL verificado:
   ```sql
   SELECT MAX(price) FROM price_btc WHERE ts >= ?
   ```

---

## Tests que deben seguir pasando

Después de cualquier cambio, ejecutar:
```bash
python3 tests/run_all.py
```

Suite completa:
- **Unit: Indicators** — compute_rsi (Wilder), compute_ma (float, no list), compute_macd, atr, stoch, williams_r, find_supp_res
- **Unit: Metric Registry** — 12 series con conversiones verificadas (hash_rate÷1e18, difficulty÷1e12, market_cap÷1e12, utxo_count÷1e6, ratios sin conversión)
- **Integration: Pipeline** — price_btc fresco (≤7 días), macro_fred fresco (≤30 días), gate score ≥95, HTML report >10KB, ATH máximo histórico, CPI YoY 0.5-10%, NFP -500K a +500K

---

## Para que ASTRA responda

1. Para cada PEN-1 a PEN-5:
   - Propuesta técnica concreta (no descripción, código o pseudocódigo)
   - Criterio de aceptación
   - Qué archivos toca
   - Qué archivos NO toca

2. Sugerencia de orden de implementación (cuál resolver primero, cuál depende de cuál)

3. Hallazgos nuevos: ¿ve algo en el código actual que no esté en los 12 incidentes del audit V2 y que sea P1/P2?
