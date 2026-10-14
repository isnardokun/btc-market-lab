# Integración productiva A/B/C — propuesta de arquitectura

Estado: PROPUESTA REVISABLE. No implementación ni ejecución live autorizada por
este documento. Preparada tras «avanza» del propietario, 2026-10-13 America/Bogota.
Base: master `ab576a8c179edf97a379976a0c9b8aefed398b6e`.

## Resultado que se busca

Conectar los contratos A/B/C ya aceptados con adquisiciones HTTP auténticas,
almacenamiento auditable y consultas/informes sobre el esquema instalado.
Descargar posteriormente todo el histórico disponible por proveedor/métrica,
sin confundir retención documentada, datos recibidos y completitud verificada.
Preparar una operación diaria recuperable y observada, activada por separado.

El alcance de ingeniería será UNA PR integrada de preparación productiva,
probada sin APIs de mercado ni acceso a la DB instalada. Su aceptación se llama
PRODUCTION_BINDING_OFFLINE_ACCEPTED. No implica PRODUCTION_ACCEPTED ni un
porcentaje global medido. El 65% comunicado es una estimación ejecutiva.

## Evidencia y brechas actuales

- A/B/C, PR 79/80/81, y compatibilidad PR 82 están OFFLINE_ACCEPTED.
- PR 82: candidato c47173e, merge ab576a8; CI 38459171364 y Hermes
  #6156783642 + corrección Git #6158774733. Cierre #60/#6158817956.
- `AcquisitionRequest` exige dominios .invalid y rutas /offline/;
  `acquire_fake_page`/pipeline aceptan solo FakeTransport; audit rechaza HTTP.
- `legacy_fingerprint` limita 100.000 filas por tabla; lector selecciona como
  máximo 10.000 puntos. No son rutinas productivas para daily: 8.977.014 filas.
- Los guards instalados exigen marcas de fixture. No pueden convertirse en
  permisos productivos cambiando un booleano o quitando el guard.
- `run_with_optional_writer_lock(None, ...)` conserva rutas legadas sin lock.
  Participación global de writers del host: NOT_VERIFIED.
- `daily.sh` no ejecutable estaba fallando en cron; cambiarlo puede activar
  APIs/noticias/publicación. No repararlo durante ingeniería offline.
- El backup WAL-safe y audit reales previos están cerrados. No se repetirán
  por este documento. La metadata exportada no contiene el DDL íntegro.

## Elección de arquitectura

Recomendado: nueva ruta explícita `installed_live_*`, que reutiliza parsers,
evidencia, analítica/render y lease verificados, con frontera de permisos propia.
Conservar los entrypoints de fixture y sus restricciones. Compartir kernels
puros donde evite duplicar contratos, sin habilitar live en comandos existentes.

Alternativas descartadas: quitar marcas/FAKE_HTTP de las rutas actuales
(rompe aislamiento y procedencia); reactivar directamente daily.sh y los
collectors legados (no aporta presupuesto/custodia/exclusión integrados).

Cinco entregables dependientes comparten UN flujo y UNA PR; no cinco PR:
permiso/identidad → transporte → ledger → evidencia/informe → operación.

## Contratos y límites globales

- Python >=3.11, Linux /proc/self/fd; stdlib, sin nueva dependencia productiva.
- CLI por defecto PLAN_ONLY_NO_IO: cero DB, red, credenciales y scheduler.
- DB, raíz de artefactos, lock y permiso explícitos; no usar DB_PATH heredado.
- Directorios nuevos privados 0700, archivos nuevos 0600; UID y dev/inode
  esperados; no-follow, nlink=1, paths/MAIN FD verificados hasta commit.
- Namespace de lease físico: btc-research-global-writer-v1; reuse del lease
  del padre, sin doble adquisición. Descriptor ambiguo/reutilizado: BLOCKED.
- Máximo técnico por run: 128 intentos, 3 por request; un permiso puede fijar
  límites inferiores. Cada retry consume presupuesto antes de HTTP.
- HTTP body máximo 4.000.000 bytes; sin redirect automático; solo HTTPS,
  host/puerto/path/params literales allowlisted; certificado verificado.
- Timeout socket 15s, deadline de run <=900s en política general; supervisor
  garantiza deadline total. El piloto propuesto reduce esos límites.
- Lotes SQLite de lectura <=2.048 filas y <=8 MiB de valores materializados;
  BLOB/TEXT grandes se hashean en chunks <=64 KiB. Nunca cargar el histórico entero.
- No modificar filas/PK/RAW/DDL de las 34 tablas legadas; nuevas adquisiciones
  van a tablas nuevas. No fabricar receipts de Funding P1 ni alterar R10-D.2.
- Publicación/cron/flags/restore productivo prohibidos en esta PR y piloto.

## 1. Permisos e identidad operativa

