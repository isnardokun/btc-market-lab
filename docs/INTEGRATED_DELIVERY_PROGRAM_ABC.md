# Programa de entrega integral A/B/C — BTC Market Lab

**Decisión del propietario:** sustituir el desarrollo por microfases R10-E por **capacidades integradas y verificables**, sin sacrificar calidad.  
**Base validada:** PR #75 / R10-E.14, merge `b534d4f3a54086e85c2c8b9a1409acfb7090141a`, 241/241 pruebas focales, runner 52/52 suites PASS según QA de Hermes.  
**Responsables:** **ChatGPT = arquitectura, implementación, corrección, commits, PR, revisión y merge**; **Hermes = exclusivamente QA independiente y reporte; STOP**; **propietario = autoriza cualquier acceso a producción, API real, DB/backup, cron, despliegue y publicaciones**.

> Ningún PASS offline acredita disponibilidad histórica real, derechos del token ResearchBitcoin Tier 2, exclusión de writers no cooperativos ni autorización productiva. `historical_completeness=NOT_VERIFIED` hasta verificar métricas/proveedores contra límites efectivos.

## Estado vigente — 2026-10-12

| Paquete | Estado | Evidencia y alcance |
| --- | --- | --- |
| A | A_OFFLINE_ACCEPTED; EXTERNAL_VERIFICATION_PENDING | PR #79, merge `45bbb353`; [cierre](PACKAGE_A_OFFLINE_ACCEPTANCE.md) |
| B | B_OFFLINE_ACCEPTED; EXTERNAL_VERIFICATION_PENDING | PR #80, merge `19b3298a`; [cierre](PACKAGE_B_OFFLINE_ACCEPTANCE.md) |
| C | PENDING | Issue #78, orquestación/rollout todavía sin aceptar |

Hermes STOP, órdenes A/B cerradas. Estos estados no autorizan API real,
DB productiva, cron, backups, flags, despliegue o publicación. El alcance y
las definiciones siguientes se mantienen como contrato del programa.

## 1. Nuevo método operativo (obligatorio)

1. **Una PR sustancial por capacidad, no por función**: A / datos e ingesta, B / analítica e informes, C / orquestación y puesta en marcha. Cada paquete puede usar varios commits coherentes, pruebas intermedias y revisión continua; evitar PR de una función, de una sola suite o solo de documentación como ciclos de entrega.
2. **Desarrollar de extremo a extremo**: integrar componentes reales, no solamente planificadores que no se conectan. Cada paquete entrega un *vertical slice* reproducible, con fixtures y prueba de flujo completo sin usar recursos productivos.
3. **CI por cada commit/cambio de contrato**: ejecutar en rama pruebas dirigidas de módulos afectados, lint/type/static cuando existan y tests de integración sintéticos. Un único runner completo al cierre de la entrega y otra vez cuando un fix cambie el SHA final. No duplicar rutinariamente 50+ suites como comandos focales y otra vez runner en cada microcommit.
4. **Hermes una sola orden al cierre de cada paquete** sobre SHA exacto, base master conocida y worktree nuevo limpio; puede haber nueva orden SOLO si un defecto requiere nuevo commit/SHA. Hermes NO desarrolla, modifica código, instala paquetes, commitea, mergea, toca la DB real ni elimina worktrees.
5. **Merge controlado**: revisión de diff/seguridad/esquema/compatibilidad, GitHub CI PASS, QA Hermes PASS sobre HEAD vigente, sin P0/P1 y sin desincronización con master. ChatGPT firma y fusiona. Cambios de migración irreversibles quedan separados por un plan de rollout/rollback, aunque formen parte del mismo paquete lógico.
6. **No bloquear la ingeniería offline por acceso productivo**. Los adaptadores deben pasar fixtures y fake HTTP. Toda prueba de entitlement real, retención histórica, cuota y piloto requiere autorización específica y se informa como `EXTERNAL_UNVERIFIED` hasta entonces.
7. **Reporte ejecutivo por paquete**: capacidades entregadas, matriz de fuentes y cobertura, evidencia de tests/QA, riesgos P0/P1, principales brechas de producción y decisión PASS/BLOCKED. Avance global por criterios de aceptación, no por cantidad de PR/tests.

## 2. Paquete A — Datos e ingesta histórica integral (PR única de integración)

