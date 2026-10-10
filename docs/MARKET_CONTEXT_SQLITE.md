# Mercado institucional y derivados — ingestión SQLite v1

## Contrato fundamental

**Único almacén de datos de mercado:**
`/home/ignotus/btc-research/db/btc_research.db`. No almacenar
observaciones, respuestas API, historiales, calendarios ni revisiones en
CSV/JSON/archivos paralelos. La base usa migración aditiva, tabla de
respuestas originales como BLOB, índices e integridad de revisiones.
Los HTML de presentación y logs son artefactos operativos, **no un segundo
repositorio de series financieras**.

No se mezclan ETF flows con AUM, open interest con volumen,
ni funding liquidado con predicción de la próxima liquidación.

## Fuentes realmente implementadas

| Proveedor | Variables | Acceso | Limitación |
|---|---|---|---|
| Binance USD-M | BTCUSDT openInterestHist 5m y fundingRate liquidado | API pública sin token | Estadísticas históricas de OI ~30 días; límite de páginas y frecuencia |
| Bybit V5 linear | BTCUSDT OI 5m, funding/history | API pública sin token | Cursor/paginación; unidades OI en BTC, no en USD |
| Farside Investors | Flujos diarios por ETF + total publicado, millones USD | HTML público estructurado | Sin API oficial declarada; parsing sujeto a estructura/acceso/licencia; dato provisional |
| BLS | Calendario de publicaciones macro oficiales | iCalendar HTTPS | Hora solo cuando TZ explícita, nunca inferida |
| FRED | Fechas de publicaciones económicas | API con token FRED existente | Fechas sin hora; no aporta consenso ni valor de dato; primeros 1000 registros/consulta |

**No se incluyen todavía:** fuente homogénea OKX, holdings oficiales
ETF por emisor, datos CME, calendario FOMC detallado, calendario BEA
independiente, consensos de analistas, cifras de sorpresa económica.
No afirmar cobertura completa para estos módulos. Están previstos
para iteraciones y validaciones por separado.

## Modelo SQLite

La migración v1 añade solo objetos nuevos, sin modificar tablas previas:

- `market_context_migrations`: control de versión.
- `market_source_runs`: ejecución/fallos/recuento por proveedor.
- `market_source_cursors`: checkpoint persistente de backfill por
  proveedor/serie. No reinicia el histórico desde el origen en cada intento.
- `market_raw_payloads`: todas las respuestas fuente aceptadas,
  BLOB original, SHA256, endpoint limpio, fuente, hora UTC y formato;
  no guarda API keys en URL.
- `market_etf_flows`: ticker, fecha, valor USD millones, estado,
  fecha de captura y SHA de fuente. `TOTAL` es la cifra declarada
  por Farside, independiente de valores parciales por fondo.
- `market_derivatives`: fuente/instrumento, tipo, UTC, intervalo,
  valor bruto, unidad (BTC, USD, fracción), valor nocional original
  del proveedor si existe, endpoint y SHA. **Atención:** la columna heredada
  `quote_usd` conserva literalmente `sumOpenInterestValue` de Binance;
  su nombre NO demuestra denominación USD fiat. No rotularlo como USD
  verificado ni sumar nocionales entre venues sin documentar conversión.
- `market_calendar_events`: identificador por emisor, UTC verificado,
  precisión de fecha/hora, estado, fecha de evento, fuente y SHA.
- `market_context_revisions`: copia anterior y nueva JSON dentro de
  la propia SQLite ante corrección de un dato semánticamente distinto.

**Reglas:** fuente y unidad obligatoria; errores de red sin convertir
en ceros; BLOB crudo original para auditoría; entradas idempotentes;
actualizaciones reales generan revisión append-only; cambios de
HTML/JSON contenedor sin cambio de cifra no generan revisión ficticia;
NO se suman OI de exchanges hasta normalizar unidades, nocionales,
tipo de contrato y fecha compatible.

Las series históricas no se consideran completas por existir las tablas.
El histórico de funding inicial se limita a 45 días si es ingesta normal;
backfill histórico explícito con `--history` y cursores persistidos.
BLS/FRED traen las fechas publicadas disponibles en cada consulta
(FRED devuelve página acotada); Farside, la tabla pública que realmente
entrega al momento de la extracción. No inventar periodos anteriores.

## Instalación local — SOLO Hermes y con respaldo

No he ejecutado comandos en `/home/ignotus/btc-research`: la SQLite
privada está en el equipo del usuario. Tras actualizar `master` con
árbol limpio y usar el actualizador seguro:

