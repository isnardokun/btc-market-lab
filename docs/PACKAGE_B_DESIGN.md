# Package B — diseño de integración de research auditable

Base: 8fc5a60, Paquete A aceptado offline. Alcance autorizado: Issue #77 y
programa A/B/C. Una PR B; Hermes únicamente QA final de SHA congelado.

## Intención y diseño

Un informe profesional BTC/SPY/GLD/macro/on-chain/derivados reproducible a
partir de SQLite sintética auditada, sin HTTP, fuentes remotas ni publicación.
Mantener Mercados Research Studio (ink/ivory/sea, serif y números tabulares).
Cada cifra y gráfico debe enlazar su serie, unidad, observaciones UTC,
receipts RAW y request provenance; ausencia explícita NO_DATA/UNVERIFIED.

Se eligió ensamblaje modular sobre el ledger A. Parchear el HTML legacy
mantendría fetch/DB/render acoplados; reescribirlo íntegro arriesga el flujo
actual. El candidato B independiente utiliza los contratos actuales y los
checks financieros existentes, sin activar daily.sh ni sustituir el informe
productivo durante desarrollo. Decisión conforme al flujo autónomo aprobado:
se concreta spec/plan revisable en esta PR y se implementa offline; no se
solicitan aprobaciones repetidas de la ingeniería ya autorizada.

## Interfaces

- analysis/package_b_evidence.py: snapshot mode=ro/query_only, auditor RAW y
  selección as-of/captured/vintage; immutable dataclasses y hash del conjunto.
  Guard de fixture A, no default productivo; rechazo de RAW/provenance corruptos.
- analysis/package_b_analytics.py: OHLCV alineado por fecha, matemática pura,
  parámetros/versiones/input SHA; RSI Wilder correcto, SMA/MACD/ATR, niveles
  y escenarios condicionales del motor existente, sin objetivos fabricados.
- rendering/package_b_report.py: documento autocontenido, SVG con ejes/unidad,
  portada y lectura observada, bloques de calidad, fuentes y anexo de evidencia.
  Oro es ETF GLD USD/participación; no se presenta como GC=F ni oro spot.
- scripts/package_b_report_fixture.py: generador de entradas sintéticas vía
  adaptadores A, runner de ejemplo y export local; sin callback/red/credenciales.
- tests/unit/test_package_b_research_report.py: E2E fuente→ledger→snapshot→
  analytics→HTML/gate, ausencias, tampering, lookahead, vintage, fechas y unidades.

## Datos y temporalidad

Usar únicamente receipts exitosos y native request provenance íntegra dentro
  del snapshot. observed_utc < corte diario UTC; captured_utc <= generación;
  FRED vintage <= generación. Una revisión conocida posteriormente nunca
  reescribe una vista histórica. Fuente, unidad y escala se conservan; unidades
  Bitview sin comprobar se muestran UNVERIFIED y no generan señales.
Un símbolo requiere OHLCV con fechas idénticas; faltantes y nuevas velas
  abiertas no se rellenan. Freshness por fuente; sesiones bursátiles no se
  inventan como calendario NYSE. No agregar OI entre exchanges ni transformar
  funding eventos en velas o tasa anual sin contrato explícito.

## Aceptación y entrega

HTML determinista, cifras trazables y reconciliadas con su JSON de evidencia,
SVG offline, print A4 y screenshots 320/768/1440, fallos bloqueando datos
críticos, tests/CI y QA final independiente. Reutilizar content_checks y
portable_report; el gate productivo existente se preserva y su integración
se verifica antes de B_OFFLINE_ACCEPTED. No basta una plantilla bonita.
ETFs/news/calendar/liquidaciones sin dataset verificable figuran NO_DATA.
Los valores de ejemplo tienen watermark SINTÉTICO y no son investigación real.

## Estado del candidato integrado

Ver PACKAGE_B_EXECUTION_LEDGER.md. El generador alimenta 57 series locales
con fuentes fake de los seis proveedores y exporta HTML + JSON de evidencia
+ gate LOCAL y contrato B marcado dentro de `validation/publication_gate.py`.
El gate B reconstruye RAW/lineage/snapshot/analítica y usa las cinco categorías,
pesos y umbral >=95, con bloqueo por cualquier crítico. Los checks de tablas
legacy ajenas al esquema A y `run()` del gate diario conservan su contrato.
Los checks financieros puros existentes ahora reconocen atributos section y
entidades HTML escapadas sin perder los checks anteriores. El renderer tiene
QA local real 320/390/768/1440 y A4; B requiere CI del SHA final y QA Hermes
antes de aceptación. Tooling visual: `scripts/package_b_visual_qa.cjs --help`;
baseline estructural versionado: `tests/fixtures/package_b_visual_contract.json`.
