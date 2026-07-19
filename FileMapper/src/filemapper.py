#!/usr/bin/env python3
"""
FileMapper — match, rename, and move/copy files based on a config.

Usage:
    python3 filemapper.py [--config PATH] [--section NAME] [--dry-run]
    python3 filemapper.py [--mapping PATH] [--dry-run]  # legacy
"""

import argparse
import re
import shutil
import sys
from pathlib import Path
from datetime import datetime

import yaml

DEFAULT_FALLBACK_WINDOW_DAYS = 3


def load_config(config_path: Path) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def parse_pattern(pattern: str) -> tuple:
    """Convert a token pattern like '{date} {time}.{ext}' to a regex."""
    token_re = re.compile(r'\{(\w+)\}')
    tokens = token_re.findall(pattern)
    # Escape literal parts only, not the token placeholders
    parts = token_re.split(pattern)
    regex = ""
    for i, part in enumerate(parts):
        if i % 2 == 0:
            regex += re.escape(part)
        else:
            regex += f'(?P<{part}>.+?)'
    return re.compile(f'^{regex}$'), tokens


def extract_fields(filename: str, pattern: str) -> dict | None:
    compiled, _ = parse_pattern(pattern)
    m = compiled.match(filename)
    return m.groupdict() if m else None


def get_date_prefix(name: str) -> str | None:
    """Extract YYYY-MM-DD from start of a filename or folder name."""
    m = re.match(r'^(\d{4}-\d{2}-\d{2})', name)
    return m.group(1) if m else None


def match_chronological(sources: list[Path], targets: list[Path]) -> list[tuple]:
    """Pair sources and targets in chronological order."""
    sources = sorted(sources)
    targets = sorted(targets)
    pairs = []
    for i, src in enumerate(sources):
        tgt = targets[i] if i < len(targets) else None
        pairs.append((src, tgt))
    return pairs


def match_date_prefix(sources: list[Path], targets: list[Path]) -> list[tuple]:
    """Match sources to targets by shared date prefix."""
    target_map = {}
    for t in targets:
        d = get_date_prefix(t.name)
        if d:
            target_map.setdefault(d, []).append(t)

    pairs = []
    for src in sorted(sources):
        d = get_date_prefix(src.name)
        candidates = target_map.get(d, [])
        if len(candidates) == 1:
            pairs.append((src, candidates[0]))
        else:
            pairs.append((src, None))  # ambiguous or missing — needs interactive
    return pairs


def nearby_candidates(date: str, targets: list[Path], window_days: int = DEFAULT_FALLBACK_WINDOW_DAYS) -> list[Path]:
    """Targets whose date prefix is within window_days of date, closest first."""
    try:
        src_date = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return []
    scored = []
    for t in targets:
        d = get_date_prefix(t.name)
        if not d:
            continue
        try:
            t_date = datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            continue
        delta = abs((t_date - src_date).days)
        if delta <= window_days:
            scored.append((delta, t))
    scored.sort(key=lambda pair: pair[0])
    return [t for _, t in scored]


def interactive_match(src: Path, targets: list[Path]) -> Path | None:
    """Let the user pick a target for an unmatched source."""
    print(f"\nNo automatic match for: {src.name}")
    print("Available targets:")
    for i, t in enumerate(targets, 1):
        print(f"  {i}. {t.name}")
    print("  0. Skip")
    while True:
        try:
            choice = int(input("Match to: ").strip())
            if choice == 0:
                return None
            if 1 <= choice <= len(targets):
                return targets[choice - 1]
        except ValueError:
            pass
        print("Invalid choice.")


def build_output_name(src: Path, matched_target: Path | None, cfg: dict) -> str:
    """Apply the name_template to produce the output filename."""
    template = cfg["target"]["name_template"]
    src_fields = extract_fields(src.name, cfg["source"]["pattern"]) or {}

    fields = dict(src_fields)

    if "date" not in fields:
        d = get_date_prefix(src.name)
        if d:
            fields["date"] = d
    if "ext" not in fields:
        fields["ext"] = src.suffix.lstrip(".")

    # Pull title from matched folder name. Strip the *target's own* date prefix,
    # not the source's -- they can differ now that nearby (non-exact) date
    # matches are possible, and using the source's date here would leave the
    # target's date prefix stuck onto the title.
    if matched_target and cfg["fields"].get("title", {}).get("source") == "folder_name":
        folder_name = matched_target.name
        target_date = get_date_prefix(folder_name) or fields.get("date", "")
        title = folder_name.removeprefix(target_date).lstrip("_-")
        fields["title"] = title

    # Prompt for any missing fields
    for key, field_cfg in cfg.get("fields", {}).items():
        if key not in fields and field_cfg.get("prompt_if_missing"):
            fields[key] = input(f"Enter {key} for {src.name}: ").strip()

    # Fill template
    result = template
    for k, v in fields.items():
        result = result.replace(f"{{{k}}}", v)

    return result