`RuntimeGrant` se vincula al SHA de código aceptado, SHA del plan, target DB,
identidad física, raíz, lock, fuentes/endpoints/rangos, operaciones permitidas,
UTC de vigencia, intentos/payload/DP y referencia de aprobación del propietario.
Un JSON con approved=true no acredita aprobación ni exclusión global: se exige
el artifact SHA aprobado y su referencia, aportados por el operador. El programa
no emite aprobaciones. Cambiar target/plan/rango/identidad invalida el grant.

Dos targets explícitos:
1. LIVE_PRIVATE_PILOT: directorio/DB NUEVOS; no copiar/leer producción. Solo
   tablas live y datos realmente adquiridos; ninguna fila sintética en el
   informe/evidencia real. Exige permiso API y creación privada.
2. INSTALLED_DB: exige permiso específico de lectura/migración/escritura,
   DDL observado y perfil contrastado, backup reciente/rollback y ventana
   operativa de writers aprobada antes de activar la ruta.

La preparación del target privado y la migración instalada son operaciones
diferentes, cada una con sus permisos. El backup anterior no autoriza ninguna.
Los tests usan FIXTURE_TEST con transporte simulado y no generan evidencia
REAL_ACQUISITION ni certificados de operación del host.

## 2. Transporte y adquisición recuperable

`LiveRequest` declara proveedor, specs, origen/path, parámetros sin secretos,
ventana UTC y dirección backward/forward/bounded_window. Registro versionado de
contratos literales, con procedencia de documentación y parser existente.
HTTP implementado solo tras validate_runtime_grant y adquisición del lease.
Keys se resuelven localmente solo en apply autorizado; no dotenv automático.
Auth se añade según contrato de fuente: RBN X-API-Token; FRED api_key en query
solo dentro del cliente. No incluir auth en identidad pública, logs o manifests.

Reservar intento/cuota en transacción corta y commit ANTES de la red; no BEGIN
de escritura durante HTTP. Guard/lease se revalidan antes de HTTP y precommit.
Capturar bytes, status, UTC inicio/fin/recepción y headers útiles allowlisted;
RAW+SHA, receipt, observaciones/versiones y cursor forman un commit atómico.
Cursor sigue frontera/paginación nativa acreditada, nunca salta al end pedido
por recibir una página truncada. Dirección backward/forward tiene namespace
separado; funding usa eventos, no una cuadrícula de intervalos supuestos.
Error HTTP con body conserva RAW; timeout conserva intento incierto sin RAW.
Vacío/rechazo/fallo no adelantan cursor. Crash después de commit reusa receipt
durable sin red, sin comparar contra un nuevo body vivo ni hacer refetch.

Deduplicar body no deduplica peticiones; identidad es request+run+ordinal.
Los budgets son por grant y proveedor, compartidos entre runs/reanudaciones:
un nuevo run_id no reinicia cuota. Datos/receipts borrados bloquean audit;
contadores high-water no se reembolsan. 429/403 abren circuito; 5xx/timeout
solo tienen retry acotado si el permiso lo incluye. Respetar Retry-After dentro
del deadline; no esperar indefinidamente ni ampliar el permiso automáticamente.

RBN requiere presupuesto DP además de peticiones: estimar timestamps × valores
numéricos/dimensiones con límite contractual antes de solicitar. Forma sin
límite de cardinalidad o entitlement desconocido bloquea esa métrica. Reserva
conservadora no se devuelve tras timeout. La cuota efectiva del token queda
pendiente de verificación real; la página Tier 2 no verifica el token concreto.

## 3. Almacenamiento y preservación a escala

Namespace nuevo: live_schema_migrations, live_authorizations, live_runs,
live_raw_payloads, live_requests, live_observations, live_cursors, live_coverage.
DDL/version/FK/índices exactos, prefijo aislado de compat_* y package_a_*.
Auditor por lotes reconcilia request→RAW→parser→filas/cursor en snapshot único;
provenance HTTP/REAL_ACQUISITION exige custodia real. FIXTURE_TEST sigue
SIMULATED_REQUEST_VERIFIED, incluso al ejercitar el mismo kernel.

Nueva inspección productiva acepta SOLO legacy observado + addons exactos
conocidos. No ampliar silenciosamente el allowlist del inspector de fixture.
Perfil estructural nunca reconstruye constraints/defaults/triggers desconocidos.

Fingerprint completo incremental por lotes: PK en orden determinístico mediante
índice real, codificación tipada, SHA256 de filas y SQL real de schema. Memoria
acotada, sin LIMIT de 100.000 que convierta una muestra en PASS. La consulta
selecciona claves/metadata sin cargar BLOB/TEXT completos; su hash/longitud se
obtienen por lectura incremental. Bloquear claves/fila fuera del byte budget
si no existe una ruta incremental verificable, sin retornar un PASS parcial.
El batch_rows es un techo de filas, no una afirmación suficiente sobre memoria.
Una pasada
completa en snapshot anclado; cancelación implica INCOMPLETE, nunca PASS.
Resume entre procesos solo sobre copia inmutable cuyo SHA/identidad concuerden;
no unir chunks de snapshots cambiantes. Capturar métricas de tiempo/memoria.

