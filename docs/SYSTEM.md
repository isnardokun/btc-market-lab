# Mercados Daily Pro — Arquitectura y Documentación

> Sistema de inteligencia financiera auditable. Generación automática de reportes diarios BTC + SPY + Oro con datos on-chain, macro y noticias.

---

## Arquitectura del Sistema

```
┌──────────────────────────────────────────────────────────────┐
│                  DAILY.SH (cron 06:00 UTC)                   │
│                 Pipeline de 11 pasos                         │
└─────────────────────┬────────────────────────────────────────┘
                      │
       ┌──────────────┼──────────────┬───────────────┐
       ▼              ▼              ▼               ▼
┌────────────┐ ┌──────────┐ ┌──────────┐ ┌─────────────────┐
│ INGESTION  │ │VALIDATION│ │ANALYSIS  │ │   RENDERING     │
│            │ │          │ │          │ │                 │
│ingest_price│ │publication│ │daily_   │ │ charts.py       │
│ingest_fred │ │_gate.py  │ │report.py │ │ archive_report  │
│ingest.py   │ │audit_    │ │          │ │ export_dash-    │
│news_pipeline│ │incidents │ │          │ │ board.py        │
│backfill_*  │ │archive_  │ │          │ │                 │
│            │ │metrics.py │ │          │ │                 │
└─────┬──────┘ └────┬─────┘ └────┬─────┘ └────────┬────────┘
      │              │            │                 │
      ▼              ▼            ▼                 ▼
┌───────────────────────────────────────────────────────────────┐
│                     BTC_RESEARCH.DB                           │
│  price_btc │ daily (on-chain) │ macro_fred │ daily_metrics │
│  reports   │ ingest_instruments│                              │
└───────────────────────────────────────────────────────────────┘
```

**Flujo de publicación (CRÍTICO):**
- Pasos 1-6: ingestión y generación (sin publicación)
- Paso 7: `publication_gate.py` — **ANTES de cualquier publicación**
- Pasos 8-9: `archive_report` + `export_dashboard` — **SOLO si gate pasa**
- Si gate falla: borrador → `reports/rejected/`, exit 1

---

## Módulos

### 1. `ingestion/` — Adaptadores de datos crudos

| Script | Fuente | Frecuencia | Datos |
|---|---|---|---|
| `ingest_price.py` | Yahoo Finance | Diario | BTC-USD OHLCV desde Sep 2014 |
| `ingest_fred.py` | FRED API | Diario | 14 series macro con reconciliación retroactiva |
| `ingest.py` | bitview local DB | Diario | On-chain: MVRV, SOPR, hash_rate, difficulty, etc. |
| `news_pipeline.py` | Exa API (mcporter) | Diario | 5+5+5 noticias BTC/SPY/GOLD + macro |
| `backfill_metrics.py` | Yahoo Finance | Manual | Historial SMA, RSI para todos los activos |
| `backfill_onchain_metrics.py` | bitview | Manual | Historial on-chain desde Sep 2014 |
| `metric_registry.py` | bitview | Estático | Conversiones explícitas por series_id |
| `config.py` | — | Estático | API keys, series IDs, thresholds |

**`ingest_fred.py` — Reconciliación retroactiva:**
- Cada ejecución re-verifica los últimos 60 días contra FRED
- Detecta y alerta sobre revisiones (CPI, NFP, retail sales se revisan 1-3 meses)
- No sobreescribe datos históricos sin auditoría — solo registra discrepancias

