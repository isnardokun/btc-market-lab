# Package B — aceptación offline e integración

**Estado actual: `B_OFFLINE_ACCEPTED; EXTERNAL_VERIFICATION_PENDING`.**
Fecha: 2026-10-12. Producción no activada. Este cierre prevalece sobre los
estados históricos DRAFT/QA_PENDING del ledger y la implementación.

- PR integrada: [#80](https://github.com/isnardokun/btc-market-lab/pull/80).
- HEAD probado: `bcbd1749c563cd83a19ba1340b945b0457bed0b3`.
- Base: `8fc5a60f5b6a3b8aba72edebb0c4c010ece76d35` (A aceptado offline).
- Merge: `19b3298a805be2e600fd5cfd06c50eaeadb066d1`.
- Árbol del merge idéntico al HEAD probado:
  `581bda8f8eb799e3d715f0912eae50756261824c`.

## Evidencia y decisión

[Orden QA congelada](https://github.com/isnardokun/btc-market-lab/pull/80#issuecomment-6127429865)
y [reporte Hermes](https://github.com/isnardokun/btc-market-lab/pull/80#issuecomment-6127727828):
Python local 3.14.7; 76 suites registradas, 75 offline ejecutadas/PASS,
cero FAIL, una live excluida por alcance; runner RC0; retention RC0/0.224s;
HEAD exacto y árbol limpio, sin API real/DB productiva/cron/publicación.
El propietario identifica el worktree como `/home/ignotus/pr-80-qa`;
se conserva junto a los históricos de A.

[CI del HEAD](https://github.com/isnardokun/btc-market-lab/actions/runs/38270621629)
SUCCESS: runner/conteos, retención, prohibición de artefactos runtime en Git,
generación sintética, Publication Gate B 100/0 críticos, browser 151.0.7922.34,
320/390/768/1440 y PDF A4 de 11 páginas; cero recursos externos.
Python CI real 3.11.17, comprobado en logs; 3.12.14 corresponde al autor local.
La referencia a Python CI 3.12.14 del reporte Hermes es una atribución errónea
de metadata y se corrige aquí sin alterar el reporte original.

[Ruling de aceptación](https://github.com/isnardokun/btc-market-lab/pull/80#issuecomment-6127788311):
el reporte no enumera UTC, Node/skips internos, detached/merge-base ni todas
las aserciones B. Se mantienen como **NOT_REPORTED**; no se inventan datos.
HEAD/cleanliness/aislamiento y RC están reportados, y base/ejecución de casos B
están verificados de forma independiente en CI del mismo código. Engineering
owner acepta esta limitación de metadata sin repetir suites ni ampliar permisos.
No se afirma que el estado detached de Hermes haya sido demostrado.

Revisión de código y QA visual local precedentes en
[ledger](PACKAGE_B_EXECUTION_LEDGER.md): defectos encontrados reproducidos y
corregidos; no P0/P1 confirmados abiertos en el alcance revisado. Autor local
Python 3.12.14/Node 24.19.0; 75/75 offline/RC0. Chromium 143.0.7499.0/Playwright 1.62.1:
cuatro viewports, capturas repetidas estables y revisión de las 11 páginas A4.
HTML de ejemplo inspeccionado SHA256:
`6254ae8772dfe3dab0b4c24e5f9abfbfd371d3f5130bd851e92a4c34f137e9ef`.
No se confunde PASS estructural del browser con la revisión manual del diseño.

## Capacidad aceptada offline

Flujo reproducible de seis proveedores fake/57 series, mediante adaptadores A
reales y RAW/request provenance, hacia SQLite marcada, snapshot temporal RO,
analítica, HTML autocontenido/JSON/gates y render responsive/A4.

Snapshot inmutable: auditoría de integridad y selección en una sola transacción,
captured/observed/vintage sin lookahead ni velas diarias abiertas. BTC-USD/SPY/GLD
requieren OHLCV alineado/unidades correctas/freshness; datos críticos ausentes
bloquean el informe. RSI Wilder cronológico corregido y parámetros/versiones
explícitos; SMA/MACD/ATR y escenarios sobre pivotes históricos, sin objetivos
fabricados. GLD se conserva como ETF USD/participación.

Research Studio exclusivo: hechos/lectura condicional/riesgos, tres charts SVG,
FRED/vintages, exchanges separados, RBN/Bitview, NO_DATA/STALE/UNVERIFIED visibles,
anexo y links internos a las series. Skill versionado con perfil offline y
roles probados mediante tarea de referencia antes/después, sin Sereno.

`validation/publication_gate.py` incorpora contrato B de fixture marcada:
reconstruye desde DB, comparte categorías/pesos/umbral 95 y bloquea críticos o
tampering. El contrato diario y checks de tablas legacy se preservan, no se
ejecutan contra el esquema A incompatible. CLI B exporta solo tras ambos gates.
El control visual declara revisión manual necesaria y no instala browser.

## Límites y siguiente paquete

No se acredita investigación diaria real ni histórico descargado completo.
Entitlement/cuotas/retención/unidades/calendarios externos siguen pendientes;
Bitview sin unidad verificada no genera señales, RBN conserva fracciones RAW,
OI no se agrega entre exchanges y funding no se annualiza por inferencia.
ETF/liquidaciones/noticias/agenda sin dataset verificable permanecen NO_DATA.
Un nuevo receipt puede cambiar hashes de evidencia; el mismo snapshot/modelo
produce el mismo HTML. PNG/PDF/HTML/JSON runtime permanecen fuera de Git público.

No API real, SQLite productiva, cron/flags, backups/migraciones productivas,
Telegram/deploy/publicación. El reporte B no sustituye ni activa daily.sh.
La orquestación/recuperación/runbook y rollout corresponden a C (#78), con
verificación offline y autorizaciones productivas separadas.

**Hermes DONE/STOP. Orden B cerrada.** A y B aceptados offline; C pendiente.
Ni el merge ni QA autoriza acceso externo o puesta en producción.