**Objetivo:** pipeline de obtención histórica e incremental verificable, reconciliado e idempotente, desde adaptadores de proveedor hasta RAW + SQLite + auditores. No declarar descargado el histórico externo mientras solo haya fixtures.

### A1. Inventario de escritores y exclusión real (offline)

- Completar matriz E13: entrypoints activos, librerías, jobs de `daily.sh`, opt-in, cron y manuales; separar rutas **potencialmente escritoras** de las que efectivamente escriben en la misma SQLite.
- Establecer **un archivo global de lock compartido**, con ruta explícita/configurable y namespace único, verificar herencia/vida útil del FD. Evitar doble adquisición del lock por procesos internos; si el orquestador envuelve un proceso hijo, no bloquearse a sí mismo.
- Unificar entradas operativas **sin activar producción**: por ejemplo, un launcher común para jobs que modifican `btc_research.db`, y rutas manuales seguras, evitando parchear ciegamente 24 módulos y sin confiar solo en `flock` (advisory). Documentar escritores que aún puedan esquivarlo; `GLOBAL_EXCLUSION_NOT_VERIFIED` mientras existan.
- Fixtures multiproceso: dos trabajos que compiten, bloqueo ANTES de HTTP/SQL, crash/timeout, liberación y reanudación, idempotencia de reintento y aislamiento de bases distintas.

### A2. Esquema y contratos de datos

- Revisar el esquema actual completo: tablas, PK/unique, índices, unidad, escala, columna UTC/as-of/vintage, flags de calidad, linaje `provider/endpoint/params/request` y nombres de cursores.
- Mantener **migraciones versionadas, forward-compatible y reversibles o con rollback documentado**. Ninguna migración de DB productiva antes de backup WAL-safe y autorización.
- Conservar RAW/BLOB+SHA, receipts de solicitudes nuevas, request lineage, audit status y `source_run`; **no fabricar receipts** para las 1.035 observaciones de Funding P1 heredadas.
- Validar separación entre schema/metadata declarativa, datos observados, ventanas verificadas y faltantes candidatos. No reasignar `entitlement_verified=true` porque el plan declare `tier=2`.

### A3. Adaptadores de fuentes y cobertura histórica

- **ResearchBitcoin Tier 2:** usar catálogo real y sus formas de dato (escalares y, si son necesarias, estructuras por dimensión con adapter explícito); política por métrica para inicio/fin, timestamp, unidad y raw scale; `entitlement=UNVERIFIED` hasta prueba oficial autorizada.
- **Bitview:** series diarias/cadenas y dimensiones pertinentes, evitar afirmar 180 días/retención total como disponibilidad verificada.
- **Yahoo Finance:** OHLCV por ticker/sesión y símbolos, cambios por split/revisión, no contar fines de semana de acciones como huecos. Preservar cierre UTC de `price_btc`.
- **FRED:** calendario/frecuencia por serie, observaciones mensuales/diarias/semanales, revisión de `realtime_start`/vintages y releases; no convertir ausencia de feriado en error.
- **Binance/Bybit:** OI 5m backward/forward, funding evento/settlement, límites/paginación/429/rate limit; namespaces de cursores separados y publicación de evidencias por página. La ventana antigua real del proveedor debe verificarse bajo permiso.
- Construir **ledger por proveedor×métrica×resolución×dimensión**, con `requested_window`, `observed_window`, `provider_verified_window`, gaps, stale, `unavailable_by_provider`, `entitlement`, `raw_receipt_coverage`, reintentos y `historical_completeness`. Estados: `NOT_CHECKED`, `AVAILABLE_VERIFIED`, `PARTIAL_VERIFIED`, `RESTRICTED`, `UNAVAILABLE_VERIFIED`; no deducir disponibilidad de ausencia de filas.

### A4. Ingesta integrada y recuperación

- Estructura común `plan → provider adapter → raw payload+SHA → parse/normalize → upsert idempotente → lineage/cursor → audits`, con transacciones cortas: **no mantener un BEGIN de escritura mientras se espera HTTP**.
- Controlar lotes/limites paginación, presupuestos de peticiones y cuotas, 429/5xx, retry con jitter/backoff acotado, dedupe por PK natural, commit parcial de páginas seguras y reanudación.
- Sin avance del cursor si página falla, gap invalida bloque, rollback o receipts no concuerdan. Distinguir respuestas `empty` de cobertura completa.
- Protección de no modificación de histórico previo mediante checksums PK/ROW, RAW/TEMPORAL/LINEAGE, FK/integrity y reconciliación de SQL.
- Probar contrato de adapter con fixture de respuesta de cada proveedor y una **integración E2E multiproveedor completamente offline**, incluida reanudación/crash, fechas frontera y discrepancia por formato.

