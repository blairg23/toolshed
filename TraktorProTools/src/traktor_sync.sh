#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/load_config.sh"

DRY_RUN="${1:-dry}"
[[ "$DRY_RUN" == "dry" ]] && echo "[DRY RUN]"

FLAGS=("${RCLONE_FLAGS[@]}")

if [[ "$DRY_RUN" == "dry" ]]; then
    FLAGS+=(--dry-run --log-level INFO)
else
    FLAGS+=(-P --log-level ERROR --stats-one-line --stats 2s)
fi

rclone sync "$BUNDLE_DIR" "$CLOUD_DST/bundles" "${FLAGS[@]}"
