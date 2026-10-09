# Protocolo obligatorio de aceptación operativa — Hermes / Research Studio
**Versión:** 2026-10-09 · **Canal de respuesta:** comentario en PR #23 ·
**Base única:** `db/btc_research.db`.

## Regla de ejecución y alcance
Este protocolo verifica **instalación real**, **comportamiento de API**
y **contenido histórico en SQLite**. No reemplazarlo por un simple
`tests/run_all.py PASS` o `Publication Gate 100`: esas pruebas validan
otros contratos, no que se hayan descargado ETF flows/OI/funding/calendario.

Cada paso requiere un estado **PASS**, **FAIL**, **BLOCKED** o **NO EJECUTADO**.
No omitir pasos silenciosamente. Incluir el número de salida de cada
comando y la fecha UTC. Si existe una falla crítica o datos imposibles,
**detener activación/publicación**, conservar evidencia LOCAL y
reportar error sanitizado. No forzar resultados `PASS`, no modificar
manualmente DB/gate/HTML/fixtures para hacer coincidir números.

## 0. Reglas previas
- Trabajar en `/home/ignotus/btc-research` sin exponer el directorio
  privado en GitHub público. Conservar backups y cambios sin rastrear.
- No emitir Telegram: `SEND_TELEGRAM_AUTO=0`. **No ejecutar**
  `scripts/send_report.py` salvo `--dry-run`. No usar `--public`
  en el puente de Hermes ni subir `*.db`, HTML ni logs crudos.
- No ejecutar backfill masivo (`--history`), activar cron 5m, variar
  `MARKET_CONTEXT_DAILY_ENABLED` permanentemente ni usar scraping de
  Farside sin revisar autorización del acceso.
- No registrar la API key FRED ni tokens en salida, URL o comentario.

## 1. Estado Git y actualización
```bash
cd /home/ignotus/btc-research
date -u '+%Y-%m-%dT%H:%M:%SZ'
git branch --show-current
git rev-parse HEAD
git rev-parse origin/master
git status --short
bash scripts/update_local_and_test.sh
git rev-parse HEAD
python3 tests/run_all.py
python3 -m unittest -v tests.unit.test_fred_cli_and_market_evidence
```
Si hay modificaciones locales legítimas, evitar reset/checkout destructivo
y declarar bloqueo. Reportar SHA completo, lista **de categorías**
de archivos no rastreados (sin datos privados), suites PASS/FAIL y
cantidad de tests individuales. El runner contiene múltiples suites:
**19/19, 20/20**, etc. es número de suites, no pruebas individuales.

## 2. FRED — solucionar el error demostrado y probar ejecución real
Después de actualizar `master`, ejecutar:
```bash
python3 ingestion/ingest_fred.py --help
python3 -m ingestion.ingest_fred --help
python3 -m unittest -v tests.unit.test_fred_cli_and_market_evidence
```
La causa de `ModuleNotFoundError: No module named 'storage'` era una
ruta de importación incompatible con el CLI. La corrección usa
`ingestion.config` y añade la raíz del repo. A diferencia de antes,
los errores HTTP/timeout/parsing marcan la ingesta como FAIL, no “sin
observaciones nuevas”.

**Comprobar también el proceso con la API real** (si
`FRED_API_KEY` ya está cargada en el entorno):
```bash
python3 ingestion/ingest_fred.py
echo "fred_exit=$?"
```
Validar recuentos `macro_fred` por serie, fechas mínimo/máximo,
última observación/revisión y nuevos valores persistidos en SQLite.
No imprimir claves, respuesta completa o URLs autenticadas.
**Si FRED falla, no afirmar que `daily.sh` terminó bien.**
`daily.sh` verifica `$?` del ingestor y debería abortar antes de
generación y gate. Si aparece gate 100 tras un FRED fallido, exigir
timestamp del archivo gate y del último intento: puede ser un gate
antiguo, invocado por separado o bajo otro entorno.

## 3. Migración SQLite y respaldo WAL
```bash
python3 scripts/market_context_migrate.py
python3 scripts/market_context_migrate.py --apply
python3 scripts/market_context_evidence.py --full-check
```
El `--apply` crea o confirma esquema aditivo. Para primera instalación
debe existir un backup `sqlite3.backup()` WAL-safe y comprobado ANTES
de crear tablas. Si la migración ya estaba aplicada, demostrar la
integridad del backup preexistente; no afirmar falsamente que acaba
de realizarse uno. No usar `shutil.copy2` sobre SQLite con WAL.