```bash
cd /home/ignotus/btc-research
bash scripts/update_local_and_test.sh
python3 scripts/market_context_migrate.py
python3 scripts/market_context_migrate.py --apply
python3 scripts/market_context_ingest.py
python3 -m unittest -v tests.unit.test_market_context_sqlite
python3 tests/run_all.py
```

El primer comando migratorio **solo PLAN** y el `--apply` hace
respaldo SQLite WAL-safe e `integrity_check` antes de añadir tablas.
Si el respaldo falla, ninguna migración debe continuar.
La ingesta sin `--apply` no hace HTTP ni escribe SQLite.

Ingesta fuente por fuente, evitando ráfagas:

```bash
python3 scripts/market_context_ingest.py --apply --sources binance,bybit --max-pages 2
python3 scripts/market_context_ingest.py --apply --sources bls,fred
python3 scripts/market_context_ingest.py --apply --sources farside
```

Farside: **verificar antes** condiciones de uso, robots, estabilidad de la
tabla y permisos de redistribución, y desactivar si no es compatible.
El scraper no introduce bypass de login/pago, proxy o antibot.
La fuente de consulta diaria debe ser la SQLite, no la página externa.

Para backfill histórico **supervisado** (no en cron diario):

```bash
python3 scripts/market_context_ingest.py --apply --history --sources binance,bybit --max-pages 2
```

Repetir controladamente solo tras revisar cuota, intervalos, últimos
registros y cursores. En Binance OI no se promete superar la ventana
histórica del proveedor. En Bybit, no afirmar disponibilidad total
sin auditar la primera/última observación de cada instrumento.

Después de verificar pruebas y que los datos reales no están vacíos,
activar el refresco DIARIO como opt-in en el entorno de Hermes:

```bash
export MARKET_CONTEXT_DAILY_ENABLED=1
SEND_TELEGRAM_AUTO=0 bash scripts/daily.sh
```

El pipeline trata esta nueva fuente como complementaria (fallas parciales
no rompen el reporte base). Solo renderiza derivados con antigüedad
máxima de 36 h y ETF con fecha de máximo siete días de antigüedad,
siempre mostrando su corte y carácter provisional. Calendarios:
horizonte de 14 días; nunca inventar horarios a partir de fechas
FRED.

**No** activar `SEND_TELEGRAM_AUTO`, nuevas tareas cron cada cinco minutos,
scrapers adicionales o backfill masivo sin consentimiento explícito.
La configuración del job diario no crea programación intradía: para
OI 5m la frecuencia real de observación requiere cron autorizado y
medición de cuota.

## Verificación mínima de integridad

```sql
SELECT provider,status,requests,points,started_utc
FROM market_source_runs ORDER BY started_utc DESC LIMIT 15;
SELECT provider,MIN(trade_date),MAX(trade_date),COUNT(*)
FROM market_etf_flows GROUP BY provider;
SELECT provider,metric,raw_unit,MIN(observed_utc),MAX(observed_utc),COUNT(*)
FROM market_derivatives GROUP BY provider,metric,raw_unit;
SELECT provider,MIN(event_date),MAX(event_date),COUNT(*)
FROM market_calendar_events GROUP BY provider;
SELECT table_name,COUNT(*) FROM market_context_revisions GROUP BY table_name;
SELECT COUNT(*) FROM market_raw_payloads;
PRAGMA integrity_check;
```

Verificar cada campo del HTML contra estas tablas y SHA de su payload.
Una respuesta de API 200 y un gate 100 no acreditan automáticamente
cobertura global, recálculo de precios, exactitud externa del proveedor
o licencia de redistribución.

## Documentación oficial

- Binance USD-M OI: https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest-Statistics
- Binance Funding: https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History
- Bybit OI: https://bybit-exchange.github.io/docs/v5/market/open-interest
- Bybit Funding: https://bybit-exchange.github.io/docs/v5/market/history-fund-rate
- Farside: https://farside.co.uk/bitcoin-etf-flow-all-data/
- BLS ICS: https://www.bls.gov/schedule/news_release/bls.ics
- FRED releases/dates: https://fred.stlouisfed.org/docs/api/fred/releases_dates.html

Fecha del diseño: 2026-10-09. Revisar contratos y cuotas cuando los
proveedores publiquen nuevas versiones.


## Control de envío a Telegram tras incidentes #11 y #12

`scripts/send_report.py` bloquea cualquier envío real —incluyendo
invocaciones manuales y `--force`— salvo que la variable de entorno
`REPORT_SEND_APPROVED_SHA256` coincida exactamente con el SHA-256 del
HTML portátil ya aprobado. La coincidencia es por **archivo específico**,
no un permiso permanente para otro reporte. `--dry-run` valida
únicamente el archivo sin contactar Telegram.

