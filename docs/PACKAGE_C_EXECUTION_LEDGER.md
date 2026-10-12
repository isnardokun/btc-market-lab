# Package C — registro de ejecución

Estado: C_OFFLINE_ACCEPTED; EXTERNAL_VERIFICATION_PENDING / HERMES_STOP / PRODUCTION_NOT_ENABLED.
Base: `89b5a8bb03dcfb6ad67a1e996563121e95388847`.
Spec: PACKAGE_C_DESIGN.md. Plan: PACKAGE_C_IMPLEMENTATION_PLAN.md.
Rama única: `feat/package-c-offline-orchestration`; worktree C aislado de A/B.

## Decisiones

El diseño concreta Issue #78 y el alcance A/B/C autorizado. Ingeniería offline
inline, sin rondas de aprobación por decisión de implementación. Una PR
revisable, sin operaciones productivas. API/DB/cron/publicación conservan
permisos independientes; el perfil fixture no tiene bindings live.

El parent lease A abarca las siete etapas. Yahoo reutiliza el lease real,
no un namespace paralelo. Reservas y checkpoint atómicos con fsync, ninguna
transacción de escritura durante fake HTTP. Receipts nativos se reconcilian
antes de repetir una petición; reserva incierta nunca se reembolsa.

Retries usan el clasificador A y backoff/jitter virtual, sin sleep ni red.
Máximo tres intentos/job, 128 requests fake y presupuestos por proveedor.
Host writers sigue UNVERIFIED; todas las salidas productivas son false.

## Implementación y verificación incremental

- B expone 45 jobs puros / 57 streams sin cambiar RAW, valores o ventanas.
  Source suite C: 4 PASS. B E2E tras refactor: 13 PASS. Yahoo A: 107 PASS
  en su suite existente (incluye TestCase importados; no son 107 casos nuevos).
- Se inicializa SQLite con FK ON y se cierran conexiones propiedad de C.
  CLI plan por defecto sin IO; prepare/apply explícitos; flags secretos/live
  rechazados sin eco. Stage/plan/version/link/schema/budget drift fail-closed.
- Pruebas de cada etapa before/after/after-output; commit→checkpoint por job,
  RAW/HTML/archive corruptos, reservas persistentes, 429/503/timeout y rechazo
  permanente. SIGKILL real de un child sintético libera flock y recovery
  completa sin receipts duplicados. Contender: cero SQL/fake HTTP nuevo.
- Regresión previa a revisión: runner RC0, 78/78 offline PASS, 79 registradas,
  una live excluida NOT_TESTED_NOT_AUTHORIZED. No aceptación Hermes inferida.

## Revisión fresca: un pase y fixes

Revisor independiente revisó staged tree `e2a6ebc82c98ec15b56a5ad3ab3e49c6e7a19dd2`
contra base, sin modificar archivos ni correr el runner. Dos hallazgos:

1. P1: reemplazo del inode DB entre preflight y flock permitía 5 receipts Yahoo.
   Reproducción local RED: SQL alcanzado con DB sustituida. Fix: guard bajo
   lease y callback de identidad en cada verificación real del parent lease;
   guard también antes de SQL tras parse y en precommit A/Yahoo. GREEN: bloqueo
   FIXTURE_IDENTITY_DRIFT, ninguna conexión ni fake HTTP, DB intacta.
2. P2: events=[null,{token:...}] con checksum correcto alcanzaba SQL.
   RED: siete variantes inválidas alcanzaban SQL. Fix: variantes de evento
   con claves/tipos/valores allowlisted antes de IO; booleano no es intento,
   clock_wait debe ser false, códigos de error controlados. GREEN: rechazo
   STATE_EVENT_SCHEMA_INVALID, sin SQL/fake HTTP ni modificación del journal.

Suite focal post-fix source+pipeline: 20/20 PASS, RC0, 15.928s. Total C:
33 métodos (4 source, 16 pipeline/CLI, 13 recovery), con subcasos de fases/fallos.
Una pasada de corrección; no segunda ronda de revisión del candidato.

## CLI, archivo y visual local

Python 3.12.14; Node 24.19.0; Chromium local 143.0.7499.0 y Poppler.
Fixture nueva privada, UUID `606d9ca65f2b414d883efdcee5ef500b`.
Plan SHA `d691fd165eb514445be1cb9ecc600c6218495d8f45b0ab0540143a8d3cb04ca3`.
Prepare RC0; apply RC0 COMPLETE, 45 fake requests/57 streams/7 stages.
Segundo apply RC0, mismo UUID, cero requests nuevos. Gate 100/100,
archive/model/charts/HTML reconciliados; producción false, HTTP real cero.

QA visual RC0: 320/390/768/1440 px, cero overflow numérico/cuerpo, etiquetas
visibles, enlaces resueltos, capturas repetidas estables, cero requests externos.
PDF A4 11 páginas, inspección manual del contacto de todas las páginas y de
cover/primer activo en cuatro anchos: texto/tablas/charts legibles, sin cortes
críticos. Charts móviles tienen scroll local intencional del contrato B.
Los artefactos runtime y herramientas permanecen fuera de Git.
Retención aislada: 1 PASS, RC0, 0.166s.

## Gate pendiente

Regresión final post-fix: runner RC0, OFFLINE_SUITES_EXECUTED=78,
OFFLINE_SUITES_PASSED=78, REGISTERED_SUITES=79, LIVE_SUITES_SKIPPED=1,
LIVE_STATUS=NOT_TESTED_NOT_AUTHORIZED. Umask 022, TMPDIR propio y
BTC_RESEARCH_HOME del worktree C. Después commit/PR DRAFT, CI de HEAD y una
orden QA Hermes concreta. C_OFFLINE_ACCEPTED requiere reporte Hermes PASS
revisado por ChatGPT; merge/producción no se infieren de estas pruebas.

## 2026-10-12 — aceptación offline e integración

Este cierre prevalece sobre el gate pendiente histórico anterior.
HEAD probado `167cdf5409b9d42ae7a56b1a137f9a8c8564c0c8`, base89b5a8b.
CI38299924256 SUCCESS: 78/78 offline, retención y visual B/C. Python CI3.11.17;
Chromium151.0.7922.34, cuatro anchos/A4 11 páginas, cero requests externos.
QA Hermes6131951839: Python3.14.7, UTC20:14:24Z–20:20:34Z, detached y limpio,
79 registradas/78 PASS/0 FAIL/1 live omitida, runnerRC0, retenciónRC0/0.224s,
tres suites C PASS y aislamiento confirmado. Umask/TMPDIR/skips individuales
no enumerados: NOT_REPORTED; no se inventan ni se repiten pruebas por metadata.
Ruling6131995237 acepta evidencia y cierra orden. Merge
`fdb7867a539b5a1104d207b9a51d758565017280`, árbol idéntico al candidato
`f0debffb368a9abcce5066787d02f108a9a1809c`. Sin P0/P1 confirmados abiertos
según revisión y regresión del alcance. Cierre en PACKAGE_C_OFFLINE_ACCEPTANCE.md.
Hermes DONE/STOP; worktrees A/B/C conservados, sin nueva orden ni producción.
