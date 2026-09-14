#!/usr/bin/env python3
"""
GDriveTools CLI: rclone-backed backup, verify, and audit for a local <->
Google Drive directory pair.

Usage:
    python gdrive.py backup [dry|run] [--local-src PATH] [--cloud-dst REMOTE:PATH]
    python gdrive.py verify [--local-src PATH] [--cloud-dst REMOTE:PATH] [--output PATH]
                            [--drive-manifest PATH] [--verbose]
    python gdrive.py audit [--cloud-dst REMOTE:PATH] [--output PATH] [--top-n N]

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
    audit:
      cloud_dst: "..."
      output: stdout
      top_n: 20                       # how many largest files to list
      drive_cache_ttl_seconds: 86400

Every config value can be overridden per-run via the matching CLI flag, so
`verify`/`audit` can check an arbitrary one-off directory against an
arbitrary Drive path without touching config.yml.

Hashing the local side is fast (it's just whatever folder you're checking),
but Drive doesn't support "find me the file with this hash" -- only listing,
which returns each file's hash as metadata. So the Drive side is scanned in
full at least once. Since a typical workflow is "check folder A, then B,
then C against the same Drive account", the Drive hash listing is cached to
disk per cloud_dst and reused across runs until it goes stale (see
--refresh-drive-cache / drive_cache_ttl_seconds).

`audit` is read-only, Drive-only: it never compares against a local
directory and never writes to Drive. It answers three questions about the
cached Drive listing -- which content is duplicated, which entries match
known junk patterns, and how space breaks down by top-level folder -- so
cleanup can be planned before any deletion tooling is built.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import threading
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


def rclone_lsjson_hash(path, label):
    """Return {rel_path: [{"md5": str|None, "size": int|None}, ...]} for
    every file under path, via `rclone lsjson --hash`, which returns path +
    size + MD5 in one recursive call (plain `rclone md5sum` returns hash +
    path only, no size).

    Each rel_path maps to a *list* of records, not a single record --
    unlike a local filesystem, Drive allows multiple distinct files to
    share the same name within a folder, so `lsjson` can legitimately
    return more than one entry for the same Path. Keying by path alone and
    overwriting would silently drop every record but the last one seen.

    Unlike rclone_md5sum()'s plain-text output, lsjson's JSON array isn't
    safely parseable line-by-line, so stdout is buffered in full and parsed
    once the scan completes. Progress feedback during that wait comes from
    rclone's own periodic --stats output, drained from stderr on a
    background thread and printed as it arrives so a long fast-list scan
    still shows signs of life.
    """
    proc = subprocess.Popen(
        [
            "rclone",
            "lsjson",
            "--hash",
            "--hash-type",
            "md5",
            "--fast-list",
            "-R",
            "--stats",
            "10s",
            path,
        ]
        + exclude_flags()
        + RCLONE_RETRY_FLAGS,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    def _stream_stderr():
        for line in proc.stderr:
            line = line.strip()
            if line:
                print(f"  [{label}] {line}", flush=True)

    stderr_thread = threading.Thread(target=_stream_stderr, daemon=True)
    stderr_thread.start()
    stdout_data = proc.stdout.read()
    returncode = proc.wait()
    stderr_thread.join()

    if returncode != 0:
        raise subprocess.CalledProcessError(returncode, proc.args)

    entries = {}
    for item in json.loads(stdout_data or "[]"):
        if item.get("IsDir"):
            continue
        rel_path = item.get("Path", "")
        if not rel_path:
            continue
        digest = (item.get("Hashes") or {}).get("md5")
        size = item.get("Size")
        entries.setdefault(rel_path, []).append(
            {
                "md5": digest.lower() if digest else None,
                "size": size if isinstance(size, int) else None,
            }
        )
        print(f"  [{label}] {rel_path}", flush=True)
    return entries


def iter_entries(entries):
    """Yield (rel_path, md5, size) for every Drive object in the rich
    {rel_path: [{"md5":..., "size":...}, ...]} cache shape -- one yield per
    object, even when two objects share the same rel_path."""
    for rel_path, records in entries.items():
        for info in records:
            yield rel_path, info.get("md5"), info.get("size")


def digest_to_paths(entries):
    """Project the rich {rel_path: [{"md5":..., "size":...}, ...]} cache
    entries into the {digest: [rel_paths]} shape verify()'s local-vs-Drive
    comparison already relies on -- objects with no hash (e.g. native
    Google Docs/Sheets/Slides) are excluded, same as rclone_md5sum() simply
    never emitting a hash line for them. A rel_path shared by two objects
    with the same digest appears twice in that digest's list, matching
    there really being two such Drive objects."""
    by_digest = {}
    for rel_path, digest, _size in iter_entries(entries):
        if digest:
            by_digest.setdefault(digest, []).append(rel_path)
    return by_digest


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
    """Return the cached {rel_path: [{"md5":..., "size":...}, ...]} entries
    for cloud_dst, or None if there's no cache, it's past ttl_seconds, or
    it's a legacy cache written before the audit subcommand (digest->paths
    "hashes" shape instead of path->records "entries") -- treated as a
    miss rather than raising, so it's simply rescanned once."""
    path = cache_path_for(cloud_dst)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    age = time.time() - data["hashed_at"]
    if age > ttl_seconds:
        return None
    entries = data.get("entries")
    if entries is None:
        return None
    print(f"Using cached Drive hash listing for {cloud_dst} ({int(age)}s old, ttl {ttl_seconds}s)")
    return entries