Durante diagnósticos no establecer `REPORT_SEND_APPROVED_SHA256`,
mantener `SEND_TELEGRAM_AUTO=0` y no enviar documentos manualmente.
El bot ya no puede transmitir solo porque el entorno tenga un token y
un identificador de chat. Cuando el usuario autorice una publicación
concreta, el operador debe documentar esa autorización, confirmar el
SHA y configurar la variable **solo para ese envío**; limpiar el
permiso después.

**La actualización de código del PR no supone consentimiento para
publicar reportes por Telegram.** No cambiar variables, cron ni
recibos de envíos previos para aparentar cumplimiento.


## Validación directa de respuestas originales contra SQLite

Con el módulo de mercado instalado y observaciones reales de Binance/Bybit,
ejecutar, **sin hacer peticiones HTTP ni escrituras**:

```bash
python3 scripts/market_raw_reconcile.py
```

El auditor lee el **BLOB original** de cada respuesta de la SQLite existente,
verifica SHA256 íntegro, decodifica campos nativos por endpoint, reconstruye
las marcas UTC, y contrasta **cada observación persistida** (valor, intervalo,
unidad, referencia SHA y el nocional opcional) con los campos del JSON fuente.
Entrega cantidad de filas y observaciones verificadas por fuente/métrica,
además de errores de conciliación. Exit 0 implica `PASS_SQLITE_TO_RAW`;
exit 1 implica una discrepancia o flujo sin cobertura, y exit 2 implica
que la comprobación no pudo ejecutarse. Ningún estado prueba por sí mismo
que el exchange haya reportado valores económicamente correctos.

Ejemplo de verificación del piloto: Binance OI 500, funding 135;
Bybit OI 200, funding 200: **1.035 registros objetivo**. La cifra
sigue siendo un objetivo hasta que Hermes entregue la salida de la
consulta sobre su base, no un resultado afirmado a priori.

La cifra `sumOpenInterestValue` informada en el piloto fue
7.648.807.675,8016 unidades cotizadas por 92.702,932 BTC:
precio implícito 82.508,80 por BTC. El texto original de Hermes
«≈ 4,7B (76K × 61K)» no concilia y es un error aritmético.
No renombrar retrospectivamente el contenido de origen ni sustituir
un valor por un supuesto USD/USDT hasta confirmar el contrato y la
moneda real. No entregar claves, BLOB, informes integrales o tokens
en comentarios públicos de GitHub.


## Auditoría temporal y cobertura antes de cualquier histórico

Superar `PASS_SQLITE_TO_RAW` significa que las **filas existentes**
concuerdan con sus BLOB originales verificados. NO significa que se haya
guardado la totalidad de filas que la API entregó, que exista continuidad
de 5 minutos, que la ventana histórica del proveedor esté cubierta, ni
que una fuente externa haya calculado correctamente el valor.

La segunda comprobación, de **solo lectura y sin red**, es:

```bash
python3 scripts/market_temporal_quality.py
```

Incluye, por Binance/Bybit y OI/funding: mínimo/máximo UTC real,
número de observaciones, tiempo de última captura, delta entre registros,
**huecos de 5 minutos dentro del tramo disponible**, timestamps
duplicados o en el futuro y observaciones de respuestas originales
que **no tienen ninguna fila persistida**. Registra el recuento original
por BLOB y la diferencia contra las filas existentes; indica aparte
cuando un SHA fuente previo fue reemplazado por uno posterior.

Para funding NO se presuponen ciclos fijos de 8 horas. Bybit documenta
que la frecuencia depende del instrumento y puede variar; se requiere
consultar `instruments-info` antes de declarar faltantes con base
en un intervalo de liquidación. Las distribuciones de deltas se
publican como diagnóstico, no como prueba automática de un gap.

Estados:
- `SNAPSHOT_INTERNAL_QA_OK`: las ventanas de observaciones disponibles
  no presentan irregularidades detectadas con estas pruebas.
- `REVIEW_REQUIRED`: se detectó un hueco temporal, fuente sin persistir,
  duplicado, timestamp irregular o otro hallazgo.
- `historical_completeness = NOT_VERIFIED`: **siempre** hasta contrastar
  límites del proveedor, paginación, cursores, revisiones y calendario de
  operaciones. Sin un backfill formal no existe completitud demostrada.

