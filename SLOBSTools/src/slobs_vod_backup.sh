#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILEMAPPER="$SCRIPT_DIR/../../FileMapper/src/filemapper.py"
CONFIG="$SCRIPT_DIR/../config.yml"
MODE="${1:-dry}"

if [[ "$MODE" != "dry" && "$MODE" != "run" ]]; then
    echo "Usage: $0 [dry|run]  (default: dry)" >&2
    exit 1
fi

FLAGS=("--config" "$CONFIG" "--section" "vods")
[[ "$MODE" == "dry" ]] && FLAGS+=("--dry-run")

cd "$SCRIPT_DIR/../../FileMapper" && poetry run python3 src/filemapper.py "${FLAGS[@]}"
