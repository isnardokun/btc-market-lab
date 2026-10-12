# Package C — aceptación offline e integración

**Estado actual: `C_OFFLINE_ACCEPTED; EXTERNAL_VERIFICATION_PENDING`.**
Fecha: 2026-10-12. Este cierre prevalece sobre estados históricos DRAFT,
QA_PENDING y gate pendiente del ledger/plan. Producción no activada.

- PR integrada: [#81](https://github.com/isnardokun/btc-market-lab/pull/81).
- HEAD probado: `167cdf5409b9d42ae7a56b1a137f9a8c8564c0c8`.
- Base: `89b5a8bb03dcfb6ad67a1e996563121e95388847` (A/B aceptados offline).
- Merge: `fdb7867a539b5a1104d207b9a51d758565017280`.
- Árbol del merge idéntico al HEAD probado:
  `f0debffb368a9abcce5066787d02f108a9a1809c`.

## Evidencia y decisión

[Orden única](https://github.com/isnardokun/btc-market-lab/pull/81#issuecomment-6131586438)
y [reporte Hermes](https://github.com/isnardokun/btc-market-lab/pull/81#issuecomment-6131951839):
Python 3.14.7; UTC 2026-10-12T20:14:24Z–20:20:34Z; worktree detached
`/home/ignotus/pr-81-qa`, limpio antes/después; 79 suites registradas,
78 offline ejecutadas/PASS, cero FAIL, una live omitida por alcance;
runner RC0; retención PASS/RC0/0.224s; tres suites C PASS. Aislamiento:
SQLite sintética, HTTP fake y locks temporales; sin DB productiva/API real/cron
ni publicación. Worktrees QA A/B/C conservados y limpios según propietario.

[CI del HEAD](https://github.com/isnardokun/btc-market-lab/actions/runs/38299924256)
SUCCESS en todos los pasos: runner/conteos, retención, rechazo de runtime en
Git, informe B y flujo C completo con prepare/apply/reapply/archivo/gate,
responsive 320/390/768/1440 y A4 de 11 páginas. Reapply conserva UUID y solicita
cero páginas nuevas. Python CI 3.11.17, retención 0.201s, Chromium151.0.7922.34;
cero recursos externos durante render. El merge de prueba de CI también
posee el mismo árbol del candidato. Evidencia CI verificada en logs del job.

[Ruling ChatGPT](https://github.com/isnardokun/btc-market-lab/pull/81#issuecomment-6131995237):
HEAD/base sin drift; gates y QA coinciden. No reviews/threads bloqueantes;
sin P0/P1 confirmados abiertos en el alcance revisado. Umask/TMPDIR y skips
individuales de unittest no enumerados por Hermes quedan NOT_REPORTED, sin
inventar metadata ni repetir suites. Las versiones/RC/identidad/UTC/aislamiento
sí están reportados. El inventario C contiene 33 métodos: 4 source,16 pipeline,
13 recovery; no confundir ese inventario con un conteo granular de skips.

La revisión fresca previa encontró un P1 de identidad DB y un P2 de eventos
no tipados; se reprodujeron RED, corrigieron GREEN y corrió el runner completo
post-fix sobre el código final. Autor local Python3.12.14: 78/78 offline RC0,
retención RC0/0.166s. Visual local Node24.19.0/Chromium143.0.7499.0: cuatro
anchos, capturas estables, PDF11 páginas e inspección manual. Detalles y
resultados históricos en [ledger](PACKAGE_C_EXECUTION_LEDGER.md).

## Capacidad aceptada

Un run explícito plan → prepare → apply sobre raíz nueva marcada ejecuta
`ingest → validate → analytics → charts → report → archive → gate` usando
contratos reales A/B y fuentes exclusivamente fake: 45 jobs nativos/seis
proveedores/57 streams. Plan por defecto sin IO; ningún binding live.

Parent flock A único, reutilizado por Yahoo y páginas legacy; ContextVar y
comprobación de inode/lease, con guard root/DB bajo lock, antes de SQL y en
precommit. No transacción SQLite de escritura durante fake HTTP. Manifest
atómico/fsync vinculado a UUID/planSHA, artefactos/digests y eventos tipados
allowlisted. Recursos/circuitos/retries acotados y consejos A virtuales.

Reconciliación de receipts RAW antes de pedir de nuevo una página comprometida;
reservas inciertas consumen presupuesto y no se reembolsan. Recuperación de
cada etapa before/after/after-output, commit→checkpoint y SIGKILL real de un
child de test, contender bloqueado antes de SQL/fake HTTP. Corrupción de
RAW/DB/HTML/archive/journal, links o plan bloquea sin reparación oculta.

Archive enlaza modelo/charts/reporte/auditoría; el gate final reconstruye
DB/modelo/HTML/archivo y un COMPLETE se reevalúa en cada apply. Gate100/0
críticos en fixture; un fallo no habilita publicación. Reporte C se somete
al contrato visual B. [Runbook](PACKAGE_C_OFFLINE_RUNBOOK.md) incluye stop,
recuperación, permisos reales y condiciones de rollback/cutover.

## Brechas externas y cierre del programa

A/B/C quedan aceptados **offline** por sus respectivas PR #79/#80/#81 y QA.
No se certifica histórico real descargado, entitlement/cuotas/retención,
calendarios/unidades externos ni participación de todos los writers del host.
Funding P1 y evidencia retrospectiva R10-D.2 conservan sus reservas.

C de fixture no se convierte en live al aprobar un piloto: bindings
productivos y writer participation deben concretarse y revisarse antes de
operar. Preflight host/DB, backup/migración, requests reales/backfill, shadow,
cron/flags y publicación conservan permisos separados del propietario y
requieren evidencia real. Sin ellas, C_PRODUCTION_ACCEPTED permanece pendiente.
No se ejecutó ni autorizó ninguna de esas operaciones en este cierre.

**Hermes DONE/STOP. Orden C cerrada.** Seguimiento externo/productivo en #60.
