# R10-C.3 — Puerta PRE de calidad de datos (solo lectura)

## Alcance

Se incorpora `scripts/market_r10c_quality_gate.py`, una puerta
de evidencia **PRE v2** que agrupa, en un solo resultado JSON, el
verificador `market_v3_preflight.inspect(phase='pre')` y los auditores
ya existentes:

- RAW: `market_raw_reconcile.audit`, estado obligatorio
  `PASS_SQLITE_TO_RAW`, filas iguales a verificadas, bytes de
  fuente vinculados a SHA, ninguna incidencia.
- TEMPORAL: `market_temporal_quality.inspect`, obligatorio
  `SNAPSHOT_INTERNAL_QA_OK`, sin gaps/incidencias y
  `historical_completeness=NOT_VERIFIED`.
- LINEAGE: `market_request_lineage_audit.audit`, obligatorio
  `PASS_REQUEST_LINEAGE`, **todos** los receipts verificados, total
  backward exacto definido por operador y cero forward.
- PRE SQLite: tablas, `integrity_check`, `foreign_key_check`,
  columnas v2, ausencia de v3 y hashes/counts por tabla. PRE se
  ejecuta dos veces y exige fingerprint idéntico después de los
  auditores para detectar cambios concurrentes entre comprobaciones.

La API programática admite inyección de auditores **solo para tests**;
el CLI siempre utiliza los auditores reales. No ejecuta red, DDL,
DML, backups, cron ni writes directos. Solo imprime JSON a stdout.
La base SQLite debe existir.

**Limitación importante:** los auditores abren conexiones de
solo lectura independientes. La doble huella detecta *ciertos*
cambios concurrentes, pero NO prueba una instantánea global de todo
el tiempo de auditoría. Para futura operación real se exige
congelar escritores/ingestas y preservar evidencia externa. Tampoco
determina que el proveedor exponga todo su historial.

## Códigos y estados

- `0 PASS_OFFLINE_PRE_EVIDENCE`: evidencia interna PRE coherente,
  sin autorización para migración.
- `1 BLOCKED`: preflight o alguno de los auditores incumplió
  invariantes, o cambió alguna huella.
- `2 GATE_UNAVAILABLE`: error de acceso, esquema u otra excepción
  manejada; sin éxito supuesto.

Siempre `historical_completeness=NOT_VERIFIED` y
`authorization=EVIDENCE_ONLY_NO_MIGRATION_NO_HTTP`.

## Prueba offline para Hermes

Utilizar exclusivamente SHA congelado que se publicará en comentario
de esta PR. En worktree separado:

```bash
export PYTHONDONTWRITEBYTECODE=1
python3 --version
git rev-parse HEAD
python3 -m unittest -v \
  tests.unit.test_market_r10c_quality_gate \
  tests.unit.test_market_v3_migrator_atomicity \
  tests.unit.test_market_v3_migration_rehearsal \
  tests.unit.test_market_v3_preflight \
  tests.unit.test_market_history_forward \
  tests.unit.test_market_request_lineage_audit \
  tests.unit.test_market_request_lineage_v2
python3 tests/run_all.py
```

Reportar conteos, nombres fallidos, tracebacks, exit code,
`git rev-parse HEAD`, Python, CI y estado por suite. El test del
gate usa fixtures SQLite temporales e inyección de resultados de
auditores para ejercitar decisiones positivas y negativas: esto
NO reemplaza una auditoría de 102 requests reales, ya cubierta
por fases previas y pendiente de comprobación sobre la SQLite
auténtica bajo autorización futura.

**NO ejecutar todavía el gate CLI sobre SQLite real; NO migrar
esquema v3; NO respaldo productivo, HTTP forward, cron, Telegram
ni publicación.** R10-C operativa y R10-D continúan bloqueadas.

## Uso orientativo futuro, no autorizado ahora

`python3 scripts/market_r10c_quality_gate.py --db RUTA_REAL --expected-backward 102`

El valor 102 es la última línea base reportada. Antes de operarlo
debe conciliarse con el estado efectivo actual de los receipts.
Ese eventual PRE se realizará solo cuando sea expresamente
autorizado y con escritores detenidos. El resultado nunca
autoriza por sí solo un `--apply` ni el inicio de R10-D.