**`metric_registry.py` — Conversiones verificadas:**
```
Series ID  Métrica         Fuente_Unidad  → Display  Factor
1097       hash_rate       H/s            EH/s      ÷1e18  ✅
550        difficulty      compact        T          ÷1e12  ✅
1330       market_cap      USD            T$         ÷1e12  ✅ (dtype=Dollars)
1978       realized_cap    USD            T$         ÷1e12  ✅ (dtype=Dollars)
7          active_cap      USD            T$         ÷1e12  ✅ (dtype=Dollars)
1357       mvrv            ratio          ratio      ×1.0   ✅
2052       sopr_1w         ratio          ratio      ×1.0   ✅
104        asopr_1w        ratio          ratio      ×1.0   ✅
2807       nupl            ratio          ratio      ×1.0   ✅
2806       rhodl_ratio     ratio          ratio      ×1.0   ✅
2375       utxo_count      count          M          ÷1e6   ✅
5          active_addrs    count          count      ×1.0   ✅
```
**NO se usa umbral 1e15** para market_cap/realized_cap/active_cap.
Las series con `dtype=Dollars` YA están en USD.

### 2. `validation/` — Calidad y gobernanza

| Script | Responsabilidad | Criterio |
|---|---|---|
| `publication_gate.py` | Score ponderado de calidad antes de publicar | ≥ 95/100 + gate ANTES de publicación |
| `audit_incidents.py` | Verificación de los 12 incidentes del OCT-8 | 10/12 mínimo |
| `archive_metrics.py` | Archivar métricas diarias en `daily_metrics` | 74 métricas/día |

**Publication Gate — Scoring:**

| Categoría | Peso | Descripción |
|---|---|---|
| Exactitud | 35% | Datos dentro de rango válido, fuentes verificadas |
| Consistencia | 25% | ATH >= 52W High, precio dentro de S/R válido |
| Trazabilidad | 20% | Todo dato tiene source + timestamp |
| Análisis | 15% | Narrativas basadas en datos, no fabricaciones |
| Presentación | 5% | HTML sin errores, CSS coherente |

**Hard gates (no compensables):** errores de API, valores inventados, fechas inconsistentes, columnas faltantes.

### 3. `quant_engine/` — Cálculos deterministas

**`indicators.py`** — Funciones puras sin side effects. Mismos inputs → mismo output.

| Función | Descripción | Contrato |
|---|---|---|
| `compute_rsi(closes, period)` | RSI de Wilder (TradingView method) | float 0-100 or None |
| `compute_ma(closes, period)` | SMA simple | float or None (NO lista) |
| `ema_python(data, n)` | EMA con k=2/(n+1) | float or None |
| `compute_macd(closes)` | MACD(12,26,9) | (macd, signal, hist) |
| `stoch(highs, lows, closes)` | Estocástico lento (14,3,3) | (k, d) |
| `williams_r(highs, lows, closes)` | Williams %R | float or None |
| `cci(highs, lows, closes)` | Commodity Channel Index | float or None |
| `atr(highs, lows, closes)` | Average True Range | float or None |
| `find_supp_res(closes, highs, lows)` | Soportes y resistencias locales | ([sup], [res]) |
| `compute_scenarios(price, ...)` | Escenarios condicionales con activación, objetivo, catalizadores, invalidez | (bull, base, bear) |

**`compute_scenarios()` — Diseñado sin:**
- Confianza "alta/media" (sin backtesting no es calibrable)
- Horizontes derivados de ATR (no predictivos)

**Sí incluye:** nivel de activación, objetivo condicional, catalizadores concretos, condición de invalidez.

### 4. `storage/` — Acceso a datos

**`repositories.py`** — Capa de acceso a datos, SEPARADA de lógica de presentación.

| Función | Datos |
|---|---|
| `get_btc_ohlc(days)` | OHLCV de price_btc |
| `get_btc_onchain_latest()` | On-chain con conversiones verificadas |
| `get_macro_latest(series_ids)` | Valores macro de macro_fred |
| `get_data_freshness()` | Timestamps de última actualización |

### 5. `analysis/` — Interpretación y narrativa

**`daily_report.py`** (~1500 líneas)
- Coordina todos los módulos
- Genera HTML del reporte diario
- Fetch de datos: Yahoo (precio), bitview (on-chain), FRED (macro)
- Construcción de narrativa: BTC, SPY, GOLD, Macro
- Integración de news pipeline