### Definición de terminado A

**A_OFFLINE_ACCEPTED** únicamente si: todos los adapters necesarios tienen fixture contractual; pipeline integrado de extremo a extremo pasa en DB sintética; bloqueo falla cerrado con contender y sin HTTP/SQL cuando está ocupado; esquema/versiones, PK/upserts, lineage/cursors y RAW/TEMPORAL/LINEAGE son coherentes; recupera un crash sin pérdida/corrupción; runner completo PASS y Hermes certifica SHA.

**A_EXTERNAL_VERIFIED** es un estado **posterior** y diferente: entitlement/cuotas/ventanas externas observadas en llamadas reales previamente autorizadas y auditadas. Si no hay permiso, A puede cerrarse solo como `A_OFFLINE_ACCEPTED; EXTERNAL_VERIFICATION_PENDING`. La ingesta de todo el histórico disponible permanece pendiente. No confundir estos dos estados.

## 3. Paquete B — Motor analítico + informes profesionales

**Objetivo:** transformar datos persistidos y de calidad conocida en informes ejecutivos reproducibles y un skill visual exclusivo del proyecto.

- Unificar contratos de datos/fechas/unidad y *data provenance* del motor BTC/SPY/GOLD/Macro/derivados/on-chain; usar de forma trazable las tablas del Paquete A, evitando datos ficticios y fallback silencioso de fuentes antiguas.
- Construir **skill local de diseño propio**, en sustitución operativa controlada del diseño genérico `sereno`: paleta, tipografía, grid, tablas de datos, visualizaciones, portada, riesgos, evidencia, apéndices, exportación y reglas de consistencia. Versionar skill, previews y tests visuales/baseline.
- Reporte modular: executive summary, escenarios bull/base/bear y catalizadores, price action/market structure, derivados (OI/funding/liquidaciones), on-chain (STH/LTH, supply, SOPR, MVRV), flujos ETF cuando verificados, macro FRED y agenda de riesgos, fuentes, timestamp/cutoffs y calidad de cada dato.
- Quality gates por bloque: datos faltantes muestran `NO_DATA/UNVERIFIED`; no usar un gráfico sin su serie exacta; cifras reproducibles y unidades correctas, sin conclusiones de fuente con entitlement no verificado.
- PDF/HTML (según rutas del proyecto) con pruebas de render, legibilidad, enlace a evidencias y snapshot/regresión visual. Mantener Publication Gate ≥95/100 cuando ese sea el contrato vigente, con **FAIL CLOSED** ante datos imprescindibles ausentes.

### Definición de terminado B

Un informe completo de ejemplo, alimentado desde SQLite/fixtures controladas y sin internet, es reproducible en gráficos/números/textos, pasa Publication Gate y comparación de diseño. La generación real diaria no se activa sin aprobación. Una sola PR integrada, Hermes QA final y evidencia de render.

## 4. Paquete C — Orquestación integral, resiliencia y despliegue controlado

**Objetivo:** `ingesta → control calidad → cálculos → gráficos → informe → archivo → publication gate` como flujo unitario observable, con recuperación y permisos explícitos.

- Orquestador con DAG/etapas, entradas y outputs definidos, lock global compartido, idempotency keys, estado por run, reintentos diferenciados, correlación de logs, métricas de completitud y latencia; evitar ejecutar fuentes innecesarias al reparar una sola página.
- Probar `NO_LIVE` por defecto, dry-run, `--apply` separado, circuit-breakers, budgets API, error 429, crash en cada etapa, recuperación, backups WAL-safe solo cuando estén autorizados y política de rollback/migración.
- Prueba E2E en SQLite de TemporaryDirectory y fuentes fake, validación de reportes, archive, audit gates y Publication Gate con datos faltantes, cruce de tiempo y concurrencia de procesos.
- Redactar runbook de operación/incident response, matriz de responsables, medidas de observabilidad y cron cutover/rollback; inventariar y desactivar *writers* legados **solo con autorización productiva**.
- Realizar, **únicamente con autorización específica del propietario**, secuencia escalonada: (1) preflight de host/DB y backups, (2) un pequeño piloto por proveedor, (3) reconciliación post-piloto, (4) backfill por lotes/quota con checkpoints, (5) shadow runs del informe, (6) activación scheduler con rollback listo.

