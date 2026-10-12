# Paquete A — seguridad de inode físico para el bloqueo global cooperativo

**Alcance:** implementación de control de exclusión POSIX y ensayos en SQLite desechable. `A_IN_PROGRESS`; no se ejecuta `daily.sh`, no se modifica cron, no se usan tokens/APIs reales ni se declara exclusión global del host.

## Riesgo corregido

POSIX `flock` bloquea **un inode abierto**, no un nombre de archivo abstracto. Si `btc-research-global-writer-v1.lock` desaparece de un directorio mientras un proceso conserva abierto su FD, otro proceso puede crear un **archivo distinto con el mismo nombre** y adquirir un segundo `flock`, cada proceso protegido por un inode diferente. El archivo no debe borrarse ni reemplazarse durante la vida del trabajo.

El bloqueo anterior comprobaba propiedad y permisos del propio archivo (regular, un link, mode 0600), pero **no exigía que su directorio fuera controlado por el propietario** ni verificaba que el pathname continuara apuntando al inode abierto tras adquirir el bloqueo.

## Cambios de la librería

`scripts/market_r10e11_writer_exclusion.py::hold_writer_lock` ahora:

- Exige que el directorio del candado exista, tenga `st_uid` igual al usuario efectivo y **no sea escribible por grupo ni por otros**. `0700` o `0755` son aceptados; `0775`, `0777` y `1777` no. No se crea ni modifica el directorio automáticamente.
- Compara la identidad física `(st_dev, st_ino)` del FD del directorio recién abierto con su pathname, para bloquear cambios de directorio durante `open`.
- Tras adquirir el `flock`, entrega una atestación con `physical_lock_device`, `physical_lock_inode` y la función `check_identity()`. Esta comprueba el FD original frente a `stat(..., follow_symlinks=False)` del archivo en su directorio, preservación del único hard link, propiedad, permisos y directorio físico.
- Repite la comprobación al finalizar normalmente el contexto; si el pathname fue sustituido o desapareció se informa `BLOCKED_WRITER_LOCK_INODE_REPLACED` / `DISAPPEARED` o `BLOCKED_WRITER_LOCK_DIRECTORY_REPLACED`. La excepción original del worker nunca se silencia.

`scripts/package_a_parent_lock.py` ahora mantiene en la lease, además del directorio, la función **viva** de verificación del inode original. `verify_offline_parent_lease` rechaza un simple booleano o directorio parecido y vuelve a verificar la identidad física.

`scripts/package_a_ingest_fixture.py` verifica ese inode **antes del fake HTTP** y de nuevo **dentro de la transacción** después de todos los audits y antes del `COMMIT`. La ruta Yahoo `scripts/package_a_yahoo_fanout.py` verifica el inode antes de su único mock HTTP y dentro de su transacción común de cinco series. El orquestador de catálogo RBN/Bitview reutiliza ese control en cada página que abre vía la lease padre.

## Evidencia de calidad

La nueva suite `tests/unit/test_package_a_writer_lock_inode_guard.py` contiene 12 escenarios sintéticos:

- Candado legítimo y reentrada denegada; directorios `0777`, `0775`, `1777` denegados y `0755` permitido.
- Reemplazo del archivo mientras el FD antiguo permanece abierto, demostrando que el segundo archivo permite un segundo `flock` independiente; `check_identity()` evita tratar el primer proceso como un holder válido.
- Eliminación sin reemplazo, ampliación insegura de permisos y cambio del directorio durante la lease.
- Lease padre con archivo sustituido, y fallas inyectadas justo antes del COMMIT tanto para recibo individual RBN como para transacción Yahoo de cinco streams, donde **todo el intento nuevo** se revierte y la tabla anterior permanece intacta.

La validación se ejecuta exclusivamente sobre `TemporaryDirectory` marcado, sin abrir la base productiva ni llamar red. La suite es un **único nuevo bloque** del runner portable, junto a todas las suites anteriores.

## Límite de la garantía

Exigir directorio controlado por su dueño dificulta a otros usuarios reemplazar el inode. No defiende por sí solo contra procesos **del mismo usuario** que deliberadamente borren o renombren archivos, contra root, fallos físicos o sustitución entre dos verificaciones de software. La verificación antes de COMMIT reduce riesgo y revierte fallos detectados, pero no crea un candado universal para escritores no cooperativos.

No se han comprobado los inodes del host, los permisos reales del directorio seleccionado para producción, los worktrees paralelos, `systemd`, cron o escritores manuales. El estado correcto sigue siendo `HOST_GLOBAL_WRITERS_UNVERIFIED`, `PR_DRAFT`, `HERMES_STOP`, `PRODUCTION_NOT_AUTHORIZED`.
