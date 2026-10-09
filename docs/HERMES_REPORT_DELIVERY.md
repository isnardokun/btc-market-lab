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


## 7. Skill nativo para Hermes y actualización segura en la máquina

La guía anterior explica el uso manual. Además, el repositorio contiene
skills/mercados-daily-pro/SKILL.md con formato nativo para Hermes (frontmatter
YAML y procedimientos para actualización, validación, HTML, Telegram, fallos).

El skill no queda instalado en la máquina simplemente por existir en GitHub.
Primero se debe integrar el PR #1 a master; después, en el equipo:

~~~bash
cd /home/ignotus/btc-research
git status --short
git branch --show-current
git remote -v
git fetch origin master
git merge --ff-only origin/master
bash scripts/install_hermes_skill.sh
hermes skills list
~~~

Si la rama actual no es master o hay cambios locales, NO continuar hasta
revisarlos. El instalador deja un backup de un skill anterior personalizado.

En la siguiente actualización, el script puede realizar el fast-forward de
master de forma segura, instalar el skill y ejecutar pruebas:

~~~bash
bash scripts/update_local_and_test.sh
~~~

Para ensayar una rama que aún no fue integrada, usar su checkout existente y:

~~~bash
bash scripts/update_local_and_test.sh --no-update
~~~

Para regenerar realmente el pipeline a partir de la BD local, tras verificar
FRED_API_KEY y disponibilidad de APIs:

~~~bash
bash scripts/update_local_and_test.sh --pipeline
~~~

El script fuerza SEND_TELEGRAM_AUTO=0 al probar el pipeline. En modo normal no
realiza ingesta ni envíos. Los tests de integración con SQLite se omiten
explícitamente cuando no existe la BD; nunca se reportan como aprobados.

En Hermes se puede invocar explícitamente:

~~~text
/mercados-daily-pro Actualiza el repositorio en local y prueba el informe. No envíes Telegram hasta que te lo autorice.
~~~

Hermes descubre skills instalados en ~/.hermes/skills y los utiliza en nuevas
sesiones (o por activación explícita según versión). Alternativamente, en
versiones compatibles se puede usar:

~~~bash
hermes skills install isnardokun/btc-market-lab/skills/mercados-daily-pro
~~~

Este comando consulta el repositorio GitHub; para el trabajo en la máquina es
preferible el instalador local y su copia sincronizada con el checkout.


## 8. Diálogo de diagnósticos Hermes ⇄ ChatGPT

Para ayudar a revisar los problemas del HTML desde una sesión distinta de
la máquina, usar scripts/hermes_review_cycle.sh y scripts/hermes_bridge.py.

- **Local (predeterminado)**: actualización, pruebas, gate y paquete con log
  depurado, datos no sensibles del equipo, estado de SQLite y HTML disponible.
- **GitHub (opt-in)**: abrir un Pull Request BORRADOR con el paquete, en una
  rama hermes/feedback-..., sin modificar master ni enviar Telegram.
- **Comentarios PR**: sirven para la revisión y las respuestas entre este
  asistente y Hermes cuando el usuario solicita leer el PR. No existe
  conexión directa ni vigilancia automática entre agentes.

~~~bash
# Primera revisión sin publicación de datos
bash scripts/hermes_review_cycle.sh --no-update --pipeline --include-html

# Solo con autorización explícita de publicar en repo GitHub PÚBLICO
bash scripts/hermes_review_cycle.sh --no-update --pipeline --include-html --public

# Hermes consulta las observaciones del revisor de un PR
python3 scripts/hermes_bridge.py inbox --pr NUMERO
~~~

El HTML dentro del PR es una **copia de diagnóstico**, que puede corresponder
a un reporte rechazado o anterior a la ejecución. No implica gate PASS.
Siempre inspeccionar run.json: report.kind, report.source_mtime_utc y gate.

La implementación es deliberadamente de participación humana:
la depuración automática de logs nunca garantiza privacidad absoluta. La
revisión de secretos es esencial, sobre todo porque este repositorio es público.

**Seguridad:** FRED, bot Telegram y Exa/ScrapeGraph tuvieron secretos
expuestos en documentos Git; los valores actuales se retiraron, pero las
credenciales antiguas requieren rotación y el historial persiste.

Manual completo: [docs/HERMES_GITHUB_BRIDGE.md](HERMES_GITHUB_BRIDGE.md).
