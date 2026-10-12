# Package A — aceptación offline e integración

**Estado actual: `A_OFFLINE_ACCEPTED; EXTERNAL_VERIFICATION_PENDING`.**
Fecha: 2026-10-12. La estimación previa de ingeniería ≈90% queda como corte
histórico anterior al QA; no describe el estado de aceptación actual.

- PR integrada: [#79](https://github.com/isnardokun/btc-market-lab/pull/79).
- HEAD probado: `7744ade1267145ea41fe6808891408f0b7eeed5c`.
- Base: `8ca80e0f221ad2b0b276f6557d16ee00fe0ee381`.
- Merge: `45bbb3530df7f177844decf6e7df5f17d51d4ff4`.
- Árbol integrado idéntico al HEAD probado: `git diff HEAD_PROBADO MERGE` RC0,
  árbol `a6c0214b6aa9d4b40bfdb58281d9d3086cbb545c`.

## Evidencia y límites de observabilidad

[QA Hermes 72/72](https://github.com/isnardokun/btc-market-lab/pull/79#issuecomment-6125884334)
y [complemento](https://github.com/isnardokun/btc-market-lab/pull/79#issuecomment-6126148993):
Python 3.14.7, 73 suites registradas, 72 offline PASS, una live excluida;
retention PASS/RC0/0.225s; mismo SHA, worktree limpio y aislamiento confirmado.
[CI del SHA](https://github.com/isnardokun/btc-market-lab/actions/runs/38194215364)
SUCCESS en runner completo, assert de conteos, retention y gate de artefactos.
Validación local del autor: Python 3.12.14, RC0, 72/72 offline PASS.

[Ruling técnico de aceptación](https://github.com/isnardokun/btc-market-lab/pull/79#issuecomment-6126185175):
el RC original Hermes no se capturó explícitamente como dato independiente.
Se conserva la limitación. El marcador final reportado corresponde al camino
`all_ok=True` seguido de `sys.exit(0)` del runner inmutable; sumado a las
72 suites PASS y a CI con pipefail/RC0 independiente, se aceptó sin repetir
el runner completo. No se atribuye una captura inexistente a Hermes.
El worktree Hermes usó branch `qa`, no detached, pero ejecutó el SHA fijo
correcto y permaneció limpio/aislado. Normalización umask/TMPDIR de tests
respetó el guard owner-controlled: no se modificó para permitir modo 0775.

No quedan P0/P1 confirmados pendientes en el alcance revisado. Las seis
correcciones y diez regresiones RED→GREEN constan en
[revisión transversal](PACKAGE_A_FINAL_OFFLINE_REVIEW.md). Minor conservado:
TestCase auxiliares importados pueden descubrirse otra vez; conteos por
método no equivalen a tests únicos.

## Lo aceptado y lo pendiente

Aceptado offline: integración sintética de seis proveedores/46 streams,
RAW/SHA/native request/run custody/cursors, migraciones compatibles, lock
PID+inode, fallos, atomicidad, recuperación entre shards y gate de aceptación
en snapshot read-only único. La documentación anterior de la ejecución
se conserva como historial; este cierre prevalece para el estado actual.

Pendiente externo: entitlement/cuotas/retención y cobertura real por API,
unidades Bitview, dimensiones Tier2 RBN, calendarios Yahoo y participación
host-wide de writers. No se acredita descarga histórica real ni producción.
Funding P1/D.2 conservan sus reservas; no se fabrican receipts retrospectivos.

No se ejecutaron API real, SQLite productiva, cron/flags, backups, deploy o
publicación. La suite live queda `NOT_TESTED_NOT_AUTHORIZED` por alcance,
no por falta de keys. El único workflow integrado ejecuta tests offline.

**Hermes STOP.** La orden A queda cerrada. B y C fueron aceptados después
mediante sus propios gates: [B](PACKAGE_B_OFFLINE_ACCEPTANCE.md),
[C](PACKAGE_C_OFFLINE_ACCEPTANCE.md). La verificación externa y producción
se controlan por permisos y evidencia independientes, sin inferirlas del merge.
