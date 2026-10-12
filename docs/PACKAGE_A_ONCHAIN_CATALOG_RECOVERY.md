# Paquete A — recuperación completa del catálogo RBN/Bitview por shards

**Estado:** implementación offline; solamente SQLite desechable marcada,
respuestas de proveedores simuladas y bloqueo POSIX cooperativo. No activa
API real, cron, daily.sh, deployment, merge ni históricos productivos.

## Por qué hacía falta

El barrido de las 36 series RBN+Bitview ya dividía su ejecución en runs
v3 de máximo 32 páginas. Si una respuesta 429/503 o timeout detenía el
segundo shard, repetir el orquestador inicial desde la primera página
generaba nuevos recibos para páginas históricas aceptadas y solicitudes
ficticias redundantes. La recuperación previa aceptaba un único
`source_run`, por lo que un operador debía reconstruir los shards a mano.

## Nueva función recuperable

`scripts/package_a_onchain_catalog_batch.py` expone
`recover_offline_onchain_catalog(db_path, lock_dir, plan, responses,
prior_runs=..., now=...)`. El resultado original
`run_offline_onchain_catalog` contiene ahora el
`recovery_manifest`: una lista explícita de intentos
`{source_run_id, start_page, planned_pages}`, sin ningún path libre,
comando, token ni cron. Si la recuperación vuelve a detenerse, el
resultado retorna un **manifiesto extendido** que incluye los intentos
originales y nuevos, apto para el siguiente intento explícito.

La misma función `_bind_offline_catalog_pages` sirve a ejecución
inicial y recuperación: recalcula el plan fuente completo y lo compara
contra SHA, métrica, símbolo, ventanas nativas, orden, páginas y shards.
Antes de abrir SQLite valida **todas** las respuestas fake y presupuestos
de RAW. La recuperación exige un manifiesto no vacío de tamaño acotado,
IDs de run en formato exacto, entradas contiguas sin duplicados, y runs
cuyo número de páginas corresponde exactamente al resto del shard v3.

## Algoritmo fail-closed

1. Adquirir una única lease POSIX del padre. La lease verifica que el
   archivo de bloqueo sigue correspondiendo al **mismo inode físico**.
2. Por cada run histórico, llamar al preflight de solo lectura
   `plan_synthetic_resume` con sus páginas originales exactas.
   Comprueba fuente, rango, orden, receipt, RAW SHA-256, unidad y escala
   de cada **página anteriormente aceptada**, además de integridad de
   base y estado de run. Un run inexistente, truncado, reordenado o con
   RAW previamente aceptado cambiado impide ejecutar nuevas solicitudes.
3. Si un run terminó, **saltar todas sus páginas** sin volver a invocar
   el HTTP falso, reescribir observaciones o mover cursor. Si terminó
   por fallo, retomar exactamente desde el primer receipt no aceptado;
   ese receipt fallido permanece inmutable como evidencia de intento.
4. La continuación usa el mismo `recover_synthetic_batch` del ledger
   v3, ahora con opción interna `_parent_lock_held` que valida la lease
   activa y evita adquirir por segunda vez el mismo flock. El intento
   nuevo recibe otro run_id; ninguno de los runs anteriores se edita.
5. Solo tras completar el sufijo de ese shard puede crearse un run
   nuevo para el siguiente shard. Un nuevo 429/503/timeout detiene el
   resto; no hay retries silenciosos ni tareas en segundo plano.
6. Retornar el SHA del plan, runs previos verificados, nuevos runs,
   total de requests fake realmente intentadas, observaciones nuevas,
   manifiesto actualizado y auditoría integral de SQLite.

Un catálogo completamente aceptado devuelto para recuperación genera
**cero solicitudes y cero nuevos source-runs**. El sistema nunca declara
completitud externa, entitlement Tier2 o datos realmente descargados.
El usuario puede optar expresamente por volver a ejecutar el método
offline con una respuesta corregida.

## Evidencia adversarial

Se ampliaron los E2E de
`tests/unit/test_package_a_onchain_catalog_batch.py` con diez escenarios
de recuperación: 429 en el primer shard de 36 streams, 503 en el segundo,
timeout en su frontera, dos intentos 429→503→200, recuperación no-op,
RAW aceptado adulterado, manifiestos omitidos/reordenados/duplicados,
run_id desconocido y plan modificado, lock ocupado y base corrupta.
Todos comprueban que el histórico original permanece sin sobrescritura
y que únicamente se ejecutan páginas pendientes.

La regla de ejecución sigue siendo **NO live**. El QA independiente de
Hermes todavía requiere un único SHA congelado; estos casos no habilitan
permisos de host, SQLite productiva ni descarga de históricos reales.
