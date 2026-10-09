# Complemento on-chain: Bitview + ResearchBitcoin V2

**Alcance:** ampliación **opt-in** del informe diario BTC. La base local y el
dashboard siguen funcionando sin token ResearchBitcoin. **No se altera** ninguna
fila de `daily`, `series`, `price_btc`, `daily_metrics` ni el `publication_gate`.
Tampoco se activa envío automático o consultas adicionales en `scripts/daily.sh`.

## Fuentes verificadas

- Bitview REST: https://bitview.space/api — API con `/api/series/search`,
  `/api/series/list`, `/api/series/{series}/{index}`, `/api/series/bulk`
  y `/api/urpd/{cohort}/{date}`. La ruta `/api/metrics` está **deprecada**.
  Nuestro ingest actual utiliza `/api/series/.../1d`.
- Bitcoin Lab / ResearchBitcoin: https://api.researchbitcoin.net/docs/ ;
  FAQ https://api.researchbitcoin.net/faq/ ; catálogo de 348 métricas
  https://researchbitcoin.net/metrics/ (octubre 2026).
  Las rutas métricas V2 son `/v2/<grupo>/<slug>`; la ruta proporcionada
  `/v2/token` corresponde al acceso a credenciales, **no a un feed**.
- Acceso: header `X-API-Token`. Exclusivamente variable de entorno local
  `RESEARCHBITCOIN_API_TOKEN`; nunca parámetros URL, GitHub o HTML.
  El token caduca a los 90 días y puede requerir renovación.
- Consultas: `resolution=d1`, `output_format=json`, `from_time` inclusivo,
  `to_time` **exclusivo** en UTC. Solo velas UTC finalizadas.
  La documentación advierte límites por cuota *data points* y `429/422`.
  No se ejecutan retries automáticos que gasten cuota.
- Tier 0: 55.000 data points/semana y 1 año de histórico; las 13
  métricas seleccionadas tienen ficha `Tier 0` y resolución `d1`.
  Ver https://api.researchbitcoin.net/tier
- La API ResearchBitcoin requiere atribución visible a researchbitcoin.net
  cuando se redistribuyen datos.

## Qué añadir al informe y por qué

| Prioridad | Categoría | Métricas ResearchBitcoin | Aporte |
|---|---|---|---|
| Alta | Costo base STH/LTH | realized_price_sth, realized_price_lth | Precio vs costo de cohortes |
| Alta | Valoración activa | true_market_meanprice | Referencia alternativa al realized price |
| Alta | Valuación STH | mvrv_sth, mvrv_z_sth | Estrés / beneficio del grupo STH |
| Alta | Gasto STH | sopr_sth, realized_loss_sth | Presión vendedora y pérdidas realizadas |
| Alta | Rentabilidad de oferta | supply_in_profit_sth_percent, supply_in_profit_percent | % de oferta en ganancias |
| Complementaria | Gasto LTH / beneficios | sopr_lth, realized_profit_sth, net_realized_profit_loss_sth | Actividad de cohortes y pérdidas netas |
| Complementaria | Cointime | liveliness | Monedas activas vs inactivas |

Cada nombre y su endpoint están en `ingestion/researchbitcoin_catalog.py`.
Los valores de proveedores distintos **no son equivalentes automáticamente**
aunque compartan nombres (cohortes, tratamiento de churn, agregación y hora de
corte pueden diferir).

**Inventario de Bitview local:** el documento existente `docs/DATABASE_SCHEMA.md`
describe 2.807 nombres y millones de observaciones, pero no certifica cuáles
de estos 13 slugs ya existen ni su cobertura. La consulta `--inventory` mide
coincidencia textual de nombres con el catálogo local y cobertura del proveedor
externo. La equivalencia semántica requiere inspección por Hermes.

## Estructura técnica añadida

- `ingestion/researchbitcoin_catalog.py`: slugs, endpoint, unidad, prioridad,
  fuente y enlace a ficha oficial.
