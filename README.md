# Mercados Daily Pro

> Generación automática de reportes financieros diarios: BTC + S&P 500 + Oro. Datos on-chain, macro y noticias — todo verificable, nada inventado.

## Quick Start

```bash
# Ejecutar pipeline completo
bash scripts/daily.sh

# Verificar calidad
python3 validation/publication_gate.py
python3 validation/audit_incidents.py
```

## Estructura

```
btc-research/
├── docs/SYSTEM.md          # Documentación completa
├── db/btc_research.db     # SQLite — 4 tablas de datos
├── ingestion/              # APIs → BD
├── validation/             # Quality gates
├── quant_engine/           # Cálculos puros (RSI, MA, ATR...)
├── analysis/               # daily_report.py (~1250 líneas)
├── rendering/              # Charts, dashboard, archive
├── reports/                 # Reportes HTML publicados
└── dashboards/             # Dashboard HTML
```

## Pipeline (11 pasos → 06:00 UTC)

```
ingest_price → ingest → ingest_fred → charts → daily_report
→ archive_metrics → publication_gate → [archive_report → export_dashboard → snapshot]
```

## Datos en BD

| Tabla | Contenido | Desde |
|---|---|---|
| `price_btc` | BTC-USD OHLCV Yahoo | Sep 2014 |
| `daily` | On-chain bitview | Sep 2014 |
| `macro_fred` | 14 series macro FRED | Sep 2014 |
| `daily_metrics` | 74 métricas diarias | 337 días |

## Quality Gates

- **Publication gate**: 99/100 → auto-publicado (umbral: 95)
- **Audit incidents**: 10/12 PASS mínimo

## Reglas de oro

1. Ningún dato sin identidad: instrumento + unidad + origen + fecha + validación
2. El LLM no inventa cifras
3. RSI = Wilder smoothing (no SMA simple)
4. SMA200 = 252 días de datos
5. DTWEXBGS ≠ DXY (Broad Index)

## Documentación

→ `docs/SYSTEM.md` — arquitectura completa, tablas, fuentes, mantenimiento