Reportar: `market_context_migrations` versión/hora UTC, resultado
`PRAGMA quick_check`, `PRAGMA integrity_check`, conteo de
`PRAGMA foreign_key_check`, estado de respaldo y si puede
restaurarse. Confirmar que la BD original **no fue reemplazada**.

## 4. Ingestas de mercado controladas e independientes
**Nunca** usar la ejecución general como único diagnóstico: hacer
una llamada separada por proveedor, cada una con código de salida,
hora UTC, `market_source_runs` y variación de conteos antes/después.

```bash
python3 scripts/market_context_evidence.py
python3 scripts/market_context_ingest.py --apply --sources binance --max-pages 2
python3 scripts/market_context_ingest.py --apply --sources bybit --max-pages 2
python3 scripts/market_context_ingest.py --apply --sources bls
python3 scripts/market_context_ingest.py --apply --sources fred
python3 scripts/market_context_evidence.py --strict
```

**Farside**: documentar primero si las condiciones/robots y la
estructura de la tabla permiten leerla. Solo si compatible:
```bash
python3 scripts/market_context_ingest.py --apply --sources farside
python3 scripts/market_context_evidence.py --strict
```
Si se bloquea Farside, informar causa en términos técnicos
(HTTP status, falta de tabla, formato modificado, permisos). No
usar bypass, proxy, login, scraping de servicios prohibidos
ni registrar un flujo ETF sintético.

**En cada proveedor indicar:**
- URL/base y endpoint (solo host/path, sin credenciales ni query).
- ¿Hubo HTTP 200 y cuerpo parseable? ¿o qué tipo de error y código?
- `market_source_runs`: último `status`, `requests`,
  `points`, `started_utc`, `ended_utc`, `error_class`.
- `market_raw_payloads`: cantidad y bytes de respuestas originales
  dentro de SQLite; ¿hay SHA verificables?
- `market_derivatives`: Binance y Bybit por
  `metric,raw_unit,interval_label`, filas, primera/última UTC,
  1–2 muestras recientes, hash prefijo y diferencia en horas
  respecto al momento de revisión; `funding_settled` es liquidado.
- `market_etf_flows`: filas por ticker, primera/última fecha, las
  cifras IBIT/FBTC/GBTC y `TOTAL` si existen, USD millones, estado
  preliminar, fuente y reconciliación. Celda vacía ≠ cero.
- `market_calendar_events`: eventos BLS/FRED por fuente y tipo de
  precisión (UTC verificada, date-only, unconfirmed), próximos 3–5
  con hora UTC **solo si confirmada**.
- `market_context_revisions`: revisiones por tabla;
  `market_source_cursors`: serie, cursor, fecha de actualización,
  sin afirmar historial completo por tener cursor.
- Registrar lagunas, ventanas del proveedor, paginación y cuota.
  `0` filas reales es diferente de `0` filas nuevas por upsert.
- Distinguir **datos brutos**, **normalizados**, **revisiones**,
  **pruebas unitarias con fixtures** y **observaciones efectivamente
  descargadas**. No sumar OI de exchanges sin unidades comparables.

`scripts/market_context_evidence.py` es **solo lectura**, imprime
estadísticas y muestras limitadas; `--strict` devuelve código distinto
de cero si faltan fuentes/categorías. Un FAIL de `--strict` es
diagnóstico, no instrucción para fabricar registros.

## 5. Integración final del pipeline y reporte
Ejecutar una sola vez bajo entorno verificado y sin Telegram:
```bash
date -u '+%Y-%m-%dT%H:%M:%SZ'
SEND_TELEGRAM_AUTO=0 MARKET_CONTEXT_DAILY_ENABLED=0 bash scripts/daily.sh
echo "daily_exit=$?"
```
La ingesta de mercado se probó por separado en el paso 4;
no volver a ejecutarla automáticamente en esta prueba.
Reportar fecha de generación, cierre real BTC UTC,
fecha más antigua on-chain, SHA256 y tamaño del HTML original
**y** portable, metadato Research Studio v2, bloque
`id="market-context"` cuando hay datos frescos,
`id="research-methodology"` y firma corpus.

Verificar:
- `reports/gate_YYYY-MM-DD.json` es del **mismo run**;
  comprobar score, PASS, `critical_count`, `report_sha256`
  y mtime. No reutilizar la puntuación de ayer.
