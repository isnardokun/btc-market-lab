# Paquete A — Funding histórico basado en eventos, no en velas artificiales

**Alcance:** solo integración **offline**, HTTP completamente congelado y SQLite
temporal con marcador explícito. La política descrita aquí no constituye una
consulta ni certificación de disponibilidad, entitlement, quota, retención,
frecuencia ni paginación real de Binance o Bybit.

## Contrato nativo integrado

`scripts/package_a_historical_window_planner.py` ahora permite
`SeriesSpec(provider="binance"|"bybit", metric="funding_settled",
symbol="BTCUSDT", frequency="event", unit="fraction", raw_scale="native")`.

- Divide el intervalo UTC **[inicio, fin)** en hasta 32 ventanas contiguas
  de **7 días calendario** por solicitud sintética. Siete días es un
  **presupuesto interno conservador**, NO el máximo de la API.
- `native_slots=None`, `requested_native_slots=None` y
  `source_calendar_observation_expectation=NOT_INFERRED_FUNDING_SETTLEMENT_EVENTS`.
  Nunca se asume que exista un evento por 8 horas, ni que los horarios o el
  número de liquidaciones permanezcan constantes en el tiempo.
- `_request_for` deriva las solicitudes originales existentes:
  Binance `/fapi/v1/fundingRate` con `limit=1000`, y Bybit
  `/v5/market/funding/history` con `limit=200`,
  `category=linear`. Los límites describen exactamente **los parámetros
  de nuestros constructores**; NO se afirma que estén verificados como
  límites efectivos de los servidores.
- El parámetro `startTime` usa el comienzo UTC en milisegundos;
  `endTime` usa el **milisegundo anterior** a la siguiente ventana
  exclusiva. Evita duplicar eventos de frontera entre peticiones locales.
  La solicitud produce un digest SHA-256 de su constructor nativo, que
  contribuye al digest determinista del plan; nunca habilita red.

## Prevención de truncamiento silencioso

Si el resultado HTTP 200 contiene **1000 o más** filas de funding de Binance
o **200 o más** de Bybit, `parse_fixture` devuelve
`BLOCKED_FUNDING_EVENT_RESULT_SATURATED`, porque el resultado **podría
haber sido truncado** por el límite de registros consultados.

Este rechazo deja un `package_a_receipts` con `status=rejected`,
`http_status=200`, RAW exacto y SHA de los bytes. **No** inserta una parte
de la historia de esa página ni mueve el cursor. Se evita convertir una
respuesta aparentemente exitosa pero potencialmente incompleta en cobertura
verificada. Resolver una ventana saturada requeriría **dividirla con un nuevo
plan** y conservar sus recibos originales; no se aumenta arbitrariamente
el límite ni se hace fetch real sin permiso.

Incluso una respuesta con menos filas que el límite solo prueba **datos
recibidos localmente**; no acredita la disponibilidad de toda la historia.
Una ventana sin eventos produce `PAGE_EMPTY_UNVERIFIED`, no un relleno
automático ni una declaración de historial completo.

## Transacciones y recuperación

`run_offline_planned_history` usa la misma integración fuente:
plan→constructor nativo→HTTP falso→RAW/SHA→normalización→recibos/SQLite
v3→cursors→auditoría independiente, con bloqueo global cooperativo.

- Una respuesta `429`, `503`, timeout o `rejected` detiene el lote
  antes de ejecutar la siguiente petición falsa.
- El último `source_run` conserva el fallo y la página RAW previa; la
  recuperación `recover_offline_planned_history` valida el digest de
  páginas confirmadas y crea **un nuevo run** desde el último fallo o
  primer ordinal no almacenado.
- El resultado de lote ahora expone `pages[].status` y
  `pages[].failure_class` para distinguir explícitamente fallo HTTP,
  timeout y HTTP 200 con payload rechazado, sin leer una base de datos
  productiva ni inferir el motivo a partir de la falta de observaciones.
- El manifiesto fuente clasifica funding como
  `FUNDING_SETTLEMENT_EVENTS`; siempre
  `local_grid_state=NOT_APPLICABLE_SOURCE_CALENDAR_OR_EVENTS` y sin
  supuestas barras o candidatos de huecos de 8 horas.

## QA sintético

`tests/unit/test_package_a_historical_window_planner.py` incluye 9
casos nuevos de funding: partición 7+7+1, query nativa con límites y
fronteras exactas, E2E multiproveedor sobre la misma SQLite, recuperación
429 Binance y 503 Bybit sin refrescar páginas confirmadas, rechazo de
respuestas saturadas (ambos proveedores), respuesta `empty` sin cursor,
33 páginas bloqueadas *antes* de tocar SQL, y ausencia de falsos huecos
de ocho horas.

La ejecución permitida es exclusivamente el runner portable que utiliza
fixtures `TemporaryDirectory`, HTTP en memoria y una integración legacy
potencialmente productiva **omitida expresamente**.

## Requisitos externos todavía pendientes

Para usar datos reales deben validarse por proveedor, bajo autorización:
endpoints efectivos, parámetros de paginación, retención histórica,
`fundingInterval` por contrato/período, políticas `429` y costes/cuotas.
También hay que verificar la exclusión efectiva de todos los escritores,
respaldo/migración WAL-safe e implementar un piloto acotado. **Nada de este
código autoriza APIs reales, producción, cron o publicación.**
