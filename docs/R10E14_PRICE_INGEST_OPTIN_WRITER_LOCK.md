# R10-E.14 — Primera adopción OPT-IN del bloqueo global: ingesta Yahoo/BTC

## Alcance del cambio

El nuevo PR modifica **el código del entrypoint real** `ingestion/ingest_price.py`, pero no sus procesos activos: `scripts/daily.sh`, cron, scheduler, fuentes de datos, rutas predeterminadas y variables de entorno de producción permanecen intactos.

El comando previamente usado por el cron, `python3 ingestion/ingest_price.py`, mantiene el comportamiento anterior, **sin lock global**, y por tanto **NO** debe considerarse protegido. El único modo nuevo es **opt-in explícito** `--global-lock-dir /ruta/existente`, no utilizado por `daily.sh`. No ejecutar este nuevo modo contra DB real hasta autorización separada del propietario.

## Implementación

1. Se preserva el cuerpo anterior de ingestión en `_ingest_unlocked()` (consultar BTC-USD 1d Yahoo, ignorar día UTC abierto, depurar cierres parciales/futuros antiguos, y escribir `price_btc`).
2. `ingest(lock_directory=None)` conserva compatibilidad de llamadas anteriores. Solo si se suministra directorio `lock_directory`, abre el contexto `hold_writer_lock` E11 con namespace literal global `btc-research-global-writer-v1`. **Obtiene el lock antes de HTTP y SQL** y lo conserva hasta que se cierra la transacción SQLite.
3. El argumento CLI `--global-lock-dir` es opt-in, sin default productivo; si otro proceso posee el lock, `main` termina con **RC=3**, `BLOCKED_WRITER_BUSY` y sin llamar a Yahoo ni abrir SQLite.
4. La librería E11 valida permisos privados, symlinks, hardlinks y propietario del archivo de lock. El archivo permanece tras liberar el FD para evitar carreras de inodos.
5. No hay una bandera `AUTO_ENABLE`, cambios en crontab, despliegue, migración o conexión de la ejecución del cron con el lock global.

## QA sintético

`tests/unit/test_market_r10e14_optin_price_lock.py` contiene **19 tests** que usan la ingesta existente con `DB_PATH` parcheado a una SQLite recién creada en TemporaryDirectory, y `urllib.request.urlopen` sustituido por un payload Yahoo JSON sintético en `BytesIO`. Prueban:

- Escritura exclusiva de **cierres UTC completados**, depuración de la barra en progreso, idempotencia de dos actualizaciones en la SQLite ficticia, permisos del lock 0600;
- segundo actor en el mismo proceso o **proceso hijo independiente** bloqueado antes de HTTP/SQLite, liberación normal y por crash SIGKILL, lock distinto incapaz de proteger la misma base;
- CLI `--global-lock-dir` ocupado devuelve RC 3; ruta ausente/permisos inseguros bloquean; fallo de HTTP artificial libera lock;
- camino predeterminado sin nuevo flag se preserva para `daily.sh`; sin invocación real de Yahoo, SQLite productiva ni cron;
- test runner con E14 una sola vez, ahora **52 suites**.

**Estimación pendiente de QA:** 19 focales E14 + 222 E0–E13 = **241 focales**; runner **52 suites**, cifras solo esperadas hasta que Hermes las ejecute. NO reportar PASS a priori.

## Límite de adopción

**R10-E.14 NO declara que el sistema esté listo para producción.** Agrega una opción protegida a **un** entrypoint; `daily.sh` todavía lo invoca sin la opción. Los otros 23 contratos E13, escritores indirectos y cron/host quedan pendientes. Un `flock` cooperativo jamás bloquea escritores que no lo solicitan. El uso correcto exige además que los procesos compartan exactamente el MISMO archivo de lock (misma ruta y namespace), no solo el mismo texto de namespace.

La política `BLOCKED_UNVERIFIED_GLOBAL_WRITER_ADOPTION` de E13 no se modifica. `historical_completeness=NOT_VERIFIED`, derechos ResearchBitcoin Tier 2 no comprobados, fuente Funding P1 y reserva R10-D.2 pendientes.

**Operaciones productivas prohibidas en este PR:** no `--apply`, no consultas HTTP reales, no acceso SQLite real, no cambios cron, backups, migraciones ni despliegue. ChatGPT implementa/revisa/mergea después del QA; Hermes usa SHA exacto en worktree nuevo, con pruebas sintéticas, publica un único informe y STOP.
