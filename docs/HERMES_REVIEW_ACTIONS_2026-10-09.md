# Hermes: observaciones obligatorias y plan de mejora — 2026-10-09

**Alcance:** sistema local Mercados Daily Pro / Research Studio. Este
documento constituye instrucciones operativas para el agente Hermes.
No es evidencia de que el backfill ya fue ejecutado. No modificar ni
publicar datos o archivos privados sin instrucción del operador.

## Hallazgos de la revisión del HTML del 9 de octubre

| Severidad | Hallazgo concreto | Acción requerida | Criterio de aceptación |
| --- | --- | --- | --- |
| P0 | RSI 36 podía aparecer como «sobreventa» o compra | Aplicar umbral descriptivo 30/70 para todas las secciones; MA encima/debajo no es señal operativa | Pruebas uniformes en BTC/SPY/GC=F y escenarios |
| P0 | «CPI 14-oct» incrustado en escenarios con calendario sin verificar | Eliminar fechas ad hoc. Conectar calendario oficial verificado antes de nombrar un evento | Evento con fuente, UTC, importancia y última actualización o mensaje explícito «no verificado» |
| P0 | RBN 08-10-2026: profit STH 294.211.856, loss STH 472.771.459 y neto API -180.285.307 | Contrastar endpoint, unidad, cohorte, filtros y precisión; conservar todos los valores sin forzar igualdad | Diferencia aritmética US$1.725.704 reportada como pendiente, nunca corregida silenciosamente |
| P1 | 13 métricas escalares RBN vs Tier 2 más amplio | Catalogar endpoints restantes y formas no escalares en familias validadas | Cobertura e ingestión por métrica, ventana y modalidad; no afirmar 348 series completas |
| P1 | Gráficos muestran estilo heredado, resumen ejecutivo antes estaba al final | Validar v2: síntesis inicial en tres capas, tres activos, móvil y A4 | Capturas reales 320/390/768/1440 y prueba de impresión sin cortes |
| P1 | Titulares de prensa «Sin evaluar» clasificados temáticamente | Preferir fuentes primarias, verificar fechas y separar rumor/inferencia/hecho | Etiqueta de descubrimiento y evidencia primaria para cada causalidad publicada |
| P1 | Código de histórico ≠ histórico descargado | Auditar SQLite real con history_coverage.py por proveedor, punto y fecha | Ventanas, periodos, huecos, revisions, estado de completitud por métrica |
| P2 | Falta comparación temporal 1/7/30 días y percentiles por cohorte | Proponer métricas derivadas sobre archivo histórico validado con fechas de corte | Valores reproducibles, sin look-ahead, con unidad/metodología |
| P2 | Publication Gate 100/100 se usa como sello editorial | Añadir tests semánticos y revisión humana | PASS se reporta como control automatizado, no certificación investigativa |

## Rutina de Hermes para cada edición

1. Confirmar SHA Git, versiones instaladas de skills y estado de `master`
   antes de actualizar. No `git reset --hard`; retener artefactos locales.
2. Leer `docs/REPORT_DESIGN_SYSTEM.md` y este documento, inspeccionar
   la cobertura con `python3 scripts/history_coverage.py`.
3. Antes de emitir cifras, registrar `fuente/instrumento/unidad/fecha UTC/`
   `raw/transformación/estado de validación`. No cruzar días distintos.
4. Ejecutar pruebas:
   `python3 -m unittest -v tests.unit.test_research_studio_v2`
   y `python3 tests/run_all.py`. Gate solo sobre HTML generado *antes*
   de crear la copia portable, verificando hash del manifiesto.
5. Inspeccionar el HTML a 320, 390, 768 y 1440 px y A4. No inventar
   evidencia de navegador si no hay capturas.
6. Interpretar RBN y Bitview de forma independiente; nunca sustituir
   métricas homónimas ni promediar sus valores. Contrastar solo
   observaciones del mismo cierre UTC.
7. Noticias: preferir Federal Reserve/BLS/BTC Optech y research rastreable.
   Las búsquedas Exa/fragmentos de prensa no autorizan afirmar causalidad.
8. Informar al revisor: commit, gate, hash HTML, corte por fuente,
   número de métricas descargadas, fechas límite/lagunas, incidencias
   y propuesta con archivo/función/test concreto.
9. No subir a GitHub público HTML de producción, SQLite, credenciales,
   receipts de Telegram o identificadores privados.

## Investigación a desarrollar sin improvisar

- ResearchBitcoin Tier 2: retroceder a inicios reales de cada serie
  según plan/permiso; ventanas 14 días, cuota, reanudación, empty eras.
  Completar familias escalares y adaptadores dimensionales con
  pruebas de escala, denominador y versionado.
- Bitview: d1 completo, distinguir series por altura de bloque,
  matrices/URPD; no interpolar días inexistentes.
- Yahoo: OHLCV original de cada símbolo del catálogo, sesiones
  no operadas sin observaciones inventadas.
- FRED/ALFRED: observaciones y vintages reproducibles por fecha.
- Reporte: cambios de régimen, drawdown, volatilidad, diferenciales
  de costo base por cohortes, calificación de evidencia para
  noticias y escenarios; indicadores de derivados y ETF solo
  cuando haya origen consultable, permisos y fechas confiables.
- Diseño Research Studio: reemplazar en fase posterior el resto de
  la distribución heredada de tarjetas; ejes, leyendas y unidades
  en SVG; anexos de cobertura temporal y limitaciones.

**Regla de cierre:** si una fuente no responde, indicar omisión y
estado de frescura; no sustituirla por cifras antiguas ni inventadas.
La ausencia de evidencia reduce el alcance del análisis, nunca la
honestidad de la publicación.
