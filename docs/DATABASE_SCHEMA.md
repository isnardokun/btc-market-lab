# BD Schema — btc_research.db

## Tablas

### `price_btc` (4,406 filas)
| Columna | Tipo | Descripción |
|---|---|---|
| `id` | INTEGER | PK |
| `ts` | INTEGER | Unix timestamp (UTC) |
| `price` | REAL | Precio BTC-USD (Yahoo Finance, adjusted close) |

**Rango:** Sep 2014 → 2026-10-08. Frecuencia: diario (cierre NYSE).

---

### `daily` (8,977,014 filas)
| Columna | Tipo | Descripción |
|---|---|---|
| `id` | INTEGER | PK |
| `series_id` | INTEGER | FK → `series.id` |
| `ts` | INTEGER | Unix timestamp (UTC) |
| `block_height` | INTEGER | Altura del bloque BTC |
| `value` | REAL | Valor de la métrica (raw, sin conversión) |

**Importante:** valores raw requieren conversión según `series.dtype` (ver `metric_registry.py`).

---

### `series` (2,807 filas) — Catálogo de métricas on-chain
| Columna | Tipo | Descripción |
|---|---|---|
| `id` | INTEGER | PK — usar este ID para filtrar `daily` |
| `name` | TEXT | Nombre (ej: `mvrv`, `hash_rate`) |
| `idx` | TEXT | Índice alternativo |
| `dtype` | TEXT | Tipo de dato: `Dollars`, `StoredF64`, `StoredU64`, `StoredF32`, `StoredU32` |
| `description` | TEXT | Descripción (muchos NULL) |

**Series usadas por el sistema (12):**

| series_id | name | dtype | Conversión |
|---|---|---|---|
| 1357 | mvrv | StoredF32 | ratio — sin conversión |
| 1097 | hash_rate | StoredF64 | ÷1e18 → EH/s |
| 550 | difficulty | StoredF64 | ÷1e12 → T |
| 1330 | market_cap | Dollars | ÷1e12 → T$ (ya USD) |
| 1978 | realized_cap | Dollars | ÷1e12 → T$ (ya USD) |
| 7 | active_cap | Dollars | ÷1e12 → T$ (ya USD) |
| 2052 | sopr_1w | StoredF32 | ratio — sin conversión |
| 104 | asopr_1w | StoredF32 | ratio — sin conversión |
| 2807 | nupl | NULL | ratio — sin conversión |
| 2806 | rhodl_ratio | NULL | ratio — sin conversión |
| 2375 | utxo_count | StoredU64 | ÷1e6 → M |
| 5 | active_addrs_average_24h | StoredF32 | count — sin conversión |

---

### `macro_fred` (18,168 filas)
| Columna | Tipo | Descripción |
|---|---|---|
| `id` | INTEGER | PK |
| `series_id` | TEXT | FRED series ID (ej: `DGS10`, `CPIAUCSL`) |
| `date` | DATE | Fecha de observación |
| `value` | REAL | Valor |
| `quality_status` | TEXT | `VERIFIED` o NULL |
| `fetched_at` | TIMESTAMP | Timestamp de ingesta |

**FRED series_ids:**

| series_id | Descripción | Ultimo valor | Frecuencia |
|---|---|---|---|
| `CBBTCUSD` | BTC/USD FX rate | 81,657 | Diaria |
| `CPALTT01USM661S` | CPI All Items | 135.15 | Mensual |
| `CPIAUCSL` | CPI Urban Consumers | 334.13 | Mensual |
| `CPI_YOY` | CPI YoY % (precalculada) | 3.353% | Mensual |
| `DGS10` | Treasury 10Y % | 5.28% | Diaria |
| `DGS2` | Treasury 2Y % | 4.77% | Diaria |
| `DTWEXBGS` | Trade Weighted USD Broad | 121.38 | Diaria |
| `NFCI` | NFCI Chicago Fed | -0.494 | Semanal |
| `NFP_CHANGE` | NFP Change (K) | +29K | Mensual |
| `PAYEMS` | payrolls nivel | 159,044K | Mensual |
| `PPIACO` | PPI All Commodities | 287.93 | Mensual |
| `PPI_YOY` | PPI YoY % (precalculada) | 9.85% | Mensual |
| `RSXFS` | Retail Sales | 646,347M | Mensual |
| `UNRATE` | Unemployment % | 4.2% | Mensual |
| `VIXCLS` | VIX | 15.08 | Diaria |

---

### `daily_metrics` (32,659 filas)
| Columna | Tipo | Descripción |
|---|---|---|
| `id` | INTEGER | PK |
| `report_date` | DATE | Fecha del reporte |
| `asset` | TEXT | `BTC`, `SPY`, `GOLD`, `MACRO` |
| `metric` | TEXT | Nombre de métrica |
| `value` | REAL | Valor normalizado |
| `unit` | TEXT | Unidad legible |
| `source` | TEXT | `Yahoo`, `FRED`, `bitview`, `derived` |
| `created_at` | TIMESTAMP | Timestamp de creación |

**PK compuesto:** `(report_date, asset, metric)` — UNIQUE constraint activo.

---

### `reports` (15 filas)
| Columna | Tipo | Descripción |
|---|---|---|
| `id` | INTEGER | PK |
| `report_date` | DATE | Fecha del reporte |
| `version` | INTEGER | Número de versión |
| `asset` | TEXT | `ALL`, `BTC`, etc. |
| `section` | TEXT | Sección |
| `filename` | TEXT | Ruta del HTML archivado |
| `created_at` | TIMESTAMP | Timestamp |
| `snapshot` | TEXT | JSON del snapshot |

---

### `ingest_instruments` (33 filas) — Catálogo de instrumentos
| Columna | Tipo | Descripción |
|---|---|---|
| `metric_id` | TEXT | ID (ej: `btc_price_usd`) |
| `asset` | TEXT | `BTC`, `SPY`, `GOLD`, `MACRO`, `OIL`, `SILVER`, `SPX` |
| `instrument` | TEXT | Instrumento |
| `instrument_type` | TEXT | Tipo |
| `provider` | TEXT | `Yahoo Finance`, `bitview`, `FRED` |
| `yahoo_symbol` | TEXT | Símbolo Yahoo (ej: `BTC-USD`, `SPY`) |
| `bitview_series_id` | INTEGER | FK → `series.id` |
| `fred_series_id` | TEXT | FRED series_id |
| `description` | TEXT | Descripción |

---

## Relaciones

```
price_btc.ts → Yahoo Finance (BTC-USD daily close)
daily.series_id → series.id → metric_registry (conversión)
macro_fred.series_id → FRED API (series_id = FRED ID string)
daily_metrics(report_date, asset, metric) ← pipeline daily
ingest_instruments → sources (Yahoo/bitview/FRED)
```

## Acceso en código

```python
# Siempre vía repositories o ingestion/config
from storage.repositories import get_btc_price_data, get_btc_onchain, get_macro_fred

# Conversiones vía metric_registry
from ingestion.metric_registry import convert_metric, METRIC_REGISTRY
```

**NO** usar SQL raw directo en `analysis/` — usar `storage/repositories.py`.
