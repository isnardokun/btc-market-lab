# Paquete A — Coordinación de escritores: diseño de cutover sin activación

**Estado:** `A_IN_PROGRESS / SOURCE_ONLY / HOST_NOT_VERIFIED / NO_PRODUCTION_PERMISSION`

**Artefactos:** `scripts/package_a_writer_cutover.py`, `tests/unit/test_package_a_writer_cutover.py`, `scripts/market_r10e11_writer_exclusion.py`, `scripts/package_a_writer_adoption_review.py`.

## Problema operativo

La mayoría de las entradas Python ofrecen `--global-lock-dir` **opt-in**, pero el
`scripts/daily.sh` actualmente instalado en el repositorio **no pasa ese
argumento**. El código no puede afirmar que el cron real está protegido. Un
escritor manual puede escribir sin adquirir `flock`, incluso si un proceso
cooperativo ya lo retiene. Múltiples copias del proyecto podrían utilizar
directorios de bloqueo distintos. `flock` es asesor y no reemplaza las
transacciones SQLite ni la auditoría de datos.

## Estrategia candidata: un único bloqueo en el proceso padre

Un futuro launcher autorizado adquiriría el mismo archivo de bloqueo POSIX
`btc-research-global-writer-v1.lock` y lo retendría **durante toda la vida
de `bash scripts/daily.sh`**. El hijo no heredaría el descriptor del lock,
pero su proceso padre lo conservaría hasta su finalización; otros procesos
cooperativos que compartan exactamente el mismo inode quedarían excluidos.

Los hijos de `daily.sh` **se invocarían sin** `--global-lock-dir` para
evitar un segundo `flock` incompatible dentro de los subprocesos que ya
están cubiertos por el lock padre. No se cambia `scripts/daily.sh` ni se
reprograma cron en esta PR. Esta estrategia puede serializar todas las
fases del reporte, inclusive tareas que no escriben SQLite; es un
compromiso deliberado de seguridad antes de optimizar duración del lock.

La función `plan_parent_locked_daily(repo_root)` solo inspecciona fuentes
y genera un inventario. `run_offline_parent_locked_child` es una **prueba
ejecutable exclusivamente sobre directorios `package-a-child-*` marcados**
con un `worker.py` temporal. No existe aquí un launcher operativo que
ejecute `daily.sh`.

## Matriz de exclusión / autorización

| Vía de escritura | Cobertura del candidato | Estado actual |
|---|---|---|
| Hijos invocados por el `daily.sh` envuelto | Dentro de la vida del bloqueo padre, **si se desplegara** | Solo propuesta, sin ejecución real |
| Opt-ins `--global-lock-dir` manuales | Cooperativos si utilizan el mismo inode | Sin identidad física verificada en host |
| RBN incremental y Market Context opcionales del shell | Cubiertos en teoría cuando los ejecuta el padre | Flags actuales no modificados |
| Escritura por librerías `storage/*` invocadas por el shell | Dentro del lock de ese proceso hijo; no evita otros callers | Call sites externos sin inventario completo |
| Backfill/operaciones manuales que evaden el launcher | **No protegidos** | Bloquean certificación global |
| Cron existente, servicios auxiliares, worktrees paralelos | **No demostrado** | Sin acceso autorizado al host |
| Procesos no cooperativos/direct DB access | **No protegidos por `flock`** | Requieren inventario y mitigación |

La salida `all_writers_cooperating=false` y
`physical_lock_inode_verified_on_host=false` son obligatorias antes del
cutover real. No se debe inferir un PASS porque el lock existe en fixtures.

## Evidencia offline y fallos inyectados

`tests/unit/test_package_a_writer_cutover.py` valida:

- Manifest real de llamadas Python del `daily.sh` sin ejecutar sus imports
  ni abrir la base de datos;
- Bloqueo padre alrededor de un subproceso Python desechable, sin doble lock;
- Competencia desde otro proceso **antes de invocar el worker**, sin
  efectos del proceso perdedor;
- Liberación en falla no-cero, timeout y terminación abrupta del titular;
- Distintos directorios de lock **no se excluyen mutuamente**;
- Rechazo de symlinks, marcadores faltantes, rutas ajenas y presupuestos de
  timeout fuera de rango.

Los tests demuestran la semántica local de `flock` para Python sintético;
**no demuestran cobertura de escritores operativos de la máquina**.
El proceso padre usa `subprocess.run(...,close_fds=True,timeout=...)` y
no transfiere credenciales al fixture. Un crash abrupto del **launcher**
real requeriría además una política de gestión del grupo de procesos hijo,
no probada por el fixture actual.

## Gate para un cutover productivo posterior (requiere permisos separados)

1. Aprobar inspección **solo lectura** de cron, servicios, copias de repositorio
   y lista de escritores concurrentes (incluidos callers directos a SQLite).
2. Identificar la ruta canónica de `btc_research.db` y el directorio
   permanente del lock; verificar propietarios/permisos y `st_dev/st_ino`
   del archivo físico compartido por cada proceso.
3. Revisar excepciones manuales y escritores no cooperativos; eliminar los
   bypass mediante wrapper o controles equivalentes **antes** de marcar
   `GLOBAL_EXCLUSION_VERIFIED`.
4. Aprobar por separado backup WAL-safe, migración, APIs, cuotas y cutover
   de cron. Este documento no concede ninguno de esos permisos.
5. Ensayar en staging con la misma topología del host, verificar locks
   antes de HTTP/SQL, recuperación ante `SIGKILL` y fallos de hijos.
6. Solo entonces realizar un cambio explícito de launcher, con diff del
   scheduler, snapshot previo y rollback probado. Nunca lanzar un segundo
   cron concurrente durante el cambio.
7. Vigilar periodos de ejecución, deadlines, WAL, cursores, failed receipts,
   integridad y el publication gate. Ante lock sin coordinación, volver al
   scheduler original y detener nuevas escrituras hasta reconciliar.

**Decisión actual:** `CUTOVER_BLOCKED_HOST_VERIFICATION_AND_AUTHORIZATION`.
Esta revisión no requiere ni solicita que Hermes ejecute nada todavía.