No ejecutar `--history` para intentar borrar un error del auditor: primero
revisar el alcance del proveedor y autorizar la ingesta, con cuotas,
respaldo y checkpoint por serie. Binance Open Interest 5m está limitado
por su proveedor aproximadamente al último mes; no prometer años de datos.
La estrategia de históricos **debe ser específica por proveedor y métrica**.

La evidencia técnica se puede entregar a ChatGPT como JSON de salida
saneado/Markdown privado. No subir SQLite, BLOB, tokens, recibos Telegram,
archivos de variables de entorno ni datos de terceros al repositorio
público. El reporte del Publication Gate del flujo legacy es independiente
de la certificación de estos nuevos datasets.


## Backfill histórico controlado de una sola página — ETAPA 2E

Tras obtener `PASS_SQLITE_TO_RAW` y `SNAPSHOT_INTERNAL_QA_OK`,
los snapshots están internamente auditados. **Esto no demuestra
histórico completo**. Los endpoints de derivados requieren rescate
hacia atrás específico por fuente/métrica.

Para evitar la ruta antigua `market_context_ingest.py --history`
(la cual NO pagina OI de Binance hacia atrás, y mezcla mecanismos
de paginación de funding/Bybit), se introduce un ejecutor SEPARADO
de una página: `scripts/market_history_pilot.py`.

| Fuente/métrica | API | Máx. una página | Contrato |
|---|---|---:|---|
| Binance OI | `/futures/data/openInterestHist` | 500 registros | UTC < primer OI registrado; límite de proveedor ~1 mes |
| Binance funding | `/fapi/v1/fundingRate` | 1.000 registros | UTC < primer settlement registrado |
| Bybit OI | `/v5/market/open-interest` | 200 registros | UTC < primer OI registrado |
| Bybit funding | `/v5/market/funding/history` | 200 registros | UTC < primer settlement registrado |

**PLAN SIN RED NI ESCRITURA**:

```bash
python3 scripts/market_history_pilot.py --provider binance --metric open_interest
```

**Sólo bajo autorización expresa tras backup SQLite WAL-safe comprobado**:

```bash
python3 scripts/market_history_pilot.py --apply --provider binance --metric open_interest
python3 scripts/market_history_pilot.py --apply --provider binance --metric funding_settled
python3 scripts/market_history_pilot.py --apply --provider bybit --metric open_interest
python3 scripts/market_history_pilot.py --apply --provider bybit --metric funding_settled
```

La invocación acepta **una sola página**, **no** `--max-pages`.
Deriva `endTime` inmediatamente anterior a `MIN(observed_utc)` en
la SQLite actual, exige respuesta anterior al límite, valida símbolo y
contrato, guarda cada BLOB original con SHA, observaciones nuevas y
cursor `history_backward_v1_<metric>` atómicamente **en la misma SQLite**,
y registra un `market_source_runs` con intentos/estado/errores sanitizados.
No cambia datos de períodos más recientes; si el API contradice el límite
o si existe un cursor incoherente, hace rollback del lote. Una respuesta
vacía no equivale a histórico completo: puede ser ventana del proveedor.

Después de **una sola página por proveedor y métrica**, PARAR, no
ejecutar una segunda página sin revisar:

```bash
python3 scripts/market_raw_reconcile.py
python3 scripts/market_temporal_quality.py
python3 scripts/market_context_evidence.py --full-check
```

Exigir `PASS_SQLITE_TO_RAW`, revisar `SNAPSHOT_INTERNAL_QA_OK` y la
continuidad entre la primera fila previa y última fila añadida, contadores
`market_source_runs`, SHA nuevos y `market_source_cursors`, cambios
de historial legado (idealmente cero) y `PRAGMA` de integridad/FK.
Guardar output técnico **solo como evidencia privada**.

**Límites esenciales:** OI Binance más allá de la ventana ~30 días
**no se recupera por este endpoint**. Para más años habrá que evaluar una
fuente alternativa con contrato, condiciones de acceso y metodología.
Bybit histórico retrocede por `endTime` y se validará por página; no
inferir que una muestra sin huecos prueba todo el catálogo histórico.
Funding no se fuerza a un intervalo fijo de ocho horas si el proveedor
ofrece un intervalo distinto para el instrumento. Nunca activar cron
intradía, Telegram ni refresh automático como parte del backfill.


## Recuperación histórica por lotes acotados (Etapa 2F)

Una página exitosa por stream es una prueba de paginación, **no** una
autorización automática para descargar todo el historial. El operador
puede ejecutar, tras una nueva instrucción de alcance específico,
`scripts/market_history_batch.py` contra la **SQLite original**.