- `ingestion/researchbitcoin_v2.py`: comandos manuales de catálogo, auditoría
  local, prueba de estructura de respuesta, y sincronización de **una** serie
  bajo autorización explícita. El parser solo admite `data` como lista de
  observaciones escalares con `date/time/timestamp/datetime` y `value`; si
  el JSON real tiene otra forma, **aborta sin escribir** hasta adaptar con
  un fixture validado. Nunca infiere unidades ni reescala porcentajes.
- Tabla aislada `onchain_external_observations` en SQLite:
  `provider, metric, observed_date, observed_at_utc, value, unit,
  source_endpoint, fetched_at_utc`, única por proveedor/métrica/día.
- `rendering/onchain_complement.py`: read-only; añade fichas con valor,
  fecha UTC, metodología y fuente visible **solo cuando** hay datos locales
  válidos y no atrasados (>4 días respecto al cierre del informe).
  **No tiene conexión a la API.** Si no se creó tabla o no hay datos,
  retorna HTML vacío: el reporte original queda idéntico.

No se realiza backfill masivo, ni se modifica el motor de análisis ni
el score. La nueva sección es informativa, **no genera señales de compra**.

## Pasos para Hermes en el PC (sin exponer token ni BD)

1. Revisar `git status --short` y actualizar `master` solo si está limpio.
2. Ejecutar `python3 -m unittest -v tests.unit.test_researchbitcoin_complement`.
3. Ejecutar **sin credenciales ni red**:

   ```bash
   python3 ingestion/researchbitcoin_v2.py --catalog
   python3 ingestion/researchbitcoin_v2.py --inventory
   ```

   Esto compara el catálogo local para decidir qué ya existe realmente.
4. Con el token ya configurado **en la máquina**, identificar solo la
   **estructura** del proveedor, sin mostrar valores ni claves:

   ```bash
   python3 ingestion/researchbitcoin_v2.py --sample realized_price_sth --days 2
   ```

5. Si la forma de JSON coincide con el parser y el operador aprueba la
   ampliación, ejecutar **una** serie de prueba (con datos reales):

   ```bash
   python3 ingestion/researchbitcoin_v2.py --sync realized_price_sth --days 3
   python3 ingestion/researchbitcoin_v2.py --inventory
   ```

   Si responde `RECHAZADO: Formato JSON...`, recopilar **solo los nombres
   de campos y tipos** del `--sample`. Adaptar el parser con pruebas antes
   de volver a intentar; no insistir consumiendo cuota.
6. Generar un informe por el flujo habitual sin envío Telegram:

   ```bash
   SEND_TELEGRAM_AUTO=0 bash scripts/daily.sh
   python3 scripts/send_report.py --dry-run
   ```

   Corroborar que la nueva tarjeta aparece con *fuente, fecha y unidad*
   cuando haya datos válidos, y que `publication_gate` continúa en PASS.

**Seguridad:** no subir tokens, respuestas crudas, `.env`, SQLite ni HTML
privados al repo público. El PR diagnóstico es público incluso si está en
borrador. Las claves previamente expuestas en el historial de este proyecto
deben rotarse separadamente.

## Auditoría de escala de Supply in Profit (09-oct-2026)

Hermes inspeccionó la SQLite local y el `--sample` autenticado. Dos slugs
independientes devuelven un campo escalar cuyo nombre coincide con su slug:

| Slug | Valor original RBN (08-oct-2026) | Display fraccional **provisional** |
| --- | ---: | ---: |
| `supply_in_profit_percent` | `0.683561` | `≈68.4%*` |
| `supply_in_profit_sth_percent` | `0.709276` | `≈70.9%*` |

Las fichas públicas de ResearchBitcoin denominan ambas series `percent`, pero
**no especifican de forma inequívoca** si el JSON usa fracciones 0..1 o
valores 0..100. Los datos observados en la API (0.63..0.93) sustentan la
**hipótesis operativa** de fracciones. La validación antigua `0 <= x <= 100`
aceptaba ambas escalas; no demostraba que fuese porcentual 0..100. El reporte
ya contenía ambas métricas y las mostraba como `0.7%`, potencialmente
subestimadas por un factor 100.

