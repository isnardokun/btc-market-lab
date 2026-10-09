## Confirmación del usuario: ResearchBitcoin Tier 2 (2026-10-09)

La configuración efectiva comunicada por el propietario es **Tier 2**,
con capacidad documental declarada de 40.000.000 puntos/semana y
sin límite temporal del plan. El token privado no se validó en este PR.

Este proyecto debe planificar la recuperación histórica de las
13 series escalares ResearchBitcoin del catálogo desde 2009-01-01
(`--history --tier 2 --from 2009-01-01`), en lotes de hasta 14 días
por petición, con ventanas `empty` auditables. Las demás métricas Tier 2
deben incorporarse por familias de forma (scalar, binned, cohorte)
después de documentar endpoint, unidades, JSON y límites. No declarar
que las 348 métricas fueron descargadas mientras solo haya 13.

**Migración de esquema y provenance:** `docs/ARCHIVE_SCHEMA_TIER2.md`
tiene el modelo de tablas, WAL-safe backup y pasos locales obligatorios.

# Política obligatoria: archivo histórico integral de mercados y Bitcoin

**Regla operativa:** Mercados Daily Pro es simultáneamente un motor de
publicación diaria y una **base de investigación longitudinal**. El
histórico recuperable de cada API debe conservarse de manera persistente
en SQLite para consultas retrospectivas, análisis de ciclos y
comparaciones en la misma fecha. **Un reporte PASS no significa
histórico completo.** El reporte diario se alimenta del cierre reciente;
el archivo histórico debe mantener cobertura anterior e incremental.

## Inventario de proveedores e historia disponible

| API | Datos | Política / límites reales | Archivo local |
| --- | --- | --- | --- |
| Bitview | Series on-chain d1, además altura de bloque, matrices/URPD | Histórico según disponibilidad de la serie, endpoints de series; índices `height` requieren esquema propio | `series`/`daily` (d1); `historical_fetch_windows` |
| ResearchBitcoin V2 | 13 métricas d1 curadas, UTC | **Tier 0: último año, 55.000 DP/semana**; Tier 1/2 sin límite temporal según tabla, pero con cuotas y acceso del usuario; 422 por petición demasiado grande | `onchain_external_observations` |
| Yahoo Finance | BTC + símbolos del catálogo `ingest_instruments` | `1d&range=max`: toda la historia que responda Yahoo para ese símbolo, no garantiza desde creación del activo; intradía es otro límite | `price_btc` + `market_ohlc_history` |
| FRED | Todas las series fuente de `FRED_SERIES` | API `series/observations` soporta `observation_start`, `limit=100000`, `offset`. Revisiones/vintages son objeto de ALFRED adicional | `macro_fred` |
| Noticias RSS/Exa | Noticias/acontecimientos relevantes | Descubrimiento reciente no equivale a un archivo de noticias desde 2009; licencia, disponibilidad y retención por proveedor | `research_news_archive`: fuente, título, URL, fecha de captura; sin cuerpo |

**Límites explícitos:** no asegurar disponibilidad de historial pre-2014
en Yahoo BTC ni pre-1 año en RBN Tier 0, ni afirmar
cobertura total Bitview de `height`/URPD sin un adaptador propio.
No inventar fechas ni llenar ausencias con ceros. La coincidencia de
nombres no acredita equivalencia de metodologías entre proveedores.

Referencias oficiales:
- https://bitview.space/api
- https://api.researchbitcoin.net/faq/
- https://api.researchbitcoin.net/tier
- https://fred.stlouisfed.org/docs/api/fred/series_observations.html
- https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html

## Flujo requerido de Hermes (carga inicial y mantenimiento)

### 0. Salvaguardas

Operar únicamente dentro de
`/home/ignotus/btc-research` y en `master` verificado. Antes del
histórico, comprobar integridad y realizar respaldo SQLite con
`scripts/backup_db.py` conforme a su procedimiento; confirmar
espacio en disco y `PRAGMA integrity_check`. No compartir copias
SQLite ni datos brutos privados en GitHub. No ejecutar
`ingestion/ingest_all.py --backfill` (modo legacy deshabilitado por
no garantizar exhaustividad). No usar `git reset --hard`.

