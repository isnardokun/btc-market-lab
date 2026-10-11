# Paquete A — política de reintentos offline con recuperación de checkpoints v3

**Ámbito:** simulación determinista, **sin red, sin esperas reales,
sin temporizadores ni scheduler**, con HTTP congelado explícito y
`package_a_synthetic.db` en `TemporaryDirectory` marcado. No utilizar
para invocar API productivas ni como implementación de `Retry-After` real.

## Qué implementa

`scripts/package_a_offline_retry_policy.py` enlaza tres componentes
existentes, **sin duplicar escrituras ni recalcular la historia desde cero**:

1. `plan_offline_historical_windows`: construye el plan fuente de rango UTC
   exactamente contiguo y digest SHA-256 por contrato, símbolo,
   unidades/escala y query nativa legada.
2. `run_synthetic_batch`: realiza la primera ejecución contra HTTP falso;
   cada página es una transacción corta RAW/SHA→SQL→`source_run`→auditoría
   PRECOMMIT y cursor seguro. Al detectar falla detiene las páginas futuras.
3. `recover_synthetic_batch`: tras suministrar **manualmente** el manifiesto
   completo de respuestas de la siguiente simulación, toma un único lock
   cooperativo, verifica el run persistido y los SHA de las páginas ya
   confirmadas, y reanuda en el primer ordinal sin éxito, creando un
   **nuevo** `source_run`. Los anteriores no se reescriben.

### Recuperación tras fallos sucesivos

La diferencia crítica respecto de un simple reintento es la identidad
de la ventana. Si un plan original tiene tres páginas, ocurre lo siguiente:

| Intento simulado | Ventanas consultadas | Resultado | Run persistido |
|---|---|---|---|
| 1 | 0 correcta, 1 falla 429 | Bloqueado, ventana 2 sin consultar | `run_A`, plan local 0–2 |
| 2 | 1 correcta, 2 falla 503 | Bloqueado, ventana 0 no se repite | `run_B`, plan local 1–2 |
| 3 | 2 correcta | Aceptado sin repetir ventanas 0/1 | `run_C`, plan local 2 |

`source_window_base_index` identifica el desplazamiento absoluto respecto
del primer plan. La **recuperación del run_B** debe recibir como plan original
únicamente sus dos ventanas (1 y 2); pasarle de nuevo tres ventanas fallaría
`BLOCKED_RECOVERY_PLAN_LENGTH_DRIFT`. El código conserva ese contexto
automáticamente para sucesivos intentos, incluso cuando dos fallos
consecutivos ocurren en **el mismo ordinal** o uno es un timeout sin
respuesta RAW.

## Política de clasificación (fail-closed)

| Resultado del intento anterior | Decisión |
|---|---|
| `SYNTHETIC_HTTP_429`, `500`, `503` | Candidato a retry offline explícito |
| `SYNTHETIC_TRANSPORT_TIMEOUT` | Candidato; **sin inventar cuerpo ni SHA de respuesta** |
| HTTP 200 no parseable o `status=rejected` | No retry; inspeccionar RAW y contrato |
| `PAGE_EMPTY_UNVERIFIED` | No retry automático; se desconoce la historia del proveedor |
| Resultado saturado de funding | `rejected`, no retry del mismo intervalo: requiere nuevo plan más acotado |
| `BLOCKED_WRITER_BUSY` | No retry dentro de esta simulación y **ningún source-run persistido** |
| Lote completo | `RETRY_NOT_REQUIRED`, nunca consumir manifestos de futuros intentos |
| Política agotada | `RETRY_BUDGET_EXHAUSTED`, todos los receipts anteriores permanecen |

La clasificación utiliza explícitamente `batch.pages[].status` y
`failure_class` propagados por el orquestador, sin confundir HTTP 200
fallido con HTTP 503 ni reintentar una respuesta inválida.

## Límites obligatorios

`OfflineRetryPolicy` impone **1–4 intentos máximos** incluyendo el
primero, un total máximo de **128 solicitudes simuladas** y máximo de
**32 MB de bytes RAW** congelados en todos los manifiestos.

La sugerencia de espera es **exponencial con jitter determinista**
(5 segundos iniciales por defecto, máximo 60 segundos) basada en
el digest del plan, ordinal y número de intento. Se registra únicamente
como `virtual_delay_seconds`, con
`clock_wait_executed=false` y `real_http_authorized=false`.
**Ninguna espera sucede en la ejecución**, por lo que estos tiempos no
sirven para afirmar cumplimiento real de cuotas, intervalos del
proveedor o la cabecera `Retry-After`.

`run_offline_retry_rehearsal` exige *todos* los manifiestos de
respuestas falsas de cada intento planeado, en listas explícitas. Se
verifican sus tipos, cuerpo, tamaño, status y cantidad **antes** de abrir
SQLite. Nunca se invoca un callback, un fetcher, un endpoint auténtico
o una recolección automatizada de respuestas de red.

## Estados de salida

- `OFFLINE_RETRY_REHEARSAL_ACCEPTED`: todas las ventanas del plan sintético
  completadas, sin exigir verificación externa;
- `OFFLINE_RETRY_REHEARSAL_AWAITING_OPERATOR`: falla elegible y presupuesto
  aún disponible, pero no se facilitó otro manifiesto;
- `OFFLINE_RETRY_REHEARSAL_EXHAUSTED`: agotado el máximo de intentos sin
  cobertura local completa;
- `OFFLINE_RETRY_REHEARSAL_STOPPED`: falla que no debe reintentarse
  automáticamente (rejected/empty/lock/no receipt).

Se informa `attempts[].source_run_id` **solo si existe una página
persistida**. Los registros de intentos incluyen ordinal global,
identidad del run precedente, páginas ejecutadas, nueva observación,
`status`, `failure_class` y recomendación determinista.

## QA E2E

Suite `tests/unit/test_package_a_offline_retry_policy.py`: 18 pruebas
nuevas que cubren el **camino completo SQL real temporal**:

- Dos fallos consecutivos en páginas distintas (429 y 503), tres runs y
  30 puntos RBN únicos sin duplicación;
- Dos fallos repetidos en la misma página (timeout sin RAW y HTTP 500),
  recuperación desde el mismo ordinal y cursor correcto;
- Retry con máximo dos intentos: agotamiento conservando los receipts;
- 200 inválido y 200 vacío: fallan permanentemente sin iterar;
- Diferencias de SHA en una página previamente exitosa: bloquear antes
  de nuevas escrituras;
- Lock ocupado: cero mock HTTP, cero SQLite y cero run persistido;
- Lote aceptado en el primer intento: no ejecutar un segundo aunque el
  usuario haya suministrado un manifiesto;
- Tipos inválidos, intentos adicionales, respuestas ausentes, presupuesto
  de solicitudes y planificación de Yahoo no permitida.

**Lo que continúa pendiente para producción:** comprobar límites efectivos
de API/retención/Tier2, `Retry-After` real, evaluación de cuota y costes,
lock global de *todos* los escritores en host, pruebas de integración
autorizadas y QA independiente sobre SHA congelado. Esta feature no
modifica `cron`, `daily.sh`, configuración del host, DB productiva ni
publica reportes.
