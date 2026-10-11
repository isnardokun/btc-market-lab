# R10-E.9 — Conciliación de evidencia E7/E8 y cola de revisión histórica

**Estado:** código offline sin acciones productivas. Desarrollado por ChatGPT; Hermes solo valida SHA congelado. Sin API real, SQLite productiva, --apply, scheduler o migración.

## Decisión arquitectónica

E7 registra límites de lo ya almacenado; E8 clasifica huecos internos de esas mismas familias de series. **Ambas herramientas abren sus propias transacciones RO independientes.** Sus datos pueden cambiar entre ejecuciones, aunque cada lector sea internamente coherente. Por eso E9 **NO afirma que compartan un snapshot atómico**. Solo concilia los agregados: registros por serie, MIN/MAX y presencia en cada reporte.

El nuevo módulo `scripts/market_r10e9_evidence_review_queue.py` es una función **pura** sin CLI, DB, HTTP, rutas, credenciales ni scheduler:

`reconcile_review_queue(manifest_e7, gap_audit_e8, max_items=40)`

### Política de entrada y bloqueo

- E7 debe ser `ARCHIVED_RANGES_READ_ONLY` y E8 `INTERNAL_GAP_CANDIDATES_READ_ONLY`, con cuatro fuentes únicas, estado `historical_completeness=NOT_VERIFIED`, `execution_authorized=false`, `http_requests=0`, `sqlite_writes=0` y ruta REDACTED.
- Ambos reportes deben indicar UTC, no afirmar acceso externo, y conservar todas sus series con métricas únicas. E7 puede contener 13 métricas RBN y 12 series FRED **catalogadas pero sin datos**; E8 solo contiene series que realmente tienen registros.
- Para cada serie con observaciones, E7 y E8 deben concordar en **número de filas y fechas archivadas primera/última**. Si hubo drift entre las dos lecturas: `ValueError` y **NO cola parcialmente autorizada**.
- El método de gap no se acepta ciegamente desde E8. E9 verifica de forma independiente: ResearchBitcoin/Bitview d1 = días UTC; FRED mensual = meses, según `FRED_SERIES`; FRED diario y Yahoo = `EXTERNAL_PUBLICATION_CALENDAR_REQUIRED`. Cualquier informe que fabrique días bursátiles o de publicación es rechazado.
- Las muestras son ordenadas, únicas, coherentes con recuento y límites MIN/MAX, y con máximo 40 slots de ejemplo por serie. Las series sin baseline no pasan a la cola como solicitudes posibles.
- La salida `ARCHIVE_GAP_REVIEW_ONLY` identifica candidatos internos de revisión, ordenados por tamaño del faltante y empate proveedor/métrica. Presupuesto máximo de **40 elementos de cola** (configurable dentro de 1–40), truncado explícitamente; no genera URL, `startTime`, `endTime`, método HTTP ni ningún objeto ejecutable.

### Qué significa un resultado

`REVIEW_CANDIDATES_PRESENT` = hay huecos **candidatos** en la cobertura archivada local, NO significa que existan en el proveedor ni que sean recuperables. `NO_INTERNAL_REVIEW_CANDIDATES` tampoco certifica que el histórico esté completo antes de MIN o después de MAX.

`source_reports_correlated=true` y `independent_reports_consistent_on_aggregates_only=true` indican concordancia del conteo/fechas. **`atomic_cross_report_snapshot_verified=false` siempre.** Para acreditar igualdad transaccional haría falta generar ambos reportes dentro de una sola transacción RO con conexión compartida, sin falsear esta condición a partir de timestamps similares.

Para toda salida: `request_ready=false`, `execution_authorized=false`, `http_requests=0`, `sqlite_reads=0`, `sqlite_writes=0`, `backfill_cursor_advanced=false`, `historical_completeness=NOT_VERIFIED`, `real_tier2_entitlement_verified=false`, `real_provider_availability_verified=false`.

## QA autorizada

- Nueva suite `tests.unit.test_market_r10e9_evidence_review_queue`: **22 tests**, sobre E7 y E8 ejecutados en la misma SQLite sintética temporal, pero cada uno como lectura independiente. Incluye reconciliación exitosa, mes FRED, RBN/Bitview d1, ausencia de calendario real para Yahoo/FRED, conteo discordante, MIN/MAX discordantes, falsificación de días, proveedor duplicado, límites de cola, no-mutación de reportes, ausencia de clientes HTTP/DB e invariantes del runner.
- `tests/run_all.py`: añade una única suite, registro de **47** entradas, mientras E0–E8 suman **124** pruebas focales previas. **146 pruebas focales** y **47 suites** son expectativas, a comprobar por Hermes.
- Si hay FAIL, **solo ChatGPT modifica código**. Hermes reporta exactamente RC y errores, preserva todos los worktrees originales.

## Pasos productivos fuera de alcance

No se habilitan ingestion jobs, ResearchBitcoin Tier2, backfill, FRED/ALFRED, Yahoo Trading Calendar ni nuevas tablas. No se ejecutará contra la SQLite productiva por esta orden. El siguiente gate técnico debería validar la consistencia en **una sola transacción** RO sobre SQLite de fixture, añadir provenance de los slots y solo posteriormente, con permiso específico del propietario, comprobar disponibilidad real del proveedor. Funding P1 de 1.035 RAW heredados y la reserva R10-D.2 siguen abiertos.
