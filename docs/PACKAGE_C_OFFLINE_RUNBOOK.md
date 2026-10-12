# Package C — operación offline, stop y rollout condicionado

Perfil implementado: fixture synthetic, schema A v3, modo plan por defecto.
A/B aceptados offline no acreditan historia/entitlement ni operación real.
Este runbook no es una orden Hermes ni una autorización productiva.

## Preflight y preparación

ChatGPT en worktree C limpio y aislado: verificar SHA/base, Python, suite y
alcance Issue78. No instalar ni modificar host/cron/DB como preflight.
Plan puro, sin SQLite/fake HTTP ni filesystem writes:

```bash
python3 scripts/package_c_pipeline_fixture.py
```

Esperado PLAN_ONLY_NO_IO:45 solicitudes fake/57 streams,7 stages, flags
productivos false. No pasar secretos; live/token/DB productiva no son opciones.
Crear una raíz NUEVA cuyo basename empiece package-a-fixture-; su padre debe
existir. No reutilizar artefactos de otra corrida ni un directorio productivo:

```bash
python3 scripts/package_c_pipeline_fixture.py --root /ruta/temporal/package-a-fixture-c-run --prepare
python3 scripts/package_c_pipeline_fixture.py --root /ruta/temporal/package-a-fixture-c-run --apply
```

PREPARED_OFFLINE_FIXTURE primero, después COMPLETE_OFFLINE_FIXTURE_ONLY/RC0.
Root0700, marker A+C, locks del namespace btc-research-global-writer-v1,
SQLite sintética y run_state.json. No WAL activo; esquema/FK se inicializan
solo en preparación explícita. Apply sin preparación falla/RC3, no crea DB.

## Archivo y observabilidad

Stages: ingest, validate, analytics, charts, report, archive, gate.
Artifacts: audit.json, evidence.json, charts.json, report.html, archive.json,
gate.json. Todo se mantiene bajo root/artifacts, sin envío/deploy/upload.
Archive enlaza hashes de fuente/modelo/charts/report; gate después del archive
reconstruye DB y HTML, score>=95/cero críticos. Handoff productivo false siempre.
El estado registra UUID/planSHA, prefijo completed, presupuestos reservados,
eventos de etapa/request y advice retry virtual; errores públicos allowlisted,
sin str(exception)/argv sensibles. Un fallo de calidad no produce permiso de
publicación, aunque el HTML parcial exista. Revisar resultado actual y gate,
nunca solo existencia del archivo o un viejo estado COMPLETE.

## Recuperación y STOP

Repetir --apply sobre LA MISMA fixture/plan. El mismo UUID se conserva.
Página ya comprometida: auditoría y receipt matching antes de otro fake HTTP;
no se duplican receipts ok, rows o cursores. Outputs con checkpoint deben
conservar SHA; COMPLETE se reconcilia otra vez contra DB/HTML/archivo.

Reserva antes de HTTP: si el proceso cae antes de persistir receipt, el intento
se conserva como presupuesto consumido/incierto. Nunca resetear contadores
para conseguir PASS. 429/500/503/timeout: max3/job, max128 total y límites
por proveedor (Yahoo9, RBN39, Bitview69, FRED6, Binance6, Bybit6). Backoff/jitter
se calcula con el clasificador A, **no se duerme ni se contacta una API**.
Formato/unidad/schema/RAW/empty rechazados: permanente, sin retry.

- Writer busy: RC3, cero SQL/fake HTTP nuevo. Conservar state/locks; no borrar
  el archivo flock ni matar un proceso ajeno. Reintentar tras liberar el owner.
- Proceso propio muerto: el kernel libera FD/flock; verificar identidad y
  recuperar. Nunca dar a un hijo el booleano parent-lock sin lease PID/inode.
- Circuito/budget agotado: STOP. Cambiar policy/manifest está bloqueado por
  planSHA. Una nueva rehearsal necesita preparación/plan explícitos en otra
  fixture; no editar la anterior ni presentar otro run como su continuación.
- RAW/DB/artefacto/manifest/version/link corrupto: BLOCKED, conservar evidencia.
  No reparar silenciosamente, copiar cifras, relajar guards o falsificar hashes.
- Symlink/hardlink o namespace/inode reemplazado: STOP antes de IO protegido.

Pruebas autorizables por orden QA final: runner offline, suites C y retención
en fixtures. Fault before/after/after-output por cada stage y commit→checkpoint,
SIGKILL de UN child de test, contender/release y reservas persistentes. Hermes
no modifica código, reconfigura servicios, instala herramientas o aplica CLI.

## Calidad editorial y archivo visual

Usar tooling B con Playwright/Chromium y Poppler previamente instalados por
ChatGPT/CI; no descargan recursos de mercado durante el render:

```bash
node scripts/package_b_visual_qa.cjs --html /ruta/fixture/artifacts/report.html --gate /ruta/fixture/artifacts/gate.json --output-dir /ruta/temporal/visual-nueva
```

320/390/768/1440, SVG3, series57, texto visible, cifras sin desbordes,
capturas repetidas estables y A4. VISUAL_STRUCTURE_PASS_MANUAL_REVIEW_REQUIRED
exige inspeccionar PNG y todas las páginas, no valida investigación real.
No publicar runtime artifacts en Git. Los nuevos receipts cambian snapshotSHA;
misma evidencia produce mismo HTML, no se impone hash cross-run ficticio.

## Escalera real y rollback/cutover — NO ejecutada

| Paso | Permiso/evidencia necesarios | Condición de STOP/rollback |
| --- | --- | --- |
| Host/writers preflight | Propietario autoriza lectura exacta de host/DB; inventario de TODOS los writers/locks | Writer no cooperativo o namespace distinto: no habilitar ejecución real |
| Backup/migración | Permiso DB target, backup WAL-safe/restauración probada y revisión schema | Backup no verificado: no migrar; restaurar solo con permiso del propietario |
| Piloto por fuente | Fuente/endpoint/rango/volumen/quota/credencial gestionada y presupuesto explícitos | Entitlement/schema/429 inesperado: detener fuente y preservar RAW/receipt |
| Auditoría y backfill | Cobertura/provenance reconciliadas, ventanas efectivas verificadas, lotes bajo quota | No avanzar cursor ni afirmar historia completa ante gaps/empty/unavailable |
| Shadow report | Permiso de lectura productiva y fuente realmente verificada; comparar con reporte vigente | Datos stale/missing o gate fallido: no cutover; mantener flujo previo |
| Cron/publicación | Permiso separado, runbook firmado, rollback del scheduler/flags y destino exacto | Divergencia, writer nuevo o quality FAIL: detener nuevo entrypoint autorizado y restaurar configuración previa bajo permiso |

El código C de fixture **NO se convierte en live mediante esos permisos**:
los bindings productivos/writers y prueba host-wide deben concretarse y
revisarse antes del piloto. No apuntar --root a datos reales ni sustituir
daily.sh por este CLI. C_PRODUCTION_ACCEPTED requiere implementación y operación
real autorizadas/observadas; C_OFFLINE_ACCEPTED es un hito distinto.
