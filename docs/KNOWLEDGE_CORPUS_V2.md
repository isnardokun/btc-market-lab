# Corpus metodológico Research Studio v2 — contrato de evidencia

**Versión:** `2.0.0` · **revisión editorial:** 2026-10-09 UTC · **tipo:** resúmenes originales y enlaces de consulta; **NO** descarga de documentos ajenos ni almacenamiento de cotizaciones.

## Objetivo

Expandir las seis fichas iniciales del RAG a **36 fichas metodológicas**
versionadas, con cinco ámbitos: on-chain (15), macroeconomía (10),
derivados (5), protocolo Bitcoin (1), calidad/linaje (5). Incluye referencias a
ResearchBitcoin, Glassnode, Coin Metrics, Federal Reserve FRED, BLS, CFTC, SEC,
Bitcoin Optech y metodología del funding del proveedor de derivados.

Se conserva la generación reglada: el método explica **cómo interpretar**
un dato que llegó por el pipeline y su límite. **Nunca aporta un dato de
mercado, un porcentaje de probabilidad ni una recomendación.** La ficha
`source_kind=editorial_rule` identifica normas de investigación diseñadas
para este sistema, no reglas normativas impuestas por el enlace relacionado.

## Arquitectura y contrato

- Archivo autorizado: `knowledge/corpus_v2.json`. `schema_version=2`,
  `corpus_version=2.0.0`, `reviewed_utc` = fecha de curaduría, **no**
  prueba de disponibilidad actual de una web.
- Cada ficha: `metric` estable, `family`, `title`, `keywords`,
  `method` (resumen elaborado para el proyecto), `caveat`, `url` HTTPS,
  `source_kind`. No se copian artículos y no se incluyen claves.
- `analysis/knowledge_rag.py` valida duplicados, familia, categoría,
  dominio HTTPS, metadatos y mínimos de explicación. **Si el corpus no valida,
  falla de forma explícita** en vez de continuar con datos inventados.
- Búsqueda offline: SQLite FTS5 `unicode61` con pesos por identificador
  y fallback lexical si SQLite no compila FTS5. Se eliminan términos
  de consulta excesivos; las búsquedas solo construyen literales SQL
  parametrizados. Consulta de métrica exacta prioritaria.
- `corpus_manifest()`: versión, fecha editorial, total por ámbito y SHA256
  del JSON original. Este hash permite demostrar qué corpus utilizó una
  generación sin incrustar documentos privados.
- `evidence_bundle(query, limit, families)` devuelve objetos estructurados:
  método, precaución, URL y procedencia. No consulta internet.
- `explain_snapshot` y `render_research_note` conservan el contrato anterior:
  solo interpretan `mvrv`, `asopr` y `nupl` cuando llegaron valores finitos
  reales; nunca rellenan indicadores ausentes. Los nuevos temas enriquecen
  el corpus recuperable por Hermes, **no se publican como métricas nuevas**.

## Uso operativo para Hermes

```bash
cd /home/ignotus/btc-research
python3 scripts/corpus_query.py --manifest
python3 scripts/corpus_query.py --query 'mvrv'
python3 scripts/corpus_query.py --query 'vintages de FRED' --family macro --limit 5
python3 scripts/corpus_query.py --query 'funding' --family derivatives
python3 -m unittest -v tests.unit.test_knowledge_corpus_v2
python3 tests/run_all.py
```

**No confundir:** `tests/run_all.py PASS` valida contratos offline, no
certifica que los enlaces sigan activos ni que el histórico de APIs esté
completo. No iniciar backfill ni enviar Telegram por consultar el corpus.
El corpus es código/documentación versionados; la ingesta temporal de
Yahoo, FRED, Bitview y ResearchBitcoin mantiene su sistema SQLite separado.

## Jerarquía de fuentes y limitaciones

1. Para series públicas, BLS/FRED/CFTC/SEC (documentación metodológica
   primaria) fijan definiciones oficiales y, en su caso, revisiones.
2. Para on-chain, ResearchBitcoin/Glassnode/Coin Metrics ofrecen **métodos
   del proveedor**. No tratar nombres iguales como equivalencia entre
   metodologías; la fuente del dato operativo debe indicarse aparte.
3. Para funding de perpetuos, la descripción del exchange es método
   **específico del instrumento**, no una regla uniforme para todas
   las bolsas ni sustituto de datos históricos verificados.
4. Para prudencia editorial (`editorial_rule`), el enlace es contexto
   documental y la regla es un **control propio de Research Studio**.
5. No afirmar que una ficha define cotización BTC, flujos ETF exactos,
   open interest agregado de todos los exchanges, causalidad macro o
   eficacia predictiva sin fuente numérica independiente.

La URL verificada al redactar una ficha puede cambiar, restringir acceso
o mostrar nuevas versiones. Revalidar URLs y licencias durante revisiones
periódicas; `reviewed_utc` no equivale a chequeo automático de enlaces.

## Control de cambios de corpus

1. Elegir `metric` estable, comprobar método y URL, escribir explicación
   original y advertencia que impida inferencias abusivas.
2. Definir fuente (primaria/proveedor/protocolo/regla propia), fecha de
   revisión y familia; no añadir textos externos completos.
3. Cambiar versión al modificar el contrato/semántica, ejecutar tests de
   integridad, recuperación, ausencia de datos y ataques de URL/consulta.
4. Comparar SHA256 de manifest antes/después y revisar el diff de fichas.
5. Publicar PR y revisar CI; el despliegue se hace con fast-forward y
   reinstalación de skill; no modificar SQLite ni sobrescribir datos históricos.

## Criterios de aceptación

- `corpus_manifest` devuelve 36 fichas, cinco familias y hash reproducible.
- FTS y fallback retornan evidencia de BTC, macro, derivados y calidad;
  el filtro de dominio evita resultados cruzados.
- Slugs duplicados, fuentes HTTPS impostoras, URLs con credenciales,
  fichas sin advertencias y familias desconocidas fallan.
- La sección HTML previa MVRV/aSOPR/NUPL sigue funcionando solo con
  snapshot válido y mantiene hipervínculos de método.
- CI y Hermes incluyen `test_knowledge_corpus_v2` y
  `test_research_studio_v2` en las baterías oficiales.
- Reportar siempre el límite: el corpus describe métodos y cautelas,
  **no prueba coberturas de ResearchBitcoin Tier 2**.

## Referencias principales

- ResearchBitcoin: https://researchbitcoin.net/metrics/
- Glassnode: https://docs.glassnode.com/basic-api/endpoints/indicators
- Coin Metrics: https://gitbook-docs.coinmetrics.io/network-data/network-data-overview/addresses/active-addresses
- FRED: https://fred.stlouisfed.org/docs/api/fred/series_observations.html
- BLS CPI: https://www.bls.gov/cpi/methods-overview.htm
- BLS PPI: https://www.bls.gov/ppi/overview.htm
- BLS empleo: https://www.bls.gov/bls/news-release/empsit.htm
- CFTC: https://www.cftc.gov/MarketReports/CommitmentsofTraders/ExplanatoryNotes/index.htm
- SEC: https://www.sec.gov/newsroom/speeches-statements/gensler-statement-spot-bitcoin-011023
- Bitcoin Optech: https://bitcoinops.org/en/topics/replace-by-fee/
- Funding perpetuos: https://www.binance.com/en/academy/articles/what-are-funding-rates-in-crypto-markets