Este ejecutor es **PLAN ONLY por defecto**:
```bash
python3 scripts/market_history_batch.py --provider binance --metric open_interest --max-pages 20
```

Y solo bajo autorización expresa de ChatGPT/usuario, tras nuevo backup
WAL-safe validado, se permite la variante `--apply` para una fuente
y métrica concreta:

```bash
python3 scripts/market_history_batch.py --apply --provider binance --metric open_interest --max-pages 20 --pause-seconds 2
```

Controles explícitos: máximo 100 páginas por invocación; mínima pausa
de 1 segundo entre llamadas; ingesta **secuencial** sin concurrencia;
una transacción por página que conserva BLOB original, SHA256, nuevas
filas, cursor, estado y contador de la solicitud; `PRAGMA quick_check`
y claves foráneas cada cinco páginas; parada inmediata en HTTP error,
cursor no avanzado, respuesta vacía u otra inconsistencia. No reintenta
errores ni elude 403/429. Después de cerrar las escrituras, ejecuta
reconciliación fuente→SQLite y auditoría temporal de **solo lectura**.

Los estados `page_budget_exhausted` y `empty` no garantizan
que no haya más datos históricos; `historical_completeness`
se mantiene `NOT_VERIFIED` hasta documentar el **límite externo de
disponibilidad** por fuente, símbolo, período y su ventana. No
continuar con nuevos lotes cuando un auditor falla, aunque ya se hayan
almacenado páginas correctas: conservar backup y evidencia, reportar
el punto exacto y esperar dictamen.

**Contratos especiales:**
- Binance OI 5m: solo aproximadamente el último mes desde consulta,
  no se recuperan años por este endpoint.
- Binance funding: acepta hasta 1.000 registros por petición según la
  documentación; una respuesta con menos de 1.000 **no significa**
  por sí sola fin de histórico.
- Bybit OI 5m: máximo 200 registros; para historiales extensos se
  necesitan múltiples páginas y control de ventana/latencia.
- Bybit funding: máximo 200; frecuencia exacta puede variar por
  instrumento/fecha, por lo que no se infieren huecos comparando
  siempre contra ocho horas.

**Deuda arquitectónica identificada:** `market_source_runs` sigue
registrando proveedor y no un identificador de stream o parámetros
públicos de la solicitud; `market_raw_payloads` tampoco guarda
parámetros no secretos. Antes de automatizar backfills masivos
recurrentes debe añadirse un registro persistente sanitizado de
solicitud (run_id, stream, UTC, parámetros de paginación sin claves,
SHA de respuesta, estado) enlazado a la respuesta cruda. Esta mejora
de linaje es independiente de verificar valores y no puede inferirse
retrospectivamente a partir de BLOB sin contexto.

En ningún caso `--apply` de este programa autoriza activar cron
intradía, Telegram, FRED/BLS, Farside ni la publicación de información
no certificada.


## Fallos URLError: diagnóstico local previo a nuevas ingestas

Un `URLError` en `market_history_batch.py` registra un intento HTTP
fallido sin datos persistidos. Si el cursor y los conteos permanecen
invariantes y los dos auditores regresan PASS, **solo se ha confirmado
la preservación del histórico anterior**: el lote terminó en fallo.
`post_audit_ok=true` no se debe presentar como ejecución exitosa.
La salida nueva presenta por separado `batch_outcome`.

La ejecución de un error `URLError` antiguo se conservó únicamente
como clase general, sin su causa interna. **No se puede reconstruir
retrospectivamente** un diagnóstico DNS/TLS/timeout a partir del valor
SQLite. No suponer bloqueo regional, limite API, proxy ni error 403.

Para obtener evidencia **sin repetir el acceso al endpoint ni escribir
en SQLite**, ejecutar:

```bash
python3 scripts/market_network_preflight.py
```

La comprobación hace exclusivamente consultas **DNS** a
`fapi.binance.com` y `api.bybit.com`, y consulta la presencia de
configuración local de proxy y certificados CA. No abre conexiones HTTP
o TLS, no captura IP, credenciales, URLs de proxy, rutas locales ni
cuerpos y no consulta API de mercado. Un `dns=RESOLVED` no demuestra
que HTTPS, el endpoint ni la API funcionen: el handshake y el estado HTTP
quedan explícitamente como `NOT_TESTED`.

Los **nuevos** fallos se registrarán como categorías seguras:
`URLError_DNS`, `URLError_TLS`, `URLError_TIMEOUT`,
`URLError_REFUSED`, `URLError_RESET` o
`URLError_UNCLASSIFIED`; para un HTTP conocido, `HTTPError_<status>`.
Nunca se registra el texto de la excepción, direcciones de proxy,
consultas con claves ni datos privados.

