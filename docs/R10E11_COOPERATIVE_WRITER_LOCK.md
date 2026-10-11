# R10-E.11 — Exclusión mutua real ENTRE PROCESOS (solo fixtures, sin producción)

**Contexto:** la [PR #71 / R10-E.10](https://github.com/isnardokun/btc-market-lab/pull/71) cerró la lectura de evidencia histórica en una sola transacción SQLite RO, pero R10-E.0–E.10 usaban banderas o comprobaciones **simuladas** de writer exclusion. El [runbook de R10-E](R10E_ROLLOUT_RUNBOOK.md) exige exclusión de escritores antes de iniciar peticiones reales o modificar SQLite productiva.

## Implementación E11

`scripts/market_r10e11_writer_exclusion.py` ofrece `hold_writer_lock(lock_directory, namespace="market-history-v3")`. Es un **context manager no bloqueante y fail-closed con `fcntl.flock(LOCK_EX|LOCK_NB)` de Unix/Linux**:

- El caller debe elegir expresamente un directorio **existente**; no existe ruta ni SQLite por defecto, no acepta namespace fuera de la sintaxis restringida de 1–64 caracteres.
- Abre directorio con `O_DIRECTORY|O_NOFOLLOW`; crea/reutiliza un archivo `<namespace>.lock` con permisos 0600, verifica archivo regular, propietario actual y único hardlink. Rechaza symlinks directos, pipes y archivos compartidos con permisos grupo/mundo.
- Si otro proceso **cooperativo** tiene el lock, devuelve `WriterLockBusy` sin esperar ni iniciar adquisiciones; el sistema operativo libera `flock` al cerrarse el FD, incluso si el proceso es terminado.
- El contexto conserva el FD abierto hasta terminar, sin hacer HTTP, SQL ni borrar el archivo de lock. **Jamás deslinkear lockfile después de liberar:** hacerlo introduce carreras de inodos y rompe exclusión.
- La salida `COOPERATIVE_LOCK_HELD` incluye obligatoriamente `excludes_uncooperative_writers=false` y `production_authorized=false`. **No es evidencia de que cron y otros escritores hayan adoptado el mismo lock.**

**No se modifica** `daily.sh`, `market_history_forward.py`, `market_context_ingest.py`, ResearchBitcoin archiver, SQLite schema, cron, scheduler ni permisos de producción. Este cambio prepara el primitive, **pero aún no lo integra** con el pipeline de ninguna fuente.

## Cobertura de pruebas

`tests/unit/test_market_r10e11_writer_exclusion.py` contiene **18 tests** exclusivamente sobre `TemporaryDirectory` y procesos hijo locales:

- Dos descriptores en el mismo proceso y dos **procesos OS diferentes** no pueden tener el mismo namespace simultáneamente.
- Liberación normal, excepción y SIGKILL simulado: siguiente proceso puede adquirir sin borrar/recrear el lockfile.
- Diferentes namespaces pueden coexistir, lo que muestra por qué todos los escritores de **la misma SQLite** necesitarán usar el mismo namespace global.
- Permisos 0600, owner, symlink al archivo y directorio, hardlink, FIFO, directorio ausente y namespace inválido.
- AST confirma que el módulo no importa SQLite, HTTP ni subprocess (subprocess se usa **solo dentro de tests**).
- Runner central de tests pasa de **48 a 49 suites**, con presencia única de E0–E11 y sin volver al defecto de hardcode exacto en suites previas.

**Resultados esperados para QA:** 18 pruebas E11 + 165 focales E0–E10 = **183 focales**, **49 suites** registradas en el runner. Todavía no son resultados certificados de Hermes.

## Gates pendientes antes de un piloto live

1. **Integración cooperativa real aún PENDIENTE:** inventariar todos los escritores que acceden a `btc_research.db` (pipeline diario, market context, forward, backward, ResearchBitcoin, Bitview, Yahoo, FRED, RAG/archivos). Todos deben utilizar la **misma exclusión global** o quedar detenidos durante la ventana autorizada. Un test positivo de `flock` no prueba exclusión de procesos que ignoran el lock.
2. Diseñar adquisición/liberación del lock y manejo de 429/error con endpoints live, evitando llamadas HTTP bajo transacción de escritura; validar una operación por página y rollback con SQLite sintética, sin huellas productivas.
3. Confirmar backup WAL-safe de la SQLite real y manifest PK+SHA PRE/POST, exclusión de cron, checks RAW/TEMPORAL/LINEAGE/FOREIGN KEY, autorización explícita del propietario y fecha de piloto. **Nada de esto está autorizado por esta PR.**
4. ResearchBitcoin Tier2 entitlement real y cobertura de endpoints aún NO_VERIFIED; no confundir `plan_tier=2` con acceso autenticado. Funding P1 conserva 1.035 observaciones heredadas sin receipts prospectivos; reservas de R10-D.2 no se cerraron aquí.

**Régimen de QA:** ChatGPT desarrolla/commitea/revisa; Hermes SOLO verifica SHA exacto en worktree nuevo, usa únicamente archivos y procesos temporales, publica RC/logs sanitizados y STOP. Sin APIs de mercado, SQLite productiva, cron, cambios en pipelines ni eliminación de worktrees originales.
