# Package C Implementation Plan

> Ejecución inline con superpowers:executing-plans; una revisión fresca final.

Goal: un run A→B recuperable, acotado y observable sobre fixture marcada.
Architecture: etapas fijas, parent flock A, receipts reconciliados y JSON
durable; defaults plan/offline, operación real ausente.
Tech Stack: Python stdlib/SQLite/flock, contratos A/B, Chromium CI ya instalado.
Spec: PACKAGE_C_DESIGN.md. Base89b5a8b, una rama/PR C.

## Restricciones globales

No live/DB productiva/cron/credenciales/publicación. Max3 intentos/job y128
requests fake; por proveedor explícito. Roots package-a-fixture-* nuevas.
Lock A antes de SQL/HTTP; reuse verificable, no lock nuevo incompatible.
Artefactos runtime fuera de Git; conservar worktrees y evidencia A/B.

## Review Focus

1. Crash entre SQL commit y checkpoint: reconciliar receipt antes de repetir.
2. Reserva previa sin receipt: no recuperar presupuesto automáticamente.
3. Campo JSON no tipado/plan drift: rechazar antes de SQL o fake HTTP.
4. Estado COMPLETE con archivo editado: revalidar calidad, nunca falso PASS.
5. Symlink/hardlink/contender/inode sustituido: fail-closed antes de side effects.

## Task1: jobs nativos B y parent lease Yahoo

Files: scripts/package_b_report_fixture.py, scripts/package_a_yahoo_fanout.py,
tests/unit/test_package_c_source_jobs.py.
Interfaces: ExampleJob(key,kind,specs,start,end,raw_bytes); example_jobs()→tuple;
apply_example_job(db,locks,job,*,status_code=200,now,_parent_lock_held=False)→dict.
- [x] Tests RED:45 jobs/57 streams; misma B fixture; parent real reutilizado;
  boolean sin lease rechazado antes de HTTP/SQL; source statuses y conteos.
- [x] Refactor generador puro; no alterar RAW/valores/ventanas nativas.
- [x] Añadir fanout reuse mediante verify_offline_parent_lease+nullcontext.
- [x] Ejecutar suite C source+B E2E; GREEN y ledger.

## Task2: estado durable y ejecución A→B completa

Files: scripts/package_c_state.py, scripts/package_c_pipeline.py,
scripts/package_c_pipeline_fixture.py, tests/unit/test_package_c_pipeline.py.
Interfaces: plan_fixture_pipeline(*,policy)→dict; prepare_fixture(root,*,policy)
→dict; run_fixture_pipeline(root,*,apply=False,policy,fault=None,
http_failures=None)→dict. Faults before/after:<stage> levantan InjectedCrash.
- [x] Tests RED plan cero IO, preparación explícita, links/root/plan/version;
  E2E COMPLETE con7 checkpoints, gate100,57 streams, archiveSHA y flags false.
- [x] Implementar guard/checksum+atomic fsync y journal de reservas/eventos.
- [x] Ejecutar stages con lease A único, artifacts/digest y revalidación B.
- [x] CLI plan por defecto; prepare/apply separados, output solo dentro root.
- [x] Targeted GREEN y ledger; no repetir full runner todavía.

## Task3: recuperación, presupuesto y fallos reales

Files: tests/unit/test_package_c_recovery.py; pipeline/state donde corresponda.
- [x] Tests RED before/after de cada stage, commit→checkpoint por job,
  restore sin duplicate rows/receipts y COMPLETE revalidado.
- [x] Tests RED 429/503/timeout/permanente y reservas/circuito persistentes;
  presupuesto insuficiente antes de SQL/HTTP.
- [x] Tests RED contender, SIGKILL/reacquire y corrupción RAW/artifact/manifest.
- [x] Fix mínimo por prueba, consejos retry A; GREEN y ledger.

## Task4: cierre integrado y QA independiente

Files: docs/PACKAGE_C_EXECUTION_LEDGER.md, PACKAGE_C_OFFLINE_RUNBOOK.md,
tests/run_all.py, test_package_a_runner_offline_gate.py,
.github/workflows/portable-report-tests.yml.
- [x] Integrar suites/conteos CI y C HTML/gate en visual tooling B.
- [x] Runbook/stop/permisos reales/rollback/cutover sin activar host.
- [x] Revisión fresca final; dos hallazgos P1/P2 reproducidos RED→GREEN.
- [x] Full runner final post-fix: 78/78 offline PASS, RC0, una live omitida.
- [x] Commit en PR #81; CI HEAD PASS, orden única y QA Hermes PASS.
- [x] Merge con árbol idéntico; cierre C_OFFLINE_ACCEPTED documentado.
- [x] Política de DRAFT/no aceptación hasta QA Hermes; ninguna operación live.
