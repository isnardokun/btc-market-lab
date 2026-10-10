# R10-E — Runbook de aprobación y despliegue gradual (SOLO DISEÑO)

**Prohibición activa:** este runbook no autoriza acceso a API real, SQLite productiva, cron/systemd, cambios de flags ni envío de reportes. Gobierno: propietario aprueba producción, ChatGPT implementa y revisa, Hermes solo ejecuta pruebas ordenadas.

## 1. Gate de desarrollo offline — vigente

1. ChatGPT realiza cambios en rama nueva desde master, publica commits y PR; no toca la rama original dirty ni los worktrees R10-C/D.
2. Hermes usa un worktree de pruebas separado sobre SHA exacto de PR e indica el SHA base; sin reset ni limpieza de otros worktrees.
3. Ejecuta test focalizado y runner central en fixtures SQLite temporales; no usa HTTP real ni credenciales. Reporta Python, RC, PASS/FAIL/skips, estado dirty y errores.
4. Hermes publica resultado y se detiene. No cambia scripts, tests, commits, PR ni realiza merges. ChatGPT revisa y corrige.
5. Sin nueva autorización, ningún resultado de pruebas permite habilitar --apply o cron.

## 2. Rollout futuro por etapas (no autorizado)

| Gate | Prerrequisito | Acción futura propuesta | STOP |
|---|---|---|---|
| B0 | PR revisada y pruebas offline | PLAN_ONLY en copia autorizada de SQLite; cotejar filas y ventanas | discrepancia, gap, cambio de SHA |
| B1 | Autorización del propietario, ventana sin writers, backup WAL-safe y manifest PRE | Una página de un solo proveedor live | API fail/429, gap, RC no cero, SHA mismatch |
| B2 | QA independiente B1 y nueva orden | Una página del otro proveedor | cursores inconsistentes, temporal/lineage incompleto |
| B3 | Dos pilotos reconciliados y exclusión mutua probada | Proponer scheduler shadow/PLAN; no activar hasta permiso | writers solapados, frescura inválida, flags opt-in activos |
| B4 | Ventana real de API y cuotas probadas | Backfill backward separado en lotes auditados | endpoint no disponible, solicitudes fallidas, límites incumplidos |

## 3. Invariantes

- Un stream forward no se actualiza desde dos rutas concurrentes. Revisar daily.sh y sus flags RBN_INCREMENTAL_ENABLED y MARKET_CONTEXT_DAILY_ENABLED; mantener OFF.
- Cursores forward_v1_* y history_backward_v1_* no se mezclan. Recalcular frontera dentro de cada transacción; no insertar barras parciales ni reconstruir datos ausentes.
- BLOB fuente con SHA, receipt, acquisition y run por intento; condición de continuidad por timestamp/número de intervalos. Una página vacía no prueba completitud externa.
- RAW/TEMPORAL/LINEAGE sobre todos los receipts antes de la siguiente página; PK+SHA de filas PRE, integridad/FK y backup WAL-safe antes de ejecución futura.
- Fallo post-commit conserva evidencia, sin retry ni restauración automática. Cualquier alteración productiva requiere orden nueva.
- Funding P1 (1.035 RAW sin receipts históricos) permanece sin metadata retrospectiva inventada; historical_completeness=NOT_VERIFIED.
- Secretos no se registran en PR, issue, memoria o logs. Entitlement Tier 2 exige validación separada.

## 4. Formato del reporte Hermes

Reportar SHA probado, master/base SHA, hora UTC, Python, estado dirty y rutas solo de fixtures; test focalizado tests.unit.test_market_r10e_plan_only y tests/run_all.py, RC, suites y comprobación de cero HTTP externo y cero lectura/escritura de DB productiva. Conservar worktrees de evidencia salvo orden expresa.

**Salida:** QA_PASS / QA_FAIL / QA_BLOCKED. Ninguno implica merge ni autorización del scheduler.
