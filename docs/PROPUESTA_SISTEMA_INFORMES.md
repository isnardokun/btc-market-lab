# Sistema de Informes Profesional — Propuesta v2
**07 Oct 2026 | FRED API verificada con key funcional**

---

## Resumen de Decisiones

| Decision | Respuesta |
|---|---|
| Series FRED | Las mas relevantes para mercado (confirmadas via API live) |
| Predicciones | Automaticas |
| Dashboard | Interfaz grafica |
| Notion | No por ahora |
| Frecuencia | Daily + Weekly |

---

## 1. Diagnostico del Estado Actual

| Componente | Estado |
|---|---|
| `btc_research.db` | 674 MB — 3 tablas: `series`, `daily` (8.9M filas), `price_btc` (4,404 filas) |
| Datos on-chain | bitview local — 1,525 series ingestadas |
| Precio BTC | Yahoo Finance `price_btc` — 2014-presente |
| Reportes | Archivos HTML sueltos — sin version, sin evaluacion |
| Macro/Fed | Sin fuente estructurada en BD |
| Predicciones | No registradas ni evaluadas |

---

## 2. Objetivo

> Generar informes diarios y semanales profesionales (BTC + Renta Variable + Commodities) con historial versionado en BD, predicciones automaticas rastreables con evaluacion de precision, datos macro de la Fed ingestionados via FRED API, y una interfaz grafica para consultar todo el sistema.

**Principio:** cada informe es una _version_. Cada sesgo/pronostico es una _prediccion_ con fecha de validacion y score. Cada prediccion expira y se evalua automaticamente.

---

## 3. Arquitectura de Base de Datos

### 3.1 Esquemas SQL

