# Arquitectura histórica — BTC Market Lab / Tier 2

## Contrato de conservación
**Prioridad:** conservar el máximo histórico REAL permitido por los contratos
de Bitview, ResearchBitcoin Tier 2, FRED y Yahoo Finance, de forma auditable,
sin sustitución silenciosa de series y sin destruir los ~9 millones de
observaciones `daily` ya archivadas. La base local de Hermes es la verdad
operativa y no se incluye en GitHub.

**Separación obligatoria:**
1. `bronze`: mediciones originales + fuente, unidad, timestamp UTC,
   número de versión y SHA256; las revisiones no se sobrescriben.
2. `silver`: series reconciliadas, calidad/cobertura y conversiones
   documentadas por indicador.
3. `gold`: `daily_metrics`, informes aprobados y tablero.
No mezclar metodologías Bitview y ResearchBitcoin por coincidencia de slug.

## Tablas existentes preservadas
- `series` + `daily`: Bitview (~2.807 series, ~8.977.014 filas
  según inventario documental de 2026-10-09; revalidar en SQLite real).
- `price_btc`: precios diarios BTC utilizados por el gate.
- `macro_fred`: valor FRED por serie/fecha (no es una base ALFRED de
  vintages completos).
- `onchain_external_observations`: último valor RBN por métrica y día,
  con `unit`, endpoint y `fetched_at_utc`; consumido por el renderer/gate.
- `daily_metrics`, `reports`, `ingest_instruments`: salidas derivadas
  y metadatos de instrumentos; no modificar durante backfill.
- `historical_fetch_windows` Bitview: ledger de lotes completados.
- `market_ohlc_history`: OHLCV Yahoo por símbolo/fecha (no confundir con
  cierre BTC original ni con ajustes por dividendos).

## Migración SQL aditiva — storage/archive_schema.py
Nuevas tablas normalizadas sin DROP:
- `archive_sources`: proveedor, tier, frecuencia y última actualización.
- `archive_series`: identidad compuesta (proveedor, métrica), unidad,
  escala cruda, endpoint y límites accesibles.
- `archive_fetch_runs`: cada petición, ventana UTC, status/filas; índices
  para seguimiento e incidentes.
- `archive_observation_revisions`: valor **original** numérico, unidad,
  timestamp de observación, fecha de captura y hash de identidad.
  Permite versiones sucesivas sin sobrescribir observaciones previas.
- `archive_coverage`: número/primera/última observación, último inventario
  y bandera de integridad. El estado inicial siempre es `partial`.

La migración solo se ejecuta cuando se solicita `--apply` en RBN.
El reporte y el gate siguen leyendo la tabla original, que conserva
todos los controles de fuente y consistencia ya probados.

## APIs y modos de ingesta
| Fuente | Ingesta histórica | Modo operativo | Restricciones |
|---|---|---|---|
| ResearchBitcoin Tier 2 | `ingestion/researchbitcoin_archive.py --history --tier 2 --from 2009-01-01` | `--incremental`, no cron por defecto | hasta 14 días por petición en **nuestro cliente**; hasta `--max-requests` |
| Bitview | `ingestion/bitview_history.py --all-daily --from 2009-01-01` | `ingestion/ingest.py` actual | segmentos d1 de 180 días; altura de bloque requiere diseño adicional |
| FRED | `ingestion/ingest_fred.py --history` | `ingestion/ingest_fred.py` | valores disponibles; no inventar vintages antiguos |
| Yahoo | `ingestion/yahoo_history.py` | ingesta BTC + datos del reporte existentes | range=max; disponibilidad varía según instrumento |

Sin `--apply`, las herramientas históricas dan **plan de ejecución,
no llaman a APIs y no escriben SQLite**. Se debe obtener una copia
de seguridad `sqlite3 ... .backup` y revisar el plan antes de permitir
las descargas. Ejecutar en lotes acotados y reanudar. Ninguna descarga
del histórico se considera automáticamente completa: hay que comprobar
cobertura, huecos, fecha inicial real, límites del proveedor y cambios
metodológicos. Los errores detienen nuevas peticiones dentro del lote.

**Tier 2:** 40 millones de data points semanales según la documentación
del proveedor revisada al diseñar el flujo. La cuota no significa que
la serie exista desde el génesis o que todos los datos estén disponibles.
El `--from` explícito evita inventar que no hay límite histórico y
permite auditar la fecha solicitada.

### Ejecución inicial segura (solo inspección)
```bash
cd /home/ignotus/btc-research
python3 scripts/history_coverage.py --output reports/history-coverage.json
python3 ingestion/researchbitcoin_archive.py --history --tier 2 --from 2009-01-01 --max-requests 8
python3 ingestion/bitview_history.py --all-daily --from 2009-01-01 --max-requests 8
python3 ingestion/ingest_fred.py --history
python3 ingestion/yahoo_history.py --limit 4
```
Luego de revisar backup/cuotas/cobertura, ejecutar cada comando
aprobado con `--apply`, empezando por lotes pequeños. Estos comandos
NO activan nuevos cron, Telegram ni publicación externa.

### Integridad que todavía requiere ampliación
- ALFRED/FRED: conservar *vintages* por fecha de revisión si la API y
  el plan permiten extraerlas; `macro_fred` solo conserva un valor
  por `series_id,date` y la reconciliación puede actualizarlo.
- Bitview: series `height`, índices no diarios y matriciales necesitan
  adaptadores con índices/PK propios; nunca asignar hora aproximada
  como si fuera la fecha oficial de un bloque.
- News/RSS/Exa: preservar metadatos con origen, UTC, licencia y deduplicación;
  el texto completo depende de derechos/condiciones del proveedor.
- La tabla `archive_observation_revisions` guarda **cada revisión
  observable a partir de ahora**; no se pueden reconstruir cambios
  de valor ya perdidos en épocas anteriores.
- `archive_coverage` debe pasar a `complete_verified` solo tras
  auditoría explícita de límites/huecos, nunca por tener filas.
- Política de retención/compresión y copias offsite privadas antes de
  masificar el histórico.