**Narrativa BTC incluye:**
- Precio actual, cambio %, ATH, 52W high/low, from-ATH
- MVRV, SOPR, HODL R, NUPL, hash_rate, difficulty, active_addrs, UTXO count
- RSI (Wilder), MACD, Estocástico, CCI, Williams %R
- SMA 20/50/100/200 (Yahoo Finance)
- Soportes y resistencias dinámicos
- Escenarios (bull/base/bear) con activación, objetivo, catalizadores, invalidez
- 5 noticias de Exa validadas

### 6. `rendering/` — Output y visualización

| Script | Output | Formato |
|---|---|---|
| `charts.py` | SVGs de precio con SMA 20/50 | Inline en HTML |
| `archive_report.py` | Versiones HTML en BD | `reports` table |
| `export_dashboard.py` | Dashboard JSON con semáforo | `dashboard_latest.json` |

### 7. `scripts/` — Orquestación

**`daily.sh`** — Cron job (06:00 UTC — 01:00 Colombia):

```bash
# FASE 1: INGESTA
1. python3 ingestion/ingest_price.py      # BTC precio Yahoo
2. python3 ingestion/ingest.py             # On-chain bitview
3. python3 ingestion/ingest_fred.py        # Macro FRED + reconciliación

# FASE 2: GENERACIÓN
4. python3 rendering/charts.py             # SVGs
5. python3 analysis/daily_report.py        # HTML + news + narrativa
6. python3 validation/archive_metrics.py    # 74 métricas en BD

# FASE 3: PUBLICACIÓN CONDICIONAL
7. python3 validation/publication_gate.py   # QUALITY GATE — ANTES de publicar
   ↓ SI PASA (exit 0):
8.   python3 rendering/archive_report.py     # Archivar HTML aprobado
9.   python3 rendering/export_dashboard.py   # Actualizar dashboard
   ↓ SI FALLA (exit 1):
     cp reporte → reports/rejected/
```

---

## Base de Datos — `btc_research.db`

### Tablas

| Tabla | Filas | Descripción |
|---|---|---|
| `price_btc` | 4,404 | BTC-USD daily OHLCV, Yahoo, Sep 2014→hoy |
| `daily` | ~9M | Series on-chain bitview (ts en Unix seg) |
| `series` | 2,807 | Metadata de series bitview (name, dtype, provider) |
| `macro_fred` | 18,151 | 14 series macro, Sep 2014→hoy |
| `daily_metrics` | ~35K | Métricas diarias archivadas (SMA, RSI, etc.) |
| `reports` | N | Versiones HTML de reportes |
| `ingest_instruments` | 33 | Catálogo de instrumentos con metadata |

### Series FRED (macro_fred)

| series_id | Descripción | Unidad |
|---|---|---|
| DGS10 | Yield 10Y Treasury | % |
| DGS2 | Yield 2Y Treasury | % |
| DTWEXBGS | Trade Weighted USD Broad Index | índice |
| VIXCLS | VIX | índice |
| NFCI | NFCI (Chicago Fed) | índice |
| UNRATE | Tasa de desempleo | % |
| PAYEMS | Nivel de empleo total (miles) | miles |
| RSXFS | Ventas minoristas | M$ |
| CBBTCUSD | BTC de FRED | USD |
| CPIAUCSL | IPC All Items (índice) | índice |
| PPIACO | PPI | índice |
| CPI_YOY | CPI YoY calculado (12-mo lag) | % |
| PPI_YOY | PPI YoY calculado | % |
| NFP_CHANGE | Cambio mensual NFP (PAYEMS diff) | miles |

**Nota:** DTWEXBGS ≠ DXY. Es el Trade Weighted USD Broad Index (26 monedas, no DXY Futures).

---

## Fuentes de Datos y Confiabilidad