### 1. Cobertura inicial, SIN red

~~~bash
python3 scripts/history_coverage.py --output reports/history_coverage.json
python3 ingestion/researchbitcoin_v2.py --inventory
~~~

Revisar para cada fuente: conjunto de métricas, fecha UTC inicial y
final, recuento, vacíos documentados, frecuencias, permisos,
desfase y cobertura sobre la vida real de la métrica.
El archivo generado es local y no debe publicarse.

### 2. Bitview — reconstruir historia verificable, por tramos

~~~bash
# Plan. Primer lote sobre series de producción confirmadas
python3 ingestion/bitview_history.py --max-requests 8

# Ejecutar lotes en vivo, sin truncar ni recrear tablas
python3 ingestion/bitview_history.py --max-requests 8 --apply

# Para todas las series identificadas como índices diarios 1d:
python3 ingestion/bitview_history.py --all-daily --max-requests 8
python3 ingestion/bitview_history.py --all-daily --max-requests 8 --apply
~~~

Las peticiones son intervalos inclusivos de máximo 180 días por
defecto. Cada petición exitosa, **también respuesta vacía**, se registra
en `historical_fetch_windows`; repetir el comando continúa donde
terminó sin repetir automáticamente intervalos ya revisados.
El proceso de **todas** las series d1 debe continuar en sesiones
controladas hasta que `remaining_windows_before = 0`. Un 0 significa
que se intentaron todas las ventanas planeadas, **no** que el proveedor
tuvo datos en todas ellas: consultar estados `empty`.

Las series por altura de bloque y matrices/URPD no deben convertirse
a fechas artificiales; necesitan su propio plan de almacenamiento y
validación. Registrarlas como pendientes en el inventario.

### 3. ResearchBitcoin — totalidad realmente accesible al tier

~~~bash
# Solo plan, no usa cuota
python3 ingestion/researchbitcoin_archive.py --history --tier 0 --max-requests 13

# Ejecutar lotes acotados, con token local ya configurado
python3 ingestion/researchbitcoin_archive.py --history --tier 0 --max-requests 13 --apply

# Si el usuario tiene Tier 1 o 2, bajo confirmación real de cuenta,
# puede fijar fecha histórica inicial de forma explícita:
python3 ingestion/researchbitcoin_archive.py --history --tier 1 --from 2010-01-01 --max-requests 13
~~~

Repetir los lotes de Tier 0 hasta cubrir todas las fechas d1
disponibles en el año permitido; el proceso detecta días faltantes y
no reconsulta observaciones presentes. Las ventanas API respetan
`from_time` inclusivo, `to_time` exclusivo y máximo 14 días.
Solicitudes fallidas pueden gastar cuota: ante fallos detener y
consultar, no insistir. No cambiar de tier sin confirmación. La
completitud histórica de RBN Tier 0 **siempre es parcial desde la
perspectiva de la historia completa de Bitcoin**.

### 4. Yahoo — todos los símbolos explícitos del catálogo

~~~bash
python3 ingestion/yahoo_history.py --limit 4 --offset 0
python3 ingestion/yahoo_history.py --limit 4 --offset 0 --apply
python3 ingestion/yahoo_history.py --limit 4 --offset 4 --apply
~~~

Continuar incrementando `--offset` en lotes hasta cubrir el catálogo.
`market_ohlc_history` conserva OHLCV originales por símbolo y UTC;
`price_btc` y los informes previos no se sobreescriben.
No inferir los cierres de mercado no negociados (fines de semana)
como “datos faltantes” de una acción.

### 5. FRED — toda observación disponible de cada fuente configurada

~~~bash
python3 ingestion/ingest_fred.py --history
python3 ingestion/ingest_fred.py --history --apply
~~~

La descarga usa fecha inicial 1776-07-04 y hasta el corte
correspondiente, solo por series configuradas; no supone
observaciones anteriores al inicio real de cada una. Los datos
existentes se preservan mediante `INSERT OR IGNORE`, con
reconciliación habitual de revisiones. Los **vintages** ALFRED
históricos no están reconstruidos por este script; registrar ese
requisito como línea de investigación adicional.

