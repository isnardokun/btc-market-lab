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
