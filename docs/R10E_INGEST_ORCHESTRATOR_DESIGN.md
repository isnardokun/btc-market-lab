# R10-E — Contrato técnico de arquitectura y primera implementación offline

**Gobierno:** El propietario autoriza el alcance y las acciones productivas; ChatGPT diseña, modifica, publica commits/PR y revisa; Hermes **solo valida** mediante órdenes específicas y reporta. Issue #60: https://github.com/isnardokun/btc-market-lab/issues/60.

## Código existente y riesgo de integración

- scripts/market_history_forward.py: piloto forward una página OI 5m, Binance 500/Bybit 200, v3 con BLOB original, acquisition, run, receipt, cursor y gates de calidad.
- scripts/market_history_pilot.py / market_history_batch.py: backfill backward con checkpoints independientes. No invocar desde scheduler forward.
- scripts/market_context_ingest.py: ingestor generalista distinto al contrato estricto forward v3; no usar como sustituto automático.
- scripts/daily.sh ya tiene RBN_INCREMENTAL_ENABLED y MARKET_CONTEXT_DAILY_ENABLED opcionales; **mantenerlos apagados**. No se edita daily.sh con esta PR.
- ingestion/researchbitcoin_archive.py: histórico RBN de 14 días/petición; el tier introducido por CLI no acredita autorización de acceso Tier 2.
- FRED conserva vintages/revisiones; Yahoo utiliza sesiones de mercado; Bitview y métricas on-chain necesitan inventario de fecha/unidad. No confundir con OI.
- Funding heredado: 1.035 observaciones RAW sin receipts request-level; jamás fabricar metadata de HTTP.

## R10-E.0 — Implementación inicial

El nuevo scripts/market_r10e_plan_only.py es **PLAN_ONLY estricto** y está deliberadamente separado del ingestor:

1. Requiere --db explícita; no tiene base por defecto ni opción --apply. Se rechaza la ausencia de archivo.
2. Conecta únicamente con SQLite mode=ro, PRAGMA query_only=ON y BEGIN snapshot; no ATTACH ni escrituras ni imports de red.
3. Lee stamps de BTCUSDT/open_interest/5m en los dos proveedores; detecta ausencia de baseline, duplicados, desalineación y huecos de cinco minutos.
4. Usa un margen de cierre de 10 minutos. cutoff = floor((now-10min)/5min)*5min; next = MAX+5min; slots esperados = max(0, floor((cutoff-next)/5min)+1). El +1 corrige el anterior off-by-one inclusivo.
5. Calcula páginas estimadas para límites internos de 500/200, pero **no verifica que la API realmente entregue esos slots**.
6. Informa STATE PLAN_ONLY_READ_ONLY, http_requests=0, sqlite_writes=0, provider_availability=NOT_CHECKED e historical_completeness=NOT_VERIFIED. En presencia de hueco interno devuelve estado bloqueante; no propone reescritura.
7. Tests con SQLite de TemporaryDirectory y hora UTC inyectada, casos exactos de corte, 200/201, errores, lectura RO y rechazo de --apply.

Uso únicamente para **SQLite de fixtures**:

    python3 scripts/market_r10e_plan_only.py --db /ruta/synthetic.db --now-utc 2026-10-10T22:00:00Z

## R10-E.1+ — Diseño futuro sujeto a gates posteriores

**Contrato de adquisición:** mutex global coherente con cron/otros writers, no sostener una transacción escritora durante HTTP; después de obtener respuesta, transacción que recalcula MAX y falla ante concurrencia. Por cada página: BLOB SHA256, observations, source_run, request_lineage con direction, request_acquisition con endpoint y SHA, y cursor solo tras progreso válido. Status failed/empty guarda evidencia según política sin mover cursores.

**Orquestación:** presupuesto explícito, una página por stream en pilotos, cooldown y circuit breaker ante 429/403/451, DNS/TLS, respuesta incompleta, timestamps inválidos, gaps o cuotas agotadas; sin retries automáticos de intentos ya contabilizados.

**QA:** RAW, TEMPORAL y LINEAGE desde snapshot RO coherente de toda la SQLite, integridad/FK, manifest PRE↔POST PK+SHA, SHA del respaldo WAL-safe antes de cualquier operación productiva. Auditar todos los receipts después de cada página **antes** de la siguiente; no solo el último run.

**Semántica:** OI requiere 5m exacto; Funding conserva tiempo de settlement realmente declarado sin período fijo artificial; RBN d1 usa día UTC completado y unidades nativas; FRED registra vintages; OHLC atiende calendario de sesiones. La cobertura histórica recuperable es un dato observado, no una inferencia.

**Migraciones y scheduler:** esquema v3 vigente; propuesta v4 únicamente sobre SQLite sintética si resulta imprescindible. Shadow planner → una página live con autorización nueva → QA → segunda fuente con autorización → scheduler solo mediante aprobación expresa. No implementar daemon/cron ni activar banderas en este cambio.

**Fuera de alcance:** descargas, lectura de producción, cambios en daily.sh, backfill real, publicación Telegram, reportes o claims de completitud histórica externa.
