# R10-C.1 — Validación reforzada OFFLINE (sin acceso a producción)

## Hallazgo

Las 45 pruebas unitarias y las 33 suites de Hermes del PR #52 acreditaron
el verificador PRE/POST, pero la prueba positiva de migración usaba
`storage.market_context.install_request_lineage()` directamente. No ensayaba
el comando real `scripts/market_context_migrate.py --apply`, que crea un
backup WAL-safe, modifica el esquema y ejecuta integridad/FK.

Además, el POST comprobaba solamente la existencia de ciertas columnas y de
`market_request_acquisitions`. Una tabla falsificada con esas columnas pero
sin FKs o `UNIQUE(request_id)` podía aparentar un esquema v3 válido.

## Cambios de esta entrega

1. `scripts/market_v3_preflight.py`: POST exige tipos, NOT NULL, PK,
   FK a `market_request_lineage(request_id)`, FK a
   `market_raw_payloads(sha256)`, `UNIQUE(request_id)`, y CHECK para
   `direction IN ('backward','forward')`.
2. Tests negativos que falsean intencionalmente el esquema de adquisiciones
   en una base temporal; POST debe devolver `BLOCKED`.
3. `tests/unit/test_market_v3_migration_rehearsal.py`: instala un v2
   sintético en una SQLite temporal, genera 102 receipts backward con FKs
   consistentes, conserva un cursor backward y bytes RAW, abre modo WAL
   con una transacción confirmada pendiente de checkpoint, y ejecuta
   **el CLI real de migración únicamente contra esa base temporal**.
4. Compara fingerprints PRE, BACKUP y POST, integridad/FK, preservación de
   102 receipts, raw/WAL/cursor; verifica idempotencia y rechazo por
   corrupción posterior.
5. Registra estas pruebas en `tests/run_all.py` y GitHub Actions.

Estos fixtures verifican preservación lógica del almacenamiento, NO prueban
cobertura histórica del proveedor ni representan 102 páginas reales de API.

## Orden para Hermes (tras el SHA final de la PR)

Solo en worktree aislado, con Python 3.14.7, sin modificar código ni producción:

```bash
export PYTHONDONTWRITEBYTECODE=1
python3 --version
git rev-parse HEAD
python3 -m unittest -v \
    tests.unit.test_market_v3_migration_rehearsal \
    tests.unit.test_market_v3_preflight \
    tests.unit.test_market_history_forward \
    tests.unit.test_market_request_lineage_audit \
    tests.unit.test_market_request_lineage_v2
python3 tests/run_all.py
```

Reportar SHA, `Ran N tests`, `OK/FAIL`, exit codes y diagnóstico con tracebacks,
y verificar el CI del mismo HEAD. Todo `--apply` que aparece en la suite
actúa exclusivamente sobre SQLite efímeras de `tempfile.TemporaryDirectory`.

**NO ejecutar contra la SQLite real**: preflight productivo, migración DDL,
backup, consultas HTTP de mercado, scheduler, Telegram o publicación.

## Contrato de autorización

- R10-B y R10-C offline integrados en `master`.
- Esta PR solo propone hardening y tests **OFFLINE**.
- R10-C operativa con base real: NO AUTORIZADA.
- R10-D / R10-E: NO AUTORIZADAS.

Un PASS de CI y Hermes habilita **revisión/fusión del código**, no ejecución
productiva. Esta PR no añade cron ni acciones automáticas de migración.
