# R10-E.13 — Inventario de adopción del bloqueo global de escritores

**Objetivo:** localizar, clasificar y bloquear los puntos de entrada que pueden modificar la misma SQLite y aún no están **demostrablemente** protegidos por `flock`. Este cambio se limita al **análisis estático del árbol de código**. No ejecuta scripts, no abre SQLite, no contacta APIs, no lee la DB productiva ni modifica cron. ChatGPT implementa y revisa; Hermes únicamente ejecuta QA sobre SHA fijo.

## Contexto operativo

E11 demostró exclusión entre procesos que cooperan. E12 envolvió **una ejecución de forward v3 únicamente sintética** con el namespace propuesto `btc-research-global-writer-v1`. Eso no cierra el riesgo de otros escritores: `scripts/daily.sh` invoca distintas herramientas que tienen conexiones SQLite propias; otras rutas pueden ejecutarse manualmente o como backfill.

Un lock propio en cada proceso **no sirve** si no coincide exactamente el archivo/namespace de los demás writers. Incluso un lock correcto en código no prueba que un servicio en ejecución lo adopte, que un cron legado haya sido retirado o que no haya copias antiguas del repositorio en el host.

## Qué implementa

`scripts/market_r10e13_writer_adoption_inventory.py` define un registro revisable de **24 rutas** existentes con clases `daily`, `opt_in_daily`, `manual`, `migration` o `library`. Incluye:

- Ingestas diarias: `ingestion/ingest_price.py`, `ingestion/ingest.py`, `ingestion/ingest_fred.py`, `ingestion/snapshot.py`, `validation/archive_metrics.py`, `rendering/archive_report.py`, `rendering/export_dashboard.py`.
- Historias/backfill/manuales: `ingestion/ingest_all.py`, `bitview_history.py`, `yahoo_history.py`, `researchbitcoin_archive.py`, `researchbitcoin_v2.py`, `backfill_metrics.py`, `backfill_onchain_metrics.py`, y `scripts/market_history_forward.py`, `market_history_pilot.py`, `market_history_batch.py`.
- Market Context, bibliotecas y mantenimiento: `scripts/market_context_ingest.py`, `market_context_migrate.py`, `archive_db_migrate.py`, `ingestion/market_context_sources.py`, `ingestion/news_archive.py`, `storage/repositories.py` y `storage/archive_schema.py`.

El módulo lee `scripts/daily.sh` como texto para localizar scripts invocados, busca de manera heurística módulos con `sqlite3.connect` o literales SQL de escritura en `ingestion/`, `scripts/`, `storage/`, `validation/`, `rendering/`, `analysis/`, e identifica rutas no clasificadas para revisión. Los harnesses `scripts/market_r10e*.py` aprobados como sintéticos se excluyen **solo de la detección automática**; los escritores reales permanecen en el registro.

Mediante AST diferencia cadenas de documentación que mencionan `hold_writer_lock`, llamados con namespace incorrecto y referencias explícitas a `hold_writer_lock(..., namespace="btc-research-global-writer-v1")`. **Incluso esa referencia válida produce `STATIC_GLOBAL_LOCK_REFERENCE_UNVERIFIED` y NO demuestra cobertura de todos los callsites.** Cualquier fuente ausente, symlink o Python inválido hace fallar la inspección.

## Política fail-closed

La salida fija:

- `decision=BLOCKED_UNVERIFIED_GLOBAL_WRITER_ADOPTION`
- `all_runtime_writer_exclusion_verified=false`
- `operational_writer_discovery_complete=false`
- `deployment_authorized=false`, `real_api_authorized=false`
- `sqlite_database_opened=false`, `sqlite_writes=0`, `http_requests=0`
- `historical_completeness=NOT_VERIFIED`

La lista de escritores **no certifica exhaustividad**. AST solo detecta patrones visibles; importaciones indirectas, SQL dinámico, subprocess, otros repositorios y los procesos reales del servidor exigen inspección posterior autorizada. Los reportes E13 no incluyen un permiso de activar cron o descargar históricos.

## Pruebas y resultados esperados

`tests/unit/test_market_r10e13_writer_adoption_inventory.py` contiene **18 pruebas offline**. Comprueba el registro de 24 rutas, relación con `daily.sh`, detección de escritores nuevos desconocidos, rechazo de faltantes/symlinks/errores de sintaxis, falso positivo de docstrings, namespace global versus namespace incorrecto, no concesión de permiso aun con referencia de lock, sólo lectura de código, ausencia de DB y unicidad del runner.

El runner crece de 50 a **51 suites**, y los tests focales de E0–E13 llegarían a **222 = 204 anteriores + 18 nuevos**, **solo si Hermes ejecuta y confirma los resultados**. No asumir PASS por esta documentación.

## Gates para empezar realmente la recuperación histórica

1. Revisar y completar el inventario: cualquier escritor nuevo, módulo con SQL dinámico o comando del daily sin clasificación debe investigarse sin borrar evidencias.
2. Adoptar un **namespace y ruta de lock global únicos**, en todos los procesos que puedan escribir `btc_research.db` o bloquear su ejecución; verificar que ningún cron/servicio no cooperativo permanezca activo. Debe hacerse mediante PRs y tests adicionales, no mediante flags a ciegas.
3. Probar integración multiproceso/scheduler con una copia sintética y separar HTTP remoto de transacciones SQL de escritura. Verificar receipts, cursores, filtros de fuentes, RAW/TEMPORAL/LINEAGE, backups y rollback.
4. Solo con una **autorización productiva expresa** del propietario: inspección real de procesos/configuración, backup WAL-safe, piloto controlado de una solicitud real y conciliación posterior. No se concede ese permiso por QA offline.

**Fuera de alcance:** ResearchBitcoin Tier 2 real no comprobado; cobertura histórica externa `NOT_VERIFIED`; Funding P1 heredado (1.035 registros RAW sin receipts por solicitud) no admite fabricar provenance retrospectiva; reserva R10-D.2 abierta. No se modifican `daily.sh`, colectores, SQLite productiva, cron, respaldos, migraciones ni credenciales.
