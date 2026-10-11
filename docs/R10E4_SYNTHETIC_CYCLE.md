# R10-E.4 — Ciclo único sintético: E0 → E1 → E2 → E3

**Gobernanza:** propietario autoriza operaciones productivas; ChatGPT escribe y gestiona el código; Hermes ejecuta únicamente pruebas bajo orden sobre SHA exacto. Issue #60. Esta etapa NO tiene autorización de uso de APIs, SQLite productiva ni scheduler.

## Alcance del ciclo

Se incorpora una función de simulación en scripts/market_r10e4_synthetic_cycle.py. Es una **prueba integrada, no un ejecutor**: no tiene CLI ni --apply, no acepta rutas de BD ni credenciales, no inicia scheduler y no usa un cliente HTTP real. Crea una SQLite v3 completamente nueva dentro de TemporaryDirectory; las respuestas de una sola petición se generan en memoria con un callback fake_fetch.

1. **E0** lee en modo SQLite RO la **misma fixture temporal** que, si pasan los controles, se usará para persistir la página ficticia; calcula atraso de ambos streams OI.
2. **E1** valida y selecciona la fuente con mayor backlog con desempate determinista (en la fixture ambos comienzan igual y selecciona Binance).
3. **E2** evalúa una política obligatoriamente etiquetada synthetic_fixture, con presupuesto, cooldown, circuit breaker, writer exclusion SIMULADO y frescura del plan. El resultado SHADOW_ELIGIBLE **no autoriza** live: execution_authorized es siempre false.
4. **E3/v3**: SOLO después de un SHADOW_ELIGIBLE invoca una única vez el colector forward ya existente con fetch=fake_fetch. Usa una SQLite recién creada y no puede recibir el path de la DB del operador. El colector inserta observaciones, BLOB, acquisition, run, forward receipt y cursor únicamente según el estado de la respuesta ficticia.
5. Tras el mock, calcula manifiestos por PK y SHA256 de las diez tablas market_context y ejecuta los tres auditores RAW, TEMPORAL y LINEAGE sobre un snapshot RO coherente, además de SQLite integrity_check y foreign_key_check. Si auditorías fallan o mutan filas PRE, el escenario no se marca aceptado.

## Pruebas y resultados esperados (NO resultados reales)

| Escenario sintético | Fetch fake | E4 state esperado |
|---|---:|---|
| Página OI 5m continua (+2) | 1 | MOCK_ONE_PAGE_ACCEPTED |
| Respuesta vacía | 1 | MOCK_EMPTY_STOP, sin avance de cursor |
| HTTP 429 simulado | 1 | MOCK_FAILURE_STOP, receipt failed, sin avance |
| Primer timestamp discontinuo | 1 | MOCK_FAILURE_STOP, 0 observaciones persistidas |
| Cuota cero | 0 | MOCK_PRE_GATE_BLOCKED |
| Circuito abierto | 0 | MOCK_PRE_GATE_BLOCKED |
| Lock SIMULADO ausente | 0 | MOCK_PRE_GATE_BLOCKED |
| Plan con más de 300 segundos | 0 | MOCK_PRE_GATE_BLOCKED |
| Cooldown no cumplido | 0 | MOCK_PRE_GATE_BLOCKED |
| Última barra ya almacenada | 0 | MOCK_NO_WORK |

Estas salidas son exclusivamente **evidencia sintética**. Ninguna contiene permiso de ejecución live, y todas mantienen historical_completeness=NOT_VERIFIED.

## Invariantes y límites

- 0 llamadas HTTP externas a mercados, 0 lectura o escritura de la DB productiva, 0 cambios de cron, servicios, daily.sh, migraciones o flags.
- El runner añade **una** suite nueva E4: **42 entradas estáticas** esperadas. Se requieren 16 tests focales E4 más regresiones E3/E2/E1/E0 y runner 42/42 **solo si la ejecución real lo confirma**.
- Las pruebas antiguas E2 y E3 ya quedaron diseñadas para tolerar crecimiento del registro (mínimos + presencia única de suites obligatorias).
- Este harness NO es un job recurrente ni un scheduler. No demuestra locks entre procesos, cuota real, backfill histórico, derechos ResearchBitcoin Tier 2, disponibilidad del endpoint, gestión real de fallos HTTP ni integridad sobre producción.
- La evidencia de R10-D.2 pendiente de inventario retrospectivo y Funding P1 (1.035 registros heredados sin receipts por solicitud) permanecen independientes.
- Las fuentes de prueba se limitan a Binance y Bybit OI. No se fabrican receipts históricos ni se amplía la cobertura de APIs.

**Flujo de aprobación:** ChatGPT publica PR en borrador → Hermes ejecuta pruebas exclusivamente offline en worktree nuevo → Hermes informa y STOP → ChatGPT revisa, corrige y decide merge. Para cualquier activación productiva debe mediar orden distinta del propietario.
