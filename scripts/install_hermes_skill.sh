#!/usr/bin/env bash
# Install/update this repository's Hermes skill without changing the system.
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$PROJECT/skills/mercados-daily-pro/SKILL.md"
DEST_BASE="${HERMES_SKILLS_HOME:-$HOME/.hermes/skills}"
DEST="$DEST_BASE/finance/mercados-daily-pro"
TARGET="$DEST/SKILL.md"

if [[ ! -f "$SOURCE" ]]; then
    echo "ERROR: No encuentro SKILL.md en $SOURCE" >&2
    exit 1
fi
if [[ -L "$DEST" || -L "$TARGET" ]]; then
    echo "ERROR: Skill destino es un enlace simbólico; revisión manual requerida" >&2
    exit 1
fi

mkdir -p "$DEST"
if [[ -f "$TARGET" ]] && ! cmp -s "$SOURCE" "$TARGET"; then
    backup="$DEST/SKILL.md.backup-$(date -u +%Y%m%dT%H%M%SZ)"
    cp -p "$TARGET" "$backup"
    echo "Anterior conservado en $backup"
fi
install -m 0644 "$SOURCE" "$TARGET"
echo "SKILL INSTALADO: $TARGET"

# Research editorial design has its own skill, independent of generic Sereno.
DESIGN_SOURCE="$PROJECT/skills/mercados-research-design/SKILL.md"
DESIGN_DEST="$DEST_BASE/finance/mercados-research-design"
DESIGN_TARGET="$DESIGN_DEST/SKILL.md"
if [[ ! -f "$DESIGN_SOURCE" || -L "$DESIGN_DEST" || -L "$DESIGN_TARGET" ]]; then
    echo "ERROR: Skill editorial ausente o destino symlink" >&2
    exit 1
fi
mkdir -p "$DESIGN_DEST"
if [[ -f "$DESIGN_TARGET" ]] && ! cmp -s "$DESIGN_SOURCE" "$DESIGN_TARGET"; then
    cp -p "$DESIGN_TARGET" "$DESIGN_TARGET.backup-$(date -u +%Y%m%dT%H%M%SZ)"
fi
install -m 0644 "$DESIGN_SOURCE" "$DESIGN_TARGET"
echo "SKILL INSTALADO: $DESIGN_TARGET"

if command -v hermes >/dev/null 2>&1; then
    echo "Comprueba con: hermes skills list"
    echo "Operación: /mercados-daily-pro; diseño exclusivo: /mercados-research-design"
else
    echo "Hermes CLI no disponible en esta sesión. Skill copiado; comprobar dentro de Hermes."
fi
