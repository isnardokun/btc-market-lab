# R10-E.6 — Acceso, catálogo y planificación histórica multifuente

**Estado: desarrollo y QA OFFLINE.** El propietario conserva toda autorización productiva; ChatGPT desarrolla/gestiona PR y Hermes solo ejecuta pruebas con SHA congelado. Ningún endpoint real, token, SQLite productiva, cron ni backup son usados por este cambio.

## 1. ResearchBitcoin: contrato actual demostrado por lectura del código

**Catálogo existente:** `ingestion/researchbitcoin_catalog.py` contiene **13 métricas escalares d1** de Bitcoin (cost bases STH/LTH, MVRV y MVRV Z STH, SOPR STH/LTH, ganancias/pérdidas realizadas, supply in profit y liveliness), cada una con endpoint, unidad y raw_scale. Dos métricas de Supply-in-Profit tienen `raw_scale=fraction_0_1`. No convertir estas unidades silenciosamente en porcentaje de otro orden de magnitud. La compatibilidad de un slug en el catálogo no demuestra licencia o disponibilidad histórica.

**Historiador existente:** `ingestion/researchbitcoin_archive.py` admite plan incremental/histórico, agrupación de fechas y ventanas de **14 días**, registra `archive_ingest_windows`, y exige start explícito en modo Tier 1/2. El código `allowed_earliest(cutoff,tier,requested_start)` permite un corte anterior al año móvil al declarar tier > 0. **Esa declaración CLI no es prueba de acceso**. La tabla `archive_sources` conserva `plan_tier` y `entitlement_verified` por separado, inicializando esta última sin verificación automática.

**Nuevo E.6:** `scripts/market_r10e6_rbn_tier2_shadow.py` contiene una función pura `plan_rbn_tier2_fixture` y `inventory_static`, sin CLI, red, SQLite, credenciales ni --apply.

- Solo acepta `coverage.mode=synthetic_fixture` con inventario explícito de fechas por cada una de las **13 métricas**, incluyendo inventarios vacíos. No confundir ausencia de inventario con serie comprobada sin filas.
- `entitlement.mode=synthetic_fixture`, `reported_tier=2`, `network_authorized=false`; `attestation=MISSING` bloquea todas las propuestas. `attestation=SIMULATED` habilita únicamente el ejercicio sintético, jamás acredita un token real.
- Corte último día UTC completado; `from_time` inclusivo, `to_time_exclusive` exclusivo. No solicite el día actual abierto; no genere ceros para días ausentes.
- Expone faltantes, ventanas <=14 días y máximo **13 propuestas** por plan, asignadas con round-robin por métrica. No almacena cursores, ventanas `ok/empty`, payloads ni snapshots.
- Todos los informes incluyen `real_entitlement_verified=false`, `real_provider_window_verified=false`, `historical_completeness=NOT_VERIFIED`, `execution_authorized=false`, `http_requests=0`, `sqlite_reads=0`, `sqlite_writes=0`.
- Por ahora SOLO métricas escalar d1 existentes. Los endpoints histogram/distribution/matrix deben obtener adaptadores de esquema específicos; no reducir un vector a un escalar inventado.

**Gate futuro, NO autorizado aquí:** comprobar los derechos del token ResearchBitcoin Tier 2 por mecanismo y respuesta oficiales, cuota y primera fecha realmente recuperable **sin incluir tokens en PR/logs**. Solo después, con permiso del propietario, se podrá ordenar una petición live acotada, respaldada por baseline/backup WAL-safe y auditoría. Confirmar también la documentación externa y términos de consumo: E.6 aún no los verificó.

## 2. Otras fuentes: rutas existentes y riesgos por conciliar

| Fuente | Código existente | Ventana / semántica de código | Estado en E.6 | Control pendiente |
| --- | --- | --- | --- | --- |
| Bitview | `ingestion/bitview_history.py` y `ingestion/config.py` | Serie d1, ventanas históricas configuradas hasta 180 días, progreso persistente | INVENTARIADA, NO CONSULTADA | catálogo completo de series realmente disponibles, unidad, cutoff, fallos, offsets y migraciones |
| Yahoo Finance | `ingestion/yahoo_history.py` | `range=max`, OHLCV diario, tickers del catálogo local | INVENTARIADA, NO CONSULTADA | sesiones, símbolos, precios corregidos/revisiones, máximos realmente ofrecidos |
| FRED | `ingestion/ingest_fred.py` y `config.FRED_SERIES` | 12 series en configuración, backfill inicial desde 2024, revalidación 60 días, fechas realtime/vintage | INVENTARIADA, NO CONSULTADA | decidir extensión a histórico anterior, series/paginas, archivo completo de vintages |
| Binance/Bybit OI | `scripts/market_history_forward.py`, backfill backward existente y E0–E5 | 5m, 500/200 por página, timestamps UTC, raw/receipt v3 | SIMULADO, NO NUEVAS DESCARGAS | ventana real proveedor, backfill por capas, mutex, gates por página |
| Funding | legado y `market_history_pilot.py` | Eventos de funding, no asumir 8h constantes | PENDIENTE | adquisiciones prospectivas, sin fabricar recibos para 1.035 RAW legacy |

La fecha 2024 para FRED es **configuración de backfill inicial actual**, no fecha histórica mínima del proveedor. `range=max` de Yahoo es la petición del código, **no garantía de recibir toda la historia**. El episodio R10-D.2 conservó evidencia operativa QA PASS con reservas de manifests retrospectivos; no declararla subsanada aquí.

## 3. Próximos pasos lógicos antes del cron

1. **R10-E.7 propuesto:** a partir de una fuente de evidencia contratada, comprobar el entitlement real de ResearchBitcoin y preparar una matriz por métrica (endpoint, shape, start/end accesibles, revisiones, cuota, costo). Por ahora será solo diseño y tests; **ningún HTTP live** sin nuevo permiso.
2. Implementar mutex/writer exclusion con SQLite sintética y procesos controlados, más auditoría global RAW/TEMPORAL/LINEAGE que cubra fallos de intentos. Simulación shadow ya existe, pero no equivale a exclusión mutua productiva.
3. Para toda API, separar **histórico recuperable**, completitud de la ventana observada, contigüidad interna, frescura y trazabilidad. No confundir una respuesta vacía con histórico completo.
4. Solo tras PRs/QA y **autorización distinta del propietario**, piloto HTTP real limitado y validado. Scheduler se plantea después de pilotos reconciliados; `daily.sh` sigue con `RBN_INCREMENTAL_ENABLED` y `MARKET_CONTEXT_DAILY_ENABLED` desactivados.

## 4. QA

Se esperan **15 pruebas E.6** y **44 entradas de suites** en `tests/run_all.py` (antes 43), más la regresión E.0–E.5. Deben ejecutarse en nuevo worktree con fixtures sintéticas. Hermes informa RC, conteos y fallos y hace STOP. ChatGPT corrige y revisa.

**No se aprobará un reporte que convierta SIMULATED en VERIFIED, que afirme tener acceso Tier 2 solo por `plan_tier=2`, o que cree requests o filas productivas.**
