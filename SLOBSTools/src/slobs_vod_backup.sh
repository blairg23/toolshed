#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILEMAPPER="$SCRIPT_DIR/../../FileMapper/src/filemapper.py"
CONFIG="$SCRIPT_DIR/../config.yml"
DRY_RUN="${1:-dry}"

FLAGS=("--config" "$CONFIG" "--section" "vods")
[[ "$DRY_RUN" == "dry" ]] && FLAGS+=("--dry-run")

cd "$SCRIPT_DIR/../../FileMapper" && poetry run python3 src/filemapper.py "${FLAGS[@]}"