def save_drive_cache(cloud_dst, entries):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path_for(cloud_dst)
    path.write_text(
        json.dumps({"cloud_dst": cloud_dst, "hashed_at": time.time(), "entries": entries}),
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

    if not local_hashes:
        print(f"\nERROR: no local files found under {local_src} -- nothing to verify.")
        print("This is NOT the same as \"everything is backed up\" -- it means nothing was")
        print("actually checked. Common cause: an unmounted/disconnected drive looks like")
        print("an empty directory. Confirm the path is really accessible before trusting")
        print("any result from this tool.")
        return 2

    drive_entries = None if args.refresh_drive_cache else load_drive_cache(cloud_dst, ttl)
    if drive_entries is None:
        print(f"Listing/hashing Drive contents under {cloud_dst} (slow if this is a large account or root)...", flush=True)
        drive_entries = rclone_lsjson_hash(cloud_dst, label="drive")
        save_drive_cache(cloud_dst, drive_entries)
    drive_hashes = digest_to_paths(drive_entries)

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


# ---------------------------------------------------------------------------
# audit: read-only Drive-only analysis (duplicates, junk, space breakdown)
# ---------------------------------------------------------------------------

DEFAULT_AUDIT_TOP_N = 20

# Junk patterns reused from mobile-backup's cleanup conventions
# (cleanup_folder.py's is_unwanted_name(), plus the _1/_conflicts leftover
# patterns from fix_suffix_file()/fix_suffix_dir()) rather than reinvented.
JUNK_EXACT_NAMES = {"contents.csv", "desktop.ini"}  # case-insensitive
CONFLICTS_DIR_NAME = "_conflicts"


def _has_1_suffix(name):
    """True for a name matching mobile-backup's *_1 duplicate-suffix
    convention: `name_1` (no extension, as for a directory) or
    `name_1.ext` (as for a file)."""
    return name.endswith("_1") or "_1." in name


def _is_unwanted_name(name):
    """Mirrors cleanup_folder.py's is_unwanted_name() for a single path
    segment (file or directory name), extended with the _conflicts/ and
    *_1 leftover patterns from the same module's suffix fixers."""
    return (
        name.startswith(".trashed")
        or name.lower() == ".thumbnails"
        or name.lower() in JUNK_EXACT_NAMES
        or name == CONFLICTS_DIR_NAME
        or _has_1_suffix(name)
    )


def is_junk_path(rel_path):
    """True if rel_path or any ancestor directory name matches one of the
    reused junk patterns."""
    return any(_is_unwanted_name(part) for part in rel_path.split("/"))


def format_size(num_bytes):
    """Human-readable byte size, e.g. 1536000 -> '1.46 MB'."""
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} B" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"  # effectively unreachable for a real Drive account


def audit_duplicates(entries):
    """Group Drive objects by MD5; return only digests with 2+ locations,
    each as (digest, size, reclaimable, [rel_paths, ...]) sorted by
    reclaimable space descending. reclaimable is size * (copies - 1):
    what's freed if only one copy of each duplicate is kept. Two objects
    with identical content share both digest and size by definition, so
    the group's size is taken from whichever matching object is seen
    first."""
    size_by_digest = {}
    for _rel_path, digest, size in iter_entries(entries):
        if digest and digest not in size_by_digest:
            size_by_digest[digest] = size or 0

    by_digest = digest_to_paths(entries)
    groups = []
    for digest, paths in by_digest.items():
        if len(paths) < 2:
            continue
        size = size_by_digest.get(digest, 0)
        reclaimable = size * (len(paths) - 1)
        groups.append((digest, size, reclaimable, sorted(paths)))
    groups.sort(key=lambda g: g[2], reverse=True)
    return groups


def audit_junk(entries):
    """Return (count, total_size, sorted [rel_paths...]) for every Drive
    object matching the reused junk patterns -- counted per object, so two
    junk objects sharing a path (see iter_entries()) both count and both
    appear in the path list."""
    junk_records = sorted(
        (rel_path, size)
        for rel_path, _digest, size in iter_entries(entries)
        if is_junk_path(rel_path)
    )
    total_size = sum(size or 0 for _rel_path, size in junk_records)
    return len(junk_records), total_size, [rel_path for rel_path, _size in junk_records]


