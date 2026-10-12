# Package A — Cuadro de avance por criterios de aceptación

**Corte:** 2026-10-11 · **PR:** #79 (borrador) · **Estado:** `A_IN_PROGRESS`
**Avance estimado de ingeniería offline:** **≈88%** · **Confianza:** orientativa, ±5 puntos porcentuales.
Este es un juicio técnico de los entregables frente a `docs/INTEGRATED_DELIVERY_PROGRAM_ABC.md`
y al Issue #76, **NO** un KPI generado automáticamente, un progreso temporal
lineal, la proporción del histórico de APIs descargado ni una certificación
de producción. La incertidumbre se reducirá con el QA independiente final
de Hermes y la revisión de rutas restantes. Los cinco ejes tienen peso
idéntico: 20% cada uno.

| Eje del contrato | Avance técnico estimado | Evidencia aceptada | Brecha que limita el porcentaje |
|---|---:|---|---|
| **A1 — Writers y exclusión** | **80%** | Inventario de entradas y opt-in `flock`, lock padre y lease POSIX; nueva defensa contra reemplazo de inode físico con directorio owner-controlled, device+inode estables y verificación en PRECOMMIT sobre página única y Yahoo fanout; auditoría SHA de writer sources y supervisión de subprocesos | `daily.sh` real, cron, invocaciones manuales y copias paralelas sin protección global certificada en host; launcher productivo no creado ni autorizado |
| **A2 — SQLite y linaje** | **91%** | Migración sintética v1→v3, RAW/SHA, PK/FK/unique, receipts, source runs y cursores; nueva auditoría independiente de identidad nativa por endpoint, símbolo y ventanas que rechaza metadatos falsificados aunque tengan SHA coherente, dentro del gate PRECOMMIT | Revisión completa de esquema histórico productivo, tabla de dimensiones/provenance detalladas y migración con snapshot WAL-safe autorizada aún pendiente |
| **A3 — Adaptadores y cobertura** | **94%** | Seis proveedores simulados; barrido offline completo de 13 métricas escalares RBN de catálogo y 23 series d1 Bitview (36 streams) en shards durables 32+4, validación de índices diarios sin truncamiento, slug-value RBN y diferenciación explícita de unidades Bitview desconocidas; Yahoo OHLCV atómico, FRED vintages, Binance/Bybit OI y funding | Yahoo `range=max` sin paginación verificable, almacenamiento RAW copiado en cinco recibos (grupo fetch sin persistencia v4), más símbolos/calendarios reales, dimensionalidad Bitview/RBN, ALFRED, intervalos y cuotas/ventanas de funding **real** no verificados |
| **A4 — Pipeline y recuperación** | **96%** | Plan→legacy fake HTTP→RAW→SQLite→cursors→audits, paginación bounded y hasta 4 reintentos offline; recuperación de catálogo completo RBN/Bitview entre source_runs v3 de 32 páginas, verifies prefix RAW+plan SHA y retoma solo suffix no aceptado sin refetch; Yahoo cinco streams atómicos | Espera/Retry-After reales, límites de requests y coste externos, fetch real y controles de cuota bajo autorización; no hay cron/scheduler productivo habilitado |
| **A5 — Integración y aceptación** | **81%** | 72 suites registradas, 71/71 offline PASS, una live aislada; 10 casos adicionales de recuperación E2E entre shards/429/503/timeout y múltiples intentos, 12 de inode y rollback PRECOMMIT, 19 RBN/Bitview, 19 Yahoo, 12 request SHA, 14 writers, 7 procesos y 18 reintentos | QA único de Hermes **no solicitado aún**; auditoría final de diff/seguridad, aceptación `A_OFFLINE_ACCEPTED`, resolución de issues residuales y merge aún pendientes |

**Cálculo:** (80 + 91 + 94 + 96 + 81) / 5 = **88,4%, ≈88% estimación offline**.
Cada porcentaje es una estimación explícita, **no** el número de pruebas
que aprobaron ni un conteo de commits. Solo cambia ante avances materiales
de criterios, nunca por nuevos tests de una misma capacidad.

## Validación más reciente de ingeniería

- El barrido del catálogo RBN/Bitview ahora expone `recovery_manifest` durable por source_run v3 y un reanudador que verifica RAW/SHA de todos los prefijos aceptados, reconstruye la cadena de lotes (incluidos fallos repetidos 429→503) y ejecuta solo páginas pendientes bajo una única lease; 10 nuevos E2E validan reintento 0-refetch, fail-stop y manifest tampering, sin automatizar proveedores reales.
- El candado global POSIX ahora rechaza directorios modificables por grupo/terceros, verifica inode/dev físico del archivo que sigue existiendo y revalida ese mismo inode dentro de la transacción antes de cada PRECOMMIT; si se elimina o reemplaza, la página única o fan-out Yahoo revierte sus nuevos recibos. No certifica escritores no cooperativos ni reemplazos entre verificaciones.
- RBN y Bitview se planifican ahora a escala de catálogo completo: 13 métricas RBN escalares y 23 series Bitview d1 en shards de 32 páginas máximo bajo un único lock, con preflight completo de manifest y fail-stop. El índice Bitview exige posición y longitud de día exactas; unidades Bitview se declaran no verificadas y métricas Tier2 aún no se inventan.
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
- Código+pruebas validados por GitHub Actions. **71/71 suites offline**; una
  integración potencialmente productiva sigue excluida.
- **No** se abrió ninguna API real, no se verificó el token Tier2 ni
  retención de fuente, no se accedió a SQLite productiva, no se cambió
  `daily.sh`/cron, ni se activó publicación.

## Tres porcentajes que NO deben confundirse

1. **≈88% — trabajo de desarrollo offline del Paquete A:** estimación arriba.
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