- Reconcilia las tarjetas ETF/OI/funding contra SQLite y BLOB SHA
  mediante el Publication Gate. ¿Qué valores se ocultaron por
  antigüedad o falta de datos?
- Confirma eventos reales y horario UTC de calendario, sin
  “Consensus” o resultados no respaldados.
- Revisa presentación 320/390/768/1440 px + A4 e informa qué
  comprobaste de verdad; sin inventar capturas.
- Si falla `daily.sh`, NO calificar la ejecución como exitosa
  aunque exista un informe HTML viejo o el Gate anterior sea 100.

## 6. Incidente Telegram #11 — auditoría obligatoria
Hermes reportó envío Telegram #11 tras pedirle pruebas sin envío.
Determinar con evidencia local:
1. Fecha UTC, versión de script y **cómo se disparó**:
   `daily.sh` con `SEND_TELEGRAM_AUTO=1`, uso manual de
   `send_report.py`, cron, automatismo externo u otra vía.
2. ¿Quién/qué lo autorizó? Si no se puede probar, estado
   `NO AUTORIZACIÓN DOCUMENTADA`.
3. ¿El reporte enviado corresponde al gate del mismo run?
   Comparar solo SHA256 y timestamp; **no** exponer receptor,
   token, chat_id, mensaje completo o recibo privado.
4. Cómo evitar repeticiones: mantener envío desactivado,
   corregir origen si hubo activación inesperada. No borrar
   registros para ocultar el hecho. No reenviar mensajes.
5. Si un reporte se envió pese a Gate fallido, marcar **P0**
   y detener publicación hasta corregirlo.

## 7. Formato obligatorio de respuesta al PR #23

Contestar en el **comentario del PR #23**, NO creando un commit de
datos o nueva issue. Encabezado `Hermes → ChatGPT — Auditoría
operativa post PR #36`; incluir exactamente:

**A. Identidad de ejecución:** UTC inicio/fin, HEAD SHA completo,
`origin/master`, rama, estado del árbol saneado, Python y SO.

**B. Pruebas:** suite/run count, tests individuales, resultado
`python3 ingestion/ingest_fred.py --help`, `python3 -m`, FRED
real + exit code, `tests/run_all.py` y archivo CI de PR.

**C. DB/backup:** tamaño aprox. de DB si no sensible, SHA de backup
NO requerido, estado verificable, versión de migración, fechas,
`quick_check`, `integrity_check`, FK failures y tablas instaladas.

**D. 5 proveedores:** cinco subbloques separados Binance, Bybit,
Farside, BLS, FRED; para cada uno: endpoint, status HTTP/error,
`status` run, requests, changed, total rows SQLite, first/last UTC,
unidad, 1 ejemplo normalizado (si existe), cobertura faltante
y estado `PASS/FAIL/BLOCKED/NO EJECUTADO`.

**E. Archivo histórico:** tabla breve por indicador de rangos,
revisiones y cursors; indicar si el histórico está parcial,
completo verificado o no disponible; comparaciones no autorizadas
con RBN/Bitview no deben realizarse.

**F. HTML/Gate:** `daily_exit`, ejecución UTC, SHA/tamaño
original/portable, corte BTC/on-chain, meta v2, tarjetas
ETF/OI/funding y calendario con fuente/UTC, gate score,
críticos, hash comparado, QA visual real.

**G. Telegram #11:** origen, momento UTC, autorización,
SHA enviado↔gate, envío habilitado actualmente y medidas
preventivas. Si no hay evidencia, escribir **NO VERIFICADO**.

**H. Hallazgos:** cada P0/P1/P2 con
`prioridad | componente | evidencia sanitizada | impacto |
hipótesis | reproducibilidad | archivo/función | propuesta |
prueba de aceptación | responsable | estado`.

**I. Próximo paso:** una acción concreta por bloqueo, sin intentar
correcciones ad hoc a producción ni saltar gates.

La respuesta no puede omitir encabezados A–I. Marcar expresamente
`NO EJECUTADO` donde corresponda. Adjuntar solo información
pública saneada; el SQLite original, reporte íntegro, tokens y logs
quedan locales. Si hay fallo, remitir solo clase de error,
endpoint host/path, línea o función y caso de prueba reproducible.

## Canal
Leer: `python3 scripts/hermes_bridge.py inbox --pr 23`
Responder: `python3 scripts/hermes_bridge.py reply --pr 23 --message-file /tmp/hermes-market-context-audit.md`

Este protocolo no autoriza vigilancia automatizada de GitHub
ni tráfico de Telegram sin consentimiento.
