#!/usr/bin/env python3
"""
Find all .m4a files in a folder tree, output a conversion plan (CSV + Markdown),
then convert to FLAC with per-folder y/n/all confirmation.

Usage:
    python convert_m4a_to_flac.py [root_path] [--keep-originals]

    root_path        Folder to scan. Defaults to the script's directory.
    --keep-originals Keep source .m4a after successful conversion (default: delete).
"""

import argparse
import csv
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def find_m4a_files(root: Path) -> dict[str, list[Path]]:
    """Return m4a files grouped by parent folder (relative to root)."""
    grouped = defaultdict(list)
    for f in sorted(root.rglob("*.m4a")):
        rel_folder = str(f.parent.relative_to(root)) or "(root)"
        grouped[rel_folder].append(f)
    return dict(sorted(grouped.items()))


def write_csv(plan: dict[str, list[Path]], root: Path) -> Path:
    out = root / "m4a_conversion_plan.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(["RelativeFolder", "SourceFile", "OutputFile"])
        for folder, files in plan.items():
            for src in files:
                writer.writerow([folder, src.name, src.stem + ".flac"])
    return out


def write_markdown(plan: dict[str, list[Path]], root: Path) -> Path:
    out = root / "m4a_conversion_plan.md"
    total = sum(len(v) for v in plan.values())
    lines = [
        "# M4A → FLAC Conversion Plan",
        "",
        f"Scanned: `{root}`  ",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"Total files: {total}",
        "",
    ]
    for folder, files in plan.items():
        lines += [f"## {folder}", "", "| Source | Output |", "|--------|--------|"]
        for src in files:
            lines.append(f"| {src.name} | {src.stem}.flac |")
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def print_plan(plan: dict[str, list[Path]]):
    total = sum(len(v) for v in plan.values())
    print(f"\nFiles to convert ({total} total):")
    for folder, files in plan.items():
        print(f"\n  [{folder}]")
        for src in files:
            print(f"    {src.name}  ->  {src.stem}.flac")
    print()


def prompt_folder(folder: str, count: int) -> str:
    """Returns 'y', 'n', or 'a'."""
    while True:
        answer = input(f"Folder: {folder} ({count} file(s)) — convert? [y]es / [n]o / [a]ll: ").strip().lower()
        if answer in ("y", "yes"):
            return "y"
        if answer in ("n", "no"):
            return "n"
        if answer in ("a", "all"):
            return "a"


def to_wsl_path(p: Path) -> str:
    """Convert a Windows path to its /mnt/<drive>/... WSL equivalent."""
    s = p.resolve().as_posix()          # D:/foo/bar.m4a
    drive, rest = s[0].lower(), s[2:]   # 'd', '/foo/bar.m4a'
    return f"/mnt/{drive}{rest}"


def convert(src: Path, keep_originals: bool, use_wsl: bool) -> bool:
    out = src.with_suffix(".flac")
    if out.exists():
        print(f"  SKIP (output exists): {src.name}")
        return True
    print(f"  Converting: {src.name} -> {out.name} ... ", end="", flush=True)
    if use_wsl:
        cmd = ["wsl", "ffmpeg", "-i", to_wsl_path(src), "-c:a", "flac", to_wsl_path(out), "-y"]
    else:
        cmd = ["ffmpeg", "-i", str(src), "-c:a", "flac", str(out), "-y"]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode == 0:
        print("OK")
        if not keep_originals:
            src.unlink()
        return True
    else:
        print("FAILED")
        print(result.stderr.decode(errors="replace").strip())
        return False


def main():
    parser = argparse.ArgumentParser(description="Convert .m4a files to FLAC.")
    parser.add_argument("root_path", nargs="?", default=None, help="Folder to scan")
    parser.add_argument("--keep-originals", action="store_true", help="Keep source .m4a after conversion")
    args = parser.parse_args()

    root = Path(args.root_path).resolve() if args.root_path else Path(__file__).parent.resolve()

    # Prefer native ffmpeg; fall back to WSL
    if shutil.which("ffmpeg"):
        use_wsl = False
    elif shutil.which("wsl"):
        result = subprocess.run(["wsl", "ffmpeg", "-version"], capture_output=True)
        if result.returncode != 0:
            print("ERROR: ffmpeg not found natively or in WSL.")
            print("Install it in WSL with:  sudo apt install ffmpeg")
            sys.exit(1)
        use_wsl = True
    else:
        print("ERROR: ffmpeg not found on PATH and WSL is not available.")
        sys.exit(1)

    plan = find_m4a_files(root)
    if not plan:
        print(f"No .m4a files found under: {root}")
        sys.exit(0)

    csv_path = write_csv(plan, root)
    md_path  = write_markdown(plan, root)
    print(f"Plan (CSV):      {csv_path}")
    print(f"Plan (Markdown): {md_path}")

    print_plan(plan)

    convert_all = False
    ok = fail = 0

    for folder, files in plan.items():
        if not convert_all:
            answer = prompt_folder(folder, len(files))
            if answer == "n":
                print("  Skipped.\n")
                continue
            if answer == "a":
                convert_all = True
        else:
            print(f"Folder: {folder} (auto-converting)")

        for src in files:
            if convert(src, args.keep_originals, use_wsl):
                ok += 1
            else:
                fail += 1
        print()

    print(f"Done. Converted: {ok}   Failed: {fail}")
    if not args.keep_originals and ok:
        print("Original .m4a files removed. Use --keep-originals to retain them.")


if __name__ == "__main__":
    main()
