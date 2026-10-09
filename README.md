# Mercados Daily Pro

> Generación automática de reportes financieros diarios: BTC + S&P 500 + Oro. Datos on-chain, macro y noticias — todo verificable, nada inventado.

## Investigación histórica + diseño editorial propio (2026)

**Dos obligaciones permanentes:**

1. **Guardar el mayor histórico auténtico disponible de todas las APIs**
   para estudios longitudinales, no solo el cierre reciente.
   [Política y backfill por fuente](docs/HISTORICAL_DATA_POLICY.md).
   Las descargas se ejecutan en lotes controlados en Hermes;
   no afirmar que una API sin acceso histórico completo lo entregó.
2. **Mercados Research Studio**: diseño profesional, móvil y A4,
   HTML autónomo, fuentes y escala auditables. Skill de Hermes
   `/mercados-research-design`, independiente de Sereno.
   [Manual editorial](docs/REPORT_DESIGN_SYSTEM.md).

Consultas y planes **offline** sobre la SQLite local:

~~~bash
python3 scripts/history_coverage.py --output reports/history_coverage.json
python3 scripts/history_query.py --provider bitview --metric mvrv --from 2024-01-01 --to 2024-12-31
python3 ingestion/researchbitcoin_archive.py --history --tier 0 --max-requests 13
python3 ingestion/bitview_history.py --all-daily --max-requests 8
python3 ingestion/yahoo_history.py --limit 4
python3 ingestion/ingest_fred.py --history
~~~

Para consultas reales, primero revisar el historial accesible,
preparar respaldo de la BD y ejecutar las ingestas de forma
explícita con `--apply` como se documenta en el procedimiento.
El pipeline diario admite incremento RBN opt-in:
`RBN_INCREMENTAL_ENABLED=1 RBN_MAX_REQUESTS=13`,
desactivado por defecto hasta la validación local de Hermes.
No se activa cron adicional ni Telegram por esa opción.

## Quick Start

```bash
# Ejecutar pipeline completo
bash scripts/daily.sh

# Verificar calidad
python3 validation/publication_gate.py
python3 validation/audit_incidents.py
```

## Estructura

```
btc-research/
├── docs/SYSTEM.md          # Documentación completa
├── db/btc_research.db     # SQLite — 4 tablas de datos
├── ingestion/              # APIs → BD
├── validation/             # Quality gates
├── quant_engine/           # Cálculos puros (RSI, MA, ATR...)
├── analysis/               # daily_report.py (~1250 líneas)
├── rendering/              # Charts, dashboard, archive
├── reports/                 # Reportes HTML publicados
└── dashboards/             # Dashboard HTML
```

## Pipeline (11 pasos → 06:00 UTC)

```
ingest_price → ingest → ingest_fred → charts → daily_report
→ archive_metrics → publication_gate → [archive_report → export_dashboard → snapshot]
```

## Datos en BD

| Tabla | Contenido | Desde |
|---|---|---|
| `price_btc` | BTC-USD OHLCV Yahoo | Sep 2014 |
| `daily` | On-chain bitview | Sep 2014 |
| `macro_fred` | 14 series macro FRED | Sep 2014 |
| `daily_metrics` | 74 métricas diarias | 337 días |

## Quality Gates

- **Publication gate**: 99/100 → auto-publicado (umbral: 95)
- **Audit incidents**: 10/12 PASS mínimo

## Reglas de oro

1. Ningún dato sin identidad: instrumento + unidad + origen + fecha + validación
2. El LLM no inventa cifras
3. RSI = Wilder smoothing (no SMA simple)
4. SMA200 = 252 días de datos
5. DTWEXBGS ≠ DXY (Broad Index)

## Documentación

→ `docs/SYSTEM.md` — arquitectura completa, tablas, fuentes, mantenimiento


## Hermes: reporte HTML independiente del dashboard

El dashboard dinámico se ejecuta localmente con SQLite, pero la copia del
reporte aprobada por publication_gate se exporta a:

    reports/portable/daily_report_YYYY-MM-DD.html

