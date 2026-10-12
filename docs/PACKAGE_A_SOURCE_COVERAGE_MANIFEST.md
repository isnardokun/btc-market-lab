# Paquete A — Matriz de cobertura histórica por fuente (evidencia local)

**Estado:** `SOURCE_MANIFEST_LOCAL_EVIDENCE_PASS` puede significar únicamente
que los recibos y observaciones sintéticos del repositorio son coherentes.
**NO** significa `FULL_HISTORY`, licencia/entitlement comprobada,
`EARLIEST_PROVIDER_VERIFIED` ni que el API del proveedor esté disponible.

## Integración con el pipeline

`scripts/package_a_source_coverage_manifest.py::source_aware_manifest(conn)`
produce un manifiesto `provider × metric × symbol × frequency` a partir
del mismo snapshot SQLite utilizado por las auditorías RAW→SQL, FK,
provenance y source runs. No abre DB ni usa red. Se calcula:

- **Antes del COMMIT de cada página**: un error real de contrato temporal,
  escala/unidad o evidencia archivada bloquea completamente la transacción.
- **En la inspección read-only** de `inspect_fixture`: aparece como
  `source_manifest` con streams y errores reproducibles.
- **Al final de un lote** `run_synthetic_batch`, inclusive tras errores:
  aparece dentro de `result.audit.source_manifest`.

Las tablas versionadas `package_a_receipts`, `package_a_observations`,
`package_a_cursors`, `package_a_request_provenance` y las ejecuciones
`package_a_source_runs` siguen siendo la fuente de verdad. Esta vista
es **derivada**: no exige una migración adicional ni crea tablas nuevas.

## Matriz de calendarios de fuente

| Fuente / frecuencia | Interpretación correcta de ausencia | Diagnóstico permitido |
|---|---|---|
| ResearchBitcoin y Bitview `d1` | Posibles días sin punto dentro del intervalo consultado; retención externa desconocida | Solo candidatos de huecos **locales** en grid UTC diario |
| Yahoo `BTC-USD d1` | Posibles velas locales de Bitcoin ausentes; no inferir retención de Yahoo | Grid UTC diario **local**, no cobertura externa |
| Binance y Bybit `open_interest 5m` | Posibles cinco-minutos locales ausentes | Grid UTC 5m, sin inferir que el API entregue toda la historia |
| Binance y Bybit `funding_settled event` | Timestamps de eventos liquidados, **no** velas 5m u 8h artificiales | Sin huecos fijos |
| FRED `d1` | Días hábiles/publicaciones/feriados; calendario externo no verificado | **No** proyectar días faltantes |
| FRED `weekly / monthly` | Calendario de serie y `realtime_start`; una misma observación puede tener varias revisiones | Registrar observación y vintage por dimensiones; **no** forzar grid |
| Yahoo acciones (futuro contrato) | Sesiones de mercado/feriados; se necesita calendario real | **No** proyectar fines de semana como huecos |

Para cada stream, el manifiesto distingue `requested_first_utc` de
`archived_first_utc`, y diferencia `successful_request_windows`
de `earliest_provider_verified_utc` (**siempre `null`** en offline).
Cuenta `ok/failed/empty/rejected` por separado, recibos exitosos con RAW,
errores HTTP con payload, timeouts sin respuesta, número de filas archivadas,
fechas distintas, dimensiones/vintages y cursor persistido.

Las ventanas de éxito se fusionan únicamente como evidencia de **recepción
local** de solicitudes simuladas; un retry de la misma página cuenta como
intento adicional pero no crea una falsa ventana distinta. Un evento
`empty` ni el éxito sintético acreditan que se verificó el rango total
disponible del proveedor.

## Estados y gates

- `SOURCE_MANIFEST_LOCAL_EVIDENCE_PASS`: integridad del manifiesto derivado,
  **no de la cobertura histórica**.
- `SOURCE_MANIFEST_LOCAL_EVIDENCE_FAIL`: corrupción de hora UTC canónica,
  ventana invertida, unidad/escala contradictorias o evidencia archivada
  no atribuible; impide COMMIT en el gate de ingestión.
- `BLOCKED_SOURCE_MANIFEST_STREAM_BUDGET`: demasiados streams para el
  presupuesto de evaluación; no se emite matriz parcial con falso PASS.
- `OFFLINE_LOCAL_GAP_CANDIDATES`: huecos observados en grid aplicable,
  todos `request_ready=false` y `provider_verified=false`.
- `BLOCKED_GAP_BUDGET` o `NOT_SCANNED_GRID_BUDGET_OR_ALIGNMENT`:
  el cálculo de huecos se omite o rebasa presupuesto; **no** prueba que
  las filas archivadas estén corruptas ni autoriza solicitudes.
- `NOT_APPLICABLE_SOURCE_CALENDAR_OR_EVENTS`: FRED y funding event,
  sin convertir ausencias en falsas deficiencias.

El manifiesto consulta la **existencia** de BLOB RAW mediante
`raw_payload IS NOT NULL`; no vuelve a materializar todos los BLOB
históricos, que ya son validados por el auditor independiente de hashes.

## QA y pruebas offline

La suite `tests/unit/test_package_a_frozen_legacy_transport.py` incluye
un E2E con **8 streams sobre 6 proveedores**, con un mismo SQLite temporal,
y pruebas adicionales de:

- Una misma fecha FRED con dos `realtime_start` distintos;
- Funding event sin inventar cinco-minutos faltantes;
- Streaming de solicitudes exitosas/duplicados/fallidas/429/503/timeout;
- Manifestaciones `failed-only` y `empty-only` sin supuesta disponibilidad;
- Anomalías temporales UTC de recibos que bloquean páginas posteriores;
- Huecos locales en RBN vs ausencia de verificación API;
- Presupuestos por slots, ventanas de huecos y cantidad de streams.

Ejecución permitida: `python3 -m unittest -v
tests.unit.test_package_a_frozen_legacy_transport` contra fixtures. El
runner completo `python3 tests/run_all.py` mantiene **1 integración live
fuera de ejecución**; nunca invocar esa suite con las rutas del proyecto.

## Límites de aceptación externa

La aplicación futura requiere autorización por fuente/endpoints/rangos/cuotas,
identidad real del token RBN Tier2, retención y ventanas de API, calendario
FRED releases/vintages, sesiones Yahoo y horarios reales de funding, además
de inventario de writers y migración/backup de SQLite. Ningún gate de este
documento concede dichas autorizaciones. Hermes permanece **STOP** hasta
emitir una única orden final sobre SHA congelado de PR #79.
