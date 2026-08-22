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

# Anything rclone would overwrite or delete on this run is moved here
# instead of lost, so we keep version history without re-archiving
# everything on every run.
BACKUP_DIR="$CLOUD_DST/live-versions/$(date +%Y%m%d_%H%M%S)"

rclone sync "$TRAKTOR_DIR" "$CLOUD_DST/live" --backup-dir "$BACKUP_DIR" "${FLAGS[@]}"
