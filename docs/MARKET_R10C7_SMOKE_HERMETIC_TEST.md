# R10-C.7 — Corrección del smoke test bajo `BTC_RESEARCH_HOME` aislado

## Incidente

Hermes ejecutó el runner completo sobre el worktree separado con:
- `BTC_RESEARCH_HOME=/home/ignotus/r10-c7-master.VFBKzG8F`
- `SEND_TELEGRAM_AUTO=0`, sin SQLite productiva.
- Resultado: 37/38 suites PASS. Falló solo
  `test_local_runtime_artifact_retention_survives_git_untracking`.

## Causa raíz

La prueba ya crea, bajo `tempfile.TemporaryDirectory()`, un repositorio
Git artificial con manifiestos, recibos y HTML **sintéticos**, y copia allí
`scripts/local_artifact_retention.py`.

Ese script selecciona su directorio de trabajo mediante
`BTC_RESEARCH_HOME` (cuando existe) antes que su ruta de archivo.
Los subprocesos de la prueba heredaban `BTC_RESEARCH_HOME` del
**worktree de smoke** y ejecutaban `backup`/`restore` allí en lugar de
hacerlo contra el repositorio sintético. No se necesitaban los informes
o recibos reales de Hermes: solo faltaba aislar el entorno del test.

## Cambio mínimo

En `tests/unit/test_hermes_skill.py`, la prueba crea
`fixture_env = os.environ.copy()` y sobreescribe
`fixture_env["BTC_RESEARCH_HOME"] = str(root)`, donde `root` es
el repositorio temporal. **Todos** los subprocesos de `backup` y
`restore` de esa prueba reciben explícitamente `env=fixture_env`.

No se cambia `scripts/local_artifact_retention.py`, ni se excluye el
test, ni se reducen las comprobaciones de SHA256, permisos 0700,
restauración de recibos o exclusión de rutas no permitidas.

CI añade la ejecución focalizada con `BTC_RESEARCH_HOME` heredado del
checkout GitHub para asegurar que el fallo no reaparezca.

## Criterios

1. Prueba focalizada PASS con `BTC_RESEARCH_HOME` apuntando al
   worktree, **sin** artefactos de runtime locales.
2. CI GitHub y `tests/run_all.py` en Python 3.14.7: **38/38 PASS**.
3. Worktree original y su archivo sin commit intactos; worktree master
   de R10-C.7 original intacto. Crear un **tercer worktree temporal**
   detached para el SHA de la PR de fix.
4. `db/btc_research.db` NO existe en worktree de fix; no acceder al
   archivo de producción, generar backups productivos o hacer HTTP.
5. Si se aprueba esta corrección, merge en master con SHA validado;
   luego nueva comprobación 38/38 del master final en un worktree
   aislado. **No** reinstalar ni desplegar de forma implícita.

## Límite operativo

Este patch solo corrige el entorno de un test. No concede permiso
para ejecutar PRE real, migración v3, ingesta R10-D, cron,
Telegram ni publicación. `historical_completeness` sigue
`NOT_VERIFIED`.
