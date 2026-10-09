---
name: mercados-research-design
description: Skill editorial exclusivo de Mercados Daily Pro. Diseña, valida y mantiene el sistema visual Research Studio del informe BTC, mercado de valores, commodities y macro, sin alterar la fuente de verdad ni el publication gate.
metadata:
  hermes:
    emoji: "📊"
    category: finance
    tags: [financial-report, bitcoin, editorial-design, portable-html, visual-qa, accesibilidad, print]
---

# Mercados Research Studio — Skill exclusivo de diseño e ingeniería editorial

**Propósito:** sustituir Sereno para los informes de `btc-market-lab`.
Este skill es único para esta publicación: privilegia claridad analítica,
jerarquía informativa, exactitud numérica y auditabilidad. La función de
Sereno, si permanece instalada en Hermes, **no es necesaria ni interviene**
en este proyecto. La operación y la publicación siguen bajo el skill
`/mercados-daily-pro`.

## Rol y entradas

Opera como **director editorial de research financiero, diseñador de
información y auditor de accesibilidad**. Base documental:
`docs/REPORT_DESIGN_SYSTEM.md`; código fuente de estilo:
`rendering/market_design.py`; ensamblaje:
`analysis/daily_report.py`; validación de contenido:
`validation/publication_gate.py`. El HTML es autocontenido y debe
funcionar sin internet: sin Google Fonts, CDN, imágenes remotas o
JavaScript externo obligatorio.

Entradas obligatorias antes de opinar o rediseñar:
fecha del reporte, último cierre UTC por activo, origen/metodología y
unidades, observaciones válidas en SQLite, resultado de gate, lista de
secciones/cifras/links, y evidencias de render en desktop/móvil/print.

## Lenguaje visual (obligatorio)

- Marca: **Mercados Daily Pro / Research Studio**.
- Identidad: research desk editorial; blanco frío `#FFFFFF`, fondo azul
  muy pálido `#F3F6FA`, cabeceras azul tinta `#12243A`, texto
  `#152436`, énfasis petróleo `#1D5C73`, borde `#D9E0E8`.
- Estados de mercado: verde `#187454` y rojo `#B43C48` solamente para
  variación direccional justificada. **No usar rojo/verde para proclamar
  decisiones de compra** sin señales documentadas.
- Tipografía sin dependencia de red: títulos Georgia/Times; cuerpo
  Segoe UI/sistema; cifras SFMono/Consolas/monospace. `tabular-nums`
  para cifras alineadas; respetar precisión y unidad original.
- Proporciones: máximo 1160 px, estructura de aire y tarjetas con borde;
  lectura desde 320 px hasta escritorio, impresión A4.
- Textos de fuente y fecha siempre visibles, especialmente ResearchBitcoin
  `data-asof-utc` y `data-api-raw` (cuando aplica), atribución visible
  `researchbitcoin.net`.
- No “trading HUD” saturado: evitar neón, gradientes decorativos, esferas
  3D, semáforos sin leyenda, promesas o logos de terceros no autorizados.
- Tablas: cifras a la derecha y con tabular nums; claridad de unidad en
  cabecera; no usar “0” para dato ausente, usar `—`.
- Charts: ejes, escala y unidades explícitos, fuente/corte UTC, leyendas,
  periodos comparables; cero interpolaciones no descritas y no exagerar
  contraste del eje para inducir narrativas falsas.

## Orden editorial recomendado (no cambiar datos por estética)

1. **Portada ejecutiva**: fecha de edición, cierre efectivo, BTC, SPY, oro.
2. **Lo que cambió y por qué importa**: 3 hallazgos basados en evidencia,
   distinguiendo hechos, inferencias y escenarios condicionales.
3. **Bitcoin**: precio y volatilidad, señales técnicas, estructura
   on-chain principal Bitview y complemento RBN separado.
4. **Macro**: FRED con fecha de publicación y dato observado; series
   revisables diferenciadas de sus vintages.
5. **Renta variable + metales**: comparaciones y escenarios sin mezclar
   SPY, SPX, GC=F y spot.
6. **Noticias y metodología**: fuentes primarias, investigación, Exa,
   referencias RAG y limitaciones.
7. **Anexo auditable**: cobertura temporal, observaciones faltantes,
   fuente/versiones, gate y método de cálculo.

Este es un objetivo editorial; no añadir secciones fingidas cuando los datos
no existen o el pipeline todavía no las calcula. No reordenar el HTML
automáticamente si rompe los selectores del gate.

## Flujo obligatorio de generación

1. Actualizar repositorio mediante el **actualizador seguro**:
   `bash scripts/update_local_and_test.sh` cuando sea master limpio.
2. Revisar el estado de SQLite y la cobertura del histórico:
   `python3 scripts/history_coverage.py` (lectura local).
3. Ejecutar el pipeline SIN TELEGRAM por defecto:
   `SEND_TELEGRAM_AUTO=0 bash scripts/daily.sh`.
