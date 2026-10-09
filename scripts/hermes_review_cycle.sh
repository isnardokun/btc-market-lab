#!/usr/bin/env bash
# Execute update/tests then prepare diagnostic bundle, even if tests fail.
# Public GitHub upload only when explicitly requested.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
args=()
PIPELINE=0
PUBLIC=0
HTML=0
usage() {
    echo "Uso: bash scripts/hermes_review_cycle.sh [--no-update] [--pipeline] [--public] [--include-html]"
    echo "Por defecto NO sube nada a GitHub. --public crea PR borrador en repositorio PÚBLICO."
}
for arg in "$@"; do
    case "$arg" in
        --no-update) args+=("--no-update") ;;
        --pipeline) PIPELINE=1; args+=("--pipeline") ;;
        --public) PUBLIC=1 ;;
        --include-html) HTML=1 ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Flag desconocido: $arg" >&2; usage >&2; exit 2 ;;
    esac
done
mkdir -p reports/bridge
raw_log="$(mktemp "$ROOT/reports/bridge/.run-XXXXXX.log")"
echo "Ejecutando actualización/pruebas locales, Telegram DESACTIVADO."
SEND_TELEGRAM_AUTO=0 bash scripts/update_local_and_test.sh "${args[@]}" 2>&1 | tee "$raw_log"
status=${PIPESTATUS[0]}
prepare=("python3" "scripts/hermes_bridge.py" "prepare" "--log" "$raw_log" "--exit-code" "$status")
if [[ "$HTML" == "1" ]]; then prepare+=("--include-html"); fi
echo "Preparando paquete diagnóstico local..."
result="$("${prepare[@]}")"
prepared=$?
if [[ "$prepared" != "0" ]]; then
    echo "ERROR: no se pudo preparar paquete. Log crudo guardado LOCALMENTE: $raw_log" >&2
    exit 2
fi
echo "$result"
packet="${result#PAQUETE LOCAL: }"
if [[ "$PUBLIC" == "1" ]]; then
    echo "Subiendo paquete a repo PÚBLICO como PR borrador (nunca a master)."
    python3 scripts/hermes_bridge.py publish "$packet" --approve-public || exit 3
else
    echo "Solo local. Para publicación pública, ejecutar:"
    echo "python3 scripts/hermes_bridge.py publish '$packet' --approve-public"
fi
if [[ "$status" != "0" ]]; then
    echo "IMPORTANTE: las pruebas del proyecto FALLARON (exit=$status); diagnóstico preservado." >&2
fi
exit "$status"
