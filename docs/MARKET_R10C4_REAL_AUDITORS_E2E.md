# R10-C.4 — Aceptación positiva end-to-end de auditoría PRE (solo offline)

## Brecha de cobertura

R10-C.3 había pasado 68/68 unit tests y 36/36 suites; pero la
prueba **positiva** del gate usaba respuestas de auditor inyectadas,
y la ejecución con auditores reales solo verificaba rechazo en
SQLite incompleta. Esa evidencia no probaba que
PRE + RAW + TEMPORAL + LINEAGE pudiera llegar a PASS simultáneamente
usando las implementaciones reales.

## Trabajo de esta PR

- `tests/unit/test_market_r10c_real_auditors_e2e.py` construye
  una SQLite v2 **temporal**, con los 4 streams (OI/funding de
  Binance y Bybit), BLOBs SHA256 verificables y **102 receipts backward
  sintéticos**, pero individualmente reconciliables contra el
  payload nativo, el run, la frontera temporal y el cursor backward.
- Ejecuta las funciones **reales** de PRE, RAW, TEMPORAL y LINEAGE,
  sin mocks positivos y sin HTTP, y exige
  `PASS_OFFLINE_PRE_EVIDENCE` con `historical_completeness=NOT_VERIFIED`.
- Pruebas negativas reales: discrepancia de valor RAW, adulteración de
  página backward, ausencia de una barra de 5 minutos, desviación de
  count y CLI con salida JSON.
- Endurece `_valid_temporal` en el gate: las cuatro series
  requieren `rows>0`, `valid_timestamps==rows`, cero duplicados
  y cero gaps dentro de los intervalos de cinco minutos de OI.
  Así, un auditor falsamente etiquetado PASS no puede tener serie
  incompleta y vacía.
- Incluye la suite en el runner y en CI.

**Limitaciones:** 102 receipts son sintéticos coherentes, no descargados
de las APIs ni representativos del estado real de producción. Estas
pruebas confirman reconciliación de los mecanismos y no garantizan
cobertura histórica ni consistencia de proveedores externos.

## Qué debe ejecutar Hermes

Desde un worktree aislado del **SHA fijo de esta PR** indicado en
el comentario de instrucciones, con Python 3.14.7:

```bash
export PYTHONDONTWRITEBYTECODE=1
python3 --version
git rev-parse HEAD
python3 -m unittest -v \
  tests.unit.test_market_r10c_real_auditors_e2e \
  tests.unit.test_market_r10c_quality_gate \
  tests.unit.test_market_v3_migrator_atomicity \
  tests.unit.test_market_v3_migration_rehearsal \
  tests.unit.test_market_v3_preflight \
  tests.unit.test_market_history_forward \
  tests.unit.test_market_request_lineage_audit \
  tests.unit.test_market_request_lineage_v2
python3 tests/run_all.py
```

Reportar versión Python, SHA exacto, conteos, exit codes,
fallos/tracebacks, CI y runner completo. Hermes **no modifica
código**, no commitea y no hace merge.

**OPERACIÓN REAL SIGUE BLOQUEADA:** no lanzar el gate en la SQLite
productiva; no migración v3, backups operativos, HTTP forward,
cron, Telegram ni publicación. R10-D/R10-E no autorizadas.