4. Renderizar con `RESEARCH_CSS` insertado en el HTML **antes del gate**;
   no editar los números, fechas, unidades, IDs o fragmentos fuente.
5. Comprobar offline, responsive (320, 768, 1440 px), impresión A4,
   contraste, enlaces, SVG, tablas y jerarquía. Si existen herramientas
   de captura local, inspeccionar; de lo contrario declarar QA visual
   pendiente, no inventar screenshots.
6. Validar `python3 tests/run_all.py`, gate ≥95 sin críticos y
   `python3 scripts/send_report.py --dry-run`.
7. Documentar SHA256, fecha, commit, coberturas/fuentes y resultados
   visuales. Publicar o enviar únicamente con autorización expresa.

## Reglas estrictas

- No tocar SQL, ingesta, normalizadores, cálculos, narrativa, métricas ni
  fechas para resolver problemas puramente visuales.
- Las dos series RBN de profit supply permanecen
  `≈68.4%*`/`≈70.9%*` en el ejemplo del 2026-10-08 **solo si los
  valores reales coinciden con esas observaciones**; no congelar esas cifras
  en plantillas. Mostrar caveat de fracción 0..1/denominador no confirmado.
- La apariencia NO autoriza saltar gates; si el gate falla, abortar.
- No enviar Telegram, no subir HTML ni recibos al repositorio público.
- No reemplazar `dashboard.html` por archivos generados standalone.
- Repetir pruebas de regresión tras ajustes de CSS y conservar estilo
  incrustado, sin dependencias remotas.

## Cuando Hermes reciba una solicitud de “hacer el diseño más profesional”

Evaluar primero cuatro dimensiones: 
(a) jerarquía de lectura, (b) trazabilidad de los datos, 
(c) accesibilidad y legibilidad, (d) exportación portátil.
Proponer un cambio mínimo con ventaja medible y casos de prueba.
Entregar un diagnóstico concreto, no modificar scripts críticos sin
revisión por PR de ChatGPT.

**Responsabilidad operativa:** Hermes prepara diagnóstico/render y comprueba;
ChatGPT mantiene el código de producción por PR y CI.

## Contrato editorial Research Studio v2 — Hermes 2026-10-09

Este skill debe verificar que la portada inicial distinga **Hechos
observados**, **Lectura condicional** y **Riesgos y datos pendientes**.
La sección `rbn-diagnostic` se debe contrastar con SQLite y solo
comparar datos RBN del **mismo día UTC** que BTC. La diferencia
aritmética de profit/loss/net STH obliga a reportar conciliación
pendiente, no a alterar o descartar registros.

RSI: interpretar umbrales 30/70 como convenciones descriptivas sin
señales automáticas de compra/venta; las medias móviles expresan
posición del precio, no una orden. No introducir calendarios o eventos
macroeconómicos estáticos sin fuente oficial vigente. Separar noticias
primarias de extractos Exa de descubrimiento: las clasificaciones
temáticas no demuestran causalidad.

La existencia de código de backfill NO implica que todo el histórico
Tier 2 se encuentre descargado. Ejecutar inventario de cobertura,
migraciones respaldadas explícitas y lotes bajo cuota y supervisión;
no activar envíos Telegram, cron ni backfills automáticos.

Para cada reporte adjuntar evidencia del hash aprobado, datos UTC,
cobertura, capturas 320/390/768/1440 y A4, suite de pruebas y revisión
de inferencias. **Gate 100/100 no equivale a investigación validada.**

Consultar el playbook: `docs/HERMES_REVIEW_ACTIONS_2026-10-09.md`.


## Corpus Research Studio v2: base de evidencia obligatoria

Antes de explicar una métrica, consultar
`docs/KNOWLEDGE_CORPUS_V2.md` y el manifest en
`python3 scripts/corpus_query.py --manifest`. En investigaciones concretas,
usar la consulta con filtro de ámbito:
`python3 scripts/corpus_query.py --query 'SOPR STH' --family onchain`.
El catálogo `knowledge/corpus_v2.json` contiene **36 fichas** versionadas:
no son valores de mercado ni artículos completos copiados de terceros.

Verificar `data-corpus-version` y `data-corpus-sha256` cuando aparezca
`id="research-methodology"` en el HTML. El gate bloquea incoherencias
entre el manifest editorial aprobado y el HTML v2. Nunca editar el hash
a mano ni desactivar el gate para recuperar puntuación.

Distinguir la fuente metodológica de la fuente de observación: una cita
de BLS/FRED/CFTC/RBN enseña cómo se mide, pero no demuestra un número
concreto. No incorporar ETF flows, funding, OI ni derivados sin API validada
y fecha/escala; no sustituir valores faltantes por cifras de noticias.
Mantener reportes de QA por fuente, cohorte, unidad, fecha y cambios de
definición. No activar backfill o Telegram para consultar este corpus.

Para cada actualización de fichas: PR de código, diferencia de SHA256,
tests `test_knowledge_corpus_v2`, `test_specialized_news_rag`,
`test_research_studio_v2` y revisión humana del contenido, fuentes y
licencias.
