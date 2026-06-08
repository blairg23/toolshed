#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/../config.yml"
DRY_RUN="${1:-dry}"

LOCAL_SRC="$(python3 -c "import yaml; c=yaml.safe_load(open('$CONFIG')); print(c['backup']['local_src'])")"
CLOUD_DST="$(python3 -c "import yaml; c=yaml.safe_load(open('$CONFIG')); print(c['backup']['cloud_dst'])")"

[[ "$DRY_RUN" == "dry" ]] && echo "[DRY RUN]"
echo ""
echo "Source: $LOCAL_SRC"
echo "Dest:   $CLOUD_DST"
echo ""

RCLONE_FLAGS=(--fast-list --transfers 8 \
    --exclude "_gsdata_/**" \
    --exclude "*.tmp" \
    --retries 10 --retries-sleep 10s --low-level-retries 10)

if [[ "$DRY_RUN" == "dry" ]]; then
    RCLONE_FLAGS+=(--dry-run --log-level INFO)
else
    RCLONE_FLAGS+=(-P --log-level ERROR --stats-one-line --stats 2s)
fi

rclone copy "$LOCAL_SRC" "$CLOUD_DST" "${RCLONE_FLAGS[@]}"