```sql
-- ══════════════════════════════════════════════
-- REPORTES VERSIONADOS
-- ══════════════════════════════════════════════
CREATE TABLE reports (
    id           INTEGER PRIMARY KEY,
    report_date  DATE    NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    asset        TEXT    NOT NULL,   -- 'BTC' / 'SPY' / 'XAU' / 'ALL'
    section      TEXT    NOT NULL,  -- 'daily' / 'weekly'
    filename     TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    snapshot     TEXT,   -- JSON: price, rsi, macd, atr, mvrv, bias,
                         -- scenario_bull/base/bear, levels{sup,res},
                         -- news_count, macro_snapshot, predictions_summary
    UNIQUE(report_date, asset, section, version)
);

-- ══════════════════════════════════════════════
-- PREDICCIONES AUTOMATICAS
-- ══════════════════════════════════════════════
CREATE TABLE predictions (
    id           INTEGER PRIMARY KEY,
    report_id    INTEGER REFERENCES reports(id),
    asset        TEXT    NOT NULL,
    pred_type    TEXT    NOT NULL,  -- 'price_target' / 'support_test' /
                                     -- 'resistance_test' / 'breakout' /
                                     -- 'bias_shift' / 'catalyst'
    direction    TEXT    NOT NULL,   -- 'bullish' / 'bearish' / 'neutral'
    price_from  REAL,               -- precio al momento de la prediccion
    price_target REAL,               -- objetivo (NULL si no aplica)
    level_tag   TEXT,               -- 'soporte_81k', 'resistencia_92k'
    expires_at  DATE    NOT NULL,   -- fecha de validacion
    status      TEXT    DEFAULT 'active',  -- 'active'/'achieved'/
                                           -- 'missed'/'partial'/'expired'
    evaluated_at DATE,
    accuracy    TEXT,               -- 'correct' / 'partial' / 'wrong'
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    note        TEXT
);

-- ══════════════════════════════════════════════
-- NIVELES DE MERCADO (S/R identificados)
-- ══════════════════════════════════════════════
CREATE TABLE market_levels (
    id           INTEGER PRIMARY KEY,
    asset        TEXT    NOT NULL,
    level_date   DATE    NOT NULL,
    level_type   TEXT    NOT NULL,   -- 'support' / 'resistance' / 'pivot'
    price        REAL    NOT NULL,
    source       TEXT    NOT NULL,   -- 'daily_report' / 'fractal' / 'weekly'
    report_id    INTEGER REFERENCES reports(id),
    is_active    INTEGER DEFAULT 1,  -- 1=vigente, 0=superado
    broken_at    DATE,
    broken_price REAL,
    UNIQUE(asset, level_date, level_type, price)
);

-- ══════════════════════════════════════════════
-- MACRO FRED (datos de la Fed ingestionados)
-- ══════════════════════════════════════════════
CREATE TABLE macro_fred (
    id           INTEGER PRIMARY KEY,
    series_id    TEXT    NOT NULL,
    date         DATE    NOT NULL,
    value        REAL,
    fetched_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(series_id, date)
);
CREATE INDEX idx_macro_series_date ON macro_fred(series_id, date);

-- ══════════════════════════════════════════════
-- SCORING DE PREDICCIONES
-- ══════════════════════════════════════════════
CREATE TABLE prediction_scores (
    id              INTEGER PRIMARY KEY,
    prediction_id   INTEGER REFERENCES predictions(id),
    report_id       INTEGER REFERENCES reports(id),
    price_achieved  REAL,
    reached_at      DATE,
    within_window   INTEGER,
    price_accuracy  REAL,   -- 0-100
    time_accuracy   REAL,   -- 0-100
    overall_score   REAL,   -- price*0.7 + time*0.3
    scored_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 4. Series FRED Confirmadas — 11 series

Verificadas via API live el 07-oct-2026. Solo series con descarga libre.

| Series | Nombre | Frecuencia | Relevancia |
|---|---|---|---|
| `DGS10` | Yield Tesoro 10Y | Diaria | Correlacion BTC inversa — driver macro #1 |
| `DGS2` | Yield Tesoro 2Y | Diaria | Expectativas Fed corto plazo |
| `DTWEXBGS` | Indice DXY Broad | Diaria | Dolar — impacto directo en BTC, oro, commodities |
| `VIX` | VIX CBOE | Diaria | Fear/greed renta variable — correlacion crypto |
| `CPALTT01USM661S` | CPI YoY EE.UU. | Mensual | Inflacion — determinante politica Fed |
| `PPIACO` | PPI Commodities | Mensual | Inflacion intermedia |
| `UNRATE` | Tasa Desempleo | Mensual | Mercado laboral — impacto en tasas y riesgo |
| `PAYEMS` | Nonfarm Payrolls | Mensual | Volatilidad en dias de publicacion |
| `CBBTCUSD` | Bitcoin (FRED) | Diaria | Cross-reference con Yahoo |
| `NFCI` | Chicago Fed NFCI | Diaria | Stress financiero — anticipa riesgo-off |
| `RETAIL` | Retail Sales | Mensual | Consumo — salud economica |

**Datos confirmados del test API:**
```
DGS10:      2026-10-06  5.27%
DGS2:       2026-10-06  4.79%
DTWEXBGS:   2026-10-02  121.3848
VIX:        disponible en FRED (series VIX)
CBBTCUSD:   2026-10-07  83239.49  ← BTC cross-reference
```

---

## 5. Predicciones Automaticas — Logica

### 5.1 Tipos generados

| Tipo | Condicion | Plazo default |
|---|---|---|
| `price_target_bull` | RSI < 40 + MVRV < 2 + precio sobre SMA200 | 14 dias |
| `price_target_bear` | RSI > 70 + MVRV > 3.5 + precio bajo SMA200 | 14 dias |
| `support_test` | Precio < 5% encima de soporte activo | 1-5 dias |
| `resistance_test` | Precio < 5% debajo de resistencia activa | 1-7 dias |
| `breakout` | ATR < 2% precio + volumen decaying | 7-21 dias |
| `bias_shift` | 3+ indicadores cambian direccion vs semana anterior | 14-30 dias |

### 5.2 Evaluacion automatica

```python
def evaluate_predictions(conn, asset, date):
    for pred in get_active_predictions(conn, asset):
        row = conn.execute("""
            SELECT MIN(price), MAX(price) FROM price_btc
            WHERE date BETWEEN ? AND ?
        """, [pred.created_at.date(), pred.expires_at]).fetchone()
        lo, hi = row[0], row[1]
        if pred.direction == "bullish" and hi >= pred.price_target:
            mark_achieved(pred, hi)
        elif pred.direction == "bearish" and lo <= pred.price_target:
            mark_achieved(pred, lo)
        elif pred.expires_at <= date:
            mark_missed(pred)
```

### 5.3 Scoring

```
price_accuracy = max(0, 100 - abs(target - achieved) / target * 100)
time_accuracy  = max(0, 100 - abs(days_target - days_actual) / days_target * 100)
overall        = price_accuracy * 0.7 + time_accuracy * 0.3

