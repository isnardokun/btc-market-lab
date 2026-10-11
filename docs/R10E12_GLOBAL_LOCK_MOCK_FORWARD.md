# R10-E.12 — Ejecutar forward v3 ficticio bajo bloqueo global de escritor

**Objetivo:** comprobar que la primitiva `flock` de E11 funciona alrededor del **código real** de un paso forward `market_history_forward.execute` — con HTTP inyectado y una SQLite v3 totalmente artificial. Esto es un **harness offline de integración**, no un colector nuevo de producción.

## Qué cambia

1. `scripts/market_r10e12_locked_mock_forward.py`: ejecuta `run_locked_mock_forward(db_path, lock_directory, provider=..., scenario=...)` SOLO en una fixture identificada inequívocamente: directorio de TemporaryDirectory cuyo nombre empieza `r10e12-synthetic-`, archivo `synthetic.db`, marcador exacto `.r10e12-synthetic-only` y subdirectorio preexistente `locks`. Rechaza rutas fuera de esa fixture, symlinks evidentes, archivo ausente y archivos WAL/SHM en uso.
2. Adquiere **antes de abrir SQLite** `hold_writer_lock(..., namespace="btc-research-global-writer-v1")`. El bloqueo se mantiene hasta después del commit/rollback, de calcular delta de filas y ejecutar las auditorías RAW/TEMPORAL/LINEAGE sobre snapshot RO. Si `flock` está ocupado, retorna `BLOCKED_WRITER_BUSY` con 0 conexiones SQLite y 0 callbacks HTTP simulados; el error no se degrada a reintento.
3. Se pasa exclusivamente un `fake_fetch` local como argumento a `forward.execute`, nunca el `download` real por defecto. Solo BTCUSDT OI 5m Binance/Bybit; escenarios `success`, `empty`, `http429` artificial y `gap` artificial. No tiene CLI, `main()`, `--apply`, scheduler, cron, API key ni rutas productivas predeterminadas.
4. Se cotejan las 10 tablas por PK/hash de filas PRE, delta de observaciones y receipt/cursor, más integridad y FK. Estado `MOCK_LOCKED_PAGE_PASS` es exclusivo del **caso sintético** y NO autoriza descargas.

## Cómo se prueba

`tests.unit.test_market_r10e12_locked_mock_forward` contiene **21 tests**: éxito Binance/Bybit, +2 observaciones, BLOB/receipt/acquisition/run/cursor, integridad, error 429, vacío y gap, inmutabilidad de filas anteriores, ocupación por otro descriptor del proceso, ocupación por **proceso hijo independiente**, ejecución del hijo bloqueada por el padre, liberación del FD y nueva ejecución, namespace diferente que **no** garantiza exclusión, validación negativa de rutas/productivo, ausencia de HTTP real y unicidad del runner.

Todas las pruebas crean el archivo SQLite v3 desde cero con `_seed` E3 dentro de TemporaryDirectory. La contención usa procesos hijo locales SIN peticiones API, contacto con `btc_research.db`, cambios en producción ni instaladores. Solo `git fetch` para obtener el código de la PR es una operación remota autorizada a Hermes.

E11 tenía runner de **49 suites** y **183 pruebas focales** E0–E11. Al añadir E12 hay **50 suites registradas** y **204 pruebas focales esperadas (21+183)**; estas cifras **NO son PASS** hasta que Hermes las ejecute y documente RC.

## Límites que siguen abiertos

- `flock` es **cooperativo**. El wrapper E12 lo utiliza, **pero `daily.sh`, `market_context_ingest.py`, `market_history_forward.py`, `market_history_pilot.py`, ResearchBitcoin, FRED, Bitview y Yahoo NO fueron editados ni integrados con el lock**. Un writer no cooperativo todavía podría escribir sobre la misma SQLite.
- El namespace global es **solo una propuesta para todos los writers**; demostrar que otro namespace no bloquea es parte de QA. Para adoptar el lock productivamente hay que diseñar la adquisición desde todos los entrypoints y prevenir deadlocks, doble adquisición/FD heredados, y operaciones HTTP bajo transacción de escritura.
- El código no incorpora guardas en pipelines reales, no crea backups, no hace migraciones ni activa cron. Los flags de opt-in siguen sin autorizarse.
- `historical_completeness=NOT_VERIFIED` y `production_authorized=false` sin excepciones. ResearchBitcoin Tier2 externo sin entitlement verificado, Funding P1 (1.035 RAW heredados sin receipts HTTP), R10-D.2 y disponibilidad de ventana histórica siguen pendientes.

**Gobierno:** ChatGPT desarrolla/revisa/commitea/mergea. Hermes solo QA en SHA congelado con worktree nuevo limpio, publica un reporte y STOP. Propietario conserva la autorización de cualquier acceso a APIs, SQLite productiva o deployment.