Si falla DNS para ambos hosts, revisar la configuración del resolver,
rutas y proxy en el equipo, sin evadir controles. Si solo falla un
host, entregar esa diferencia para evaluación. Si ambos resuelven,
todavía faltará una validación HTTPS expresa y limitada antes de
reanudar el lote; DNS correcto no autoriza nuevos backfills.


## Fase 2F-HTTPS — Una prueba de conectividad, sin recuperar mercado

El diagnóstico 2F-DIAG observó `RESOLVED` para
`fapi.binance.com` y `api.bybit.com`, con
`http_attempts=0`. Esto valida la resolución DNS en esa ejecución,
**no** el handshake TLS, alcance del endpoint, ni disponibilidad de
histórico. `ssl_cert_file_available=false` con un directorio CA
disponible tampoco demuestra una falla TLS.

La prueba oficial Binance USD-M Futures `GET /fapi/v1/ping`
consume un peso IP de 1 y sirve exclusivamente para comprobar HTTPS.
Nunca permite inferir el alcance de `openInterestHist`.

El nuevo comando es **PLAN ONLY por defecto**:

```bash
python3 scripts/binance_https_probe.py
```

Una vez **autorizada expresamente** la única prueba HTTPS, ejecutar:

```bash
python3 scripts/binance_https_probe.py --probe
printf 'ping_exit=%s\n' "$?"
```

El cliente intenta exactamente **un GET** con validación de
certificados del sistema (sin `-k`, sin desactivar TLS), timeout
de ocho segundos, sin seguir redirecciones ni reintentar; no envía
API keys, consulta históricos, escribe SQLite ni imprime cuerpos,
headers, IP, proxy o credenciales. Acepta HTTP 200 con cuerpo
vacío u objeto JSON vacío `{}`. Publica solo estado HTTP,
categoría de error sanitizada y latencia.

- `PASS_HTTPS_CONNECTIVITY` y exit 0: **únicamente** TLS/HTTP ping
  aprobado. **NO** autoriza repetir página OI ni lotes.
- Exit 1 o `HTTPError_<status>`, `URLError_DNS/TLS/TIMEOUT/...`:
  detener sin reintentar; preservar evidencia local.
- Un error de certificado debe resolverse mediante configuración
  de confianza legítima. No eludir certificados, bloqueos regionales,
  controles de proveedor, 403 o 429.

La autorización anterior de 56 páginas permanece suspendida.
Tras analizar el resultado de ping, ChatGPT decidirá sobre una
sola consulta controlada al endpoint OI o una alternativa legítima
que respete licencias, restricciones y límites del proveedor.


## Etapa 2F-OI: comprobación mínima del endpoint histórico de Binance

Tras confirmar `HTTP 200` con TLS en `GET /fapi/v1/ping`,
**solo** se acredita el acceso a ese endpoint. Las estadísticas
históricas de OI están en una ruta diferente:
`GET /futures/data/openInterestHist`, con `symbol=BTCUSDT`,
`period=5m`, `endTime` y `limit`. Binance documenta un máximo
de 500 y disponibilidad aproximada del último mes para esa serie.

El diagnóstico propuesto es **una única solicitud**, no un backfill:
`scripts/binance_oi_endpoint_probe.py` calcula, en la SQLite
existente y abierta con `mode=ro`, el `endTime` anterior al
`MIN(observed_utc)` de Binance OI; comprueba el cursor histórico.
Si no coinciden, **no realiza HTTP**. Si coinciden, mediante
`--probe` pide **una sola fila (`limit=1`)** y verifica si
corresponde exactamente a los cinco minutos anteriores al mínimo
almacenado.

**PLAN — cero red, cero escritura:**

```bash
python3 scripts/binance_oi_endpoint_probe.py
```

**Solo después de autorización de una petición de diagnóstico:**

```bash
python3 scripts/binance_oi_endpoint_probe.py --probe
printf 'oi_endpoint_exit=%s\n' "$?"
```

El cliente usa TLS validado contra el CA del sistema, máximo ocho
segundos, sin seguir redirecciones, sin reintentos, sin credenciales,
sin alterar `market_source_runs`, BLOB, cursores ni observaciones.
Solo imprime el estado HTTP, categoría de error, número de filas,
timestamp público esperado/observado y SHA parcial del cuerpo;
**el cuerpo completo se desecha y no es una ingesta científica**.
Cualquier dato histórico destinado a los análisis debe volver a
recuperarse con el colector autorizado que archive el BLOB crudo y
su linaje en SQLite.