Migración aditiva se ensaya sobre DB sintética >100.000 filas y WAL pendiente:
comparar TODAS las filas legadas y SQL antes/después, idempotencia/rollback/FK.
La rutina instalada requerirá una ventana sin writers para mantener el snapshot
estable. Nada ejecuta restore sobre la fuente: rollback de fallo precommit;
restore posterior es actuación separada aprobada y a archivo nuevo primero.

## 4. Evidencia real, cobertura e informe

Extraer kernels de lectura temporal de la frontera de fixture y utilizarlos
desde un nuevo lector operativo protegido; filtro SQL por serie/ventana/vintage
antes del fetch por lotes. Snapshot coherente; selecciones explícitas.
Legacy sigue LEGACY_NO_REQUEST/RAW/UNRECONCILED, no pasa a verified por estar
en una DB productiva. Validar acquired_utc <= as-of y coherencia de tiempos
antes de mostrar incluso un link UNRECONCILED; futuro/naive ambiguo bloquea.

Conservar OHLCV nativo nuevo; price_btc heredado sigue close-only. No inventar
ATR/escenarios, datos ETF, dimensiones o calendario. B consume procedencia real
explícita y mantiene gate >=95 y NO_DATA/UNVERIFIED cuando corresponda.
Primer piloto de una métrica produce un diagnóstico privado, no informe completo
ni permiso de publicación. HTML/PDF locales privados únicamente cuando aprobados.

Coverage ledger por fuente/métrica/resolución/dimensión registra requested,
received, provider-verified, gaps/stale/restricted, ventanas y cuota/receipts.
Estados NOT_CHECKED/PARTIAL_VERIFIED/AVAILABLE_VERIFIED/RESTRICTED/
UNAVAILABLE_VERIFIED. Éxito de una petición o min/max local no certifica
disponibilidad completa. Backfill solo se propone para contratos verificados,
con límites por lote/grant, calendario específico y checkpoint recuperable.

## 5. Operación y escritores

Preparar un launcher explícito que exige lease antes de SQL/HTTP y supervisa
hijos, deadline, cancelación y cierre. Inventario hash de collectors, paths DB,
lock/namespace y comandos activos; no atribuir participación global desde una
sola ruta opt-in ni desde fixtures. Si writer legado puede esquivar el launcher,
cutover=BLOCKED_UNCOOPERATIVE_WRITER.

Cutover real requiere evidencia de cron/jobs/manuales detenidos o migrados a
la entrada común, procesos y FD comprobados, árbol operativo reconciliado con
el SHA aprobado preservando dirty, entorno efectivo y rollback. Flock advisory
no basta por sí solo. Este trabajo entrega readiness y comandos revisables;
NO detiene procesos, chmod daily.sh, instala cron ni activa flags.

## Aceptación y permisos por hito

G0: propietario revisa esta propuesta y amplía la excepción para UNA PR nueva
de ingeniería offline. AGENTS solo autorizó la PR adicional de compatibilidad
ya cerrada; no extenderla silenciosamente a otra entrega.
G1: PR completa, TDD/contratos/fallos/concurrencia/volumen/as-of, CI actual y
Hermes final sobre SHA exacto. PRODUCTION_BINDING_OFFLINE_ACCEPTED.
G2: permiso separado y explícito del piloto privado descrito en
docs/PRODUCTION_PRIVATE_PILOT_PROPOSAL.md; ninguna llamada antes de G1/G2.
G3: evidencia externa/entitlement por proveedor, backfill por rangos/cuotas
aprobados; no considerar G2 permiso para las otras fuentes o todo el histórico.
G4: preflight DB/DDL/writers, backup/migración/despliegue controlados por sus
permisos, shadow reports y cutover observados. PRODUCTION_ACCEPTED posterior.

No solicitar a Hermes metadata repetida ni nuevos backups durante G0/G1. Una
QA al cierre; fixes requieren nuevo SHA/orden, sin reabrir A/B/C anteriores.

## Referencias primarias consultadas (2026-10-14 UTC)

- Binance USD-M funding history: https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History
  start/end inclusivos, límite hasta 1000; no asumir un intervalo fijo de funding.
- RBN tiers: https://api.researchbitcoin.net/tier — cuota en DP; valores
  dimensionados multiplican consumo. Tier publicado no prueba token activo.
- FRED observations: https://fred.stlouisfed.org/docs/api/fred/series_observations.html
  preservar real-time/vintage y paginación; no confundir latest con as-of.

Self-review: alcance único, interfaces/permisos/estados consistentes, cero
defaults productivos, ningún permiso implícito ni placeholder ejecutable.