### Definición de terminado C

**C_OFFLINE_ACCEPTED** = runbook ejecutable sobre fixture, pruebas E2E de fallo/recuperación/concurrencia y publicación bloqueada por fallos relevantes, CI y Hermes PASS. **C_PRODUCTION_ACCEPTED** = autorizado, desplegado y observado con gates productivos aprobados; es un hito independiente y NO debe declararse sin evidencia real y permiso.

## 5. Gate QA transversal: mantener calidad sin multiplicar microciclos

**En cada commit de A/B/C:** pruebas de módulo cambiado, contract tests y CI; no ejecutar manualmente 52 suites preexistentes dos veces en cada commit. **Antes del merge:** runner completo del repo, suites de nueva capacidad, auditorías de seguridad/calidad, revisión de diff, tests de regresión y una orden formal a Hermes **sobre SHA exacto**. Si hay FAIL, ChatGPT corrige en el mismo PR y emite una orden **nueva** para el HEAD nuevo, revocando explícitamente la anterior.

QA de Hermes incluye: SHA/base/CI, Python/env, worktree nuevo limpio, tests por familia y RC, cantidad AST vs ejecutada, cambios en DB temporal, fallos de contrato, cierre de recursos y *STOP*. No tocar worktrees históricos ni ejecutar live HTTP/production DB/scripts. Solo ChatGPT acepta, cambia código o mergea.

**P0 (bloquea siempre):** DB real tocada sin permiso, fuga de credenciales, SQL corrupción, cursor falsificado, RAW sin lineage requerida, HTTP real inesperado, writer race, dato inventado en informe o activación cron no autorizada. **P1 (bloquea aceptación final):** falta de cobertura contractual de fuente necesaria, error de unidad/cadencia/vintage, recuperación no idempotente, falso PASS de completeness, report gates mal aplicadas. Los defectos P2/P3 se registran con owner y fecha, sin ocultarlos.

## 6. Protocolo de permisos, sin excepciones implícitas

Una aprobación de QA, una PR mergeada o la frase “continuar” **NO autorizan** API real, acceso a SQLite productiva, backup, migración, scheduler, publicación o envío de informes. Solicitar permiso concreto para: fuente, endpoint, volumen/rango, fecha, credencial gestionada, DB target, backup/rollback, límite de API y opción de cancelación. Los permisos son por actuación, no globales.

### Stop conditions

- Git SHA/base cambió → no usar QA anterior.
- Schema incompleto/tabla inesperada, flag de entitlement dudoso, locks inconsistentes, runner/CI rojo, datos sin lineage → STOP de esa capacidad y reporte.
- Un proveedor no entrega histórico más antiguo por política verificable → registrar `UNAVAILABLE_VERIFIED`, **no** inventar registros, no bloquear el resto de proveedores sin justificación.
- FRED/Yahoo y otros calendarios sin evidencia externa → no fabricar huecos.
- Funding P1 y reserva R10-D.2 → mantener evidencia de limitación; jamás atribuir receipts retrospectivos inexistentes.

## 7. Seguimiento ejecutivo por criterios de negocio

Cada paquete tendrá una ficha de seguimiento:

| Campo | Requerido |
|---|---|
| Capacidad de extremo a extremo | Qué hace el usuario que antes no podía hacer |
| Entregables verificables | Código integrado, schema, flujo, informe y pruebas |
| Evidencia | SHA, CI, QA Hermes, fixtures/DB checks, snapshots |
| Riesgos abiertos | P0/P1, datos/unidades/entitlement/seguridad |
| Estado | NOT_STARTED / IN_PROGRESS / OFFLINE_ACCEPTED / EXTERNAL_PENDING / PRODUCTION_ACCEPTED |
| Decisión | Merge, retener PR, piloto por autorizar o siguiente paquete |

**Definición de éxito final:** operaciones históricas recuperables por fuente y licencia realmente acreditadas; almacenamiento auditable; informes profesionales reproducibles; orquestación resiliente; y despliegue seguro observado en producción con consentimiento del propietario. “52 suites PASS” es una línea base de regresión, no un porcentaje de completitud de ese objetivo.
