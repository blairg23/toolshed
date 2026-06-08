#!/usr/bin/env python3
"""
Update a Traktor collection.nml to replace .m4a file references with .flac,
driven by a conversion plan CSV produced by convert_m4a_to_flac.py.

Also writes gap_analysis.csv and gap_analysis.md for any tracks not yet
imported into the Traktor collection.

Usage:
    python fix_traktor_collection.py [--csv PATH] [--nml PATH]
    python fix_traktor_collection.py --verify [--root PATH] [--csv PATH] [--nml PATH]

Flags:
    --verify    Read-only audit: checks each track's status on disk and in the
                collection without modifying anything. Requires --root (the root
                music folder that was scanned to produce the CSV).
    --root      Root folder of the music library (used with --verify to resolve
                file paths). Defaults to the CSV's parent directory.

Defaults:
    --csv   m4a_conversion_plan.csv (next to this script)
    --nml   Auto-detected from Documents/Native Instruments/Traktor */collection.nml
"""

import argparse
import csv
import html
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path


def find_nml() -> Path:
    base = Path(os.environ["USERPROFILE"]) / "Documents" / "Native Instruments"
    matches = sorted(base.glob("Traktor */collection.nml"), reverse=True)
    if not matches:
        raise FileNotFoundError(f"No collection.nml found under {base}")
    return matches[0]



def parse_nml_locations(nml_text: str) -> list[tuple[Path, str]]:
    """Return (full_path, raw_file_attr) for every LOCATION entry in the NML."""
    pattern = re.compile(r'<LOCATION DIR="([^"]*)" FILE="([^"]*)" VOLUME="([^"]*)"')
    results = []
    for m in pattern.finditer(nml_text):
        # Unescape XML entities (&amp; → &, etc.) before building real filesystem paths
        dir_attr  = html.unescape(m.group(1))
        file_attr = html.unescape(m.group(2))
        volume    = m.group(3)
        # DIR="/:Dropbox/:PirateNet/:Music/:foo/:" → ['Dropbox','PirateNet','Music','foo']
        parts = [p for p in dir_attr.split("/:") if p]
        try:
            full_path = Path(volume + "\\").joinpath(*parts, file_attr)
        except TypeError:
            continue
        results.append((full_path, file_attr))
    return results


