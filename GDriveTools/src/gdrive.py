#!/usr/bin/env python3
"""
GDriveTools CLI: rclone-backed backup and verify for a local <-> Google Drive
directory pair.

Usage:
    python gdrive.py backup [dry|run] [--local-src PATH] [--cloud-dst REMOTE:PATH]
    python gdrive.py verify [--local-src PATH] [--cloud-dst REMOTE:PATH] [--output PATH]
                            [--drive-manifest PATH] [--verbose]

Config (config.yml, sections are independent -- no shared keys):
    backup:
      local_src: "..."
      cloud_dst: "..."
    verify:
      local_src: "..."
      cloud_dst: "..."
      output: stdout          # default; or a file path, e.g. missing-files.csv
      drive_manifest: null    # optional; a file path to dump every Drive file's hash + location

Every config value can be overridden per-run via the matching CLI flag, so
`verify` can check an arbitrary one-off directory against an arbitrary Drive
path without touching config.yml.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

MD5_RE = re.compile(r"^[0-9a-f]{32}$")

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


def rclone_md5sum(path, label):
    """Return {md5_hash: [relative_path, ...]} for every file under path.

    Streams rclone's output line by line (rather than waiting for it to finish)
    and prints each file as it's discovered, prefixed with `label`, so a long
    scan (e.g. an entire Drive account) shows visible progress instead of going
    silent until the whole thing completes.

    Deliberately does NOT use --fast-list here: it buffers a much larger chunk
    of the recursive listing before yielding anything, which fights the
    per-file streaming this function exists to provide. --fast-list is still
    used for `backup`'s rclone copy, where progress is reported separately.
    """
    proc = subprocess.Popen(
        ["rclone", "md5sum", path] + exclude_flags(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    hashes = {}
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, rel_path = parts
        digest = digest.lower()
        rel_path = rel_path.strip()
        print(f"  [{label}] {rel_path}", flush=True)
        if not MD5_RE.match(digest):
            # e.g. "UNSUPPORTED" for native Google Docs/Sheets/Slides, which have
            # no binary content and thus no computable hash -- skip, don't crash
            continue
        hashes.setdefault(digest, []).append(rel_path)

    stderr_output = proc.stderr.read()
    returncode = proc.wait()
    if returncode != 0:
        raise subprocess.CalledProcessError(returncode, proc.args, output=None, stderr=stderr_output)
    return hashes


def join_remote(base, rel_path):
    """Join a Drive remote path (e.g. "gdrive-personal:" or "remote:sub/dir") with a
    relative path for display, without introducing a spurious slash after the colon."""
    if base.endswith(("/", ":")):
        return f"{base}{rel_path}"
    return f"{base}/{rel_path}"


def run_verify(args, config):
    local_src = resolve(args, config, "verify", "local_src")
    cloud_dst = resolve(args, config, "verify", "cloud_dst")
    output = resolve(args, config, "verify", "output", required=False) or "stdout"
    drive_manifest = resolve(args, config, "verify", "drive_manifest", required=False)
    verbose = args.verbose

    print(f"Local: {local_src}")
    print(f"Drive: {cloud_dst}")
    print()

    print("Hashing local files...", flush=True)
    local_hashes = rclone_md5sum(local_src, label="local")

    print(f"Listing/hashing Drive contents under {cloud_dst} (slow if this is a large account or root)...", flush=True)
    drive_hashes = rclone_md5sum(cloud_dst, label="drive")

    if drive_manifest:
        manifest_lines = sorted(
            f"{digest}  {join_remote(cloud_dst, rel_path)}"
            for digest, rel_paths in drive_hashes.items()
            for rel_path in rel_paths
        )
        Path(drive_manifest).write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
        print(f"Wrote {len(manifest_lines)} Drive file location(s) to {drive_manifest}")

    local_root = Path(local_src)
    matched = []
    missing = []
    for digest, rel_paths in local_hashes.items():
        drive_rel_paths = drive_hashes.get(digest)
        for rel_path in rel_paths:
            local_path = str((local_root / rel_path).resolve())
            if drive_rel_paths:
                matched.append((local_path, [join_remote(cloud_dst, p) for p in drive_rel_paths]))
            else:
                missing.append(local_path)
    matched.sort(key=lambda pair: pair[0])
    missing.sort()

    total = len(matched) + len(missing)

    lines = [f"{len(matched)}/{total} local files already have matching content in Drive"]
    if verbose and matched:
        lines.append("")
        lines.append(f"{len(matched)} file(s) verified:")
        for local_path, drive_paths in matched:
            lines.append(f"- {local_path} -> {', '.join(drive_paths)}")
    if missing:
        lines.append("")
        lines.append(f"{len(missing)} file(s) NOT found in Drive:")
        lines.extend(missing)
    else:
        lines.append("Everything is backed up.")
    report = "\n".join(lines)

    if output in ("stdout", "-"):
        print(report)
    else:
        Path(output).write_text(report + "\n", encoding="utf-8")
        print(f"{len(matched)}/{total} files backed up ({len(missing)} missing) -- report written to {output}")

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
    verify.add_argument(
        "--drive-manifest",
        dest="drive_manifest",
        default=None,
        help="Write every Drive file's hash and location to this file",
    )
    verify.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="List each verified file's local path and matched Drive path(s)",
    )
    verify.set_defaults(func=run_verify)

    return parser


def main():
    args = build_parser().parse_args()
    sys.exit(args.func(args, load_config()))


if __name__ == "__main__":
    main()
