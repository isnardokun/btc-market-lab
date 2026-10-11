# R10-E.3 — Harness integral de una página forward, exclusivamente sintético

**Estado:** diseño e implementación aislados. Esta PR NO habilita ingesta, scheduler, API real, cron, DB productiva ni restauración/backup. Gobierno: ChatGPT desarrolla y publica; Hermes solo prueba SHA congelado; propietario autoriza operaciones productivas.

## Qué se prueba

El módulo scripts/market_r10e3_mock_e2e.py utiliza el colector productivo existente scripts/market_history_forward.execute con **fetch inyectado sin conexión a internet**, nunca su fetch por defecto. Crea por sí mismo un TemporaryDirectory con una SQLite nueva llamada synthetic.db, instala esquema market v1+v2+v3 y siembra cuatro snapshots autocontenidos: OI 5m y Funding settlement para Binance y Bybit. Cada dato tiene BLOB fuente original con SHA, endpoint, unidad nativa y timestamp UTC.

Una ejecución sintética llama **una sola vez** al callback falso y pasa por la ruta normal de persistencia forward v3 del colector: transacción, SHA de BLOB, dos nuevas observaciones cuando procede, source_run, receipt, acquisition y cursor. Después verifica:

1. Manifest PRE vs POST de las **10 tablas market_** mediante PK y SHA256 de representación de cada fila original; cero cambios o eliminaciones PRE.
2. RAW, TEMPORAL y LINEAGE independientes sobre **un snapshot SQLite RO coherente**; además integrity_check y foreign_key_check.
3. Conteos delta y coherencia por escenario; receipts son nuevos tanto para success como para error (sin insertar observaciones inválidas). Nunca avanza el cursor si no hubo progreso.
4. Por cada invocación el informe indica **http_real_requests=0, production_db_reads=0, production_db_writes=0**; todas las operaciones sobre ruta temporal autogenerada, sin aceptar una DB del usuario.
5. Los estados de respuesta son MOCK_FIXTURE_PASS o MOCK_FIXTURE_FAIL y **nunca significan aprobación productiva**.

## Escenarios de fixture

| Escenario | Resultado esperado de execute | Observaciones nuevas | Receipt | Cursor |
| --- | --- | ---: | --- | --- |
| success | success | +2 | success + acquisition + BLOB | Avanza 10min |
| empty | empty | 0 | empty + acquisition y RAW vacío | No avanza |
| http429 | failed | 0 | failed, sin acquisition | No avanza |
| gap | failed | 0 | failed, sin adquisición de BLOB | No avanza |

Los datos de prueba son estrictamente sintéticos, NO los valores de 27.663 derivados reportados en PR #59. Las 13 pruebas nuevas incluyen ambos proveedores, auditorías, ausencia de efectos externos, controles de línea base, registro runner por AST y una prueba sintética que confirma que el runner devuelve RC=1 ante una suite fallida.

## Límites conocidos

Esta primera integración no implementa: bloqueo de writers entre procesos productivos, acceso/API/scheduler, paginación múltiple, HTTP retry policy, protección de cuota real, revisión externa de cobertura histórica, ni migraciones productivas. Es una **prueba de contrato atómico y recuperación de errores**, no un operador ni ejecutor de producción. Los controles R10-E.0/E.1/E.2 permanecen autónomos; aún no existe un único orquestador que los ejecute en un job real.

**Funding P1:** las 1.035 observaciones legacy sin receipt individual siguen no reconstruibles retrospectivamente. historical_completeness=NOT_VERIFIED, y Tier 2 ResearchBitcoin entitlement no validado con proveedor.

## QA de Hermes

Probar exclusivamente el SHA indicado en el comentario de orden de QA de esta PR. Se esperan 13 casos E.3, 16 E.2, 9 E.1 y 7 E.0; runner **41 suites estáticas** y misma cantidad ejecutadas si todo corre. Capturar RC, logs privados, datos de aislamiento/worktree; publicar un único comentario y STOP.

Ninguna aprobación derivada de este test permite repetir HTTP live de Bybit/Binance, activar daily.sh, tocar SQLite productiva, modificar cron, borrar worktrees de R10-C/D/E ni realizar merge por Hermes.

## Corrección de QA tras #6103986893

Hermes detectó que la prueba E.2 había fijado `len(TESTS)==40`, aunque R10-E.3 añade la suite 41. ChatGPT sustituyó los conteos exactos de pruebas heredadas por mínimos de contrato (`>=40` E.2, `>=41` E.3) con comprobación de etiquetas/comandos únicos y de presencia exacta de las suites de cada fase. **El registro real de esta PR continúa siendo de 41 suites.**

El primer informe de Hermes indicó que un runner fallido devolvió RC=0, incompatible con el final versionado `sys.exit(0 if all_ok else 1)`. Una nueva prueba sintética ejecuta una COPIA del runner con un comando artificial que termina en código 7 y exige RC=1. No se ejecuta una suite real adicional por esta prueba ni se escribe fuera de `TemporaryDirectory`. La próxima orden a Hermes exigirá registrar explícitamente el RC del runner y el conteo de resultados.
