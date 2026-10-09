#!/usr/bin/env bash
# Safe, repeatable local update and test for Hermes btc-market-lab.
# Default: only master fast-forward + tests, never sends Telegram or runs ingestion.
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT"
UPDATE=1
PIPELINE=0

usage() {
    cat <<'USAGE'
Uso: bash scripts/update_local_and_test.sh [--no-update] [--pipeline]
  (sin flags): verifica origin, actualiza master con fast-forward, instala skill y prueba.
  --no-update: probar la rama actual sin actualizar ni cambiar de rama.
  --pipeline: además ejecutar ingesta/report/gate y dry-run del HTML (sin Telegram).
USAGE
}
for arg in "$@"; do
    case "$arg" in
        --no-update) UPDATE=0 ;;
        --pipeline) PIPELINE=1 ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Opción desconocida: $arg" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ "$(git rev-parse --show-toplevel 2>/dev/null || true)" != "$PROJECT" ]]; then
    echo "ERROR: $PROJECT no es la raíz de un repositorio Git" >&2
    exit 1
fi

remote="$(git remote get-url origin 2>/dev/null || true)"
if [[ ! "$remote" =~ (^|[/:])isnardokun/btc-market-lab(\.git)?$ ]]; then
    echo "ERROR: origin inesperado. Verificar manualmente git remote -v" >&2
    exit 1
fi

echo "=== Mercados Daily Pro / Hermes ==="
echo "Repositorio: $PROJECT"
echo "Rama: $(git branch --show-current)"
echo "Commit inicial: $(git rev-parse --short HEAD)"

if [[ "$UPDATE" = "1" ]]; then
    if [[ "$(git branch --show-current)" != "master" ]]; then
        echo "ERROR: Para actualizar debes estar en master. En la rama del PR usa --no-update." >&2
        exit 1
    fi
    if ! git diff --quiet || ! git diff --cached --quiet; then
        echo "ERROR: Hay cambios locales rastreados. Respáldalos o haz commit antes de actualizar." >&2
        exit 1
    fi
    git fetch origin master
    git merge --ff-only origin/master
fi

echo "Commit ejecutado: $(git rev-parse --short HEAD)"
if [[ ! -f scripts/install_hermes_skill.sh ]]; then
    echo "ERROR: Este checkout todavía no incluye el instalador del skill." >&2
    echo "Integra primero el PR correspondiente o prueba su rama con --no-update." >&2
    exit 1
fi
bash scripts/install_hermes_skill.sh

echo "=== Verificaciones estáticas ==="
bash -n scripts/daily.sh scripts/install_hermes_skill.sh scripts/update_local_and_test.sh
python3 -m compileall -q rendering/portable_report.py scripts/send_report.py rendering/export_dashboard.py ingestion/ingest_fred.py
echo "[PASS] bash -n y compileall"

echo "=== Tests offline unitarios ==="
python3 tests/unit/test_portable_report.py
if [[ -f db/btc_research.db ]]; then
    echo "=== Suite completa (incluye integración SQLite) ==="
    python3 tests/run_all.py
else
    echo "[SKIP] db/btc_research.db no existe: se omite la suite de integración."
    echo "       No se puede afirmar que el pipeline real está probado."
fi

if [[ "$PIPELINE" = "1" ]]; then
    if [[ ! -f db/btc_research.db ]]; then
        echo "ERROR: --pipeline exige db/btc_research.db" >&2
        exit 1
    fi
    if [[ -z "${FRED_API_KEY:-}" ]]; then
        echo "ERROR: --pipeline exige FRED_API_KEY exportada en el entorno" >&2
        exit 1
    fi
    echo "=== Pipeline real SIN envío por Telegram ==="
    TZ=America/Bogota SEND_TELEGRAM_AUTO=0 bash scripts/daily.sh
    python3 scripts/send_report.py --dry-run
    echo "[PASS] pipeline real y HTML portátil"
fi

echo "=== RESULTADO ==="
echo "[PASS] Actualización/comprobación finalizada (commit $(git rev-parse --short HEAD))"
echo "Telegram: NO enviado."
