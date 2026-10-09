# Arquitectura del archivo histórico — SQLite v1 / ResearchBitcoin Tier 2

**Estado:** diseño y código revisables por PR. La migración real se ejecuta
**solo en el equipo de Hermes**, con base SQLite privada y respaldo WAL-safe
verificado, nunca mediante el CI de GitHub.

## Objetivo contractual

El archivo debe conservar **todo el histórico recuperable y autorizado de
las APIs disponibles**, desde la primera observación que cada proveedor
entregue hasta el último período UTC completado. La cobertura descargada y
la cobertura existente en el proveedor son conceptos distintos: jamás
identificar `MAX(date)` con historia completa ni inventar observaciones.

Dos tareas separadas:
- **Backfill:** recuperar historia anterior o ventanas no confirmadas, por
  tramos y con checkpoint; reanudar sin duplicar datos.
- **Incremental:** captar cierres nuevos, revisar períodos recientes y
  retener cada corrección del proveedor en una auditoría append-only.

Las series de Bitview, ResearchBitcoin, Yahoo y FRED **no se mezclan
numéricamente** por tener nombres parecidos. Los indicadores derivados,
noticias, capturas HTML, modelos cuantitativos y observaciones crudas
se almacenan con linaje y plazos propios.

## Límites de proveedor / plan

- ResearchBitcoin **Tier 2**, informado por el titular (no verificado
  contra el token): Tier 0, 1 y 2; **40 millones de data points / semana,
  histórico sin límite temporal del plan**. Una métrica puede empezar años
  después de 2009, devolver ventanas `empty` o no existir en Tier 2.
  Data points se computan por valor numérico retornado, no por simple
  número de solicitudes: https://api.researchbitcoin.net/tier
- ResearchBitcoin V2: `resolution=d1`, `from_time` inclusivo,
  `to_time` exclusivo, UTC. El cliente actual limita cada solicitud
  **a 14 días y a una métrica escalar**. Nunca estimar la cuota real de
  respuestas con múltiples bins usando esta regla.
- Bitview: el proveedor usa índices `1d` y `height` separados; el
  backfill d1 no inventa timestamps para los datos por bloque. Mantener
  tiempos UTC y valores originales `daily`.
- FRED: series/observations v1 tiene `limit` y `offset`, con
  revisiones históricas (ALFRED). La tabla `macro_fred` representa
  valores almacenados y revisados; `archive_fred_vintages` conserva
  **vintages capturados desde la migración**. No afirma haber recuperado
  retroactivamente todos los vintages ALFRED.
  https://fred.stlouisfed.org/docs/api/fred/series_observations.html
- Yahoo Finance: OHLCV históricos por símbolo en
  `market_ohlc_history`; `price_btc` se conserva intacta y se
  sigue usando para el informe actual.

## Tablas de datos existentes (no destructivas)

| Dataset | PK/logical key | Observación |
|---|---|---|
| `series` / `daily` | `series.id`; `(series_id,ts)` | bitview: potencialmente millones de filas, raw |
| `onchain_external_observations` | `(provider,metric,observed_date)` | ResearchBitcoin: valor raw, fuente, fecha UTC |
| `macro_fred` | `(series_id,date)` según esquema existente | Valores actuales/reconciliados FRED |
| `price_btc` | según esquema existente | BTC Yahoo, archivo legado |
| `market_ohlc_history` | `(provider,symbol,ts)` | OHLCV diario histórico Yahoo |
| `daily_metrics` | `(report_date,asset,metric)` | snapshot normalizado del análisis |
| `reports` | `id` | informe, versión, fecha y snapshot |
| `research_news_archive` | clave existente de noticia | Noticias con procedencia |

No ejecutar `DROP TABLE`, `VACUUM`, cambios masivos de IDs o
reescritura de valores para migrar. La tabla antigua
`historical_fetch_windows` se mantiene por compatibilidad con Bitview.

## Tablas nuevas `archive_*` (migración aditiva v1)

| Tabla | Clave | Función |
|---|---|---|
| `archive_schema_migrations` | `version` | versión aplicada y fecha |
| `archive_sources` | `source_id` | proveedores, Tier 2 reportado / no verificado |
| `archive_datasets` | `(source_id,metric)` | frecuencia, unidad, escala raw, primer período solicitado |
| `archive_ingest_runs` | `run_id` | operación, estado, solicitudes, puntos y horas UTC |
| `archive_ingest_windows` | `(source_id,metric,start_utc,end_exclusive_utc)` | checkpoint y resultado (`ok/empty/partial/failed`) |
| `archive_observation_revisions` | `revision_id` | versiones previas y nuevas cuando cambian RBN, Bitview, FRED, Yahoo OHLC close |
| `archive_fred_vintages` | `(series_id,observed_date,realtime_start)` | vintages capturados con fechas reales reportadas por FRED |

