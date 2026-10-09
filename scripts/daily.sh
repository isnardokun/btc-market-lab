#!/bin/bash
# BTC Research — Daily Cron Job
# Arquitectura 6-módulo:
#   ingestion/     → adaptadores API/SQL, reintentos, captura cruda
#   validation/    → frescura, integridad, metadatos, consistencia
#   quant_engine/  → cálculos deterministas
#   analysis/      → interpretación snapshots, noticias
#   rendering/     → HTML/PDF, validación visual
#   scripts/       → entry points + pipeline orchestration
#
# Schedule: 06:00 UTC daily (01:00 Colombia)
#   → Permite capturar cierre NYSE (16:00 EST = 21:00 UTC)
#
# ORDEN CRÍTICO: publication_gate ANTES de cualquier publicación.
# Pasos 7-8 (archive + dashboard) solo se ejecutan si gate pasa.
# Borradores rechazados van a /reports/rejected/

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
LOG="$BASE_DIR/cron.log"
REJECTED_DIR="$BASE_DIR/reports/rejected"
DATE_STR="$(date -u '+%Y-%m-%d')"
REPORT_HTML="$BASE_DIR/reports/daily_report_${DATE_STR}.html"

echo "=== $(date -u '+%Y-%m-%d %H:%M UTC') ===" >> "$LOG"
cd "$BASE_DIR"

# ── FASE 1: INGESTA (crítico — si falla, no hay reporte) ────────────────
echo "Fase 1: ingestión de datos..." >> "$LOG"

python3 ingestion/ingest_price.py >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    echo "  [FAIL] ingest_price — aborting" >> "$LOG"
    exit 1
fi
echo "  [OK] ingest_price" >> "$LOG"

python3 ingestion/ingest.py >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    echo "  [FAIL] ingest — aborting" >> "$LOG"
    exit 1
fi
echo "  [OK] ingest" >> "$LOG"

python3 ingestion/ingest_fred.py >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    echo "  [FAIL] ingest_fred — aborting" >> "$LOG"
    exit 1
fi
echo "  [OK] ingest_fred" >> "$LOG"

# ── FASE 2: GENERACIÓN (crítico — si falla, no hay reporte) ───────────
echo "Fase 2: generación de reporte..." >> "$LOG"

python3 rendering/charts.py >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    echo "  [FAIL] charts — aborting" >> "$LOG"
    exit 1
fi
echo "  [OK] charts" >> "$LOG"

python3 analysis/daily_report.py >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    echo "  [FAIL] daily_report — aborting" >> "$LOG"
    exit 1
fi
echo "  [OK] daily_report" >> "$LOG"

python3 validation/archive_metrics.py >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    echo "  [FAIL] archive_metrics — aborting" >> "$LOG"
    exit 1
fi
echo "  [OK] archive_metrics" >> "$LOG"

# ── FASE 3: PUBLICACIÓN CONDICIONAL ─────────────────────────────────────
# Gate se ejecuta PRIMERO. Solo si pasa, se archiva y actualiza dashboard.
# Si falla, el borrador se mueve a /reports/rejected/ y se alerta.
echo "Fase 3: publication gate..." >> "$LOG"

python3 validation/publication_gate.py >> "$LOG" 2>&1
GATE_EXIT=$?

if [ $GATE_EXIT -eq 0 ]; then
    echo "  [PASS] publication_gate — publicando..." >> "$LOG"

    # Archivar SOLO si gate pasó
    python3 rendering/archive_report.py \
        --html "$REPORT_HTML" \
        --date "$DATE_STR" \
        --asset "ALL" \
        --section "daily" >> "$LOG" 2>&1
    echo "  [OK] archive_report" >> "$LOG"

    # Actualizar dashboard SOLO si gate pasó
    python3 rendering/export_dashboard.py >> "$LOG" 2>&1
    echo "  [OK] export_dashboard" >> "$LOG"

    # Snapshot atómico post-publicación (para auditoría)
    python3 ingestion/snapshot.py >> "$LOG" 2>&1
    echo "  [OK] snapshot" >> "$LOG"

    echo "✅ PUBLICADO: $REPORT_HTML" >> "$LOG"
else
    # Gate falló — mover borrador a rejected/
    mkdir -p "$REJECTED_DIR"
    if [ -f "$REPORT_HTML" ]; then
        cp "$REPORT_HTML" "$REJECTED_DIR/daily_report_${DATE_STR}_REJECTED.html"
        echo "  [REJECTED] Reporte movido a $REJECTED_DIR/" >> "$LOG"
    fi
    echo "⚠️  GATE BLOQUEÓ PUBLICACIÓN — $DATE_STR" >> "$LOG"
    exit 1
fi

echo "Done at $(date -u '+%Y-%m-%d %H:%M UTC')" >> "$LOG"
