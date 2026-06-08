#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN="${1:-dry}"

echo "=== Traktor Backup ==="
echo ""

echo "--- Step 1: Recordings ---"
"$SCRIPT_DIR/traktor_recordings_backup.sh" "$DRY_RUN"

echo ""
echo "--- Step 2: Bundle ---"
"$SCRIPT_DIR/traktor_bundle.sh" "$DRY_RUN"

echo ""
echo "--- Step 3: Cleanup ---"
"$SCRIPT_DIR/traktor_cleanup.sh" "$DRY_RUN"

echo ""
echo "--- Step 4: Cloud Sync ---"
"$SCRIPT_DIR/traktor_sync.sh" "$DRY_RUN"

echo ""
echo "=== Backup Complete ==="