>= 80 → "correct"  (verde)
50-79 → "partial"  (amarillo)
< 50  → "wrong"    (rojo)
```

---

## 6. Interfaz Grafica — Spec

**Tecnologia:** HTML unico + CSS inline + JS vanilla. Sin frameworks. Datos via `dashboard_latest.json`.

### 6.1 Dashboard principal — `dashboard.html`

```
┌─────────────────────────────────────────────────┐
│  HEADER: titulo + fecha + ultimo update          │
├─────────────────────────────────────────────────┤
│  TICKER STRIP                                    │
│  BTC / SPY / XAU / DXY / VIX / 10Y             │
│  (precio + cambio + mini sparkline 7d)          │
├─────────────────────────────────────────────────┤
│  MACRO SNAPSHOT  (semafaro verde/amarillo/rojo)  │
│  10Y Yield | DXY | VIX | CPI | Desempleo | NFP │
├─────────────────────────────────────────────────┤
│  PREDICCIONES ACTIVAS                            │
│  Cards: activo | tipo | direccion | target       │
│  expires | dias restantes | border color         │
├─────────────────────────────────────────────────┤
│  HIT RATE 30 DIAS (barras por activo)           │
│  BTC ████████░░ 78%                             │
│  SPY ██████░░░░ 62%                             │
│  XAU █████░░░░░ 55%                             │
├─────────────────────────────────────────────────┤
│  NIVELES VIGENTES                                │
│  Tabla: activo | tipo | precio | dias | estado   │
├─────────────────────────────────────────────────┤
│  HISTORIAL PREDICCIONES                          │
│  Filtros: activo / tipo / estado / score        │
│  Tabla: fecha | activo | tipo | target | result  │
│  | score                                          │
├─────────────────────────────────────────────────┤
│  REPORTES RECIENTES                              │
│  Cards: fecha | activo | version | enlace HTML   │
└─────────────────────────────────────────────────┘
```

### 6.2 Detalle de reporte — `report_detail.html?id=X`

- HTML completo del informe versionado
- Metadatos: fecha, version, activo, sesgo, predicciones hechas
- Evolucion vs informe anterior (si existe)
- Tabla de predicciones resueltas del periodo

---

## 7. Workflow del Sistema

```
CRON DIARIO (05:00 UTC)
═══════════════════════════════════════════════
  ingest_price.py         → price_btc  (Yahoo)
  ingest.py               → daily      (bitview)
  ingest_fred.py         → macro_fred (FRED API)     ← NUEVO
  evaluate_predictions.py → predictions + scores       ← NUEVO
  charts.py               → PNG/SVG assets
  daily_report.py         → HTML
  archive_report.py        → versiona en BD + limpia   ← NUEVO

CRON SEMANAL (domingo 06:00 UTC)
═══════════════════════════════════════════════
  weekly_report.py        → HTML semanal
  archive_report.py        → versiona en BD
```

---

## 8. Estructura de Archivos

```
btc-research/
├── db/
│   └── btc_research.db          (674 MB — +5 tablas)
│
├── scripts/
│   ├── ingest_price.py            (existente)
│   ├── ingest.py                 (existente)
│   ├── ingest_fred.py            [NUEVO]
│   ├── evaluate_predictions.py    [NUEVO]
│   ├── charts.py                 (existente)
│   ├── daily_report.py           (refactorizado)
│   ├── weekly_report.py          [NUEVO]
│   ├── archive_report.py         [NUEVO]
│   ├── export_dashboard.py       [NUEVO]
│   ├── config.py                 [NUEVO — API keys]
│   └── daily.sh                  (extendido)
│
├── reports/                        (HTML output)
│   └── daily_report_YYYY-MM-DD.html
│
├── dashboards/
│   ├── dashboard.html            [NUEVO]
│   └── dashboard_latest.json      [NUEVO — regenerado diario]
│
└── docs/
    └── PROPUESTA_SISTEMA_INFORMES.md
```

---

## 9. Fases de Implementacion

### Fase 1 — Foundation | 1-2 dias
- [ ] Crear 5 tablas SQL en `btc_research.db`
- [ ] `config.py` con FRED API key centralizada
- [ ] `ingest_fred.py` — descarga e inserta 11 series FRED
- [ ] Integrar en `daily.sh`

### Fase 2 — Report Versioning | 1 dia
- [ ] `archive_report.py` — guarda version en `reports` + snapshot JSON
- [ ] `export_dashboard.py` — genera `dashboard_latest.json`
- [ ] Dashboard HTML basico con datos estaticos

### Fase 3 — Predictions Engine | 1-2 dias
- [ ] `daily_report.py` genera predicciones automaticas
- [ ] Guardar en `predictions`
- [ ] `evaluate_predictions.py` — evaluacion diaria, status, score
- [ ] Seccion predicciones en HTML

### Fase 4 — Weekly Reports | 1 dia
- [ ] `weekly_report.py` — analisis semanal expandido
- [ ] Resumen de predicciones de la semana
- [ ] Proyeccion semana siguiente

### Fase 5 — Dashboard UI | 1-2 dias
- [ ] Dashboard completo con las 6 secciones especificadas
- [ ] Filtros interactivos en historial
- [ ] Ticker strip con sparklines

### Fase 6 — Evolution & Polish | 1 dia
- [ ] Seccion "Evolucion" en informes: comparacion vs anterior
- [ ] Hit rate dinamico 30 dias
- [ ] Estilo visual: paleta sereno

---

## 10. Estimacion: ~6-9 dias

| Fase | Dias |
|---|---|
| 1. Foundation | 1-2 |
| 2. Report Versioning | 1 |
| 3. Predictions Engine | 1-2 |
| 4. Weekly Reports | 1 |
| 5. Dashboard UI | 1-2 |
| 6. Evolution & Polish | 1 |

---

## 11. Pendiente

Necesito tu confirmacion antes de empezar:

1. **FRED series** — ¿confirmadas las 11 series o agregas alguna? (NFCI y RETAIL son nuevas vs propuesta v1)
2. **Dashboard** — la estructura de 6 secciones, ¿te parece completa?
3. **Predicciones** — el plazo default de 14 dias para targets de precio, ¿te parece bien?
4. **Weekly** — el reporte semanal, ¿que secciones extras debe tener vs el daily?
5. **¿Empezamos por Fase 1?**

Confirmame y arrancamos.