Si responde HTTP 200 pero la lista está vacía, la fila no coincide
con el corte esperado o el cuerpo tiene un formato inesperado, el
diagnóstico **no aprueba** el endpoint; no inventar la observación.
Un ping correcto NO constituye prueba de cobertura histórica,
permiso a reanudar los 56 pedidos suspendidos ni aprobación de ETF,
BLS, FRED, informe o Telegram. Parar después del único GET incluso
si el resultado es `PASS_OI_ENDPOINT_AND_BOUNDARY`.


## Migración aditiva v2: procedencia estructurada por solicitud

Tras aprobar una página histórica R1, el sistema conserva 2.935
observaciones de derivados reportadas, diez BLOB originales y
cuatro cursores, pero la tabla v1 `market_source_runs` identifica
solo el proveedor: **no permite reconstruir qué parámetros
específicos se enviaron para cada respuesta ya capturada**.

La versión **v2** incorpora `market_request_lineage` (una solicitud
histórica por `run_id`) con estos campos:

| Campo | Significado |
|---|---|
| `request_id`, `run_id` | Llaves de solicitud y ejecución enlazadas mediante FK |
| `provider`, `stream`, `symbol`, `interval_label` | Serie exacta solicitada |
| `endpoint_path` | Solo ruta pública, sin query ni secretos |
| `requested_end_ms`, `requested_limit` | Cursor temporal y tamaño real solicitados |
| `attempted_utc`, `ended_utc`, `http_attempts` | Momento de consulta y cantidad de intentos |
| `status`, `error_class` | Resultado seguro, sin texto ni secretos de excepción |
| `raw_sha256` | FK a respuesta fuente íntegra archivada, en caso de éxito |
| `returned_rows`, `persisted_rows` | Conciliación de volumen fuente y escritura |
| `first_observed_utc`, `last_observed_utc` | Cobertura efectiva de esa respuesta |

Esta tabla no altera, renombra ni copia las series existentes; tampoco
reinterpreta las unidades `BTC`/`fraction` ni la antigua
`quote_usd`. Los registros anteriores a la migración se conservan
**sin asignarles parámetros retrospectivos inventados**. Solo las
solicitudes futuras ejecutadas por el colector histórico v2 tendrán
estos recibos individuales.

### Procedimiento v2 — SOLO CON AUTORIZACIÓN Y BACKUP

`python3 scripts/market_context_migrate.py` muestra PLAN sin
red ni escritura. `--apply` comprueba SQLite, genera un **nuevo
backup WAL-safe íntegro** y añade la tabla v2 y marca
`market_context_migrations.version=2`; se detiene ante fallo de
integridad o FK y conserva el backup. Cuando la versión 2 ya exista,
la migración es idempotente y no crea copias adicionales.

```bash
python3 scripts/market_context_migrate.py
python3 scripts/market_context_migrate.py --apply
```

La instalación v2 puede verificarse por SQL de solo lectura:

```sql
SELECT version, applied_at_utc
FROM market_context_migrations ORDER BY version;

SELECT sql FROM sqlite_master
WHERE type='table' AND name='market_request_lineage';

SELECT COUNT(*) FROM market_request_lineage;
PRAGMA integrity_check;
PRAGMA foreign_key_check;
```

Tras migración v2, pero **antes de cualquier descarga histórica**,
el contador de `market_request_lineage` debe ser 0. Los
`market_source_runs` y todos los `market_raw_payloads` antiguos
seguirán exactamente como estaban; el contador 0 es correcto
porque no se inventó metadato para ejecuciones previas.

El colector `scripts/market_history_pilot.py` exige v2 antes
de efectuar HTTP. Un éxito persiste **en una sola transacción** el
BLOB, sus observaciones, el cursor y el nuevo recibo enlazado.
Ante fallo HTTP, conserva un recibo seguro que contiene el
`requested_end_ms`, límite, tipo de error y un intento, pero
nunca una respuesta que no fue aceptada. Los demás módulos de
ingesta existentes aún no implementan esta granularidad y
requieren adaptación propia antes de declararlos completamente
auditables a nivel de solicitud.

**Prohibido** activar automáticamente históricos o publicación solo
porque la migración v2 tuvo éxito. El próximo lote deberá autorizarse
por separado, con evidencia nueva de continuidad, cursores,
SHA y calidad posterior.


## Auditoría independiente de request-level lineage (v2)

Después de migrar el esquema y **antes de autorizar** un lote histórico
superior a una página, ejecutar este validador **sin peticiones HTTP ni
escrituras SQLite**:

```bash
python3 scripts/market_request_lineage_audit.py
printf 'lineage_exit=%s\n' "$?"
```

Este auditor contrasta **cada** recibo `market_request_lineage` con:
- `market_source_runs`: mismo run_id, proveedor, estado,
  intentos, recuento persistido y categoría de fallo.
- `market_raw_payloads`: SHA256 completo recalculado y
  contrato JSON nativo decodificado por endpoint, **sin imprimir BLOB**.
- `market_derivatives`: todas las filas de origen persistidas con
  el SHA exacto, y rango temporal que coincide con el recibo.
- `requested_end_ms`, `requested_limit`, ruta y temporalidad:
  para Open Interest 5m, exige último timestamp = endTime+1−300000 ms,
  continuidad dentro de cada página y contacto con el histórico
  existente en su frontera nueva.
- `market_source_cursors`: checkpoint de la serie igual al
  timestamp más antiguo observado menos 1 ms.

`NO_REQUEST_RECEIPTS_YET` (cero recibos y exit 0) es el estado
**esperado inmediatamente después de la migración v2**; NO se
interpreta como request-level auditing ya aprobado. Luego de
una primera ingesta, `PASS_REQUEST_LINEAGE` y exit 0 significan
que los nuevos recibos, BLOB, fechas y filas concilian;
`REVIEW_REQUIRED`/exit 1 detiene nuevas descargas.

El validador **no reconstruye** endTime, límites o run IDs
de las páginas anteriores a la migración. Esas páginas mantienen
el control `PASS_SQLITE_TO_RAW` y `SNAPSHOT_INTERNAL_QA_OK`,
pero no deben presentar un recibo v2 retroactivo inventado.

La verificación de lineage debe acompañarse de las auditorías
`market_raw_reconcile.py`, `market_temporal_quality.py`,
integridad SQLite/FK y conteos PRE/POST de las tablas legacy.
`PASS_REQUEST_LINEAGE` tampoco implica histórico completo:
`historical_completeness` sigue `NOT_VERIFIED` hasta analizar
explícitamente los límites de cobertura de cada proveedor.


## Per-page hardening antes de lotes de recuperación mayores

El segundo piloto v2 (R3) fue validado por Hermes con dos páginas
Binance OI consecutivas, `PASS_REQUEST_LINEAGE`, 2.500
observaciones Binance OI y 3.935 observaciones de derivados en total.
Eso certifica esas páginas; `historical_completeness`
sigue `NOT_VERIFIED`.

El procesador `scripts/market_history_batch.py` incorpora ahora
**un control independiente obligatorio después de CADA página
confirmada en SQLite y ANTES de la próxima petición HTTP**:

1. `market_raw_reconcile.audit`: exige
   `PASS_SQLITE_TO_RAW` (fuente archivada ↔ valores guardados).
2. `market_temporal_quality.inspect`: exige
   `SNAPSHOT_INTERNAL_QA_OK` (continuidad temporal observada).
3. `market_request_lineage_audit.audit`: exige
   `PASS_REQUEST_LINEAGE` y `requests_verified=requests_total`
   (solicitud ↔ raw ↔ filas ↔ cursor).

La salida `page_qa[]` contiene por número de página los tres estados,
número de recibos verificados y `ok`. Un fallo detiene el bucle con
`stopped_because=per_page_provenance_audit_failed`, sin solicitar
otra página ni deshacer transacciones previas que ya fueran válidas;
conservar backup y reportar. La comprobación final de lectura también
incluye `request_lineage_audit` y `post_audit_ok` exige los
tres controles aprobados.

Esta garantía es distinta de tener suficientes datos de toda la
historia: el OI Binance USD-M `/futures/data/openInterestHist`
ofrece **solo aproximadamente el último mes**, con
`limit <= 500` por solicitud; la fuente puede agotar la ventana
sin servir una página completa. Ante una respuesta `empty`,
HTTP 403/429, valores anómalos o auditoría fallida, nunca
autocompletar huecos ni cambiar de endpoints para eludir límites.

## Siguiente etapa: incremento forward (aún NO AUTORIZADO)

El contrato propuesto de migración v3, independencia de los cursores
backward/forward, recibos por solicitud, control de barras cerradas,
fronteras de API, pruebas offline y despliegue por etapas se describe en
[MARKET_FORWARD_INCREMENTAL_R10.md](MARKET_FORWARD_INCREMENTAL_R10.md).

**Este diseño no instala v3 ni autoriza tráfico de red, ingesta diaria,
cron, publicación o Telegram.**
