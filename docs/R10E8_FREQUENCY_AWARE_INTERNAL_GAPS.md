# R10-E.8 — Auditoría de candidatos de huecos internos por calendario y frecuencia

**Naturaleza:** lectura local estrictamente RO sobre SQLite explícita, únicamente probada en TemporaryDirectory. No descarga, no backfill, no escrituras, no API/credenciales ni tareas programadas. Trabajo de ChatGPT; Hermes solo QA. El propietario no ha autorizado producción.

## Por qué existe

R10-E.7 informa recuentos y límites MIN/MAX locales; **dos series con los mismos MIN/MAX pueden tener diferentes huecos interiores**. E.8 analiza los timestamps realmente archivados por fuente y clasifica como **candidatos** los huecos que la cadencia interna permite detectar. La existencia de un candidato **no demuestra que una API lo provea**; la disponibilidad externa sigue en NOT_VERIFIED.

## Clasificación de huecos

| Fuente / serie | Política | Señal resultante | Restricción fundamental |
|---|---|---|---|
| ResearchBitcoin escalares d1 | Días UTC entre MIN y MAX | Número y muestra de fechas ausentes | Candidatos internos, no fallo probado del proveedor ni entitlement Tier 2 |
| Bitview indexado `idx=1d` | Días UTC entre MIN y MAX | Número y muestra de fechas ausentes | No revisar series `height` usando calendario diario |
| FRED mensual (`FRED_SERIES` `monthly`) | Meses calendario entre primero y último mes archivado | Candidatos de meses ausentes | No implica todas las revisiones ni disponibilidad histórica de vintages |
| FRED diario, semanal u otras frecuencias | Requiere calendario real de publicación por serie | `internal_gap_candidates=null` | **Nunca marcar fines de semana/festivos automáticamente como gaps** |
| Yahoo OHLC | Requiere sesiones verificadas del mercado/símbolo | `internal_gap_candidates=null` | Ni todos los instrumentos cotizan 7/7 ni `range=max` garantiza todo el histórico |

El módulo `scripts/market_r10e8_ro_internal_gap_audit.py` solo detecta huecos **internos** entre MIN y MAX de una serie almacenada. No calcula anterior a MIN, posterior a MAX, ventanas API, true recoverability o requests HTTP.

## Invariantes técnicos

- `--db` es obligatorio y no tiene path productivo por defecto; verifica que exista la SQLite. Conecta `mode=ro`, `PRAGMA query_only=ON`, `BEGIN` y `PRAGMA database_list` para un snapshot coherente; no crea WAL, cambia esquema o ejecuta SQL UPDATE.
- Tablas conocidas: `onchain_external_observations`, `daily` + `series`, `macro_fred`, `market_ohlc_history`. Si falta la tabla, devuelve `schema_state=NOT_INITIALIZED`; si existe con esquema incompatible, falla cerrado.
- Filtra `provider='researchbitcoin'`, `idx='1d'`, `provider='Yahoo Finance'` y clasifica FRED con el propio `FRED_SERIES` versionado.
- Prefiere `observations` (filas) distinto a `distinct_utc_dates` (días únicos); evita ocultar duplicados.
- Acierta el detalle mes-a-mes para series FRED mensuales; no asume que mensual equivale a 30 días ni inventa observaciones de calendarios bursátiles.
- Presupuesto máximo `MAX_OBSERVATIONS=1_000_000` registros por auditoría, fail closed si se supera (no informa resultados parciales). Muestra limitada a 40 fechas/meses ausentes por serie con indicador `sample_truncated`.
- `historical_completeness=NOT_VERIFIED`, `real_provider_availability_verified=false`, `execution_authorized=false`, `http_requests=0`, `sqlite_writes=0`, `db_location=REDACTED` siempre.

## Pruebas y recorrido

La suite `tests.unit.test_market_r10e8_ro_internal_gap_audit` comprende **17 pruebas sintéticas** de frecuencia diaria/mensual, series Bitview indexadas por altura, no fabricación de días Yahoo/FRED, errores de fechas/timestamp/tables, presupuesto, SQL solo lectura y rechazo de --apply. El registro `tests/run_all.py` crece de 45 a **46 suites**. Regresión E0–E7: 107 pruebas focales anteriores. Para esta PR se esperan **124 focales y 46 suites** solamente si Hermes lo demuestra mediante logs/RC.

## Restricciones previas a la ingestión histórica real

Los candidatos de huecos deben cruzarse posteriormente con disponibilidad externa por proveedor y autorización de gasto de llamadas, mutex de writers, provenance RAW/receipt, cuotas y backup WAL-safe. En ResearchBitcoin verificar Tier 2 de forma independiente y sin exponer token. En FRED los vintages necesitan semántica as-of y auditoría. En Yahoo es indispensable calendario por símbolo/sesión. Funding legado conserva sus 1.035 RAW sin receipts por petición retrospectivos; no fabricarlos.

El código en esta PR **no añade una tarea a daily.sh, no mueve cursores y no autoriza ni ejecuta peticiones**. QA exclusiva de Hermes sobre SHA congelado en nuevo worktree; ninguna operación sobre SQLite productiva.
