# Mercados Research Studio — Sistema editorial exclusivo v1

**Producto:** Mercados Daily Pro (Bitcoin, SPY, oro, macro, on-chain,
ResearchBitcoin y noticias). **Skill de Hermes:**
`skills/mercados-research-design/SKILL.md`, comando
`/mercados-research-design`. No es Sereno, ni depende de él.
**Implementación real:** `rendering/market_design.py` importado
por `analysis/daily_report.py`, incrustado en el HTML ANTES de pasar
por publication gate.

## Concepto

Un **investment research desk** profesional: claridad y evidencia
antes que decoración. Convertir un tablero con múltiples tarjetas
en un documento editorial jerárquico y portátil. No confundir
datos observados con escenarios o forecasts.

## Sistema visual

| Token | Valor | Uso |
| --- | --- | --- |
| deep | #12243A | Cabecera, títulos y orden visual |
| ink | #152436 | Texto y cifras |
| ivory | #F3F6FA | Fondo de página |
| paper | #FFFFFF | Tarjetas y tablas |
| sea | #1D5C73 | Enlaces, marca editorial y secciones |
| line | #D9E0E8 | Separadores y contornos |
| up | #187454 | Cambio positivo demostrado |
| dn | #B43C48 | Cambio negativo demostrado |
| gold | #8B6B32 | Referencias/metales |

Tipografías: títulos Georgia/Times, texto fuente del sistema,
números Consolas/SF Mono/Liberation Mono. El HTML NO solicita
Google Fonts ni recursos remotos, y todas las cifras se renderizan
con alineación tabular.

**Responsive:** móvil estrecho (320 px), tableta (768 px) y desktop
(1440 px). **Impresión:** A4, bordes sobrios, saltos de página, SVG
escalados, sin sombras y con hipervínculos identificables.
**Accesibilidad:** focus-visible, contraste elevado, reducción de
animación, distinción de signos +/− sin depender del color.

## Jerarquía objetivo — evolución por fases

**v1 integrado** (este PR): tokens visuales, tipografía,
ancho editorial, jerarquía de títulos, tarjetas de cifras, mejor
lectura numérica, diseño móvil y A4. Todo preservando estructura
y clases anteriores para no afectar el gate ni los tests.

**v2 candidata** (NO desplegada aún): nueva portada ejecutiva con
fecha efectiva de las fuentes; una síntesis inicial en
«Hechos observados / Implicaciones condicionadas / Riesgos y
catalizadores», secciones de BTC, macro, índices, commodities,
noticias y anexo metodológico. Debe pasar revisión separada:
requiere cambios semánticos en `daily_report.py` y tests de
integridad editorial.

**v3 candidata**: vistas históricas reproducibles por fecha,
gráficos interactivos solo como complemento opcional; la entrega
HTML debe funcionar offline sin API ni servidor. Mantener
trazabilidad de puntos y versiones del cálculo.

## Requisitos de información no negociables

1. Mostrar nombre exacto y naturaleza del instrumento (SPY ETF
   versus SPX índice, GC=F futuros versus oro spot).
2. Incluir fecha UTC de última observación, fecha de generación y
   procedencia de los indicadores.
3. Mostrar la métrica `Supply in Profit` de RBN con caveat de
   normalización provisional, no con falsa precisión ni señales.
4. Conservar Reuters/Optech/BLS/Fed/Exa según fuentes realmente
   utilizadas, sin afirmar que el feed funcionó si no respondió.
5. No alterar soporte/resistencia, RSI, escenarios, series
   ni snapshots de SQLite solo por cambios estéticos.
6. El gate debe aprobar el HTML original; toda entrega portátil
   debe coincidir en SHA256 y manifest.
7. No insertar CDN, fuentes remotas, dashboards internos públicos
   ni JavaScript de seguimiento.
8. Las tablas de histórico deberán distinguir
   `completo accesible` de `parcial` y señalar lagunas.

## Pruebas recomendadas

~~~bash
python3 -m unittest -v tests.unit.test_historical_archive_and_design
python3 tests/run_all.py
SEND_TELEGRAM_AUTO=0 bash scripts/daily.sh
python3 scripts/send_report.py --dry-run
~~~

Inspección visual real: abrir
`reports/portable/daily_report_YYYY-MM-DD.html` en navegador local
en ancho 320/768/1440 px, impresión PDF A4 desde el navegador,
comprobar que no se recorta SVG o narrativa y que las dos marcas
provisionales de RBN son visibles. Si Hermes no dispone de navegador
o captura, consignarlo como verificación visual pendiente;
CI solo verifica contrato HTML/CSS y tests, no sustitute
una evaluación pixel a pixel.

## Criterio de aceptación

- Legible sin red, CSS embebido, sin enlaces de carga externos.
- Menos ruido decorativo, mayor jerarquía entre KPI, contexto y
  gráficos, sin pérdida del nivel de detalle.
- Reporte HTML idéntico numéricamente al snapshot aprobado;
  cero advertencias críticas del gate.
- Resultado reproducible en función del commit y SQLite.
- El skill `/mercados-research-design` instalado junto a
  `/mercados-daily-pro`, sin desinstalar otros skills de Hermes.

## v2 implementada — 2026-10-09 (iteración conservadora)

El layout empieza con tres bloques deterministas: observaciones,
inferencia condicional y fuentes pendientes. Los valores proceden
del snapshot validado existente; el informe mantiene la estructura
detallada posterior y el código de publicación auditable.

La nueva sección de lectura cruzada RBN solo compara métricas locales
de igual fecha UTC; comunica discrepancias profit/loss/net como
reconciliación pendiente, sin sobreescribir la serie del proveedor.

**Deuda editorial abierta:** reordenar las secciones heredadas por
jerarquía de investigación, normalizar gráficos SVG con ejes/leyendas,
incluir coberturas y cortes por panel, e inspeccionar impresión A4 y
capturas del navegador local. No declarar la v2 como transformación
integral del report ni afirmar histórico completo por un gate PASS.
