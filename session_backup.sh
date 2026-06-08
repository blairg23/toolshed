#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN="${1:-dry}"

echo "=== Session Backup ==="
echo ""

echo "--- Traktor ---"
"$SCRIPT_DIR/TraktorProTools/src/traktor_backup.sh" "$DRY_RUN"

echo ""
echo "--- SLOBS VODs ---"
"$SCRIPT_DIR/SLOBSTools/src/slobs_vod_backup.sh" "$DRY_RUN"

echo ""
echo "=== Session Backup Complete ==="
