#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/load_config.sh"

DRY_RUN="${1:-dry}"
[[ "$DRY_RUN" == "dry" ]] && echo "[DRY RUN]"

RCLONE_FLAGS=(--fast-list --transfers 8 \
    --exclude "_gsdata_/**" \
    --retries 10 --retries-sleep 10s --low-level-retries 10)

if [[ "$DRY_RUN" == "dry" ]]; then
    RCLONE_FLAGS+=(--dry-run --log-level INFO)
else
    RCLONE_FLAGS+=(-P --log-level ERROR --stats-one-line --stats 2s)
fi

rclone sync "$BUNDLE_DIR" "$CLOUD_DST/bundles" "${RCLONE_FLAGS[@]}"
