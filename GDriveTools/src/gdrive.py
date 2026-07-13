#!/usr/bin/env python3
"""
GDriveTools CLI: rclone-backed backup and verify for a local <-> Google Drive
directory pair.

Usage:
    python gdrive.py backup [dry|run] [--local-src PATH] [--cloud-dst REMOTE:PATH]
    python gdrive.py verify [--local-src PATH] [--cloud-dst REMOTE:PATH] [--output PATH]

Config (config.yml, sections are independent -- no shared keys):
    backup:
      local_src: "..."
      cloud_dst: "..."
    verify:
      local_src: "..."
      cloud_dst: "..."
      output: stdout   # default; or a file path, e.g. missing-files.csv

Every config value can be overridden per-run via the matching CLI flag, so
`verify` can check an arbitrary one-off directory against an arbitrary Drive
path without touching config.yml.
"""

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR.parent / "config.yml"

RCLONE_RETRY_FLAGS = ["--retries", "10", "--retries-sleep", "10s", "--low-level-retries", "10"]
RCLONE_EXCLUDES = ["_gsdata_/**", "*.tmp"]


def exclude_flags():
    flags = []
    for pattern in RCLONE_EXCLUDES:
        flags += ["--exclude", pattern]
    return flags


def load_config():
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve(args, config, section, key, required=True):
    value = getattr(args, key, None)
    if value is None:
        value = config.get(section, {}).get(key)
    if value is None and required:
        flag = "--" + key.replace("_", "-")
        raise SystemExit(f"Missing '{key}': pass {flag} or set {section}.{key} in config.yml")
    return value


def run_backup(args, config):
    local_src = resolve(args, config, "backup", "local_src")
    cloud_dst = resolve(args, config, "backup", "cloud_dst")

    if args.mode == "dry":
        print("[DRY RUN]")
    print(f"Source: {local_src}")
    print(f"Dest:   {cloud_dst}")
    print()

    flags = ["--fast-list", "--transfers", "8"] + exclude_flags() + RCLONE_RETRY_FLAGS

    if args.mode == "dry":
        flags += ["--dry-run", "-v", "--stats", "0", "--log-level", "NOTICE"]
    else:
        flags += ["-P", "--log-level", "NOTICE", "--stats-one-line", "--stats", "2s"]

    return subprocess.run(["rclone", "copy", local_src, cloud_dst] + flags, check=False).returncode


def rclone_md5sum(path):
    """Return {md5_hash: [relative_path, ...]} for every file under path."""
    result = subprocess.run(
        ["rclone", "md5sum", path, "--fast-list"] + exclude_flags(),
        capture_output=True,
        text=True,
        check=True,
    )
    hashes = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        digest, rel_path = line.split(None, 1)
        hashes.setdefault(digest.lower(), []).append(rel_path.strip())
    return hashes


def run_verify(args, config):
    local_src = resolve(args, config, "verify", "local_src")
    cloud_dst = resolve(args, config, "verify", "cloud_dst")
    output = resolve(args, config, "verify", "output", required=False) or "stdout"

    print(f"Local: {local_src}")
    print(f"Drive: {cloud_dst}")
    print("Hashing local and Drive contents (this can take a while)...")
    print()

    local_hashes = rclone_md5sum(local_src)
    drive_hash_set = set(rclone_md5sum(cloud_dst).keys())

    local_root = Path(local_src)
    missing = sorted(
        str((local_root / rel_path).resolve())
        for digest, rel_paths in local_hashes.items()
        if digest not in drive_hash_set
        for rel_path in rel_paths
    )

    total = sum(len(rel_paths) for rel_paths in local_hashes.values())
    backed_up = total - len(missing)

    lines = [f"{backed_up}/{total} files backed up"]
    if missing:
        lines.append(f"{len(missing)} file(s) not found in Drive:")
        lines.extend(missing)
    else:
        lines.append("Everything is backed up.")
    report = "\n".join(lines)

    if output in ("stdout", "-"):
        print(report)
    else:
        Path(output).write_text(report + "\n", encoding="utf-8")
        print(f"{backed_up}/{total} files backed up ({len(missing)} missing) -- report written to {output}")

    return 1 if missing else 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Backup and verify a local directory against Google Drive via rclone."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup", help="rclone copy local_src to cloud_dst")
    backup.add_argument("mode", nargs="?", choices=["dry", "run"], default="dry")
    backup.add_argument("--local-src", dest="local_src", default=None)
    backup.add_argument("--cloud-dst", dest="cloud_dst", default=None)
    backup.set_defaults(func=run_backup)

    verify = sub.add_parser(
        "verify", help="Check that local_src is fully backed up under cloud_dst, by content hash"
    )
    verify.add_argument("--local-src", dest="local_src", default=None)
    verify.add_argument("--cloud-dst", dest="cloud_dst", default=None)
    verify.add_argument("--output", dest="output", default=None, help="stdout (default) or a file path")
    verify.set_defaults(func=run_verify)

    return parser


def main():
    args = build_parser().parse_args()
    sys.exit(args.func(args, load_config()))


if __name__ == "__main__":
    main()