def audit_space_breakdown(entries, top_n):
    """Return (folder_sizes: [(top_level_dir, size), ...] sorted desc,
    largest_files: [(rel_path, size), ...] top_n sorted desc), counting
    every Drive object individually (see iter_entries())."""
    folder_sizes = {}
    largest_candidates = []
    for rel_path, _digest, size in iter_entries(entries):
        size = size or 0
        top_level = rel_path.split("/", 1)[0] if "/" in rel_path else "(root)"
        folder_sizes[top_level] = folder_sizes.get(top_level, 0) + size
        largest_candidates.append((rel_path, size))
    folder_sizes_sorted = sorted(
        folder_sizes.items(), key=lambda kv: kv[1], reverse=True
    )

    largest = sorted(largest_candidates, key=lambda kv: kv[1], reverse=True)[:top_n]

    return folder_sizes_sorted, largest


def run_audit(args, config):
    cloud_dst = resolve(args, config, "audit", "cloud_dst")
    output = resolve(args, config, "audit", "output", required=False) or "stdout"
    top_n = resolve(args, config, "audit", "top_n", required=False)
    top_n = int(top_n) if top_n is not None else DEFAULT_AUDIT_TOP_N
    ttl = resolve(args, config, "audit", "drive_cache_ttl_seconds", required=False)
    ttl = int(ttl) if ttl is not None else DEFAULT_DRIVE_CACHE_TTL_SECONDS

    print(f"Drive: {cloud_dst}")
    print()

    entries = None if args.refresh_drive_cache else load_drive_cache(cloud_dst, ttl)
    if entries is None:
        print(
            f"Listing/hashing Drive contents under {cloud_dst} (slow if this is a large account or root)...",
            flush=True,
        )
        entries = rclone_lsjson_hash(cloud_dst, label="drive")
        save_drive_cache(cloud_dst, entries)

    if not entries:
        print(f"\nERROR: no files found under {cloud_dst} -- nothing to audit.")
        return 2

    lines = [f"Audit report for {cloud_dst}", ""]

    dup_groups = audit_duplicates(entries)
    total_reclaimable = sum(g[2] for g in dup_groups)
    lines.append(
        f"Duplicates: {len(dup_groups)} group(s), "
        f"{format_size(total_reclaimable)} reclaimable if only one copy of each is kept"
    )
    for digest, size, reclaimable, paths in dup_groups:
        lines.append(
            f"  {digest}  ({format_size(size)} each, {format_size(reclaimable)} reclaimable, {len(paths)} copies)"
        )
        for p in paths:
            lines.append(f"    - {join_remote(cloud_dst, p)}")
    lines.append("")

    junk_count, junk_size, junk_paths = audit_junk(entries)
    lines.append(f"Junk: {junk_count} item(s), {format_size(junk_size)} total")
    for p in junk_paths:
        lines.append(f"  - {join_remote(cloud_dst, p)}")
    lines.append("")

    folder_sizes, largest_files = audit_space_breakdown(entries, top_n)
    lines.append("Space by top-level folder:")
    for folder, size in folder_sizes:
        lines.append(f"  {format_size(size):>12}  {folder}")
    lines.append("")
    lines.append(f"Top {top_n} largest files:")
    for p, size in largest_files:
        lines.append(f"  {format_size(size):>12}  {join_remote(cloud_dst, p)}")

    report = "\n".join(lines)

    if output in ("stdout", "-"):
        print(report)
    else:
        Path(output).write_text(report + "\n", encoding="utf-8")
        print(f"Audit report written to {output}")

    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Backup, verify, and audit a local <-> Google Drive directory pair via rclone."
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
    verify.add_argument(
        "--drive-cache-ttl-seconds",
        dest="drive_cache_ttl_seconds",
        type=int,
        default=None,
        help="How long to reuse a cached Drive hash listing, in seconds (default 86400 / 24h)",
    )
    verify.set_defaults(func=run_verify)

    audit = sub.add_parser(
        "audit",
        help="Read-only Drive audit: duplicates, junk, and space usage under cloud_dst",
    )
    audit.add_argument("--cloud-dst", dest="cloud_dst", default=None)
    audit.add_argument(
        "--output", dest="output", default=None, help="stdout (default) or a file path"
    )
    audit.add_argument(
        "--top-n",
        dest="top_n",
        type=int,
        default=None,
        help="How many largest files to list (default 20)",
    )
    audit.add_argument(
        "--refresh-drive-cache",
        action="store_true",
        help="Ignore any cached Drive hash listing and rescan Drive from scratch",
    )
    audit.add_argument(
        "--drive-cache-ttl-seconds",
        dest="drive_cache_ttl_seconds",
        type=int,
        default=None,
        help="How long to reuse a cached Drive hash listing, in seconds (default 86400 / 24h)",
    )
    audit.set_defaults(func=run_audit)

    return parser


def main():
    args = build_parser().parse_args()
    sys.exit(args.func(args, load_config()))


if __name__ == "__main__":
    main()
