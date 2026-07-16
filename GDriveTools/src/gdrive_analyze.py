#!/usr/bin/env python3
"""
Read-only recursive scan of a Google Drive folder (including shared drives).

Writes the full tree (path, mimeType, size) to a file and prints a summary
that calls out .git folders, .github/CLAUDE.md/AGENTS.md/.claude presence,
git remote origin URLs, heavy binaries by size, and small text files inline
-- all to help decide what a follow-up gdrive_ops (#62) pass should do.

Usage:
    python gdrive_analyze.py FOLDER_ID [--output drive-tree.txt]

Never mutates anything -- listing and read-only file fetches only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from pathlib import PurePosixPath

sys.path.insert(0, str(Path(__file__).parent))
from auth import build_service  # noqa: E402

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
LIST_FIELDS = "nextPageToken, files(id, name, mimeType, size)"
HEAVY_EXTENSIONS = {".mp4", ".glb", ".png", ".jpg", ".jpeg", ".pdf", ".ttf", ".otf", ".woff", ".woff2"}
SMALL_TEXT_MAX_BYTES = 64 * 1024
DEFAULT_OUTPUT = Path(__file__).parent.parent / "drive-tree.txt"


def list_children(service, folder_id):
    """Yield every direct child item of folder_id, paginated."""
    page_token = None
    while True:
        response = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields=LIST_FIELDS,
                pageSize=1000,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        for item in response.get("files", []):
            yield item
        page_token = response.get("nextPageToken")
        if not page_token:
            break


def walk(service, folder_id, path_prefix=""):
    """Recursively yield (full_path, item) for every item under folder_id."""
    for item in list_children(service, folder_id):
        full_path = f"{path_prefix}/{item['name']}"
        yield full_path, item
        if item["mimeType"] == FOLDER_MIME_TYPE:
            yield from walk(service, item["id"], full_path)


def fetch_text(service, file_id):
    return service.files().get_media(fileId=file_id).execute().decode("utf-8", errors="replace")


def find_origin_url(config_text):
    in_origin = False
    for line in config_text.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_origin = line.startswith('[remote "origin"]')
            continue
        if in_origin and line.startswith("url"):
            return line.split("=", 1)[1].strip()
    return None


def categorize(items):
    """Bucket walked (full_path, item) pairs for the summary report."""
    git_folders = []
    github_dirs = []
    claude_md_files = []
    agents_md_files = []
    claude_dirs = []
    git_configs = []
    heavy_binaries = []
    small_text_files = []

    for full_path, item in items:
        name = item["name"]
        is_folder = item["mimeType"] == FOLDER_MIME_TYPE
        size = int(item["size"]) if "size" in item else 0
        ext = PurePosixPath(name).suffix.lower()

        if is_folder and name == ".git":
            git_folders.append(full_path)
        elif is_folder and name == ".github":
            github_dirs.append(full_path)
        elif is_folder and name == ".claude":
            claude_dirs.append(full_path)
        elif not is_folder and name == "CLAUDE.md":
            claude_md_files.append(full_path)
        elif not is_folder and name == "AGENTS.md":
            agents_md_files.append(full_path)
        elif not is_folder and full_path.replace("\\", "/").endswith(".git/config"):
            git_configs.append((full_path, item["id"]))
        elif not is_folder and ext in HEAVY_EXTENSIONS:
            heavy_binaries.append((full_path, size))
        elif not is_folder and size <= SMALL_TEXT_MAX_BYTES and (
            ext == ".md" or "config" in name.lower() or name == ".gitignore"
        ):
            small_text_files.append((full_path, item["id"], size))

    return {
        "git_folders": git_folders,
        "github_dirs": github_dirs,
        "claude_dirs": claude_dirs,
        "claude_md_files": claude_md_files,
        "agents_md_files": agents_md_files,
        "git_configs": git_configs,
        "heavy_binaries": heavy_binaries,
        "small_text_files": small_text_files,
    }


def print_summary(service, buckets):
    print("\n=== .git folders ===")
    for path in buckets["git_folders"]:
        print(f"  {path}")

    print("\n=== .github/ ===")
    for path in buckets["github_dirs"]:
        print(f"  {path}")

    print("\n=== .claude/ ===")
    for path in buckets["claude_dirs"]:
        print(f"  {path}")

    print("\n=== CLAUDE.md ===")
    for path in buckets["claude_md_files"]:
        print(f"  {path}")

    print("\n=== AGENTS.md ===")
    for path in buckets["agents_md_files"]:
        print(f"  {path}")

    print("\n=== git remote origin (from .git/config) ===")
    for full_path, file_id in buckets["git_configs"]:
        try:
            origin_url = find_origin_url(fetch_text(service, file_id))
        except Exception as exc:  # noqa: BLE001 -- report and continue, never abort the scan
            print(f"  {full_path}: could not fetch ({exc})")
            continue
        print(f"  {full_path}: {origin_url or '(no origin remote found)'}")

    print("\n=== Heavy binaries (by size, descending) ===")
    for full_path, size in sorted(buckets["heavy_binaries"], key=lambda pair: -pair[1]):
        print(f"  {size:>12}  {full_path}")

    print("\n=== Small text files (inline) ===")
    for full_path, file_id, size in buckets["small_text_files"]:
        print(f"\n--- {full_path} ({size} bytes) ---")
        try:
            print(fetch_text(service, file_id))
        except Exception as exc:  # noqa: BLE001
            print(f"  could not fetch ({exc})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder_id", help="Google Drive folder ID to scan")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Where to write the full tree dump")
    args = parser.parse_args()

    service = build_service()

    print(f"Scanning folder {args.folder_id}...")
    items = list(walk(service, args.folder_id))

    tree_lines = [f"{path}\t{item['mimeType']}\t{item.get('size', '')}" for path, item in items]
    Path(args.output).write_text("\n".join(tree_lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(items)} item(s) to {args.output}")

    print_summary(service, categorize(items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