| Activo | Fuente | Verificación |
|---|---|---|
| BTC precio | Yahoo Finance (BTC-USD) | Cross-checked vs CoinGecko |
| SPY precio | Yahoo Finance (SPY) | — |
| Oro | Yahoo Finance (GC=F) | — |
| SPX | Yahoo Finance (^GSPC) | — |
| Macro US | FRED API (clave en config.py) | Reconciliación retroactiva |
| On-chain BTC | bitview local DB | metric_registry con conversiones verificadas |
| Noticias | Exa API via mcporter | news_pipeline valida cada noticia |

---

## Reglas de Oro

1. **Ningún dato sin identidad**: instrumento, definición, unidad, origen, fecha, validación
2. **El LLM no inventa cifras**: informe parcial verificable > informe visualmente completo con datos fabricados
3. **RSI usa Wilder smoothing**: no SMA simple
4. **SMA200 necesita 252 días** de datos (no 90)
5. **DTWEXBGS ≠ DXY**: Broad Index, no DXY Futures
6. **CPI YoY**: series precomputada CPI_YOY desde CPIAUCSL con lag 12 meses
7. **NFP**: cambio mensual (NFP_CHANGE), no nivel (PAYEMS)
8. **compute_ma() retorna float**, no lista
9. **publication_gate ANTES de publicar**: paso 7, no paso 9
10. **Conversiones on-chain con metric_registry**: NO umbrales 1e15 arbitrarios

---

## Quality Gates

### Publication Gate (≥ 95/100 para publicar)
- Exactitud de datos: CPI correcto 3.353%, NFP +29K, no 2.33%/159K
- Consistencia: **ATH >= 52W High** (permite igualdad, tolerancia 0.5%)
- Consistencia: precio dentro de rango S/R válido (rupturas son eventos legítimos)
- Trazabilidad: todo dato con source + timestamp
- Narrativas: basadas en datos, no fabricaciones
- HTML: sin errores de parseo

### Audit Incidents (12 checks)
1. CPI Snapshot — CPI YoY 3.353% realista
2. NFP Change — +29K (no nivel 159K)
3. DTWEXBGS label — "Trade Weighted USD Broad Index"
4. Fechas macro — no se muestran fechas pasadas como "próximo"
5. Brent/WTI — no disponible en FRED público (INFO)
6. SPX vs SPY — distinción clara en ticker bar
7. SMA200 BTC — $71,799 (dato disponible)
8. Soportes/Resistencias — rango coherente con precio
9. On-chain definitions — instrumentos con unidad y metodología verificadas
10. News pipeline — filtra errores API, no expone errores como noticias
11. Buy signals — validados por gate
12. ATH/52W — jerarquía coherente (ATH >= 52W High)

---

## Mantenimiento

### Re-ejecutar backfill
```bash
# Métricas de precio (SMA, RSI) — 337 días
python3 ingestion/backfill_metrics.py

# Métricas on-chain — 33,290 filas
python3 ingestion/backfill_onchain_metrics.py
```

### Regenerar dashboard
```bash
python3 rendering/export_dashboard.py
```

### Verificar datos
```bash
python3 validation/audit_incidents.py
python3 validation/publication_gate.py
```

---

## Decisiones Abiertas vs. Implementadas

| Pregunta | Estado |
|---|---|
| Backfill manual o automático | Híbrido: manual para históricos, incremental para nuevos |
| Dashboard estático o tiempo real | Actualización periódica independiente del reporte diario |
| Telegram automático | Semiautomático: requiere aprobación manual |
| publication_gate antes de archivar | ✅ Implementado (paso 7 de 9) |
| metric_registry con conversiones explícitas | ✅ Implementado (14 series verificadas) |
| compute_scenarios sin horizontes ATR | ✅ Implementado |
| Reconciliación retroactiva FRED | ✅ Implementado (60 días) |

---

*Documento generado 2026-10-08. Última actualización tras auditoría RED TEAM.*
