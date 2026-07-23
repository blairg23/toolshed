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
      output: stdout                  # default; or a file path, e.g. missing-files.csv
      drive_manifest: null            # optional; a file path to dump every Drive file's hash + location
      drive_cache_ttl_seconds: 86400  # reuse a cached Drive hash listing for this long (default 24h)

Every config value can be overridden per-run via the matching CLI flag, so
`verify` can check an arbitrary one-off directory against an arbitrary Drive
path without touching config.yml.

Hashing the local side is fast (it's just whatever folder you're checking),
but Drive doesn't support "find me the file with this hash" -- only listing,
which returns each file's hash as metadata. So the Drive side is scanned in
full at least once. Since a typical workflow is "check folder A, then B,
then C against the same Drive account", the Drive hash listing is cached to
disk per cloud_dst and reused across runs until it goes stale (see
--refresh-drive-cache / drive_cache_ttl_seconds).
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

MD5_RE = re.compile(r"^[0-9a-f]{32}$")

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR.parent / "config.yml"
CACHE_DIR = SCRIPT_DIR.parent / ".cache"
DEFAULT_DRIVE_CACHE_TTL_SECONDS = 86400

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

    Uses --fast-list: for a backend like Drive, this fetches the listing in
    large paginated batches instead of walking one folder at a time, which is
    dramatically fewer round trips for a big account. The trade-off is that
    file/path output can arrive in a big burst rather than trickling in one at
    a time. To still show signs of life during that burst, rclone's own
    periodic --stats output is merged into the same stream (stderr -> stdout)
    and printed as it arrives, alongside each file as it's parsed.
    """
    proc = subprocess.Popen(
        ["rclone", "md5sum", path, "--fast-list", "--stats", "10s"] + exclude_flags(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    hashes = {}
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        digest = parts[0].lower() if parts else ""
        if len(parts) == 2 and MD5_RE.match(digest):
            rel_path = parts[1].strip()
            print(f"  [{label}] {rel_path}", flush=True)
            hashes.setdefault(digest, []).append(rel_path)
        else:
            # rclone's own --stats/log output, or an unhashable entry (e.g.
            # "UNSUPPORTED" for a native Google Doc/Sheet/Slide) -- print as-is
            # so a long, bursty scan still shows signs of life, but don't treat
            # it as a real hash entry
            print(f"  [{label}] {line}", flush=True)

    returncode = proc.wait()
    if returncode != 0:
        raise subprocess.CalledProcessError(returncode, proc.args)
    return hashes


def join_remote(base, rel_path):
    """Join a Drive remote path (e.g. "gdrive-personal:" or "remote:sub/dir") with a
    relative path for display, without introducing a spurious slash after the colon."""
    if base.endswith(("/", ":")):
        return f"{base}{rel_path}"
    return f"{base}/{rel_path}"


def cache_path_for(cloud_dst):
    digest = hashlib.sha256(cloud_dst.encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{digest}.json"


def load_drive_cache(cloud_dst, ttl_seconds):
    path = cache_path_for(cloud_dst)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    age = time.time() - data["hashed_at"]
    if age > ttl_seconds:
        return None
    print(f"Using cached Drive hash listing for {cloud_dst} ({int(age)}s old, ttl {ttl_seconds}s)")
    return data["hashes"]


def save_drive_cache(cloud_dst, hashes):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path_for(cloud_dst)
    path.write_text(
        json.dumps({"cloud_dst": cloud_dst, "hashed_at": time.time(), "hashes": hashes}),
        encoding="utf-8",
    )


def run_verify(args, config):
    local_src = resolve(args, config, "verify", "local_src")
    cloud_dst = resolve(args, config, "verify", "cloud_dst")
    output = resolve(args, config, "verify", "output", required=False) or "stdout"
    drive_manifest = resolve(args, config, "verify", "drive_manifest", required=False)
    ttl = resolve(args, config, "verify", "drive_cache_ttl_seconds", required=False)
    ttl = int(ttl) if ttl is not None else DEFAULT_DRIVE_CACHE_TTL_SECONDS
    verbose = args.verbose

    print(f"Local: {local_src}")
    print(f"Drive: {cloud_dst}")
    print()

    print("Hashing local files...", flush=True)
    local_hashes = rclone_md5sum(local_src, label="local")

    drive_hashes = None if args.refresh_drive_cache else load_drive_cache(cloud_dst, ttl)
    if drive_hashes is None:
        print(f"Listing/hashing Drive contents under {cloud_dst} (slow if this is a large account or root)...", flush=True)
        drive_hashes = rclone_md5sum(cloud_dst, label="drive")
        save_drive_cache(cloud_dst, drive_hashes)

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
    verify.add_argument(
        "--refresh-drive-cache",
        action="store_true",
        help="Ignore any cached Drive hash listing and rescan Drive from scratch",
    )
    verify.set_defaults(func=run_verify)

    return parser


def main():
    args = build_parser().parse_args()
    sys.exit(args.func(args, load_config()))


if __name__ == "__main__":
    main()
