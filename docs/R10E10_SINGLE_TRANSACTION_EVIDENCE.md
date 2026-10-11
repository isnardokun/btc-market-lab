# R10-E.10 — Evidencia atómica multifuente en una sola transacción SQLite RO

**Gobierno:** ChatGPT crea/revisa/mergea código; Hermes ejecuta exclusivamente QA bajo SHA congelado; propietario mantiene control de APIs, acceso a base productiva, backfill y scheduler. PR solo OFFLINE, **no** autoriza el uso de la DB operativa.

## Problema que resuelve

E7 genera un manifiesto de rangos archivados y E8 identifica huecos internos por fuente. E9 compara ambos agregados, pero **E7 y E8 abren conexiones distintas**, por lo que igualdad de COUNT/MIN/MAX no acredita la procedencia de un único snapshot si un escritor actúa entre ambas lecturas.

**E10 corrige esa limitación en una ruta nueva, sin cambiar las interfaces E7/E8/E9 existentes.** `scripts/market_r10e10_atomic_ro_evidence.py` construye E7-compatible + E8-compatible **desde cada fila archivada leída una sola vez**, dentro de **una única conexión SQLite abierta en modo=ro, query_only=ON y transacción BEGIN**. Mientras la transacción está abierta, ejecuta el reconciliador E9 con sus validaciones de rangos, conteos, calendario y metadatos. Solo entonces marca la nueva evidencia:

- `snapshot_scope=ONE_READ_TRANSACTION`;
- `source_connection_count=1`;
- `review.atomic_cross_report_snapshot_verified=true`;
- `review.snapshot_evidence=ONE_SQLITE_READ_TRANSACTION_AND_ROW_SCAN`.

La función E9 usada independientemente **continúa devolviendo false**. E10 nunca marca entitlements externos, completitud o permisos de HTTP como verificados.

## Controles técnicos

- Función `inspect_atomic_fixture(db_path, now=..., max_observations=...)`: requiere ruta explícita de archivo existente y reloj UTC consciente. **No CLI, no --apply y sin path productivo por defecto.** Hermes únicamente la invoca en tests con SQLite de TemporaryDirectory.
- Confirma que `PRAGMA database_list` solo tiene `main`; valida columnas de las tablas conocidas contra E7 y E8, y falla cerrado cuando el esquema está incompleto.
- Usa la misma consulta por serie para calcular count, MIN/MAX y muestras de gaps; el presupuesto máximo de filas es 1.000.000, sin emitir reportes parciales si se supera.
- Preserva semántica RBN/Bitview d1, FRED mensual, FRED diario y Yahoo sesiones desconocidas; no mezcla alturas de bloque y días.
- Entrega datos archivados locales sin exponer DB path: `db_location=REDACTED`.
- No hay conexión HTTP, import de requests, credenciales, cron, migración, escritura SQLite o movimiento de cursores. FRED vintages solo se cuentan cuando hay tabla.
- RBN `plan_tier`/flag local `entitlement_verified` no se convierten en prueba de acceso Tier 2 externo.
- `historical_completeness=NOT_VERIFIED`, `real_provider_availability_verified=false`, `real_tier2_entitlement_verified=false`, `execution_authorized=false`, `request_ready=false`, `http_requests=0`, `sqlite_writes=0` siempre.

## Pruebas

Se agregan **19** pruebas a `tests/unit/test_market_r10e10_atomic_ro_evidence.py`:

- 4 fuentes, igualdad de min/max/count E7↔E8, revisión E9 y no fabricación de días Yahoo/FRED;
- 13 series RBN del catálogo, flags DB y ausencia de derechos externos;
- no baseline, tablas ausentes/parciales, datos corruptos, reloj inválido, límites de 1M;
- byte equality de la SQLite sintética, sin WAL creado por lectura habitual;
- un `sqlite3.connect` RO, no varios;
- **concurrencia WAL sintética**: un escritor independiente añade un punto durante la transacción lectora, pero el análisis debe conservar los agregados del snapshot anterior; una segunda llamada observa el nuevo dato;
- runner AST extensible y unicidad de E0–E10.

Se añade una suite al runner: **48** entradas registradas. E0–E9 tenían **146** pruebas focales, por lo que E10 propone **165** focales totales, todavía NO ejecutadas ni validadas por Hermes.

## Límites: lo que aún NO está hecho

Este cambio no prueba ventanas históricas de APIs, cuotas, costos, ResearchBitcoin Tier 2 real, historia total recuperable, origen de revisiones FRED ni calendario bursátil Yahoo. No habilita descarga automática, no adquiere mutex de producción ni hace backup WAL-safe. Se necesita una autorización separada del propietario antes de cualquier piloto live o uso sobre SQLite productiva.

Funding P1 (1.035 observaciones heredadas sin receipts por petición) y reserva R10-D.2 siguen sin atribución retrospectiva. **No fabricar receipts históricos.**

**Proceso:** ChatGPT publica PR DRAFT → Hermes prueba solamente SHA indicado en comentario de QA sobre worktree nuevo → reporta PASS/FAIL/BLOCKED y STOP → ChatGPT evalúa defectos, merge y fases siguientes.
