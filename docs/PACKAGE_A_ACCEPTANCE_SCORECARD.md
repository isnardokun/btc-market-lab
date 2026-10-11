# Package A — Cuadro de avance por criterios de aceptación

**Corte:** 2026-10-11 · **PR:** #79 (borrador) · **Estado:** `A_IN_PROGRESS`
**Avance estimado de ingeniería offline:** **≈77%** · **Confianza:** orientativa, ±5 puntos porcentuales.
Este es un juicio técnico de los entregables frente a `docs/INTEGRATED_DELIVERY_PROGRAM_ABC.md`
y al Issue #76, **NO** un KPI generado automáticamente, un progreso temporal
lineal, la proporción del histórico de APIs descargado ni una certificación
de producción. La incertidumbre se reducirá con el QA independiente final
de Hermes y la revisión de rutas restantes. Los cinco ejes tienen peso
idéntico: 20% cada uno.

| Eje del contrato | Avance técnico estimado | Evidencia aceptada | Brecha que limita el porcentaje |
|---|---:|---|---|
| **A1 — Writers y exclusión** | **66%** | Inventario de entradas, opt-in `flock`, lock padre y lease protegido; ahora supervisión del grupo POSIX del hijo, kill+reap de descendientes en timeout/orfandad antes de liberar el lock, 7 nuevas pruebas multiproceso | `daily.sh` real, cron, invocaciones manuales y copias paralelas sin protección global certificada en host; launcher productivo no creado ni autorizado |
| **A2 — SQLite y linaje** | **85%** | Migración sintética aditiva v1→v3, RAW y SHA byte-exactos, PK/FK/unique, recibos y request provenance, `source_run`, cursors, auditorías independientes y WAL/rollback ensayados en fixtures | Revisión completa de esquema histórico productivo, tabla de dimensiones/provenance detalladas y migración con snapshot WAL-safe autorizada aún pendiente |
| **A3 — Adaptadores y cobertura** | **83%** | Seis proveedores simulados, ocho streams base E2E; Yahoo OHLCV BTC-USD/SPY/GLD con reconciliación legacy, sesiones bursátiles sin falsos huecos, RBN, Bitview, FRED vintages+paginación sintética, Binance/Bybit OI5m/funding events y manifiesto por fuente | Yahoo `range=max` sin paginación verificable, ausencia de fan-out de las cinco métricas por único request, más símbolos/calendarios reales, dimensionalidad Bitview/RBN, ALFRED, intervalos y cuotas/ventanas de funding **real** no verificados |
| **A4 — Pipeline y recuperación** | **90%** | Plan→legacy fake HTTP→RAW→SQLite→cursors→audits, paginación bounded 14d/90d/500/200, funding settlement 7d con respuesta saturada bloqueada; simulador de hasta 4 intentos, backoff/jitter determinista virtual, presupuestos globales, `source_run` v3 con múltiples fallos 429/500/503/timeout y recuperación exacta sin duplicaciones | Espera/Retry-After reales, límites de requests y coste externos, fetch real y controles de cuota bajo autorización; no hay cron/scheduler productivo habilitado |
| **A5 — Integración y aceptación** | **60%** | 67 suites registradas, 66/66 offline PASS, una live aislada, 7 pruebas nuevas de procesos hijos y 18 de reintentos, además de crash/race/rollback/migraciones con documentación reproducible | QA único de Hermes **no solicitado aún**; auditoría final de diff/seguridad, aceptación `A_OFFLINE_ACCEPTED`, resolución de issues residuales y merge aún pendientes |

**Cálculo:** (66 + 85 + 83 + 90 + 60) / 5 = **76,8%, redondeado a ≈77% offline**.
Cada porcentaje es una estimación explícita, **no** el número de pruebas
que aprobaron ni un conteo de commits. Solo cambia ante avances materiales
de criterios, nunca por nuevos tests de una misma capacidad.

## Validación más reciente de ingeniería

- El supervisor de cutover offline ahora termina los descendientes del mismo grupo POSIX tras timeout o si el proceso directo finaliza dejando nietos activos; mantiene el bloqueo hasta el reap, pero el host real y los descendientes evasivos siguen sin certificación.
- Política de reintentos OFFLINE: hasta 4 intentos con backoff+jitter virtual determinista, 128 mock requests y 32 MB por conjunto, reanudación v3 tras fallos consecutivos sin repetir páginas confirmadas; sin espera real ni red.
- Funding Binance/Bybit se pagina en ventanas sintéticas de 7 días UTC, sin inventar eventos cada 8 horas; respuestas con 1000/200 registros bloquean el cursor por riesgo de truncamiento.
- Yahoo OHLCV integra cinco métricas sobre BTC-USD, SPY y GLD con un parser legado independiente, RAW/SHA, sesiones diarias y verificaciones estrictas de precios y volumen. Yahoo `range=max` permanece sin paginación inventada.
- FRED mensual `CPIAUCSL` y diario `DGS10` ahora aceptan planificación
  sintética en ventanas consecutivas de hasta **90 días calendario**, separando
  días de consulta de cantidad de observaciones disponibles y preservando
  `realtime_start` de revisiones múltiples de una misma fecha.
- Un 503 en la segunda ventana conserva recibo y vintage anteriores y
  permite nueva ejecución sobre el `source_run` guardado; no crea
  cinco-minutos falsos ni huecos de fines de semana.
- Todo se prueba con clientes originales bajo HTTP congelado, SQLite temporal
  protegida por marcador y los mismos PRECOMMIT gates.
- Código+pruebas validados por GitHub Actions. **66/66 suites offline**; una
  integración potencialmente productiva sigue excluida.
- **No** se abrió ninguna API real, no se verificó el token Tier2 ni
  retención de fuente, no se accedió a SQLite productiva, no se cambió
  `daily.sh`/cron, ni se activó publicación.

## Tres porcentajes que NO deben confundirse

1. **≈77% — trabajo de desarrollo offline del Paquete A:** estimación arriba.
2. **0 validaciones nuevas de API/histórico real ejecutadas por esta PR:** el
   estado externo es `EXTERNAL_VERIFICATION_PENDING`, no una medición de
   la proporción de datos completos. Puede existir archivo legado fuera de
   este paquete; no se ha inventariado aquí como histórico descargado nuevo.
3. **0 autorizaciones de cutover/producción usadas por esta PR:** no equivale
   a decir que la arquitectura productiva anterior no existe; significa
   únicamente que no se han ejecutado operaciones en el host con este cambio.

## Próximos gates de cierre offline

Cerrar los casos importantes aún incompletos de adaptadores fuente y A1;
verificar contratos de unidades, temporalidad y formato; hacer revisión de
seguridad/diff y pruebas adversariales finales; **congelar una sola SHA**;
emitir UNA orden de QA independiente a Hermes. El QA puede cerrar
`A_OFFLINE_ACCEPTED` con `EXTERNAL_VERIFICATION_PENDING`, pero no puede
conceder permisos sobre APIs, SQLite, cron o deployments. La comprobación
de disponibilidad y descarga de TODO el histórico permitido es una
actividad posterior y por separado, sujeta al propietario.
