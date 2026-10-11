# Package A — Cuadro de avance por criterios de aceptación

**Corte:** 2026-10-11 · **PR:** #79 (borrador) · **Estado:** `A_IN_PROGRESS`
**Avance estimado de ingeniería offline:** **≈84%** · **Confianza:** orientativa, ±5 puntos porcentuales.
Este es un juicio técnico de los entregables frente a `docs/INTEGRATED_DELIVERY_PROGRAM_ABC.md`
y al Issue #76, **NO** un KPI generado automáticamente, un progreso temporal
lineal, la proporción del histórico de APIs descargado ni una certificación
de producción. La incertidumbre se reducirá con el QA independiente final
de Hermes y la revisión de rutas restantes. Los cinco ejes tienen peso
idéntico: 20% cada uno.

| Eje del contrato | Avance técnico estimado | Evidencia aceptada | Brecha que limita el porcentaje |
|---|---:|---|---|
| **A1 — Writers y exclusión** | **74%** | Inventario de entradas, opt-in `flock`, lock padre y lease protegido, supervisión del grupo POSIX, auditoría de rutas y huella SHA-256 de fuentes de escritores con detección fail-closed de nuevos writers y cambios en `daily.sh`/flock | `daily.sh` real, cron, invocaciones manuales y copias paralelas sin protección global certificada en host; launcher productivo no creado ni autorizado |
| **A2 — SQLite y linaje** | **90%** | Migración sintética v1→v3, RAW/SHA, PK/FK/unique, receipts, source runs y cursores; nueva auditoría independiente de identidad nativa por endpoint, símbolo y ventanas que rechaza metadatos falsificados aunque tengan SHA coherente, dentro del gate PRECOMMIT | Revisión completa de esquema histórico productivo, tabla de dimensiones/provenance detalladas y migración con snapshot WAL-safe autorizada aún pendiente |
| **A3 — Adaptadores y cobertura** | **90%** | Seis proveedores simulados, ocho streams base E2E; Yahoo BTC-USD/SPY/GLD ahora usa una petición mock Yahoo para fan-out atómico de cinco streams OHLCV y mantiene ruta original por métrica; reconciliación legacy, calendario de sesiones sin falsos huecos; RBN, Bitview, FRED vintages, Binance/Bybit OI5m/funding events | Yahoo `range=max` sin paginación verificable, almacenamiento RAW copiado en cinco recibos (grupo fetch sin persistencia v4), más símbolos/calendarios reales, dimensionalidad Bitview/RBN, ALFRED, intervalos y cuotas/ventanas de funding **real** no verificados |
| **A4 — Pipeline y recuperación** | **92%** | Plan→legacy fake HTTP→RAW→SQLite→cursors→audits, paginación bounded 14d/90d/500/200, funding 7d; hasta 4 reintentos offline; nuevo Yahoo single-fetch cinco streams con una transacción PRECOMMIT, crash rollback, idempotencia, 429/503/timeout sin cursores incompletos | Espera/Retry-After reales, límites de requests y coste externos, fetch real y controles de cuota bajo autorización; no hay cron/scheduler productivo habilitado |
| **A5 — Integración y aceptación** | **72%** | 70 suites registradas, 69/69 offline PASS, una live aislada; 19 nuevas pruebas de fan-out atómico, 12 de identidad request SHA, 14 de writers, 7 de procesos y 18 de reintentos adversariales | QA único de Hermes **no solicitado aún**; auditoría final de diff/seguridad, aceptación `A_OFFLINE_ACCEPTED`, resolución de issues residuales y merge aún pendientes |

**Cálculo:** (74 + 90 + 90 + 92 + 72) / 5 = **83,6%, ≈84% estimación offline**.
Cada porcentaje es una estimación explícita, **no** el número de pruebas
que aprobaron ni un conteo de commits. Solo cambia ante avances materiales
de criterios, nunca por nuevos tests de una misma capacidad.

## Validación más reciente de ingeniería

- El Yahoo OHLCV sintético ahora ingiere las cinco métricas con **un solo GET falso**, cinco recibos con RAW idéntico y una transacción SQLite atómica; ningún cursor avanza si la respuesta está parcial, inválida o el PRECOMMIT no pasa.
- Un auditor independiente compara endpoint HTTPS, path y JSON de parámetros ya firmados contra el constructor real de cada solicitud nativa y su recibo; detecta SHA internamente correcto pero firmado para otro símbolo, rango o métrica, sin reatribuir metadata histórica.
- La revisión de escritores genera huellas SHA-256 de daily.sh, scripts conocidos y desconocidos, bibliotecas potencialmente escritoras y código del lock; cambios o escritores nuevos bloquean reutilizar evidencia de QA, **sin certificar** exclusión real.
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
- Código+pruebas validados por GitHub Actions. **69/69 suites offline**; una
  integración potencialmente productiva sigue excluida.
- **No** se abrió ninguna API real, no se verificó el token Tier2 ni
  retención de fuente, no se accedió a SQLite productiva, no se cambió
  `daily.sh`/cron, ni se activó publicación.

## Tres porcentajes que NO deben confundirse

1. **≈84% — trabajo de desarrollo offline del Paquete A:** estimación arriba.
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
