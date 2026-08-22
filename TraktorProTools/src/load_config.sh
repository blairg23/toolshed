#!/usr/bin/env bash
# Parses config.yml and exports values as env vars.
# Usage: source "$(dirname "$0")/load_config.sh"

CONFIG_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../config.yml"
TMPFILE="$(mktemp /tmp/traktor_config.XXXXXX.sh)"

if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "ERROR: config.yml not found at $CONFIG_FILE" >&2
    exit 1
fi

python3 - "$CONFIG_FILE" "$TMPFILE" <<'EOF'
import sys, yaml, shlex

with open(sys.argv[1]) as f:
    cfg = yaml.safe_load(f)

# Each config entry may itself be multiple shell tokens (e.g. "--transfers 8"
# or '--exclude "_gsdata_/**"'); split with shlex first, then re-quote each
# token individually so the result is a real bash array literal. Consuming
# scripts can use "${RCLONE_FLAGS[@]}" directly -- no eval of config-derived
# text required.
rclone_tokens = []
for entry in cfg["rclone"]["flags"]:
    rclone_tokens.extend(shlex.split(entry))
rclone_flags_literal = " ".join(shlex.quote(tok) for tok in rclone_tokens)

lines = [
    f'BUNDLE_FORMAT={shlex.quote(cfg.get("bundle_format", "tar.gz"))}',
    f'TRAKTOR_DIR={shlex.quote(cfg["traktor"]["dir"])}',
    f'BUNDLE_DIR={shlex.quote(cfg["traktor"]["bundle_dir"])}',
    f'KEEP_BACKUPS={shlex.quote(str(cfg["traktor"]["keep_backups"]))}',
    f'LOCAL_DST={shlex.quote(cfg["backup"]["local_dst"])}',
    f'CLOUD_DST={shlex.quote(cfg["backup"]["cloud_dst"])}',
    f'RECORDINGS_SRC={shlex.quote(cfg["recordings"]["src"])}',
    f'RECORDINGS_DST={shlex.quote(cfg["recordings"]["dst"])}',
    f'RCLONE_FLAGS=({rclone_flags_literal})',
]

with open(sys.argv[2], 'w') as f:
    f.write('\n'.join(lines) + '\n')
EOF

source "$TMPFILE"
rm -f "$TMPFILE"