Las relaciones del catálogo de archivo son complementarias: no fuerzan
reemplazar las PK de tablas antiguas de millones de registros. Índices
nuevos centrados en source/metric/cobertura, revisiones y fechas.
Los triggers solo conservan revisiones **futuras** y evitan insertar
cambios falsos cuando el valor no varía.

### Advertencias de reproducibilidad

- Una consulta `as-of` se puede reconstruir de forma íntegra solo con
  observaciones, revisiones y snapshots efectivamente archivados.
- ALFRED puede permitir recuperar valores conocidos en fechas anteriores:
  hace falta una ingesta específica de vintages históricos para completar
  la retrospectiva. `archive_fred_vintages` ya admite almacenarlos, pero
  no se debe declarar completo ese proceso antes de realizarlo.
- Una ventana RBN `empty` significa que **el proveedor respondió sin
  observaciones**. No representa precio cero, falta técnica validada
  universal ni inexistencia permanente. Los checkpoints pueden ser
  revisados selectivamente y el último tramo debe reconfirmarse.
- El sistema registra `entitlement_verified=0` hasta una comprobación
  autenticada; el usuario confirma que dispone de Tier 2.

## Migración segura en Hermes

~~~bash
cd /home/ignotus/btc-research
git status --short
bash scripts/update_local_and_test.sh

# Sólo lectura:
python3 scripts/archive_db_migrate.py

# Aplicar únicamente en el PC local, con SQLite existente y espacio
# para backup WAL-safe; crear respaldo verificado y schema v1:
python3 scripts/archive_db_migrate.py --apply

# Consultar tablas, cobertura y revisiones sin exportar la BD:
python3 scripts/history_coverage.py --output reports/history_coverage.json
~~~

No subir `db/`, `reports/history_coverage.json`, tokens,
respuestas privadas de API, recibos ni HTML público.

## Backfill Tier 2: primero PLAN, luego por lotes

~~~bash
# No requiere credenciales ni accede a la red, sólo plan:
python3 ingestion/researchbitcoin_archive.py --history --tier 2 \
  --from 2009-01-01 --max-requests 13

# Tras la migración y autorización del operador, ingesta 13 peticiones
# como máximo en la sesión; repetir el comando para avanzar:
python3 ingestion/researchbitcoin_archive.py --history --tier 2 \
  --from 2009-01-01 --max-requests 13 --apply

# Incremental (una ejecución supervisada, fuera del cron):
python3 ingestion/researchbitcoin_archive.py --incremental \
  --max-requests 13 --apply
~~~

**El runner diario** `scripts/daily.sh` no hace un backfill masivo.
La sincronización RBN sigue opt-in, con dataset y cuota controlados.
Las métricas sin datos, API bloqueada, escala cambiante o cuota insuficiente
deben generar estado explícito, no forzar datos ni modificar el gate.

## Continuidad de las demás fuentes

- Bitview: `python3 ingestion/bitview_history.py --all-daily --max-requests 8`
  (PLAN), `--apply` solo tras backup y validación de respuesta; los
  índices por bloque se tratan por un proceso aparte sin timestamps
  aproximados.
- FRED: `python3 ingestion/ingest_fred.py --history` (PLAN) y
  `--history --apply` para descargar valores históricos actuales; las
  revisiones FRED posteriores a la migración se preservan.
- Yahoo: `python3 ingestion/yahoo_history.py --limit 4` (PLAN) y
  `--apply` para OHLCV histórico por símbolo.
- RSS/Exa: archivo de noticias y enlaces ya operativos; su historia
  recuperable depende de archivo del proveedor y derechos de uso.
- `scripts/history_query.py`: consultas por proveedor, métrica y rango.
- `scripts/history_coverage.py`: mínimos, máximos y conteos locales,
  checkpoints y revisiones, sin afirmar cobertura remota total.

## QA / criterios de aceptación

1. Antes y después, comparar conteos, claves y `PRAGMA integrity_check`
   del original y copia. Los triggers no cambian filas existentes.
2. Cargar dos veces un tramo: sin duplicar observaciones ni checkpoints.
3. Si proveedor revisa un valor, conservar el anterior y el nuevo con
   fecha UTC y fuente.
4. No modificar el valor raw o unidad para satisfacer el HTML.
5. Para un `--history --tier 2`, recorrer 2009→último cierre, incluir
   intervalos realmente vacíos y reanudar sin repetir ventanas terminadas.
6. Para modo plan, no llamadas HTTP ni escrituras.
7. 15+ suites y CI, luego pipeline local Hermes sin Telegram y con gate.
8. El informe diario y el skill `mercados-research-design` continúan
   desacoplados del backfill pesado.

Fuentes: documentación oficial ResearchBitcoin Tier 2 y FRED/ALFRED,
más el esquema y adaptadores exactos del repositorio.
