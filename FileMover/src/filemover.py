#!/usr/bin/env python3
"""
FileMover — move or copy files/folders from src to dst.

Usage:
    python3 filemover.py [--config PATH] [--dry-run]
"""

import argparse
import shutil
import sys
from pathlib import Path

import yaml


def load_config(config_path: Path) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def run(cfg: dict, dry_run: bool):
    src = Path(cfg["src"])
    dst = Path(cfg["dst"])
    operation = cfg.get("operation", "move")
    filter_type = cfg.get("filter", "all")

    if not src.exists():
        print(f"ERROR: src not found: {src}")
        sys.exit(1)

    dst.mkdir(parents=True, exist_ok=True)

    items = sorted(src.iterdir())
    if filter_type == "directories":
        items = [i for i in items if i.is_dir()]
    elif filter_type == "files":
        items = [i for i in items if i.is_file()]

    if not items:
        print(f"Nothing to {operation} in {src}")
        return

    label = "[DRY RUN] " if dry_run else ""
    print(f"{label}{operation.capitalize()} — {len(items)} item(s)\n")

    for item in items:
        size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file()) if item.is_dir() else item.stat().st_size
        size_str = f"{size / 1024 / 1024:.1f}M"
        dest = dst / item.name
        print(f"  {item.name} ({size_str})")
        print(f"    from: {item}")
        print(f"      to: {dest}")
        print()
        if not dry_run:
            if operation == "move":
                shutil.move(str(item), str(dest))
            else:
                shutil.copytree(str(item), str(dest)) if item.is_dir() else shutil.copy2(str(item), str(dest))

    print(f"{label}Done.")


def main():
    parser = argparse.ArgumentParser(description="Move or copy files/folders.")
    parser.add_argument("--config", default=None, help="Path to config.yml")
    parser.add_argument("--section", default=None, help="Section to use from config.yml")
    parser.add_argument("--src", default=None, help="Source path (overrides config)")
    parser.add_argument("--dst", default=None, help="Destination path (overrides config)")
    parser.add_argument("--operation", default=None, choices=["move", "copy"], help="Operation (overrides config)")
    parser.add_argument("--filter", default=None, choices=["all", "files", "directories"], help="Filter (overrides config)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without moving")
    args = parser.parse_args()

    if args.src and args.dst:
        cfg = {}
    else:
        config_path = Path(args.config) if args.config else Path(__file__).parent.parent / "config.yml"
        if not config_path.exists():
            print(f"ERROR: config.yml not found at {config_path}")
            sys.exit(1)
        cfg = load_config(config_path)

    if args.section:
        if args.section not in cfg:
            print(f"ERROR: section '{args.section}' not found in config")
            sys.exit(1)
        cfg = cfg[args.section]

    if args.src:        cfg["src"] = args.src
    if args.dst:        cfg["dst"] = args.dst
    if args.operation:  cfg["operation"] = args.operation
    if args.filter:     cfg["filter"] = args.filter

    run(cfg, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
