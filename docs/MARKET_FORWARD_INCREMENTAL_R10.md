# R10 — Contrato de ingesta incremental hacia adelante (DISEÑO, SIN EJECUCIÓN)

**Estado: diseño por revisar.** Este archivo es una especificación técnica, NO
autoriza `--apply`, migración, tráfico a mercados ni programación de cron.
La SQLite real está en el host privado de Hermes; CI solo verifica código
y fixtures. No se suben respaldos, JSON completos de APIs ni credenciales.

## 1. Hechos comprobados hasta R9

Según evidencia comunicada por Hermes en el PR #23,
`R9-BYBIT-OI` (#6094438229):

| Serie `BTCUSDT` | Registros | Mínimo UTC | Máximo UTC | Cadencia |
| --- | ---: | --- | --- | --- |
| Binance `open_interest` | 8.589 | 2026-09-10 05:05 | 2026-10-10 00:45 | 5m exacta |
| Bybit `open_interest` | 17.600 | 2026-08-09 22:10 | 2026-10-10 00:45 | 5m exacta |
| Binance `funding_settled` | 635 | 2026-03-12 16:00 | 2026-10-10 00:00 | 8h observado |
| Bybit `funding_settled` | 400 | 2026-05-30 00:00 | 2026-10-10 00:00 | 8h observado |

Totales reportados: `market_derivatives=27.224`,
`market_raw_payloads=111`, `market_source_runs=114`,
`market_request_lineage=102` y `market_source_cursors=4`.
`raw=0`, `temporal=0`, `lineage=0`,
`PASS_REQUEST_LINEAGE 102/102`, `strict=1` por ETF/calendario.

**Toda cobertura se etiqueta `NOT_VERIFIED` hasta evaluar los
límites efectivos del proveedor.** Binance OI en particular
mantiene una ventana móvil limitada (~30 días), y el histórico
antes de la ventana observada no puede fabricarse.

## 2. Problema actual: backfill backward ≠ incremental forward

- `scripts/market_history_pilot.py` calcula
  `endTime=MIN(observed_utc)-1ms` y exige la última barra fuente
  precisamente 5 minutos antes del mínimo de SQLite.
- `scripts/market_history_batch.py` ejecuta esa operación hacia
  atrás, y `market_request_lineage_audit.py` verifica el límite
  **backward**. Reutilizarlos como ingestor forward sin cambio
  de contrato daría auditorías engañosas o fallo técnico.
- `scripts/market_context_ingest.py` / `fetch_binance` /
  `fetch_bybit` ya consultan snapshots públicos, pero no
  generan la misma trazabilidad estructurada por request v2,
  ni comprueban una frontera forward estricta antes del commit.
  No autorizarlos como "solución provisional" sin validación.
- Los cursores `history_backward_v1_*` son checkpoints
  **exclusivamente hacia atrás**. Prohibido reescribirlos con el
  `MAX` de la serie para perseguir datos recientes.

## 3. Diseño SQL propuesto — migración aditiva v3

Antes de modificar producción, crear PR aislada con pruebas de
v1→v2→v3, v2→v3, nueva instalación v3 e idempotencia. La migración
será opt-in, después de **respaldo WAL-safe verificado**, nunca
implícita en cron. No alterar observaciones, BLOB, run IDs ni
cursores existentes. El modelo propuesto:

```sql
-- DDL orientativo: verificar compatibilidad SQLite y checks en CI.
ALTER TABLE market_request_lineage
  ADD COLUMN direction TEXT NOT NULL DEFAULT 'backward'
  CHECK(direction IN ('backward','forward'));
ALTER TABLE market_request_lineage
  ADD COLUMN requested_start_ms INTEGER;
-- La versión 3 se marca solo después de schema+integridad+FK.
```

La dirección `backward` es **históricamente correcta para los
102 recibos v2** capturados con el piloto. Nunca inventar
`requested_start_ms` retroactivo; permanece `NULL` para backward.
Forward exige `requested_start_ms` entero, `requested_end_ms`
entero, `start<=end`, `limit` validado, URI sin query/secretos,
`attempted_utc`, BLOB SHA, `run_id`, `first/last` UTC,
`returned_rows/persisted_rows` y `status`.

**Por investigar antes de cerrar DDL:** `market_raw_payloads`
deduplica solo por SHA256 del cuerpo; si dos endpoints/devices
devuelven el mismo cuerpo (por ejemplo `[]`), el primer
`provider/endpoint` queda asociado al SHA y una auditoría
multifuente puede atribuir mal una adquisición posterior.
Probar este caso y, si procede, introducir una tabla aditiva
de **adquisiciones request→SHA→provider/endpoint**, sin modificar
la identidad content-addressed de los BLOB existentes. No
eliminar ni corregir artificialmente raw ya archivados.

El nuevo checkpoint forward será independiente, por ejemplo
`(provider, 'forward_v1_open_interest')`, y se comparará siempre
con `MAX(observed_utc)`, nunca con el checkpoint backward.
`market_source_cursors` no se modifica en el diseño;
su número final aumentará solo si se instala y usa la vía
forward tras autorización explícita.

## 4. Algoritmo forward exigido: OI de cinco minutos

Implementar **un piloto offline testeable por dependencia inyectada**
y un CLI `PLAN_ONLY` por defecto; en esta etapa no activarlo.

1. Rechazar DB ausente, esquema v3 ausente o `MIN/MAX`
   inconsistentes. Leer `MAX` de SQLite para proveedor,
   BTCUSDT, `open_interest`, `5m`. El siguiente timestamp
   **exacto** es `MAX+300.000ms`.
2. Calcular un `cutoff` UTC basado en barras **ya cerradas**,
   dejando un margen explícito de seguridad (por ejemplo ≥10m)
   frente a la hora UTC real. Nunca almacenar la barra
   parcial actual ni marcas futuras. El cutoff no procede
   de una respuesta no verificada.
3. Construir páginas acotadas por `startTime=next_slot`,
   `endTime=min(startTime+(limit-1)*300000,cutoff)`,
   `limit<=500` Binance y `<=200` Bybit; `period=5m`
   o `intervalTime=5min` según el proveedor.
4. Validar respuesta `BTCUSDT`/linear, tipo de payload,
   `retCode`, rango timestamps, alineación a :00/:05,
   ausencia de duplicados, valores numéricos finitos,
   secuencia temporal exacta desde `next_slot`;
   cualquier hueco/discontinuidad/valor anómalo produce
   **STOP sin avanzar el checkpoint**.
5. No usar upsert para esconder un solapamiento o corrección
   inesperados: forward escribe solo marcas `>MAX`
   verificadas. La corrección de valores existentes es
   un flujo separado y auditado con historial de revisiones.
6. Archivar en **una transacción**: fuente BLOB original,
   filas nuevas, run, receipt v3 `direction='forward'`
   y checkpoint forward (si hubo progreso). Peticiones
   fallidas guardan el recibo de fallo de forma segura,
   sin avances ni datos parciales.
7. Después de **cada página**, exigir validaciones
   read-only RAW/TEMPORAL/LINEAGE. Extender
   `market_request_lineage_audit.py` para que la rama
   forward verifique `start/end/limit`, uniones exactas
   en `MAX`, cadence, BLOB SHA y filas. Mantener
   **intactos los criterios backward** para los 102
   recibos anteriores. Un `empty` o `timeout` no
   demuestra completitud de la API.
8. Separar modo de recuperar huecos recientes de
   **backfill backward histórico**. Una ejecución no
   mueve dos fronteras en sentidos opuestos.

Para comenzar solo serán candidatos Binance OI y Bybit OI.
Funding no debe imponerse a 8h fijo: puede variar
por instrumento y contrato; requiere política específica
y validación de metadata de funding. ETF/calendario,
FRED y modelos analíticos quedan en otras fases.

## 5. Riesgos de operación que deben cubrir los tests

| Situación | Comportamiento obligatorio |
| --- | --- |
| API tarda en publicar la próxima barra | no avanzar; estado explícito; sin completar huecos |
| Servidor devuelve 200 filas duplicadas / desordenadas | ordenar para validar, rechazar timestamps duplicados |
| Baras saltadas o faltantes | STOP, rollback por página y evidencia |
| Respuesta `empty` o body válido idéntico de otra fuente | conservar provenance de adquisición, no atribución falsa |
| HTTP 403/429/451 y errores TLS/DNS | sin retry automático, guardar error clasificado |
| Error después de archivar BLOB pero antes del cursor | rollback de toda página |
| Repetición del mismo run tras caída | idempotente por timestamp, no duplicar BLOB, runs ni revisiones |
| UTC límite en cambio de día / reloj atrasado | corte UTC, nunca barras parciales ni futuras |
| Migración de producción v2 a v3 | backup WAL-safe, integridad/FK, 102 recibos antiguos invariables |
| Cron simultáneo / otro escritor de ingesta | bloqueo de exclusión mutua; nunca mezclar transacciones |
| Aumento de ingestas | mantener `raw_sha256`, `run_id` y quality gates |
| Pruebas/CI | solo fixtures offline, 0 HTTP público y 0 escritura a DB de producción |

## 6. Hitos de implementación (NINGUNO autorizado todavía)

**R10-A — Arquitectura y pruebas:** inventario read-only de
freshness y huecos desde el último `MAX`; contrato de datos
v3, diagrama de tablas, estrategia de reintentos (prohibidos
inicialmente), riesgos de blobs idénticos, tests de compatibilidad
retroactiva y de borde UTC. Entregar PR **sin `--apply`**.

**R10-B — Implementación y CI:** desarrollar migración v3,
piloto forward con modo PLAN sin red, auditor mixed-direction
y pruebas offline. Revisar PR y CI, luego autorizar merge.

**R10-C — Instalación opt-in:** migrar **solo esquema**, después
de WAL-safe backup y validación PRE/POST de conteos y auditorías;
SIN HTTP. Parar para revisión.

**R10-D — Primer piloto forward:** una sola página de un único
proveedor, previa autorización específica, batch auditado,
parar y revisar. Luego ampliar de forma prudente.

**R10-E — Scheduler:** solo tras series suficientes, auditorías
estables y controles de exclusión; cron no se habilita por
probar una página. El reporte y Telegram tienen puertas de
publicación independientes y permanecen desactivados.

## 7. Invariantes y puertas de publicación

- `historical_completeness=NOT_VERIFIED` hasta
  auditoría documentada de ventana/límites por proveedor.
- `PASS_REQUEST_LINEAGE` exige verificar **todos**
  los recibos: backward y forward; jamás tolerar solo
  la última página si anteriores fallan.
- Mantener `raw/temporal/lineage` independientemente
  del score editorial o de la calidad visual del informe.
- No iniciar consumo de métricas nuevas por el
  Report Builder hasta definir freshness, unidad
  y fallbacks no engañosos por fuente.
- Si las observaciones frescas no están disponibles,
  publicar `STALE/UNAVAILABLE` y no números inventados.
- Ninguna orden R10 permite iniciar cron, Telegram ni
  operar capital/trading.

**Documento de diseño: no autoriza ejecutar, migrar,
reintentar ni solicitar APIs externas.**
