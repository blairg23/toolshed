#!/usr/bin/env bash
set -euo pipefail
_FORMAT_OVERRIDE="${BUNDLE_FORMAT:-}"
source "$(dirname "$0")/load_config.sh"
FORMAT="${_FORMAT_OVERRIDE:-${BUNDLE_FORMAT:-tar.gz}}"
DRY_RUN="${1:-dry}"

case "$FORMAT" in
    zip)     BUNDLE_NAME="traktor_bundle_$(date +%Y%m%d_%H%M%S).zip" ;;
    tar.gz)  BUNDLE_NAME="traktor_bundle_$(date +%Y%m%d_%H%M%S).tar.gz" ;;
    tar.zst) BUNDLE_NAME="traktor_bundle_$(date +%Y%m%d_%H%M%S).tar.zst" ;;
    *) echo "Unknown format: $FORMAT"; exit 1 ;;
esac

BUNDLE_PATH="$BUNDLE_DIR/$BUNDLE_NAME"
MANIFEST_PATH="/tmp/traktor_manifest.txt"
TRAKTOR_PARENT="$(dirname "$TRAKTOR_DIR")"
TRAKTOR_NAME="$(basename "$TRAKTOR_DIR")"

[[ "$DRY_RUN" == "dry" ]] && echo "[DRY RUN]"
echo ""
echo "=== Bundle Plan ($FORMAT) ==="
echo ""

echo "Contents: $TRAKTOR_DIR"
echo ""
echo "Folders:"
for d in "$TRAKTOR_DIR"/*/; do
    name="$(basename "$d")"
    count=$(find "$d" -type f 2>/dev/null | wc -l | xargs)
    size=$(du -sh "$d" 2>/dev/null | cut -f1)
    printf "  %-20s %6s files,  %s\n" "$name/" "$count" "$size"
done

echo ""
echo "Files:"
for f in "$TRAKTOR_DIR"/*; do
    [[ -f "$f" ]] || continue
    printf "  %-36s %s\n" "$(basename "$f")" "($(du -sh "$f" | cut -f1))"
done

TOTAL_SIZE=$(du -sbc "$TRAKTOR_DIR" 2>/dev/null | tail -1 | cut -f1)

echo ""
printf "Output: %s\n" "$BUNDLE_PATH"
printf "Size before compression: %s\n" "$(awk "BEGIN{printf \"%.1f GB\", $TOTAL_SIZE/1024/1024/1024}")"
echo ""

[[ "$DRY_RUN" == "dry" ]] && exit 0

echo "=== Bundling ==="

cat > "$MANIFEST_PATH" <<EOF
Traktor Bundle Manifest
Created: $(date)
Machine: $(hostname)
Format:  $FORMAT
EOF

cd "$TRAKTOR_PARENT"

case "$FORMAT" in
    zip)
        echo -n "  Compressing... "
        zip -rq "$BUNDLE_PATH" "$MANIFEST_PATH" "$TRAKTOR_NAME"
        echo "done"
        ;;
    tar.gz)
        tar -cf - "$MANIFEST_PATH" "$TRAKTOR_NAME" | pv -s "$TOTAL_SIZE" | gzip > "$BUNDLE_PATH"
        ;;
    tar.zst)
        tar -cf - "$MANIFEST_PATH" "$TRAKTOR_NAME" | pv -s "$TOTAL_SIZE" | zstd > "$BUNDLE_PATH"
        ;;
esac

echo ""
echo "=== Done ==="
echo "Bundle: $BUNDLE_NAME"
echo "Size before compression: $(awk "BEGIN{printf \"%.1f GB\", $TOTAL_SIZE/1024/1024/1024}")"
echo "Size after compression:  $(du -sh "$BUNDLE_PATH" | cut -f1)"
