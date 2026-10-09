# Noticias especializadas y análisis fundamentado — BTC Market Lab

## Objetivo y límites

La arquitectura previa usaba una búsqueda Exa por activo y clasificaba
titulares con palabras clave. Eso no equivale a investigación especializada
ni demuestra causalidad entre noticia, precio y flujos. Ahora añadimos
publicaciones directas, trazabilidad de origen y un módulo **RAG local de
metodología**, sin sustituir las series originales de Bitview/FRED/Yahoo.

### Fuentes

| Fuente | Acceso | Dominio analítico | Evidencia |
|---|---|---|---|
| Federal Reserve | RSS primario | Decisiones monetarias | https://www.federalreserve.gov/feeds/feeds.htm |
| U.S. Bureau of Labor Statistics | RSS primario | Inflación, empleo, publicaciones oficiales | https://www.bls.gov/feed/ |
| Coin Metrics State of the Network | RSS de investigación | Network data, liquidez y tendencias | https://coinmetrics.substack.com/ |
| Bitcoin Optech | RSS de investigación | Protocolo, vulnerabilidades y Lightning | https://bitcoinops.org/en/newsletters/ |
| Glassnode Insights | Descubrimiento Exa por dominio verificado | On-chain y cohortes | https://insights.glassnode.com/ |
| Bitcoin Optech | Descubrimiento Exa por dominio verificado | Protocolo Bitcoin, seguridad, Lightning | https://bitcoinops.org/en/newsletters/ |
| Exa general | Descubrimiento existente | Noticias BTC, renta variable, oro | mediante mcporter local |

**Regla editorial:** los RSS constituyen publicaciones atribuibles, no
comprobaciones de exactitud de todas sus afirmaciones. Coin Metrics, Glassnode
y Optech producen análisis/contexto, no boletines estadísticos oficiales.
Una nota de prensa no prueba que causó una variación de precio.

## Flujo de noticias

```text
  Exa genérico (existente) ────────┐
  RSS Fed / BLS / Coin Metrics ────┼──> validación de fecha/URL/longitud
  Exa especializado por dominio ──┘          ↓
                                      deduplicación URL + título
                                               ↓
                                    fuente / tipo / categoría
                                               ↓
                                         HTML portable
```

**Principios:**
- RSS: XML RSS/Atom máximo 2 MB, sin DTD, enlace HTTPS limitado al
  dominio del emisor y fecha comprobada contra UTC; respuestas viejas excluidas.
- Se conserva la búsqueda Exa anterior. Si un RSS responde 403, formato
  incorrecto o no está disponible, el informe sigue con otros proveedores.
- La búsqueda dirigida solo acepta resultados en dominios explícitos y con
  fecha ISO comprobable hasta 21 días; se ejecuta **dos búsquedas adicionales**
  para BTC cuando está habilitada.
- Solo se publican artículos con fecha ISO verificable (prensa general hasta 7 días de antigüedad; investigación hasta 21 días). No se aceptan páginas genéricas sin fecha ni la página de «Latest Numbers» del BLS como si fuera un comunicado oficial.
- Se prioriza origen primario o investigación y se eliminan repeticiones por
  URL normalizada y título. No se extrae contenido protegido tras paywall,
  no se almacena copia completa de noticias.
- Para cada noticia el HTML muestra origen y rol editorial y enlaza
  a la pieza original. No se califica «bullish»/«bearish» automáticamente.

Configuración:
- `NEWS_RSS_ENABLED=1` en el `daily.sh` real (predeterminado allí).
- `NEWS_TARGETED_EXA=1` en el `daily.sh` real; reutiliza mcporter y
  la cuenta Exa que Hermes ya tiene.
- Para operación con cuota o sitios restringidos, fijar las variables en
  `0`; tests/unit imports no lanzan RSS ni búsqueda dirigida por defecto.
- RSS y Exa tienen latencia, cuota y licencias del origen. Evaluar fallos
  y disponibilidad con logs locales en Hermes.

## Motor de conocimiento metodológico (RAG local v1)

`analysis/knowledge_rag.py` guarda fichas metodológicas **versionadas**
y redactadas específicamente para el proyecto, con:
identificador, título, palabras clave, método, limitación y enlace a
documento original. Inicializa índice **SQLite FTS5 en memoria** para
recuperar las fichas pertinentes a MVRV, aSOPR, NUPL, direcciones activas
y métricas STH. Cuando SQLite no tiene FTS5, usa recuperación lexical
determinística.

