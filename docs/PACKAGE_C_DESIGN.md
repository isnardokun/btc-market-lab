# Package C — orquestación offline recuperable

Base89b5a8b: A y B aceptados offline. Contrato Issue #78 y programa A/B/C.
Una PR C; ingeniería inline ChatGPT, QA final Hermes/STOP sobre SHA congelado.

## Intención y alcance autorizado

Ejecutar ingest → validate → analytics → charts → report → archive → gate
como un run observable, recuperable y auditable sobre fixture temporal marcada.
No activar APIs, SQLite productiva, scheduler, flags, backup o publicación.
La delegación y alcance ya aprobados permiten concretar este diseño/plan
sin abrir aprobaciones repetidas por cada decisión de implementación.

Se elige motor Python de etapas fijas con estado JSON atómico. Envolver daily.sh
con shell conservaría IO productivo implícito; un DAG genérico de callbacks
añadiría superficie sin necesidad y permitiría HTTP no controlado. Este perfil
consume contratos A/B reales y exclusivamente respuestas fake tipadas.
El rollout real exige permisos/evidencia independientes y sigue bloqueado.

## Contratos y estructura

- B expone example_jobs(): tuple[ExampleJob,...], planificación pura de45
  solicitudes nativas /57 streams. apply_example_job reutiliza adaptadores A.
  create_example_fixture conserva compatibilidad y resultado anterior.
- Yahoo fanout acepta reutilización interna del parent lease, validada por
  ContextVar/PID/inode real igual que las páginas A. Ningún booleano concede
  lease. El pipeline mantiene el lock A único durante el run; todas las etapas
  de escritura lo verifican. El lease C revalida además root/DB/artefactos
  mediante guard bajo flock, antes de SQL y en precommit; la identidad DB
  no se acredita solo por el inode del lock. Ninguna transacción SQLite
  abarca fake HTTP.
- scripts/package_c_state.py: preparación/guard, manifest checksum, artefactos
  atómicos+fsync y guard contra links/paths ajenos. Solo roots nuevas marcadas
  package-a-fixture-* y package_a_synthetic.db. Estado1 identifica plan/run.
- scripts/package_c_pipeline.py: plan y ejecución/reanudación de7 etapas,
  checkpoints por solicitud, presupuesto persistente y retry/circuito offline.
- scripts/package_c_pipeline_fixture.py: CLI plan por defecto (cero IO),
  --prepare crea fixture nueva explícita; --apply requiere esa preparación.
  Ningún live/production/publication handler ni credencial admisible.

## Recuperación e integridad

Plan SHA vincula versión, asof/cutoff, hashes RAW, requests y políticas.
Cambiar plan/presupuesto/version después de preparar falla cerrado.
Run UUID se crea una vez y no cambia en resume. Checkpoint después del commit;
ante crash commit→checkpoint, reconstruir página desde receipts ok/RAW/audit
sin volver a pedirla. Reserva durable de intento ANTES del fake HTTP conserva
presupuesto incluso en crash; una reserva sin receipt cuenta como incierta,
nunca autoriza peticiones ilimitadas. No reescribir fallos como éxitos.

Cada etapa guarda artefactos+SHA antes de marcar DONE. Dependencias se validan
al reanudar; artefacto/DB/manifest corrupto bloquea, no autocorrige evidencia.
Un run completo se reevalúa contra DB+HTML/gate antes de devolver COMPLETE;
hashes persistidos no sustituyen la reconciliación B. Archive guarda hashes de
audit/evidence/charts/report, gate se calcula DESPUÉS del archivo. Handoff
permanece offline, production_publication_authorized=false siempre.

Retries: máximo3 intentos por job, hasta128 fake requests totales, presupuestos
por proveedor explícitos. 429/500/503/timeout: consejo A de backoff+jitter virtual,
sin sleep ni red real. Parse/schema/unidad/RAW: permanente, STOP. Tras agotar
intentos el circuito queda abierto y exige otra fixture/plan, nunca reset oculto.
El historial de observaciones no se duplica; fallos conservan receipts propios.

## Observabilidad y permisos

Manifest/eventos tipados y allowlisted antes de SQL: stage, job key, intento, estado,
fake-request presupuesto reservado y consejos de retry; no URLs/tokens/RAW
ni str(exception) no controlado. Auditor A/B entrega cobertura/freshness,
receipts/lineage y fallos. Locks prueban cooperación de fixture, no participación
host-wide de escritores legados. Live se rechaza mientras eso sea UNVERIFIED.

## Aceptación y pruebas

E2E45 fake requests/seis proveedores/57 streams, todo A→B→archive→gate.
Dry-run cero IO; rutas/links/marcadores/esquema/version/presupuesto inválidos
bloqueados. Cada etapa tiene fallo before/after y resume; crash multiproceso
después del commit no duplica receipts ni rows. Contender bloqueado antes de
SQL/fake HTTP; un proceso muerto libera flock. Retry429/503/timeout y permanente,
budget persistente, corrupt RAW/HTML/chart/manifest, COMPLETE revalidado y no
publicación ante fallo. CI incluye el reporte C en responsive/A4 real.

Runbook operativo con preflight, recuperación, stop, archivo y matriz explícita
de permisos/rollback/cutover. C_OFFLINE_ACCEPTED solo con CI, revisión P0/P1=0
y QA final Hermes del SHA. C_PRODUCTION_ACCEPTED no se infiere de este perfil.
