# R10-C.5 — PRE gate con una sola transacción SQLite de lectura

## Hallazgo cerrado

R10-C.4 pasó en Hermes (82/82 unit, 37/37 suites).
Sin embargo, el gate R10-C.3 llamaba a PRE, RAW, TEMPORAL y LINEAGE
con **distintas conexiones SQLite**. En presencia de ingestas
concurrentes, esos auditores podían leer generaciones distintas de datos
incluso si los dos fingerprints PRE coincidían por casualidad después.

## Implementación

- `market_v3_preflight.inspect(..., connection=db)`, `market_raw_reconcile.audit`,
  `market_temporal_quality.inspect` y `market_request_lineage_audit.audit`
  aceptan una conexión suministrada **solo si ya tiene una transacción
  activa y `PRAGMA query_only=1`**. No la cierran ni hacen rollback.
  Sus interfaces standalone con ruta SQLite siguen disponibles.
- `market_r10c_quality_gate.evaluate`, cuando usa **los auditores reales**
  (sin inyección de dobles de tests), crea una sola conexión URI
  `mode=ro`, `PRAGMA query_only=ON`, activa foreign keys e inicia
  `BEGIN` para todas las comprobaciones; todos leen el mismo snapshot.
- Se observa `PRAGMA data_version` **antes de BEGIN y después de
  ROLLBACK** para detectar commits externos durante la auditoría. Si
  cambió, se devuelve `BLOCKED` con
  `EXTERNAL_SQLITE_COMMIT_DURING_AUDIT`.
  Nota SQLite/WAL: `data_version` permanece congelado mientras la
  transacción está fijada; por eso debe medirse después del rollback.
- Inyección de auditores simulados se conserva únicamente para tests
  negativos y se identifica como `INJECTED_AUDIT_FIXTURE`, sin declarar
  consistencia entre conexiones.
- Los tests nuevos incluyen fixture v2 íntegro con 102 receipts
  backward y los cuatro streams, chequeo de identidad de la conexión
  y modo query-only, writer WAL independiente durante las auditorías,
  rechazo de conexión insegura, compatibilidad de auditores standalone
  y salida JSON CLI.

## Limitaciones de la garantía

La transacción única garantiza que los auditores reales lean la
**misma generación SQLite**. `PRAGMA data_version` detecta commits de
otras conexiones ocurridos dentro de la ventana observada. No impide
que un proceso escriba fuera de esa ventana, ni que otro escritor
mantenga una transacción abierta sin commit. Un PASS no equivale a
«no hay escritores» ni a completitud histórica de la API.

La operación real, si se autoriza posteriormente, debe detener
escritores y respaldar la evidencia. No hay acceso productivo en
esta PR; todos los tests usan SQLite en `tempfile`.

## Validación offline Hermes

Aplicar las instrucciones con SHA congelado publicadas por ChatGPT
en comentario de la PR. Ejecutar en worktree aislado:

```bash
export PYTHONDONTWRITEBYTECODE=1
python3 --version
git rev-parse HEAD
python3 -m unittest -v \
 tests.unit.test_market_r10c_shared_snapshot \
 tests.unit.test_market_r10c_real_auditors_e2e \
 tests.unit.test_market_r10c_quality_gate \
 tests.unit.test_market_raw_reconcile \
 tests.unit.test_market_v3_migrator_atomicity \
 tests.unit.test_market_v3_migration_rehearsal \
 tests.unit.test_market_v3_preflight \
 tests.unit.test_market_history_forward \
 tests.unit.test_market_request_lineage_audit \
 tests.unit.test_market_request_lineage_v2
python3 tests/run_all.py
```

Se requieren conteos y exit codes, CI PASS del SHA exacto,
tracebacks en caso de error. Hermes **solo prueba y reporta**.

**R10-C operación productiva, R10-D ingesta HTTP forward y R10-E
automatización/Telegram NO AUTORIZADAS.** No ejecutar ningún
CLI, migrador, respaldo o auditor sobre la SQLite real.