La etapa de generación **no es libre**: produce observaciones condicionales
únicamente de los valores y timestamps ya resueltos por el pipeline.
No inventa históricos, probabilidades, correlaciones, objetivos ni
señales. La sección HTML incluye citas de metodología y cautelas;
las citas no implican que proveedores distintos calculen igual la serie.

**Esto es un RAG funcional básico de recuperación + síntesis reglada, NO**
una capa generativa con embeddings o un LLM entrenado. La expansión
natural sería:

1. Corpus local autorizado (documentos metodológicos con versión/licencia,
   source URL, fecha de consulta y checksum), sin copiar textos ajenos
   masivamente en el repo público.
2. Indexación local de párrafos con SQLite FTS5 y, si hay beneficio claro,
   embeddings híbridos; búsqueda de top-k y revisión de evidencia.
3. LLM local gestionado por Hermes **opcional** que redacte un resumen
   a partir de un paquete estructurado con fechas, unidades y pasajes
   recuperados; un validador independiente debe evitar cifras no citadas.
4. Evaluación de calidad con fixtures reproducibles: respuesta a datos
   ausentes/contradictorios, sesgo de confirmación, afirmaciones
   causales no respaldadas, tiempo de respuesta y costes del modelo.

No existe conexión a modelos externos por defecto.

Referencias metodológicas del corpus inicial:
- https://researchbitcoin.net/metrics/mvrv/
- https://researchbitcoin.net/metrics/sopr/
- https://researchbitcoin.net/metrics/sopr_sth/
- https://researchbitcoin.net/metrics/realized_price_sth/
- https://docs.glassnode.com/basic-api/endpoints/indicators
- https://gitbook-docs.coinmetrics.io/network-data/network-data-overview/addresses/active-addresses

## Hallazgos de la revisión local Hermes #23 (09-10-2026)

El diagnóstico #23 confirmó 14/14 suites PASS, gate 100/100, RAG MVRV/aSOPR/NUPL y
fuentes oficiales Fed/BLS en SPY. También observó:
- El feed Coin Metrics devolvió `ValueError` sin detalle. Se elevó el límite
  XML de 500 KB a 2 MB; la causa real solo se confirmará en nueva ejecución
  local, y se conserva manejo fail-open. No declarar solucionado sin comprobar.
- No aparecieron noticias de Glassnode/Coin Metrics/Optech en BTC. Se
  sustituyó la consulta OR-site por dos búsquedas concretas Glassnode y
  Coin Metrics, y se añadió Optech RSS directo; todavía falta verificar
  disponibilidad real de ambos.
- Una página MarketWatch de marzo de 2026 apareció con «Fecha no confirmada».
  Ahora los resultados generales sin fecha o de más de 7 días no se muestran.
- BLS mostró un índice «Major Economic Indicators Latest Numbers», no
  un comunicado específico. Se filtra ese índice y se conservan emisiones
  fechadas con contenido sustancial.

Estos filtros son mejoras editoriales; **no constituyen verificación
independiente de datos de noticias**. Cuando una fuente falle, documentar
conteo de noticias válidas, tipos de fallos y enlaces válidos.

## Pruebas e integración

```bash
python3 -m unittest -v tests.unit.test_specialized_news_rag
python3 tests/run_all.py
NEWS_RSS_ENABLED=0 NEWS_TARGETED_EXA=0 RESEARCH_RAG_ENABLED=1 \
  SEND_TELEGRAM_AUTO=0 bash scripts/daily.sh
```

Con datos privados y acceso a red, Hermes debe hacer **otra** prueba
con `NEWS_RSS_ENABLED=1 NEWS_TARGETED_EXA=1`, revisar:
fuentes, fechas, enlaces, conteo de noticias, HTML, hash, gate y tiempo.
Una ejecución de CI no verifica conectividad desde la máquina local.

### Seguridad

No guardar tokens Exa ni secretos de API en Git. El `rag` no ejecuta
instrucciones incluidas en titulares/documentos ni realiza HTTP; el corpus
solo contiene resúmenes aprobados en código. Un PR diagnóstico de Hermes
es público aunque sea borrador: revisar cualquier archivo compartido.

### Trabajo posterior deliberadamente excluido

- API de noticias con licencia comercial y verificación contractual.
- ETF flows exactos, funding, OI o datos económicos de último minuto:
  requieren fuentes numéricas propias, no extraer una cifra de un titular.
- Calibración cuantitativa, backtesting y causalidad: no existen en este
  módulo y no deben confundirse con recuperación documental.
