# R10-C — Preflight de solo lectura (SIN autorización operativa)

**Estado:** PR de preparación técnica. R10-C operativa / R10-D NO autorizadas. Prohibido ejecutar DDL sobre producción, ingesta HTTP real, cron, Telegram o report publication.

## Alcance

Esta PR introduce el verificador `scripts/market_v3_preflight.py` para comprobar, sin escribir en la base, la preservación de toda la información legada en la eventual transición v2 → v3. La migración NO forma parte de esta PR.

La conexión SQLite utiliza URI mode=ro, PRAGMA query_only=ON y una única transacción de lectura coherente. No ejecuta DDL, DML, red, backup ni escritura de archivos. Emite JSON a stdout. Nota técnica: SQLite/WAL podría requerir archivos auxiliares de coordinación de lectura; el verificador no escribe datos ni esquema.

## Evidencia PRE / POST

- PRAGMA integrity_check y foreign_key_check.
- Versiones de esquema 1/2 en PRE y 1/2/3 en POST.
- Hashes SHA-256 y conteos de todas las filas legadas en market_context_migrations (solo v1/v2), market_source_cursors, market_source_runs, market_raw_payloads, market_derivatives, market_etf_flows, market_calendar_events, market_context_revisions y market_request_lineage.
- Los 19 campos v2 de cada receipt son la proyección estable, aunque la tabla gane columnas v3. Orden determinista por PK. El cuerpo de cada BLOB se representa en la huella por longitud y SHA-256, sin revelar bytes.
- PRE exige ausencia de esquema v3, mientras que POST exige presencia completa de esquema v3 y baseline PRE válida.
- POST compara todas las huellas/conteos, exige backward direction, NULL requested_start_ms para legado, cero acquisitions y ausencia de cursores forward_v1_*.
- historical_completeness permanece NOT_VERIFIED. Un PASS no autoriza migración ni demuestra cobertura de APIs.

Códigos de salida: 0 para PASS de preflight; 1 para controles incumplidos; 2 para base inexistente, error de esquema/SQL o JSON de baseline inválido.

## Comandos que Hermes SÍ puede ejecutar AHORA (solo worktree y fixtures offline)

```bash
export PYTHONDONTWRITEBYTECODE=1
python3 --version
git rev-parse HEAD
python3 -m unittest -v tests.unit.test_market_v3_preflight \
  tests.unit.test_market_history_forward \
  tests.unit.test_market_request_lineage_audit \
  tests.unit.test_market_request_lineage_v2
python3 tests/run_all.py
```

Reportar SHA exacto, versión de Python, número total de tests, exit codes, tracebacks sanitizados y Actions. Hermes NO modifica código ni commits, NO ejecuta el preflight sobre la SQLite real y NO ejecuta migración ni ingesta.

## Secuencia operacional FUTURA (solo bajo autorización expresa)

1. Congelar los escritores/ingestas y mantener ventana operativa controlada.
2. Ejecutar PRE en modo solo lectura. Comparar la cuenta backward contra el baseline aprobado de 102 receipts; si hubo crecimiento legítimo, verificarlo antes de ajustar la expectativa. Guardar JSON PRE fuera de GitHub, en destino seguro, mediante redirección de stdout.
3. Ejecutar por separado auditorías RAW, TEMPORAL y REQUEST_LINEAGE (read-only); detener ante cualquier fallo.
4. Con autorización adicional y distinta para DDL, verificar backup WAL-safe de esa base exacta. La migración opt-in se ejecutaría mediante un proceso separado, no desde este preflight.
5. Solo si una migración de esquema fue autorizada y ejecutada, ejecutar POST con --phase post --baseline RUTA_JSON_PRE, y exigir equivalencia de todas las huellas. Repetir auditorías de integridad, FK, RAW, TEMPORAL, LINEAGE.
6. Bloquear R10-D hasta autorización explícita posterior. Sin HTTP ni nuevos cursores forward durante R10-C.

Ejemplo orientativo de PRE cuando esté autorizado: python3 scripts/market_v3_preflight.py --phase pre --db RUTA_DB --expected-backward 102
Ejemplo orientativo de POST cuando esté autorizado: python3 scripts/market_v3_preflight.py --phase post --db RUTA_DB --baseline RUTA_JSON_PRE

**No ejecutar los ejemplos sobre producción ahora.** Un PASS solo aporta evidencia interna y no constituye autorización de ejecución ni de publicación.

## Pruebas negativas incluidas

DB inexistente sin crear archivo, esquema v3 prematuro, cuenta backward divergente, FK violada, POST sin baseline, baseline falsificada, BLOB mutado, receipt modificado, aparición de cursor forward o requested_start_ms legado y códigos CLI. La prueba de instalación v3 trabaja únicamente con SQLite temporal.

**Roles:** ChatGPT implementa y audita el código; Hermes ejecuta los tests como observador independiente, sin editar Git ni producción.
