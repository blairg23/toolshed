#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/load_config.sh"

DRY_RUN="${1:-dry}"

[[ "$DRY_RUN" == "dry" ]] && echo "[DRY RUN]"
echo ""

mapfile -t junk < <(find "$TRAKTOR_DIR" -maxdepth 1 \( \
    -name "collection_backup_invalid*" -o \
    -name "collection_backup_outdated*" -o \
    -name "collection_broken.nml" -o \
    -name "collection_2025y*.nml" \
\) 2>/dev/null || true)

# Migration backups left by Traktor version upgrades (date-only filename, e.g. 2022-07-29.tsi)
mapfile -t migration_backups < <(find "$TRAKTOR_DIR/Settings/backups" -maxdepth 1 -name "????-??-??.tsi" 2>/dev/null || true)

# Root collection.nml.backup_* — Traktor 3.11.1 no longer writes these, all backups go to Backup/Collection/
mapfile -t old_nml < <(ls -r "$TRAKTOR_DIR"/collection.nml.backup_* 2>/dev/null || true)

mapfile -t col_backups < <(ls -r "$TRAKTOR_DIR/Backup/Collection"/collection_*.nml 2>/dev/null || true)
old_col=("${col_backups[@]:$KEEP_BACKUPS}")

mapfile -t set_backups < <(ls -r "$TRAKTOR_DIR/Backup/Settings"/settings_*.tsi 2>/dev/null || true)
old_set=("${set_backups[@]:$KEEP_BACKUPS}")

# Empty version subdirectories (e.g. leftover 3.11.1/ from interrupted update)
mapfile -t empty_dirs < <(find "$TRAKTOR_DIR" -mindepth 1 -maxdepth 1 -type d -empty 2>/dev/null || true)

mapfile -t old_bundles < <(ls -t "$BUNDLE_DIR"/traktor_bundle_*.* 2>/dev/null | tail -n +3 || true)

to_delete=()
[[ ${#junk[@]}              -gt 0 ]] && to_delete+=("${junk[@]}")
[[ ${#migration_backups[@]} -gt 0 ]] && to_delete+=("${migration_backups[@]}")
[[ ${#old_nml[@]}           -gt 0 ]] && to_delete+=("${old_nml[@]}")
[[ ${#old_col[@]}           -gt 0 ]] && to_delete+=("${old_col[@]}")
[[ ${#old_set[@]}           -gt 0 ]] && to_delete+=("${old_set[@]}")
[[ ${#empty_dirs[@]}        -gt 0 ]] && to_delete+=("${empty_dirs[@]}")
[[ ${#old_bundles[@]}       -gt 0 ]] && to_delete+=("${old_bundles[@]}")

print_group() {
    local label="$1"
    shift
    local files=("$@")
    [[ ${#files[@]} -eq 0 ]] && return
    echo "$label (${#files[@]}):"
    for f in "${files[@]}"; do echo "  $(basename "$f")"; done
    echo ""
}

print_group "Junk — $TRAKTOR_DIR/"                                    "${junk[@]+"${junk[@]}"}"
print_group "Version migration backups — Settings/backups/"           "${migration_backups[@]+"${migration_backups[@]}"}"
print_group "Root collection backups (obsolete) — $TRAKTOR_DIR/"      "${old_nml[@]+"${old_nml[@]}"}"
print_group "Backup/Collection — keeping $KEEP_BACKUPS, removing"     "${old_col[@]+"${old_col[@]}"}"
print_group "Backup/Settings — keeping $KEEP_BACKUPS, removing"       "${old_set[@]+"${old_set[@]}"}"
print_group "Empty directories"                                        "${empty_dirs[@]+"${empty_dirs[@]}"}"
print_group "Old bundles — keeping 2, removing"                        "${old_bundles[@]+"${old_bundles[@]}"}"

gsdata="$TRAKTOR_DIR/../_gsdata_"
if [[ -d "$gsdata" ]]; then
    echo "_gsdata_: $(find "$gsdata" -type f | wc -l) files (GoodSync metadata)"
    echo ""
fi

echo "Total to remove: ${#to_delete[@]} files/dirs"

if [[ "$DRY_RUN" != "dry" ]]; then
    echo ""
    for f in "${to_delete[@]}"; do
        [[ -d "$f" ]] && rm -rf "$f" || rm "$f"
    done
    [[ -d "$gsdata" ]] && rm -rf "$gsdata"
    echo "Done."
fi
