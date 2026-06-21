#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/../config.yml"
MODE="${1:-dry}"

if [[ "$MODE" != "dry" && "$MODE" != "run" ]]; then
    echo "Usage: $0 [dry|run]  (default: dry)" >&2
    exit 1
fi

LOCAL_SRC="$(python3 -c "import yaml; c=yaml.safe_load(open('$CONFIG')); print(c['backup']['local_src'])")"
CLOUD_DST="$(python3 -c "import yaml; c=yaml.safe_load(open('$CONFIG')); print(c['backup']['cloud_dst'])")"

[[ "$MODE" == "dry" ]] && echo "[DRY RUN]"
echo ""
echo "Source: $LOCAL_SRC"
echo "Dest:   $CLOUD_DST"
echo ""

RCLONE_FLAGS=(--fast-list --transfers 8 \
    --exclude "_gsdata_/**" \
    --exclude "*.tmp" \
    --retries 10 --retries-sleep 10s --low-level-retries 10)

if [[ "$MODE" == "dry" ]]; then
    RCLONE_FLAGS+=(--dry-run -v --stats 0 --log-level NOTICE)
else
    RCLONE_FLAGS+=(-P --log-level NOTICE --stats-one-line --stats 2s)
fi

rclone copy "$LOCAL_SRC" "$CLOUD_DST" "${RCLONE_FLAGS[@]}"
