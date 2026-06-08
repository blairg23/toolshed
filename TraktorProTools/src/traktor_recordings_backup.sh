#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILEMOVER="$SCRIPT_DIR/../../FileMover/src/filemover.py"
CONFIG="$(cd "$SCRIPT_DIR/.." && pwd)/config.yml"
DRY_RUN="${1:-dry}"

FLAGS=("--config" "$CONFIG" "--section" "recordings")
[[ "$DRY_RUN" == "dry" ]] && FLAGS+=("--dry-run")

cd "$SCRIPT_DIR/../../FileMover" && poetry run python3 src/filemover.py "${FLAGS[@]}"