Es un archivo autocontenido que se puede adjuntar y abrir en otro dispositivo
sin conexión al servidor local. El envío por Telegram es opcional y no está
habilitado por defecto. Hermes puede enviarlo por solicitud:

~~~bash
# Primero: configurar FRED_API_KEY, TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID
python3 scripts/send_report.py --dry-run
python3 scripts/send_report.py
~~~

Para envío cron programado: SEND_TELEGRAM_AUTO=1 bash scripts/daily.sh.
La API de Telegram usa sendDocument: descargar y abrir el .html en un navegador.
Los adjuntos se bloquean si no corresponden a un gate aprobado con hash válido.

El dashboard local puede abrirse mediante un servidor HTTP restringido a
127.0.0.1. El pipeline también exporta dashboards/dashboard_standalone.html
con datos integrados, que se puede abrir offline como snapshot.

**SEGURIDAD:** la clave FRED que estaba versionada debe revocarse/rotarse en
FRED antes del despliegue; usar una nueva mediante variable de entorno.
Nunca almacenar tokens de Telegram ni claves en el repositorio.

Guía completa: [docs/HERMES_REPORT_DELIVERY.md](docs/HERMES_REPORT_DELIVERY.md).


## Skill nativo para Hermes (actualizar, probar y enviar)

Tras integrar el PR en master y traerlo a la máquina, se instala el skill desde
el directorio propio del proyecto (con backup si Hermes ya tenía uno modificado):

~~~bash
cd /home/ignotus/btc-research
bash scripts/install_hermes_skill.sh
hermes skills list
~~~

O para futuras actualizaciones controladas y pruebas:

~~~bash
bash scripts/update_local_and_test.sh
~~~

Para actualizar **y ejecutar realmente el pipeline** (sin enviar Telegram):

~~~bash
bash scripts/update_local_and_test.sh --pipeline
~~~

En una sesión nueva de Hermes, pedir:

~~~text
/mercados-daily-pro Actualiza en local, prueba el proyecto y genera el HTML portátil; no lo envíes todavía.
~~~

El skill vive en [skills/mercados-daily-pro/SKILL.md](skills/mercados-daily-pro/SKILL.md).
Si el PR aún no está integrado, los archivos no existen en master; revisar el PR y hacer merge primero. No ejecutar comandos de actualización con cambios locales sin guardar.


## Hermes ↔ ChatGPT: diagnóstico compartido por Pull Request

El agente local puede actualizar/probar el sistema y preparar un paquete
diagnóstico con el estado del host, resultados, gate, logs depurados y HTML.
El contenido se mantiene local salvo que se active la publicación a GitHub.

~~~bash
# Actualizar master, probar pipeline y preparar evidencia en reports/bridge/
bash scripts/hermes_review_cycle.sh --pipeline --include-html

# Solo con permiso explícito para subir a repo PÚBLICO:
bash scripts/hermes_review_cycle.sh --no-update --pipeline --include-html --public
~~~

Un envío público crea un Pull Request en borrador con archivos en
hermes-feedback/RUN_ID/ y deja master intacto. El revisor puede comentar allí
y Hermes puede leer la conversación:

~~~bash
python3 scripts/hermes_bridge.py inbox --pr NUMERO
~~~

No se trata de una conexión automática en tiempo real con ChatGPT: el usuario
debe facilitar el PR para que lo revise, salvo que habilite monitoreo explícito.

**SEGURIDAD**: docs/SCRAPING_TOOLS.md expuso FRED/Telegram/Exa en el historial
público. Se ha eliminado del estado actual, pero **es obligatorio revocar y
rotar** esos secretos. Los logs se depuran automáticamente, pero sigue
requiriéndose revisión humana antes de publicarlos en este repositorio público.

Protocolo: [docs/HERMES_GITHUB_BRIDGE.md](docs/HERMES_GITHUB_BRIDGE.md).
