#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN="${1:-dry}"
MODE="${2:-incremental}"

if [[ "$MODE" != "incremental" && "$MODE" != "full-bundle" ]]; then
    echo "Usage: $0 [dry|run] [incremental|full-bundle]  (default: incremental)" >&2
    exit 1
fi

echo "=== Traktor Backup ($MODE) ==="
echo ""

echo "--- Step 1: Recordings ---"
"$SCRIPT_DIR/traktor_recordings_backup.sh" "$DRY_RUN"

if [[ "$MODE" == "full-bundle" ]]; then
    echo ""
    echo "--- Step 2: Bundle ---"
    "$SCRIPT_DIR/traktor_bundle.sh" "$DRY_RUN"

    echo ""
    echo "--- Step 3: Cleanup ---"
    "$SCRIPT_DIR/traktor_cleanup.sh" "$DRY_RUN"

    echo ""
    echo "--- Step 4: Cloud Sync (bundle) ---"
    "$SCRIPT_DIR/traktor_sync.sh" "$DRY_RUN"
else
    echo ""
    echo "--- Step 2: Cloud Sync (incremental) ---"
    "$SCRIPT_DIR/traktor_sync_live.sh" "$DRY_RUN"
fi

echo ""
echo "=== Backup Complete ==="
