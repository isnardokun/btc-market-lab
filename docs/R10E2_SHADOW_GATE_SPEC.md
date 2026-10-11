# R10-E.2 — SHADOW gate sintético de elegibilidad (NO ejecución)

Rama de control-plane **offline**, complementa el planner R10-E.0 y dispatcher R10-E.1. Issue #60. **No autoriza producción.**

## Propósito

Evaluar una propuesta generada por el dispatcher usando políticas de prueba y reloj explícito. Esta capa no es un ejecutor ni un permiso operacional. La función pública es evaluate_shadow_gate(dispatch, fixture_policy, now=...), pura y sin efectos secundarios.

**Entrada:** dos JSON existentes en disco (propuesta offline + política etiquetada obligatoriamente mode=synthetic_fixture); nunca lee SQLite, ni credenciales, ni datos de APIs. El CLI exige --dispatch-json, --fixture-policy-json y --now-utc; **no acepta --apply**. No hay network client, migraciones, cron, escritura SQL ni import de otras rutas productivas.

## Controles

- Garantizar que el dispatcher informe PLAN_ONLY_DISPATCH, execution_authorized=false, HTTP=0, SQLite writes=0, historical_completeness=NOT_VERIFIED y a lo sumo una solicitud futura.
- Exactamente dos candidatos Binance/Bybit OI BTCUSDT 5m; solo páginas forward, no Funding ni backfill; longitud entre 1 y 500/200 según proveedor, endpoints UTC y cutoff de barras cerradas de 5 minutos.
- Fecha de planificación no futura ni anterior a 300 segundos; reloj UTC con offset; fecha de primera/última marca congruente con cantidad de slots (intervalos inclusivos).
- Políticas **solo simuladas**: budget remaining entre 0 y 100, max_requests_per_cycle=1, cooldown entre 30 y 3.600 segundos, circuit breaker booleano, writer_exclusion_simulated booleano y fecha opcional del último intento.
- Cualquier plan obsoleto, falta de cuota, breaker activo, cooldown, escritor no excluido o metadatos incongruentes **bloquea el SHADOW**. Un SOURCE_QA bloqueado o ausencia de velas nuevas no puede producir solicitudes.
- Aun cuando todos los mocks pasen, el resultado es SHADOW_ELIGIBLE con execution_authorized=false, http_requests=0, sqlite_reads=0, sqlite_writes=0; **no es READY_FOR_LIVE**. No se aceptan políticas declaradas como producción.

## Límites deliberados

No implementa lock real, rate-limit real, request ledger persistente, backoff ni adquisición HTTP. Es un contrato verificable mediante pruebas antes del desarrollo de un orquestador con drivers reales. No modifica el forward piloto v3, las tablas, ResearchBitcoin/FRED/Yahoo/Bitview, daily.sh o cron.

**Historical completeness** y entitlement del proveedor siguen NOT_VERIFIED. Funding histórico P1 (1.035 observaciones RAW heredadas sin request receipts) mantiene su reserva.

## QA exigida a Hermes (SIN tocar producción)

- test_market_r10e2_shadow_gate: **15 tests** esperados de controles simulados, CLI y runner AST.
- Regresión test_market_r10e_dispatch_offline: 9 tests; test_market_r10e_plan_only: 7 tests.
- Full runner: **40 entradas registradas** en tests/run_all.py según conteo AST. ¡Importante! Las cifras 39/40 reportadas previamente para E.0/E.1 no coinciden con la cantidad real de entradas versionadas (38/39). Registrar ambos valores por separado: **suite registry length** y **suite results actually executed** sin inflar una unidad.
- El usuario es propietario/aprobador de operaciones productivas; ChatGPT implementa/revisa; Hermes solo prueba SHA congelado y publica informe, STOP.

Todo QA se ejecuta con fixtures sintéticas y sin acceso DB real, requests mercado, escritura productiva, package install ni limpieza destructiva de worktrees.
