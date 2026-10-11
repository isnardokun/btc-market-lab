# Paquete A — contrato offline de Yahoo OHLCV y sesiones diarias

**Estatus:** implementado únicamente contra peticiones Yahoo legadas simuladas
y respuestas HTTP congeladas. **NO** existe nueva descarga real, derecho
de acceso, retención histórica verificada, calendarización oficial ni
paginación API habilitada.

## Fuente soportada por el contrato sintético

`ingestion/package_a_contracts.py::SeriesSpec` admite las 5 métricas
`open, high, low, close, volume` sobre **BTC-USD, SPY y GLD**, cada una como
stream separado con PK
`provider×metric×symbol×frequency×observed_utc×dimension`.

- **Precios:** `unit=USD`, `raw_scale=native`.
- **Volumen:** `unit=native_volume`, `raw_scale=native`. No se supone USD,
  BTC ni `shares` sin verificar el contrato del símbolo.
- **Cadencia:** `d1`, tiempo normalizado a **00:00 UTC del día de la
  marca de sesión**. El parser legado conserva en su payload original
  tiempos como 13:30 UTC para mercados estadounidenses; el bridge
  `ingestion/package_a_legacy_bridge.py` contrasta la serie contra ese
  parser independiente, con la **misma normalización**. El RAW BLOB
  conserva el timestamp original byte por byte, y el recibo incluye
  SHA y request lineage.
- **Sesiones:** SPY y GLD **no** proyectan una barra para sábado, domingo
  o feriado. Sin calendario de exchange verificado, una fecha ausente no
  se etiqueta automáticamente como `missing data`. El manifiesto usa
  `YAHOO_EXCHANGE_SESSIONS` y
  `NOT_APPLICABLE_SOURCE_CALENDAR_OR_EVENTS`.
- **Contrato de respuesta:** arrays de `timestamp` y de la métrica
  solicitada deben tener idéntica longitud no nula; todos los demás
  campos OHLCV presentes deben mantener la misma longitud. Si hay
  precio válido, es estrictamente positivo; volumen válido, no
  negativo; máximo >= mínimo; apertura/cierre presentes pertenecen
  al intervalo [mínimo,máximo]. Una fecha UTC diaria duplicada,
  `NaN`, infinito, precio inconsistente, volumen negativo, hora
  fuera del rango solicitado o símbolo/unidad no admitido **se rechaza**.

## Integración y errores

`scripts/package_a_frozen_legacy_transport.py` invoca
`ingestion/yahoo_history.fetch_max` con **opener falso de un solo uso**,
sin socket real, conservando el request legado
`GET /v8/finance/chart/{symbol}?interval=1d&range=max`.

Una respuesta sintética bien formada pasa por
`raw→legacy bridge→strict parser→SQLite temporal→receipt/cursor→auditor`.
Cada métrica usa **su propio recibo y su propia llamada HTTP simulada**;
este slice **NO** implementa aún fan-out económico de cinco métricas
desde un único request. Los fallos de validación bajo HTTP 200 se
registran como **receipt `rejected` con RAW/SHA preservado**, sin
observaciones nuevas ni avance del cursor. Esto NO es una solicitud
completada, ni significa `upstream unavailable`.

**Importante:** el downloader heredado usa **`range=max`**.
`plan_offline_historical_windows` lo sigue dejando en
`BLOCKED_SOURCE_NATIVE_PAGINATION_UNVERIFIED`: no se fabrican
`period1/period2` ni supuestas páginas Yahoo que dicho cliente
no solicita. El siguiente trabajo, si se requiere paginación real,
debe introducir y verificar aparte un constructor nativo con límites
por intervalo y el calendario del mercado bajo permiso del propietario.

## Cobertura de regresión

`tests/unit/test_package_a_frozen_legacy_transport.py` añade **11 casos**
de Yahoo que verifican SPY OHLCV sobre 5 streams y recibos RAW independientes,
GLD y BTC-USD, preservación de dos días de mercado separados por el fin
de semana, coincidencia con el parser independiente, anomalías cross-field
de OHLC y volumen, denegación de símbolos ajenos al catálogo sintético,
no autorización de requests de paginación artificiales y RAW forense
para errores 200 rechazados.

El resultado local es `SOURCE_MANIFEST_LOCAL_EVIDENCE_PASS` y
`LOCAL_FIXTURE_INTEGRITY_PASS` cuando las pruebas son válidas;
**`historical_completeness=NOT_VERIFIED`** incluso si todos los
puntos de un fixture están presentes.