### Noticias RSS/Exa — conservar fuentes desde ahora, sin atribuirles un pasado falso

Cada ejecución del reporte guarda en `research_news_archive` solamente
metadatos de descubrimiento: titular, URL HTTPS, proveedor, tipo de fuente,
fecha reportada, fecha UTC de captura y activo. No se archiva el cuerpo
completo ni material protegido por derechos de autor. La fuente RSS/Exa
puede no ofrecer históricos anteriores; solo declarar cobertura
desde el momento real de ingestión, sin reconstruir “noticias vistas”
en sesiones pasadas mediante un feed de hoy.

### 6. Mantenimiento incremental y reauditoría

El pipeline diario de Bitview/FRED/Yahoo original sigue siendo
autónomo. Investigación externa ResearchBitcoin es **opt-in**:
`RBN_INCREMENTAL_ENABLED=0` por defecto. Tras validar cuotas y
credenciales en Hermes se habilita expresamente:
`RBN_INCREMENTAL_ENABLED=1` con presupuesto por corrida
`RBN_MAX_REQUESTS=13`.

El paso RBN aparece **después de FRED y antes del reporte**:
falla de RBN = warning, no bloquea el reporte principal; solo se
muestran tarjetas que pasen lectura read-only y reconciliación
HTML/SQLite. **No cron de backfill masivo** ni peticiones de cuota
automáticas sin esta habilitación.

Tras cada lote histórico:
- actualizar `reports/history_coverage.json` local;
- revisar estados de ventanas y `fetched_at_utc`;
- verificar estabilidad del `publication_gate` y SHA256;
- preservar fechas reales, fuente y metodología de las observaciones;
- no publicar históricos operativos, tokens, recibos o informes por defecto.

## Consultas históricas (sin APIs ni cambios en SQLite)

La nueva CLI permite estudiar intervalos específicos de manera local
y aislada por fuente; responde valores crudos, fecha UTC y atribución.
No une series solo porque compartan nombre:

~~~bash
python3 scripts/history_query.py --provider bitview --metric mvrv --from 2024-01-01 --to 2024-12-31
python3 scripts/history_query.py --provider researchbitcoin --metric mvrv_sth --from 2026-09-01 --to 2026-10-08
python3 scripts/history_query.py --provider fred --metric DGS10 --from 2018-01-01 --to 2020-12-31
python3 scripts/history_query.py --provider yahoo --metric BTC-USD --from 2019-01-01 --to 2019-12-31
python3 scripts/history_query.py --provider news --metric BTC --from 2026-10-01 --to 2026-10-09
~~~

`--limit` recorta resultados (1..5000), marcando `truncated` si
se alcanza el límite. Los valores RBN se devuelven en **escala raw**
sin conversión implícita: cualquier comparación requiere declarar
la escala y método. No enviar resultados de la SQLite a GitHub.

## Definición verificable de completo

Para una métrica no basta con `COUNT(*) > 0`.
`COMPLETO_VERIFICADO` exige:
1. Enumeración de la **ventana accesible contractual** para el tier.
2. Consulta de toda ventana/serie pertinente de ese intervalo,
   paginada, con cuotas respetadas.
3. Reconciliación de respuestas `empty`/faltantes con soporte de
   la API antes de marcar cualquier segmento como realmente cerrado.
4. Cobertura por frecuencia y timestamps UTC, sin mezcla de
   series homónimas o unidades.
5. Comprobaciones de integridad, duplicados y resultados reanudables,
   sin borrado ni truncamiento.
6. Distinguir `COBERTURA_ACCESIBLE` de `HISTORIA_TOTAL_BITCOIN`.

Cuando alguna condición no pueda demostrarse, registrar
`PARCIAL`, `LÍMITE_TIER`, `FUENTE_NO_DISPONIBLE` o
`PENDIENTE_ADAPTADOR`; nunca etiquetar completo para ocultar lagunas.

## Control de cambios

Hermes descarga y audita localmente; ChatGPT corrige parsers y
contratos mediante PR y CI. La publicación de un informe y el
almacenamiento del histórico son flujos distintos, ambos trazables.
