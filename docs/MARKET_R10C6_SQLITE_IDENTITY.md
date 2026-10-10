# R10-C.6 — Identidad de conexión SQLite para auditores compartidos

## Brecha detectada

R10-C.5 hizo que PRE, RAW, TEMPORAL y LINEAGE compartieran una sola
transacción de lectura. Sin embargo, cada auditor aceptaba una
`sqlite3.Connection` proporcionada por el llamador siempre que
`in_transaction` fuera true y `PRAGMA query_only=ON`.

**Esas condiciones no prueban que la conexión esté abierta sobre la
misma ruta `--db` identificada en la evidencia.** Si por error se
usaba otro archivo SQLite con estructura y datos similares, podría
presentarse un resultado PASS con atribución a la DB equivocada.

## Cambios

1. Nueva función `scripts/market_readonly_snapshot.py::verify_read_snapshot`.
2. Exige una instancia real de `sqlite3.Connection`, una transacción
   activa y `PRAGMA query_only=ON`.
3. Valida `PRAGMA database_list`: solo se admite `main` y ninguna
   otra base `ATTACH`. Rechaza conexiones a SQLite en memoria.
4. Compara la ruta canónica real de `main` con `Path(--db).resolve(strict=True)`.
   Los symlinks hacia el MISMO archivo son válidos; otros archivos,
   aun con contenido idéntico, están prohibidos.
5. PRE, RAW, TEMPORAL y LINEAGE comparten el mismo verificador.
6. Se conserva el modo standalone `--db` sin API nueva obligatoria.
7. Seis tests temporales: rutas equivocadas, ATTACH, :memory:,
   conexión insegura, symlink correcto y gate integral.

## Evidencia y límites

La garantía se refiere a la **identidad lógica de la ruta SQLite**
y a la consistencia de la conexión compartida. No autoriza DDL,
migración v3, HTTP forward, backups productivos ni credenciales.
La ruta suministrada para auditar debe ser una base existente.

Las pruebas usan SQLite creadas en `tempfile.TemporaryDirectory()`,
sin red. El control de R10-C.5 contra commits concurrentes sigue
vigente. `historical_completeness=NOT_VERIFIED` incluso con PASS.

## Validación para Hermes

Ejecutar exclusivamente en worktree del SHA fijo que aparecerá en
el comentario de la PR:

```bash
export PYTHONDONTWRITEBYTECODE=1
python3 --version
git rev-parse HEAD
python3 -m unittest -v \
  tests.unit.test_market_readonly_snapshot_identity \
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

Reportar SHA, Python 3.14.7, conteos exactos, error codes,
tracebacks cuando existan, CI en SHA congelado, y resultado
del runner completo. Hermes solo prueba y reporta, no modifica
código ni realiza merge.

**R10-C operativa, R10-D HTTP real y R10-E siguen NO AUTORIZADAS.**