def run_verify(rows: list[dict], nml_text: str, root: Path, out_dir: Path):
    """Read-only audit: check disk presence and collection membership for every track.
    Also validates that every collection entry under --root actually exists on disk."""

    # --- 1. CSV plan: check each expected track against disk + collection ---
    results = []
    for row in rows:
        flac_path = root / row["RelativeFolder"] / row["OutputFile"]
        m4a_path  = root / row["RelativeFolder"] / row["SourceFile"]
        results.append({
            "RelativeFolder": row["RelativeFolder"],
            "OutputFile":     row["OutputFile"],
            "OnDisk":         "yes" if flac_path.exists() else ("m4a_only" if m4a_path.exists() else "no"),
            "InCollection":   "yes" if f'FILE="{html.escape(row["OutputFile"])}"' in nml_text
                              else ("m4a" if f'FILE="{html.escape(row["SourceFile"])}"' in nml_text else "no"),
        })

    ok      = [r for r in results if r["OnDisk"] == "yes" and r["InCollection"] == "yes"]
    missing = [r for r in results if r["InCollection"] == "no"]
    m4a_ref = [r for r in results if r["InCollection"] == "m4a"]
    no_disk = [r for r in results if r["OnDisk"] == "no"]

    print("=== Plan vs. Collection ===")
    print(f"Total tracks in plan: {len(results)}")
    print(f"  OK (on disk + in collection): {len(ok)}")
    print(f"  In collection as .m4a (needs fix): {len(m4a_ref)}")
    print(f"  Not in collection (needs manual import): {len(missing)}")
    print(f"  Not on disk (conversion failed?): {len(no_disk)}")

    if missing:
        print(f"\nNot in collection — drag these folders into Traktor to import:")
        by_folder: dict[str, list[str]] = {}
        for r in missing:
            by_folder.setdefault(r["RelativeFolder"], []).append(r["OutputFile"])
        for folder, files in sorted(by_folder.items()):
            print(f"  [{folder}] ({len(files)} track(s))")

    if m4a_ref:
        print(f"\nStill referenced as .m4a — re-run without --verify to fix:")
        for r in m4a_ref:
            print(f"  [{r['RelativeFolder']}] {r['OutputFile']}")

    if no_disk:
        print(f"\nFiles missing from disk — conversion may have failed:")
        for r in no_disk:
            print(f"  [{r['RelativeFolder']}] {r['OutputFile']}")

    # --- 2. Collection integrity: NML entries under --root that don't exist on disk ---
    print(f"\n=== Collection Integrity (entries under {root}) ===")
    locations = parse_nml_locations(nml_text)
    root_resolved = root.resolve()

    scoped = [(p, f) for p, f in locations if _is_under(p, root_resolved)]
    broken = []
    for p, f in scoped:
        if not p.exists():
            ext = p.suffix.lower()
            if ext == ".m4a":
                flac_sibling = p.with_suffix(".flac")
                if flac_sibling.exists():
                    status = "converted to FLAC – collection ref not updated"
                else:
                    status = "m4a deleted, no FLAC found"
            else:
                status = "file missing from disk"
            broken.append((p, status))

    print(f"Collection entries under root: {len(scoped)}")
    print(f"  Broken (path does not exist on disk): {len(broken)}")

    if broken:
        broken_csv = out_dir / "broken_collection.csv"
        with open(broken_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(["Folder", "File", "Status"])
            for p, status in broken:
                writer.writerow([str(p.parent), p.name, status])

        by_folder: dict[str, list[tuple[str, str]]] = {}
        for p, status in broken:
            by_folder.setdefault(str(p.parent), []).append((p.name, status))

        md_lines = [
            "# Broken Collection Entries",
            "",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
            f"Files referenced in Traktor collection but missing from disk: **{len(broken)}**",
            "",
            "| Status | Meaning |",
            "|--------|---------|",
            "| converted to FLAC – collection ref not updated | .m4a deleted but .flac exists; run fix script |",
            "| m4a deleted, no FLAC found | original gone and no FLAC replacement found |",
            "| file missing from disk | FLAC/MP3 referenced in collection but file is gone |",
            "",
        ]
        for folder, entries in sorted(by_folder.items()):
            md_lines += [f"## {folder} ({len(entries)})", "", "| File | Status |", "|------|--------|"]
            for name, status in entries:
                md_lines.append(f"| {name} | {status} |")
            md_lines.append("")

        broken_md = out_dir / "broken_collection.md"
        broken_md.write_text("\n".join(md_lines), encoding="utf-8")

        print(f"\nBroken entries written:")
        print(f"  CSV:      {broken_csv}")
        print(f"  Markdown: {broken_md}")


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Patch Traktor collection.nml: m4a -> flac")
    parser.add_argument("--csv",    default=None, help="Path to conversion plan CSV")
    parser.add_argument("--nml",    default=None, help="Path to collection.nml")
    parser.add_argument("--root",   default=None, help="Root music folder (for --verify disk checks)")
    parser.add_argument("--verify", action="store_true", help="Read-only audit; does not modify the NML")
    args = parser.parse_args()

    csv_path = Path(args.csv)  if args.csv  else Path(__file__).parent / "m4a_conversion_plan.csv"
    nml_path = Path(args.nml)  if args.nml  else find_nml()
    root     = Path(args.root) if args.root else csv_path.parent

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")
    if not nml_path.exists():
        raise FileNotFoundError(f"NML not found: {nml_path}")

    print(f"CSV:  {csv_path}")
    print(f"NML:  {nml_path}")
    if args.verify:
        print(f"Root: {root}")
    print()

    nml_text = nml_path.read_text(encoding="utf-8")

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if args.verify:
        run_verify(rows, nml_text, root, Path(__file__).parent)
        return

    # --- Patch mode ---
    stamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = nml_path.with_suffix(f".nml.backup_{stamp}")
    shutil.copy2(nml_path, backup)
    print(f"Backup: {backup}\n")

    replaced = 0
    missing  = []

    for row in rows:
        src_attr  = f'FILE="{html.escape(row["SourceFile"])}"'
        dst_attr  = f'FILE="{html.escape(row["OutputFile"])}"'
        flac_attr = f'FILE="{html.escape(row["OutputFile"])}"'

        if src_attr in nml_text:
            nml_text = nml_text.replace(src_attr, dst_attr)
            replaced += 1
        elif flac_attr not in nml_text:
            missing.append(row)

    nml_path.write_text(nml_text, encoding="utf-8")
    print(f"Updated {replaced} file reference(s).")

    if missing:
        print(f"\n{len(missing)} tracks not in collection — drag their folders into Traktor to import:")
        by_folder: dict[str, list[str]] = {}
        for r in missing:
            by_folder.setdefault(r["RelativeFolder"], []).append(r["OutputFile"])
        for folder, files in sorted(by_folder.items()):
            print(f"  [{folder}] ({len(files)} track(s))")
    else:
        print("\nNo gaps — all tracks accounted for in the collection.")


if __name__ == "__main__":
    main()