def run(config_path: Path, dry_run: bool, section: str | None = None,
        src_override: str | None = None, output_override: str | None = None,
        operation_override: str | None = None):
    cfg = load_config(config_path)

    if section:
        if section not in cfg:
            print(f"ERROR: section '{section}' not found in config")
            sys.exit(1)
        cfg = cfg[section]

    # Normalize: support match.reference as target (SLOBSTools / sectioned config style)
    if "target" not in cfg and "reference" in cfg.get("match", {}):
        ref = cfg["match"]["reference"]
        cfg["target"] = {"path": ref["path"], "name_template": ref["name_template"]}
        if "fields" not in cfg:
            cfg["fields"] = ref.get("fields", {})

    src_dir = Path(src_override) if src_override else Path(cfg["source"]["path"])
    tgt_dir = Path(cfg["target"]["path"])
    out_dir = Path(output_override) if output_override else Path(cfg["output"]["path"])
    operation = operation_override or cfg["output"].get("operation", "move")
    strategy = cfg["match"]["strategy"]
    fallback = cfg["match"].get("fallback", "interactive")

    sources = sorted([f for f in src_dir.iterdir() if f.is_file()])
    targets = sorted([d for d in tgt_dir.iterdir() if d.is_dir()])

    if not sources:
        print(f"No source files found in {src_dir}")
        return

    print(f"Source files:    {len(sources)}")
    print(f"Target folders:  {len(targets)}")
    print(f"Strategy:        {strategy}")
    print(f"Operation:       {operation}")
    print(f"Output:          {out_dir}")
    print()

    # Match
    if strategy == "chronological":
        pairs = match_chronological(sources, targets)
    elif strategy == "date_prefix":
        pairs = match_date_prefix(sources, targets)
    else:
        pairs = [(s, None) for s in sources]

    # Resolve unmatched via interactive fallback
    fallback_window_days = cfg.get("match", {}).get("fallback_window_days", DEFAULT_FALLBACK_WINDOW_DAYS)
    unmatched_targets = [t for t in targets if t not in [p[1] for p in pairs]]
    resolved = []
    for src, tgt in pairs:
        if tgt is None and fallback == "interactive":
            date = get_date_prefix(src.name)
            candidates = [t for t in unmatched_targets if get_date_prefix(t.name) == date] if date else []
            if not candidates and date:
                # No exact date match -- narrow to a nearby window instead of
                # falling through to every unmatched target
                candidates = nearby_candidates(date, unmatched_targets, fallback_window_days)
            tgt = interactive_match(src, candidates or unmatched_targets)
            if tgt:
                unmatched_targets.remove(tgt)
        resolved.append((src, tgt))

    # Preview
    print("\nMapping:")
    for src, tgt in resolved:
        if tgt:
            out_name = build_output_name(src, tgt, cfg)
            print(f"  {src.name} -> {out_name}")
            print(f"")
            print(f"    from:    {src}")
            print(f"    matched: {tgt}")
            print(f"      to:    {out_dir / out_name}")
            print(f"")
        else:
            print(f"  {src.name} → SKIPPED (no match)")

    if dry_run:
        print("\n[DRY RUN] No files moved.")
        return

    confirm = input("\nProceed? [y/N]: ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    for src, tgt in resolved:
        if not tgt:
            print(f"  SKIP: {src.name}")
            continue
        out_name = build_output_name(src, tgt, cfg)
        dest = out_dir / out_name
        if operation == "move":
            shutil.move(str(src), str(dest))
        else:
            shutil.copy2(str(src), str(dest))
        print(f"  {'Moved' if operation == 'move' else 'Copied'}: {src.name} → {out_name}  (matched: {tgt.name})")

    print("\nDone.")


def main():
    parser = argparse.ArgumentParser(description="Match, rename, and move/copy files.")
    parser.add_argument("--config", default=None, help="Path to config.yml")
    parser.add_argument("--section", default=None, help="Section to use from config.yml")
    parser.add_argument("--mapping", default=None, help="Path to mapping.yml (legacy)")
    parser.add_argument("--src", default=None, help="Override source path")
    parser.add_argument("--output", default=None, help="Override output path")
    parser.add_argument("--operation", default=None, choices=["move", "copy"], help="Override operation")
    parser.add_argument("--dry-run", action="store_true", help="Preview without moving")
    args = parser.parse_args()

    if args.config:
        config_path = Path(args.config)
    elif args.mapping:
        config_path = Path(args.mapping)
    else:
        config_path = Path(__file__).parent.parent / "mapping.yml"

    if not config_path.exists():
        print(f"ERROR: config not found at {config_path}")
        sys.exit(1)

    run(config_path, dry_run=args.dry_run, section=args.section,
        src_override=args.src, output_override=args.output, operation_override=args.operation)


if __name__ == "__main__":
    main()
