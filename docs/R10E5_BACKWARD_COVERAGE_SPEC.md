# R10-E.5 — Planeación backward de cobertura histórica, sin tráfico real

**Estado:** contrato de arquitectura y tests OFFLINE. El propietario no ha autorizado descargar nuevas páginas de mercado, escribir en SQLite productiva ni habilitar el scheduler. ChatGPT implementa; Hermes valida sobre SHA congelado y no modifica código.

## Motivo

E.0–E.4 garantizan planificación forward y simulación de una página con gates. El objetivo final del proyecto requiere además **descargar el histórico disponible** por cada fuente, diferenciando lo realmente recuperable de lo que solamente se supone disponible. Esta primera fase E.5 cubre exclusivamente la **forma de planificar backward para OI 5m Binance y Bybit**, sin lanzar peticiones.

## Contrato del nuevo módulo

La función pura `plan_backward_fixture(coverage, availability)` recibe:

1. **Informe E0** con exactamente dos streams `BTCUSDT/open_interest/5m`, marcas MIN/MAX internas, conteo de gaps, `historical_completeness=NOT_VERIFIED`, `provider_availability=NOT_CHECKED`, `http_requests=0`, `sqlite_writes=0`.
2. **Fixture de ventana histórica**, con `mode=synthetic_fixture` y `permission_for_requests=false`, más una entrada por proveedor etiquetada `UNKNOWN` o `SYNTHETIC_WINDOW`. Los límites son artificiales y NUNCA se consideran evidencia comprobada del endpoint real. Se rechaza toda etiqueta que simule ser una autorización de producción.

**Fail closed global:** si falta baseline, existe un gap interno, el proveedor es desconocido, los timestamps están desalineados o no se dispone de límite sintético, la salida es `BLOCKED_INCOMPLETE_EVIDENCE` y no propone ninguna página. Una fuente no debe avanzar mientras otra tenga calidad desconocida.

**Ventana propuesta:** por cada proveedor con límite simulado anterior a su MIN registrado, se estiman los slots pendientes a 5 minutos. Se escoge el mayor backlog, desempatando por nombre. Se propone **una sola página**, limitada a 500 Binance o 200 Bybit. El límite backward en los scripts existentes se define como `requested_end_ms = MIN(stored) - 1` (milisegundo), para evitar volver a pedir la primera observación almacenada. La salida informa rango estimado y páginas proyectadas, pero **no rellena, persiste ni solicita** registros.

**Los cursores son distintos:** un backfill debe conservar su namespace `history_backward_v1_open_interest`, nunca modificar `forward_v1_open_interest`. Este módulo no mueve ninguno.

## Límites reales expresamente NO certificados

| Sistema | Tipo de backfill | Estado en E.5 | Trabajo posterior |
|---|---|---|---|
| Binance OI BTCUSDT 5m | backward paginado | Envolvente sintética | Comprobar ventana efectiva y cuota por endpoint con autorización |
| Bybit OI BTCUSDT 5m | backward paginado | Envolvente sintética | Comprobar cursor/paginación, límites y ventana efectiva |
| Binance / Bybit Funding | Eventos de liquidación, periodicidad variable | No incluido | Lineage prospectivo sin falsificar los 1.035 receipts heredados |
| ResearchBitcoin Tier 2 | Series diarias y otras formas dimensionales | No incluido | Verificar entitlement y cobertura por métrica, ventanas de API y cuotas |
| FRED | Series revisables/vintages | No incluido | Registrar cobertura y revisiones |
| Bitview / Yahoo | On-chain y precios OHLC | No incluido | Inventario exacto de unidades, timestamps y cierres |

**Importante:** `SYNTHETIC_WINDOW` y un resultado `PROPOSE_SYNTHETIC_BACKWARD_PAGE` no prueban acceso a datos, disponibilidad histórica, autenticación ni cobertura total. `real_provider_bound_verified=false`, `execution_authorized=false`, `historical_completeness=NOT_VERIFIED` y `provider_availability=NOT_CHECKED` en todos los casos. El planificador no importa SQLite ni librerías HTTP, no tiene CLI `--apply`, credenciales, rutas de DB ni integración a cron.

## QA y próximas puertas

- Tests nuevos: **15** (rango 200/201 de Bybit, política desconocida, límites por timestamp UTC, gap, baseline ausente, metadatos adulterados, separación de cursores y ausencia de efectos externos).
- Runner: **43 suites registradas**, cifra a verificar con AST independiente y mediante ejecución real. E.4 sigue siendo 16, E.3 13, E.2 16, E.1 9 y E.0 7 pruebas focales; E.5 agrega 15.
- Próxima etapa de implementación deberá incorporar **evidencia externa real por proveedor** en forma controlada, precedida por consentimiento explícito; esta PR no presenta ninguna prueba de disponibilidad. Despliegue de scheduler y acceso productivo sujetos a una autorización distinta del propietario.
- Conservar la reserva retrospectiva R10-D.2 y Funding P1 sin inventar metadata del pasado.