Para no reescribir ni destruir observaciones, la base SQLite conserva
`value` original y `unit=percent`. El catálogo marca solamente estos dos
slugs con `raw_scale=fraction_0_1`. El parser rechaza valores fuera de 0..1;
el renderizador transforma **solo la visualización** mediante `value * 100`
y muestra `≈` y una nota explícita `*` de **normalización provisional**
junto con el dato original `data-api-raw`. No activar señales, alertas ni
narrativas direccionales basadas en estos porcentajes.

**Limitaciones:** no se ha obtenido una confirmación expresa del proveedor
sobre el formato JSON ni sobre el denominador exacto de la serie STH.
`supply_in_profit_sth_percent > supply_in_profit_percent` no prueba por sí
solo que ambas usen el mismo denominador; las fichas sobre variantes de
cohortes son demasiado generales para afirmar equivalencia. Si la API cambia
formato, la validación debe abortar y un humano debe revisar la fuente y
los ejemplos auténticos antes de migrar/escalar datos.

Fichas originales:
- https://researchbitcoin.net/metrics/supply_in_profit_percent/
- https://researchbitcoin.net/metrics/supply_in_profit_sth_percent/

## Reconciliación adicional en el publication gate (2026-10-09)

El gate de reportes originales confrontaba precios e indicadores
archivados, pero no las tarjetas de ResearchBitcoin. Una tarjeta podía
mostrar un valor RBN distinto de su observación en SQLite y conservar
un resultado global de `100/100`. Se añadió la comprobación crítica
`validation/rbn_reconciliation.py`, llamada por
`validation/publication_gate.py` **antes de publicar**.

Para la fecha del reporte se seleccionan solo observaciones RBN
locales y vigentes hasta el último día UTC completo. Por cada tarjeta
visible, se comprueba:
- Identidad exacta de métrica, proveedor y endpoint (este último
  mediante el catálogo y la fila SQLite).
- Fecha de observación UTC idéntica a la de la SQLite.
- Valor mostrado, incluyendo formato y unidades, idéntico al calculado
  **desde la observación original** por el renderizador oficial.
- Para los porcentajes 0..1, atributo `data-api-raw` coincidente y
  advertencia visible de **normalización provisional**.
- Enlace a la ficha de fuente correspondiente al slug y ausencia de
  tarjetas duplicadas, desconocidas, ocultas o ausentes.

Una discrepancia se registra como **CRITICAL** en la categoría
`consistencia` y bloquea la publicación, independientemente del score
ponderado. Si el proveedor no ha sido habilitado, su tabla no existe
o no hay datos RBN recientes **y el informe tampoco los muestra**,
el gate sigue aprobando el informe principal de Bitview/macro/precios.

Este cotejo valida **fidelidad HTML ↔ SQLite local**, no autenticidad
externa de la API ni equivalencia metodológica entre proveedores.
No prueba la escala o denominador de las métricas Supply in Profit;
estas continúan provisionalmente documentadas. No escribe datos,
no llama a APIs privadas y no activa sincronización automática.

Pruebas: `python3 -m unittest -v tests.unit.test_researchbitcoin_complement`.

## Siguientes extensiones (no incluidas)

- Añadir ingesta incremental diaria al pipeline **solo después** de validar
  esquema real, cuota, disponibilidad y tolerancia a fallos en Hermes.
- Contrastar definiciones duplicadas de Bitview vs ResearchBitcoin para
  decidir qué fuente es principal por indicador.
- Incorporar pruebas de coherencia cruzada para métricas derivadas y alertas
  de desfase, sin mezclar datos de fechas diferentes.
- Bitview URPD/Cost Basis por cohortes: nuevos snapshots pueden enriquecer el
  informe, pero son matrices de distribuciones, no series escalares; requieren
  esquema separado y no deben convertirse a un único precio sin metodología.
