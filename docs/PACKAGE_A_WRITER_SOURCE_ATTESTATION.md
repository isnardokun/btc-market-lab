# Paquete A — Auditoría de rutas de escritura y control de cambios

**Estado:** solo lectura, exclusivamente checkout de código. No habilita un
launcher, el acceso a una SQLite productiva, el cron ni clientes HTTP reales.

## Riesgo cubierto

El éxito del bloqueo POSIX entre dos procesos cooperativos no demuestra que
todos los escritores reales de BTC Market Lab participen. El inventario previo
contiene **24 contratos**, **20 candidatos con opción CLI de flock**, rutas
manuales y bibliotecas invocables sin el flag. El script daily.sh no solicita
la protección explícita por cada subproceso. Un cambio del código de una ruta,
del bloqueo o del shell después del QA invalidaría la evidencia anterior.

## Nueva capacidad

**scripts/package_a_writer_source_attestation.py**:

1. Obtiene el inventario estático existente sin importar módulos que
   ejecutan producción. Incluye los 24 escritores registrados, los demás
   candidatos potenciales, los Python children de daily.sh y los componentes
   del bloqueo y del análisis.
2. Verifica rutas, límites de tamaño y symlinks. Calcula SHA-256 sobre bytes
   UTF-8 de cada fuente y un resumen SHA-256 determinista del conjunto.
3. Adjunta señales AST de SQL de escritura directa y de llamadas indirectas
   a almacenamiento. Un archivo aparentemente solo lector nunca se
   certifica como tal por este análisis.
4. Identifica hijos desconocidos y formas evidentes de shell dinámico
   (eval, python -m, python -c y source). El parser no interpreta Bash
   completo y no promete descubrir todo subprocess dinámico.
5. La función **compare_writer_source_snapshots** marca
   BLOCKED_OFFLINE_WRITER_SOURCE_DRIFT si se añade, elimina o altera
   cualquier archivo revisado, cambia el conjunto de rutas o el digest.
   Aun con snapshot idéntico, retorna permiso operativo **false**.

## Pruebas de regresión

La suite tests/unit/test_package_a_writer_source_attestation.py contiene
**14 pruebas**: identidad reproducible; inventario 24/20; distinción entre
storage/repositories (candidato SELECT, no certificado), news_archive
(INSERT/DDL) y market_context_sources (escritura indirecta); modificación
de daily.sh, writer y flock; nuevo escritor SQL no inventariado; symlink y
archivo faltante; shell dinámico; rechazo de ruta externa; ausencia de
creación de DB/lock o HTTP.

Los ensayos de mutaciones se ejecutan sobre copias desechables del código
en TemporaryDirectory. No se modifica el checkout original durante tests.

## Bloqueos que este avance NO resuelve

El servidor real puede tener cron, otros worktrees y procesos manuales o
externos que omitan este lock. Falta conocer su misma identidad física
(st_dev, st_ino), inventariar escritores no cooperativos y comprobar
procesos que escapan del grupo POSIX. El gate estático **no sustituye**
la revisión read-only del host autorizada ni una aprobación de despliegue.

Estados que siguen siendo falsos: runtime_cron_verified,
all_operational_writers_coordinated,
shared_physical_lock_inode_verified, production_authorized.

**Fuente de verdad:** la PR #79 permanece en borrador; la evidencia nueva
acredita seguridad de análisis de fuente y reproducibilidad offline,
**no** histórico real descargado, disponibilidad Tier2 ni producción.
