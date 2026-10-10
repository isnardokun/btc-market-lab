# R10-C.2 — Migrador v3: validación fail-closed + DDL transaccional

**Estado: implementación solo offline; NO autoriza ejecutar producción.**

## Hallazgo P1

El CLI `scripts/market_context_migrate.py` devolvía éxito cuando
`market_context_migrations.version=3` estaba presente, sin comprobar
realmente la integridad del esquema ni las constraints.
Además, `_upgrade_v2_to_v3()` verificaba integridad **después**
del commit. En el modo tradicional de sqlite3 Python, `with db`
**no garantiza BEGIN implícito para DDL**; una falla podía dejar
ALTER TABLE y marca v3 parcialmente aplicados.

## Cambios

1. El camino "v3 ya instalado" llama a `_integrity_check_connection`
   antes de informar éxito. Si las adquisiciones no tienen FK, UNIQUE,
   PK, tipos o el CHECK de `direction`, devuelve fallo.
2. El camino v2→v3 ejecuta **`BEGIN IMMEDIATE` explícito** antes de
   cualquier DDL, manteniendo ALTER, CREATE TABLE, marcador v3 y
   auditoría en la **misma transacción**.
3. `PRAGMA integrity_check`, `foreign_key_check`, columnas y
   `_v3_layout_issues` corren **antes** del commit. Si falla una
   verificación, SQLite realiza ROLLBACK sin afirmar migración exitosa.
4. Cuatro pruebas aisladas, con archivos SQLite en `tempfile`:
   migración e idempotencia positiva, marker v3 falsificado,
   fallo inyectado que obliga rollback y FK huérfana que bloquea
   la transacción.
5. Suite registrada en CI y en el runner completo.

## Orden de Hermes (sin operación real)

El operador de pruebas Hermes tiene función exclusivamente independiente:
ninguna modificación de código, commits, rebase, merge, producción, DDL
real, backups reales ni consultas HTTP de mercados.

En worktree aislado del SHA indicado en el comentario de la PR ejecutar:

```bash
export PYTHONDONTWRITEBYTECODE=1
python3 --version
git rev-parse HEAD
python3 -m unittest -v \
  tests.unit.test_market_v3_migrator_atomicity \
  tests.unit.test_market_v3_migration_rehearsal \
  tests.unit.test_market_v3_preflight \
  tests.unit.test_market_history_forward \
  tests.unit.test_market_request_lineage_audit \
  tests.unit.test_market_request_lineage_v2
python3 tests/run_all.py
```

Reportar `Ran N tests`, `OK`/FAIL, ambos exit codes, errores y
CI verde o rojo en ese mismo SHA. Los comandos `--apply` usados
por unittest solo apuntan a SQLite efímeras creadas por los tests.

**No hay autorización para R10-C producción, R10-D forward real,
cron, Telegram ni publicación.** Un PASS habilita revisión e
integración de código, no el despliegue operativo.
