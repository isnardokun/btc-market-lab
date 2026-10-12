# Package B — ejecución

Estado actual B_OFFLINE_ACCEPTED / EXTERNAL_VERIFICATION_PENDING / HERMES_STOP /
PRODUCTION_NOT_ENABLED. Cierre vigente: [PACKAGE_B_OFFLINE_ACCEPTANCE.md](PACKAGE_B_OFFLINE_ACCEPTANCE.md).
Las entradas siguientes conservan sus estados históricos de desarrollo.
Spec: PACKAGE_B_DESIGN.md. Plan: PACKAGE_B_IMPLEMENTATION_PLAN.md.
Base master 8fc5a60 (cierre A), una PR B, ejecución inline.

Ruling: se continúa el diseño y alcance ya autorizados en A/B/C e Issue #77,
con artefactos concretos revisables y trabajo offline autónomo. No se altera
el flujo productivo ni se infieren permisos externos.

Ruling: el candidato visual usa GLD (contrato Yahoo validado en A) para oro;
no lo llama spot/futuro ni lo compara como mismo instrumento que GC=F.

## 2026-10-12 — primer candidato integrado, no aceptación final

Implementado snapshot inmutable de una sola transacción RO (auditor A +
selección observed/captured/vintage), 3 assets OHLCV, cálculos source-keyed,
HTML autocontenido y gate local que recompone modelo/HTML. Ejemplo E2E:
57 streams/seis proveedores vía HTTP congelado y los adaptadores A reales.

RSI legacy tenía seed del último período y después recorría cambios anteriores:
vector independiente final 30.2749128664 vs Wilder correcto 37.7887719821.
Dos regresiones RED→GREEN corrigen seed al primer período. Se conserva la
convención legacy zero-loss=100, incluyendo flat prices; no se afirma una
paridad no demostrada con todas las implementaciones externas.

Revisión independiente del diff: dos P1 y un P2 reproducidos y corregidos
RED→GREEN. Freshness por cadencia incluye STALE explícito en macro/derivados/
onchain y horas UTC visibles; GLD conserva centavos en escenarios; export
rechaza symlink/hardlink y reemplaza archivos atómicamente. Un funding 1e-8
ya no se redondea falsamente a cero. Tres suites B: 25 métodos nuevos.

Ruling: optional series STALE se pueden mostrar como histórico claramente
rotulado, con warnings y timestamps; no sostienen inferencias actuales.
Precios críticos ausentes/stale/misaligned bloquean el candidato. El score
local evalúa reconciliación, no frescura externa/entitlement ni permiso de
publicación; el gate productivo anterior se mantiene intacto.

Ruling: verificación visual todavía pendiente. No hay binario Chromium en
este entorno; la descarga de headless-shell devolvió archivo inválido y
falló. No existen capturas ni PDF A4 verificados y no se simula QA visual.
El candidato HTML es reproducible con el CLI sintético, no un deploy.

Los pendientes anteriores se resuelven en el siguiente bloque. Esa primera
entrada conserva el estado observado antes del segundo commit integrado.

## 2026-10-12 — gate B, skill y QA editorial integrada

PR única #80; primer SHA 192006206ea8afff506d4f7366874d74c6759017 con CI PASS
(run 38266625311). Se mantiene el mismo paquete y rama para este cierre.

Publication Gate: nueva función explícita evaluate_package_b_fixture dentro
del módulo existente; una lectura RO marcada A, reconstrucción de números,
HTML y provenance, cinco categorías/pesos históricos, >=95 y cero críticos.
El gate diario legacy no se modifica ni ejecuta contra tablas incompatibles.
CLI B incorpora ambos gates antes de exportar; el PASS es del informe offline,
no aceptación del paquete, validación externa ni permiso productivo.

El skill previo falló una tarea de referencia ciega: no podía seleccionar
el candidato/DB sintético y solo disponía del flujo diario. Se añadió perfil
B, generación exclusiva ChatGPT, comando visual, tipos de evidencia y orden
congelada obligatoria para Hermes. Forward-test independiente distingue los
roles y no recomienda daily/coverage/send ni instalación por Hermes. Validador
de frontmatter PASS; instalación sintética del skill sin secretos PASS.

QA visual local real: Chromium 143.0.7499.0 / Playwright 1.62.1, fuentes locales,
320/390/768/1440, capturas repetidas byte-idénticas por viewport, ningún request
de recurso externo, 3 charts, 42 series opcionales visibles y 57 en el anexo.
Texto visible medido, cifras sin desbordes, vínculos internos resueltos. A4
11 páginas inspeccionadas; headers de tablas repetidos y ninguna página vacía
o solo con pie. HTML SHA del ejemplo inspeccionado:
6254ae8772dfe3dab0b4c24e5f9abfbfd371d3f5130bd851e92a4c34f137e9ef.
Los nuevos receipts de otra fixture cambian el SHA de evidencia, no el layout.
PDF/PNG runtime se conservan fuera del Git público; solo baseline estructural
y tooling están versionados. CI añade generación+browser+A4 sin upload/deploy.

RED→GREEN visual: dos KPIs BTC cortados a 320 px, cifras de tablas partidas y
una página A4 solo con pie. Se arregla espacio móvil, scroller de tablas,
nowrap numérico y print compacto. La herramienta bloquea texto invisible,
números desbordados, página insuficiente, recurso externo y captura inestable.
La descarga estándar de browser falló; un binario alternativo se recompuso
localmente verificando SHA de bytes Brotli, y se corrigió fontconfig del host.
Nada de esto altera Hermes ni añade defaults o dependencias productivas.

Segundo pase de revisión acotado: sin P0/P1; un P2 de coerción en el gate
visual reproducido y corregido (pass booleano estricto, score numérico finito).
El test RED→GREEN confirma rechazo antes de cargar browser. Corpus/unidades
externos siguen UNVERIFIED; no se inventan noticias, ETF, liquidaciones o
calendario. FRED muestra su vintage; analítica exporta parámetros explícitos.

Regresión local: 75/75 suites offline, 76 registradas, 1 live omitida sin permiso;
28 métodos B en tres suites. Revisión de diff y checks de sintaxis limpios.
Python 3.12.14, Node 24.19.0. Pendiente para aceptación: CI HEAD final y una
orden/reporte Hermes del SHA congelado. B_CANDIDATE_READY; HERMES_STOP hasta
que se publique esa orden. No live API/DB/cron/publicación.

CI 38270083332 / SHA 90f016e: suites y retención PASS, generación/gate PASS,
browser/capturas completadas; fallo de infraestructura `spawnSync pdfinfo
ENOENT`. Se declara Poppler explícito en instalación del job. No se modifica
ni relaja el control A4; CI del nuevo SHA debe repetir todas sus etapas.

## 2026-10-12 — cierre offline e integración

QA Hermes 6127727828: HEAD bcbd174, 75/75 offline, runner RC0,
retention RC0/0.224s, Python 3.14.7 y aislamiento/árbol limpio confirmados.
CI 38270621629 del mismo HEAD PASS, incluida QA responsive/A4. Ruling 6127788311
registra metadata omitida como NOT_REPORTED y corrige Python CI a 3.11.17.
Merge 19b3298a805be2e600fd5cfd06c50eaeadb066d1 con tree idéntico
581bda8f8eb799e3d715f0912eae50756261824c. Cierre completo y límites en el
documento de aceptación. Hermes STOP; no nueva orden ni autorización productiva.
