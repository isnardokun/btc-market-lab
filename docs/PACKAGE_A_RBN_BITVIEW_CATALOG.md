# Paquete A — catálogo histórico ResearchBitcoin + Bitview (offline)

**Alcance:** paquete de pruebas con respuestas HTTP congeladas, SQLite temporal marcada y bloqueo cooperativo POSIX. Ningún endpoint real, token RBN Tier2, cron, escritura productiva, migración o activación.

## Catálogo completo sin omisiones silenciosas

`scripts/package_a_onchain_catalog_batch.py` obtiene directamente del código legado:

- Las **13 métricas escalares RBN** instaladas en `ingestion/researchbitcoin_catalog.py`, con unidad y `raw_scale` según ese catálogo (incluidas fracciones 0–1 de supply in profit). Este catálogo **no inventa nuevas métricas Tier2** ni acredita su entitlement.
- Las **23 series d1 Bitview** de `ingestion/config.py`. Sus descripciones no son un contrato explícito de unidad física: el paquete usa `SOURCE_UNIT_UNVERIFIED` y deja `bitview_source_units_verified=false`. No se atribuyen USD, BTC ni ratio sin documentación verificada.

Cada serie obtiene su propio plan de intervalos UTC nativos de hasta 14 días, SHA-256 de query y una cantidad explícita de páginas. El plan ordena determinísticamente la primera página de todas las series antes de la segunda (`source_page_ordinal`, proveedor, métrica): esa equidad no altera el orden de los cursores por serie.

Si una selección, fuente, límite o presupuesto es inválido, se bloquea **el plan entero** antes de invocar ni siquiera HTTP simulado. Presupuestos: máximo 32 ventanas por métrica, 128 solicitudes sintéticas en toda la ejecución, 32 MB de respuestas congeladas y 32 páginas por `source_run` v3. Un catálogo diario de 36 métricas genera 36 peticiones sintéticas y dos `source_run` durables (32+4), sin excluir fuentes para caber en el primer shard.

## Contrato estricto de forma y temporalidad

**Bitview:** el índice obligatorio es `1d`, el atributo `start` debe ser exactamente el desplazamiento de días desde 2009-01-01 al inicio solicitado, y la longitud de `data` debe coincidir exactamente con la cantidad de días calendario del rango semiabierto. Los valores `null` son marcadores explícitos de ausencia, **no** barras cero ni disponibilidad verificada. Un array desplazado o truncado que el decodificador legado podría recortar ya no avanza cursor: queda `rejected` con RAW preservado.

**ResearchBitcoin:** admite el campo de valor `value` o el slug exacto del catálogo, como el parser legado; exige una sola clave temporal identificable y su normalización al inicio del día UTC. Un payload ambiguo con dos horas/fechas, varias métricas de catálogo o claves `value`/slug discordantes se rechaza. El paquete no transforma series dimensionales desconocidas en nuevos streams sin catálogo, metodología y parser auditados.

**General:** la auditoría RAW→SQL reconstruye todos los recibos, el auditor de request nativo compara endpoint/query con su ventana y el PRECOMMIT verifica integridad PK/FK/cursor/manifiesto; cualquier error rechaza la página correspondiente. Solo una respuesta íntegra y no vacía puede avanzar cursor. Ninguna respuesta archivada equivale a un aval de disponibilidad total del proveedor.

## Ejecución y recuperación

`plan_offline_onchain_catalog` crea el plan reproducible sin DB, red o cuota. `run_offline_onchain_catalog` valida **el manifiesto completo de respuestas in-memory** y su digest contra el catálogo antes de abrir SQLite, adquiere una única lease/flock y ejecuta shards de hasta 32 páginas, cada página con su transacción corta y `source_run` persistido. El primer 429/500/503, timeout, respuesta vacía o rechazada detiene las páginas siguientes y conserva receipts forenses; no se atribuyen falsos datos ni se agota silenciosamente una cuota externa.

Los intentos de reanudación del shard bloqueado deben seguir el mecanismo existente `plan_synthetic_resume`/`recover_synthetic_batch`, con el **plan exacto del shard y el run_id anterior**. El orquestador de catálogo no activa reintentos por su cuenta; no se afirma una reanudación integral automática de todos los shards.

## QA

`tests/unit/test_package_a_onchain_catalog_batch.py` comprueba el catálogo entero, digest determinista, orden round-robin, dos shards v3 32+4, 36 recibos/observaciones/cursors sobre SQLite sintética, presupuesto de 72 ventanas para 15 días y rechazo de truncamiento, fallos 429/timeout, respuesta falsa inválida al final del manifiesto, manipulación del digest y contención del lock antes de cualquier HTTP falso.

También prueba la preservación de valores fraccionales RBN, el campo de valor slug RBN, el rechazo de campos de tiempo ambiguos o métricas cruzadas y el índice Bitview truncado/desplazado frente a `null` explícitos. Una suite live queda deliberadamente excluida del runner portable.

**Pendiente externo:** acceso efectivo Tier2, contrato/esquema multidimensional RBN real, unidades/calendarios/retención Bitview, auditoría del host y rango total autorizado de descarga. Hermes seguirá detenido hasta la revisión offline integral con SHA fijado.
