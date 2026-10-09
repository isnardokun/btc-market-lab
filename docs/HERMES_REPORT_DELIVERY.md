# Hermes: Dashboard local + informe HTML portátil

El proyecto genera dos salidas independientes:

- Dashboard operativo local: dashboards/dashboard.html lee dashboards/dashboard_latest.json desde un servidor HTTP local; utiliza el estado generado desde SQLite.
- Informe diario portátil: reports/portable/daily_report_YYYY-MM-DD.html. Un solo archivo HTML con CSS y SVG internos que se abre en móvil o PC, incluso sin acceso a la máquina Hermes. Los enlaces a las fuentes pueden requerir conexión a Internet.

El bot envía el HTML como documento adjunto mediante Telegram sendDocument. El receptor puede descargarlo y abrirlo en un navegador; Telegram no garantiza representación previa del HTML. No requiere servidor público, webhook ni polling.

## 1. Instalación y secretos

~~~bash
cd /home/ignotus/btc-research
python3 tests/unit/test_portable_report.py
~~~

**Rota la clave FRED**: la anterior apareció en el historial de este repositorio público. Quitársela al código no la revoca. Configura una nueva clave y el token del bot en el entorno protegido de Hermes o cron (los scripts no cargan .env automáticamente):

~~~bash
export FRED_API_KEY="CLAVE_NUEVA"
export TELEGRAM_BOT_TOKEN="TOKEN_BOT"
export TELEGRAM_CHAT_ID="ID_DEL_DESTINATARIO"
export BTC_RESEARCH_HOME="/home/ignotus/btc-research"
~~~

No almacenes secretos en Git, logs, flags de línea de comandos ni reportes. El usuario debe haber iniciado conversación con el bot o autorizado su recepción.

## 2. Pipeline y salidas

~~~bash
cd /home/ignotus/btc-research
bash scripts/daily.sh
~~~

Después del gate:

- reports/daily_report_YYYY-MM-DD.html — reporte original aprobado
- reports/gate_YYYY-MM-DD.json — aprobación y SHA256 del HTML exacto
- reports/portable/daily_report_YYYY-MM-DD.html — adjunto offline
- reports/portable/daily_report_YYYY-MM-DD.html.manifest.json — procedencia, score y SHA256
- dashboards/dashboard_latest.json — datos para dashboard
- dashboards/dashboard_standalone.html — dashboard generado con los datos integrados para consultar desde file://, sin actualización automática

El gate bloquea la exportación cuando rechaza el informe o cambia su SHA256; el envío bloquea archivos modificados respecto del manifest.

El envío automático es opcional y está desactivado por defecto:

~~~bash
SEND_TELEGRAM_AUTO=1 bash scripts/daily.sh
~~~

Una falla de Telegram no invalida un reporte ya aprobado. La entrega puede reintentarse manualmente; los recibos en reports/deliveries/ previenen duplicados.

Configura cron con TZ=America/Bogota si quieres que los nombres de archivo reflejen la jornada colombiana. Todos los scripts Python del pipeline deben ejecutarse con la misma zona horaria.

## 3. Comandos disponibles para Hermes

Comprobar último informe sin comunicarlo:

~~~bash
python3 scripts/send_report.py --dry-run
~~~

Enviar último informe aprobado:

~~~bash
python3 scripts/send_report.py
~~~

Enviar una fecha concreta:

~~~bash
python3 scripts/send_report.py --file reports/portable/daily_report_2026-10-08.html
~~~

Reenviar el mismo adjunto a pesar del recibo existente:

~~~bash
python3 scripts/send_report.py --file reports/portable/daily_report_2026-10-08.html --force
~~~

Hermes solo debe enviar cuando el usuario lo solicite o la política de entrega programada esté expresamente habilitada. No debe saltarse la validación utilizando directamente archivos de reports/.

## 4. Dashboard local

~~~bash
cd /home/ignotus/btc-research
python3 -m http.server 8765 --bind 127.0.0.1
~~~

Abre http://127.0.0.1:8765/dashboards/dashboard.html en el propio equipo.

Puedes enviar dashboards/dashboard_standalone.html como snapshot opcional para consulta offline. No expongas públicamente todo el repositorio o la base SQLite mediante un servidor HTTP sin autenticación. Para navegación continua desde fuera de casa, prefiere VPN privada.

## 5. Pruebas

~~~bash
python3 tests/unit/test_portable_report.py
python3 tests/run_all.py
python3 scripts/send_report.py --dry-run
~~~

La prueba del exportador/envío no requiere Internet ni SQLite; las pruebas de integración necesitan la base poblada del equipo. Para confirmar funcionamiento operativo hay que ejecutar las pruebas en Hermes.

## 6. Métricas y limitaciones

El dashboard ahora calcula CPI interanual desde CPIAUCSL, variación mensual de NFP desde PAYEMS (miles de empleos) y denomina DTWEXBGS como USD Broad Index, no DXY.

Continúa siendo necesario auditar frescura, revisiones FRED, metodología on-chain e indicadores cuantitativos del resto del proyecto. Este cambio no certifica como reales todos los valores históricos del dashboard.
