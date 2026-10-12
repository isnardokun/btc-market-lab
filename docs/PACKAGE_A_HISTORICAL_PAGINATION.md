# Paquete A — paginación histórica controlada por contrato de fuente

**Estado:** `OFFLINE_SOURCE_WINDOWS_PLANNED` (solo planificación y
simulación; **no** certificación de retención ni descarga real).

## Alcance integrado

`scripts/package_a_historical_window_planner.py` incorpora la etapa
`plan → ventanas nativas → HTTP simulado del cliente existente → RAW/SHA →
normalización → SQLite → receipt + cursor → auditoría → recuperación`.
Cada ventana tiene inicio UTC inclusivo, fin UTC exclusivo, ordinal
consecutivo y un SHA-256 determinista del plan. El manifiesto de todas las
respuestas debe estar completo y validado **antes de iniciar SQLite**.

La función `plan_offline_historical_windows` nunca abre red ni SQLite.
`bind_offline_frozen_history` exige un `FrozenPageBody` por cada ventana;
no acepta enlaces, callbacks, conexiones, tokens ni un número parcial de
respuestas. `run_offline_planned_history` ejecuta exactamente las ventanas
planeadas mediante los constructores originales de HTTP, pero sustituyendo
el socket por una respuesta congelada (in-memory). Todos los bytes y los
hashes se almacenan en SQLite **temporal marcada**, nunca en producción.

`recover_offline_planned_history` usa el registro persistido `source_run`
(v3), verifica todas las páginas previamente exitosas por origen, ventana,
unidad/escala y SHA de respuesta y retorna al primer ordinal no aplicado
o fallido. El lock POSIX abarca todo el preflight y el lote de reintento.
Una reanudación completada consume **cero solicitudes**. No se avanza el
cursor cuando falla una página 429/503/timeout.

## Política de ventanas: conservadora, solo sintética

| Fuente y datos | Presupuesto local por ventana | Restricción |
|---|---:|---|
| ResearchBitcoin, series catalogadas `d1` | 14 días | Contrato sintético del parser; entitlement Tier2 y rango real no verificados |
| Bitview, series `d1` reconocidas | 14 días | Política conservadora local, **no** límite oficial verificado |
| Binance BTCUSDT, OI `5m` | 500 muestras | Coincide con parámetro del constructor simulado; límites efectivos no verificados |
| Bybit BTCUSDT, OI `5m` | 200 muestras | Coincide con parámetro del constructor simulado; límites efectivos no verificados |
| FRED, series catalogadas `d1/weekly/monthly` | 90 días calendario | Solo dividir solicitudes; **NO** exigir 90 observaciones; conservar vintages y releases sin inferir calendario |
| Binance/Bybit, `funding_settled event` | **7 días calendario** por página sintética | **Solo ventanas de consulta**; no se infieren observaciones cada 8 horas; respuesta de 1000/200 filas se bloquea por posible truncamiento |

| Yahoo BTC-USD `range=max` | **Bloqueado** | El cliente legado solicita `range=max`; no adjudicarle ventanas parciales inexistentes |
| Acciones Yahoo / símbolos no reconocidos | **Bloqueado** | Requieren contratos fuente y calendario de sesiones comprobados |

**Máximo:** 32 ventanas por lote. Una ventana de 33 páginas produce
`BLOCKED_HISTORICAL_PAGE_BUDGET` y **cero páginas parciales**; el operador
debe fragmentar el período de forma explícita. Los rangos 5m deben
alinearse exactamente al minuto 0/5/10… y los diarios a 00:00 UTC.
Una frontera fraccional/no UTC, respuesta ausente, RAW vacío fuera de
timeout, HTTP desconocido o más de 2MB simulado bloquea antes de abrir SQL.

Una planificación es **no ejecutable externamente**: todas las ventanas
indican `request_ready=false`. Ni un lote con cero huecos locales puede
elevar `historical_completeness` a completo, ni marcar
`entitlement_verified=true`. No se inventa la fecha inicial de
retención ni un histórico exhaustivo.

## Evidencia E2E

Suite `tests/unit/test_package_a_historical_window_planner.py`:
- 30 días RBN paginados 14+14+2 → **30 observaciones, 3 RAW receipts**;
- Binance 501 velas OI, 500+1 → **501 observaciones, 2 receipts**;
- Bybit 201 velas OI, 200+1 → **201 observaciones, 2 receipts**;
- Bitview 29 días, 14+14+1 (plan, sin afirmar ventana oficial);
- FRED mensual 122 días calendario, 90+32 → observaciones *sparse* y múltiples `realtime_start` del mismo día preservados en SQLite;
- FRED diario con días ausentes → ningún hueco inferido de fines de semana, releases o feriados;
- FRED 503 en segunda ventana → source_run bloqueado y reanudación precisa sin alterar vintage ni RAW anterior;
- Yahoo legado `range=max` permanece bloqueado para paginación automática; funding `event` solo acepta ventanas sintéticas, sin garantizar retención real;
- 429 en la segunda ventana RBN: 14 registros persistidos, otras páginas
  sin ejecutar; tras recuperación, 30 registros sin duplicar los primeros 14;
- Tampering de primer RAW, respuestas insuficientes o tamaño/status inválido
  y contención `flock`: bloqueo antes de nuevas escrituras;
- Lote ya completo → recuperación NOOP sin request ni SQL adicional;
- Validación estricta de fechas, presupuesto y catálogo del proveedor.

Todas las respuestas proceden de fixtures programáticos y el mismo
cliente/orquestador ya existente. **Esta evidencia no corresponde a una
consulta real a ningún API**.

## Pendiente para entrega en producción

Requiere permisos separados para verificar identidad de token, rangos
históricos reales, políticas y cuotas, errores `429` y paginación, ventana
real de Bybit/Binance, revisiones FRED y calendario Yahoo. Requiere también
inventario físico del lock global, respaldo WAL-safe y revisión de
migración/rollback sobre el host autorizado. Ninguna de estas acciones se
autoriza ni activa mediante la PR.
