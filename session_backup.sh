#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN="${1:-dry}"

TRAKTOR="$SCRIPT_DIR/TraktorProTools/src/traktor_backup.sh"
SLOBS="$SCRIPT_DIR/SLOBSTools/src/slobs_vod_backup.sh"

if [[ ! -x "$TRAKTOR" ]]; then
  echo "error: TraktorProTools not found at $TRAKTOR" >&2
  echo "       Merge the feat/traktorprotools PR before using this script." >&2
  exit 1
fi
if [[ ! -x "$SLOBS" ]]; then
  echo "error: SLOBSTools not found at $SLOBS" >&2
  echo "       Merge the feat/slobstools PR before using this script." >&2
  exit 1
fi

echo "=== Session Backup ==="
echo ""

echo "--- Traktor ---"
"$TRAKTOR" "$DRY_RUN"

echo ""
echo "--- SLOBS VODs ---"
"$SLOBS" "$DRY_RUN"

echo ""
echo "=== Session Backup Complete ==="
