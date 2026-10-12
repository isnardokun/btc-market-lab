# Paquete A — Yahoo OHLCV: una petición fuente y cinco streams atómicos

**Alcance: estrictamente OFFLINE.** Solo petición HTTP simulada mediante el
cliente legado de Yahoo y SQLite temporal con marcador. No hay llamadas a
Yahoo real, acceso a producción, cron ni descarga histórica real.

## Brecha cerrada

El módulo anterior admitía cinco métricas (open, high, low, close y volume)
sobre BTC-USD, SPY y GLD, pero requería cinco solicitudes separadas a la
misma URL Yahoo de rango máximo. Eso multiplicaba por cinco las llamadas
y podía dejar unas métricas ingresadas y otras incompletas si fallaban
transacciones individuales.

El nuevo módulo **scripts/package_a_yahoo_fanout.py** utiliza exactamente
una petición HTTP **falsa** del constructor legado
GET /v8/finance/chart/{symbol}?interval=1d&range=max,
sin paginación inventada ni red. Los cinco conjuntos se interpretan
con el parser estricto y se reconcilian independientemente con
ingestion/yahoo_history.extract_daily.

La URL literal del constructor instalado es
https://query1.finance.yahoo.com/v8/finance/chart/{symbol}
y el query contiene interval=1d y range=max. Esta URL es solo la
identidad que comprueba el mock: no se invoca un servidor real.

## Garantías transaccionales

El sistema obtiene el flock global cooperativo antes del HTTP falso. Tras
validar **la totalidad de los cinco campos**, abre una transacción
SQLite IMMEDIATE, guarda cinco recibos separados con el **mismo RAW/SHA**
y cinco registros de solicitud nativa. Cada stream conserva su unidad:
precios en USD, volumen native_volume sin conversión inventada.

Únicamente una vez almacenadas las cinco métricas, se ejecuta
**un solo gate PRECOMMIT** sobre toda la base del fixture: reconstrucción
RAW→SQL, integridad de SHA, coincidencia exacta de solicitud fuente nativa,
source runs, manifiesto de cobertura, FK y PRAGMA quick_check.
Si algún componente falla, toda la transacción se revierte.

- HTTP 429/500/503/timeout: cinco recibos failed, cero observaciones y
  cero cursores avanzados; se cuenta **una** petición falsa.
- JSON inválido, OHLC imposible, volumen negativo, valores incompletos o
  arrays incongruentes: cinco recibos rejected; la respuesta RAW se
  conserva para auditoría sin fingir que los datos quedaron aceptados.
- Ausencia total de datos: cinco recibos empty, estado
  YAHOO_FANOUT_EMPTY_UNVERIFIED y ningún cursor.
- Conflicto de observación histórica o caída simulada tras 2/4 métricas:
  **cero recibos nuevos**, todas las escrituras del grupo revertidas.
- Repetición idéntica: pueden guardarse recibos nuevos del intento, pero
  ninguna observación o cursor preexistente se sustituye.

Las cinco series deben contener exactamente las mismas fechas válidas
para considerarse un grupo completo. No se rellenan huecos de volumen
con ceros. El calendario externo NYSE no está comprobado, por lo que
ausencias de fines de semana y festivos no generan falsos gaps.

## Evidencia y limitaciones

Los cinco recibos tienen IDs propios y comparten SHA del RAW y de la
petición congelada. El resultado incluye un group ID determinista
**solo en memoria**, no persistido; esa correlación no constituye
prueba criptográfica de un fetch productivo. Tampoco se implementó
un esquema v4 para almacenamiento RAW deduplicado: se conserva un BLOB
por recibo, de modo que el auditor actual puede reconstruir cada stream.

Este cambio es una nueva ruta offline de fan-out. La ruta antigua
por métrica se mantiene compatible y su test suite continúa activa.
Yahoo real mantiene range=max: no se habilitan consultas por periodos
supuestos ni se certifica el primer día disponible históricamente.

## QA

**tests/unit/test_package_a_yahoo_fanout.py**: 19 escenarios E2E.
Verifica SPY 5 métricas/3 sesiones/1 HTTP, fin de semana, SPY+GLD+BTC-USD,
replay idempotente, conflicto de close, crash tras 2 o 4 métricas o
antes del PRECOMMIT, alto/bajo inválido, volumen ausente, arrays cortos,
HTTP 429 y 503+recovery, timeout sin RAW, respuesta totalmente vacía,
contención del candado antes de iniciar fake HTTP, símbolo/argumentos
fuera de contrato, JSON inválido byte-exacto y request provenance corrupta
que bloquea un segundo fan-out.

El runner se compone ahora de **70 suites registradas, 69 offline**
y una integración live explícitamente omitida. No se ha consultado
ningún endpoint real ni modificado el host. Queda pendiente el diseño
de descarga histórica autorizada y cualquier validación real de API.
