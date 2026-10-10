# R10-E.1 — Dispatcher de planificación OI (OFFLINE, NO EJECUTABLE)

**Gobierno vigente:** el propietario autoriza producción, ChatGPT modifica/revisa/integra, Hermes ejecuta únicamente las pruebas indicadas. Continúa Issue #60 y R10-E.0 merge PR #61.

## Propósito

El planificador R10-E.0 puede medir el backlog OI BTCUSDT/5m por Binance y Bybit desde una SQLite RO explícitamente indicada. El dispatcher R10-E.1 transforma **solo** ese informe de cobertura en una recomendación determinista de cuál stream atender primero, para diseñar una futura ingesta serializada.

**No hace HTTP ni escrituras, no importa ningún cliente de mercado ni contiene --apply.** La salida no se puede conectar automáticamente a cron, scripts/daily.sh ni a una transacción de producción.

## Contrato y criterios de bloqueo

- Input: resultado de scripts/market_r10e_plan_only.plan_snapshot, exactamente dos streams OI 5m con límites 500 Binance y 200 Bybit, estado PLAN_ONLY_READ_ONLY, HTTP=0, writes=0, historical_completeness=NOT_VERIFIED y provider_availability=NOT_CHECKED.
- Evaluación: cualquier stream con status BLOCKED_ (snapshot ausente, timestamp desalineado, duplicado, gap interno, MAX posterior al cutoff) **bloquea todo el ciclo**, aun si el otro stream está apto. Una entidad faltante, duplicada, mal tipada o con cutoff inconsistente falla cerrada.
- Candidatos elegibles: status PLAN_ONLY con >=1 slot cerrado pendiente. Dos fuentes actualizadas implican NO_CLOSED_BAR.
- Selección: mayor número de slots pendientes; empate resuelto por nombre de proveedor, orden estable. Una sola página propuesta para el siguiente ciclo futuro. Requiere confirmar el resultado de la API real, que puede no devolver la página estimada.
- Salida: decision PROPOSE_ONE_PAGE_ONLY / NO_CLOSED_BAR / BLOCKED_SOURCE_QA; selected indica provider, rango UTC solicitado, máximo de observaciones esperadas y número estimado de páginas. Incluye execution_authorized=false, live_apply_available=false, maximum_future_requests_if_authorized=1, http_requests=0 y sqlite_writes=0.
- El cutoff UTC refleja barra cerrada menos margen de 10m; planned_at_utc indica reloj del análisis. Nunca confundir esos campos con disponibilidad efectiva de la API.
- Los checkpoints forward y backward NO se usan/intercambian en este dispatcher. No se calculan Funding/ResearchBitcoin/FRED/Bitview/Yahoo aquí.

## Seguridad y límites

- No intenta adquirir lock exclusivo ni comprobar escritores activos: **la planificación no acredita ventana productiva limpia**.
- La cobertura recuperable de proveedor permanece NOT_CHECKED; historical_completeness=NOT_VERIFIED.
- No se realizan llamadas HTTP simuladas contra APIs reales; únicamente SQLite TemporaryDirectory + Python stdlib, incluida comprobación del CLI por subprocess.
- Se conserva R10-D.2 con reserva documental, Funding P1 (1.035 RAW heredadas sin receipts) sin atribución retroactiva.

## QA requerida

La suite tests.unit.test_market_r10e_dispatch_offline (9 pruebas) debe verificar prioridad por backlog, empate, sin trabajo, gap/ausencia de baseline, metadata adulterada, read-only SQLite sintética, CLI sin --apply, error DB faltante y conteos HTTP=0. tests/run_all.py agrega esta suite, elevando expectativa de 39 a **40 suites**. Reportar cifras ejecutadas reales y códigos de retorno.

Hermes SOLO ejecuta QA sobre el SHA de PR y se detiene. ChatGPT corrige defectos. Prohibido merge automático, HTTP de mercado, acceso SQLite productivo, scheduler, reparaciones sobre código por Hermes y limpieza destructiva de worktrees anteriores.
