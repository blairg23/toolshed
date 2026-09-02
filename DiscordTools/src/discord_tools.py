#!/usr/bin/env python3
"""
DiscordTools - Server cleanup utility
Commands: fetch, counts, list, sort, filter, leave, stats, help, quit
"""

import os
import sys
import time
import datetime
import requests

try:
    import readline
except ImportError:
    pass  # Windows — UP arrow won't work but nothing breaks

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from rich.console import Console
    from rich.table import Table
    HAS_RICH = True
    console = Console()
except ImportError:
    HAS_RICH = False
    console = None

DISCORD_EPOCH = 1420070400000
BASE_URL = "https://discord.com/api/v9"

# Global state
guilds_raw = []
guilds = []
current_view = []
sort_by = "age"
token = None


# ── API ──────────────────────────────────────────────────────────────────────

def api_get(path, params=None):
    headers = {"Authorization": token}
    resp = requests.get(f"{BASE_URL}{path}", headers=headers, params=params)
    if resp.status_code == 429:
        retry_after = resp.json().get("retry_after", 1)
        print(f"  Rate limited — waiting {retry_after}s...")
        time.sleep(float(retry_after))
        return api_get(path, params)
    resp.raise_for_status()
    return resp.json()


# ── Helpers ──────────────────────────────────────────────────────────────────

def snowflake_to_date(sid):
    ts = (int(sid) >> 22) + DISCORD_EPOCH
    return datetime.datetime.fromtimestamp(ts / 1000, datetime.UTC).date()

def years_ago(d):
    return round((datetime.date.today() - d).days / 365.25, 1)

def enrich(g, members=None):
    created = snowflake_to_date(g["id"])
    return {
        "id": g["id"],
        "name": g["name"],
        "owner": g.get("owner", False),
        "created": created,
        "age_years": years_ago(created),
        "members": members,
        "partnered": "PARTNERED" in g.get("features", []),
        "verified": "VERIFIED" in g.get("features", []),
    }

def apply_sort(lst):
    if sort_by == "age":
        return sorted(lst, key=lambda g: g["age_years"], reverse=True)
    elif sort_by == "members":
        return sorted(lst, key=lambda g: g["members"] or 0)
    elif sort_by == "name":
        return sorted(lst, key=lambda g: g["name"].lower())
    return lst


CACHE_FILE = "discord_tools_cache.json"

def save_cache():
    import json
    data = {
        "guilds_raw": guilds_raw,
        "counts": {g["id"]: g["members"] for g in guilds},
        "cached_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)
    print(f"  Cache saved to {CACHE_FILE}")

def load_cache():
    import json
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_fetch(args):
    global guilds_raw, guilds, current_view
    reload = "--reload" in args

    if not reload:
        cached = load_cache()
        if cached:
            guilds_raw = cached["guilds_raw"]
            counts = cached.get("counts", {})
            guilds = [enrich(g, members=counts.get(g["id"])) for g in guilds_raw]
            current_view = apply_sort(guilds)
            cached_at = cached.get("cached_at", "unknown")
            print(f"  Loaded {len(guilds)} servers from cache (cached at {cached_at} UTC)")
            print(f"  Use 'fetch --reload' to fetch fresh data")
            return

    guilds_raw = []
    last_id = None
    batch_num = 0
    print("Fetching servers...")
    while True:
        params = {"limit": 200}
        if last_id:
            params["after"] = last_id
        batch = api_get("/users/@me/guilds", params)
        batch_num += 1
        print(f"  Batch {batch_num}: got {len(batch)} servers (total so far: {len(guilds_raw) + len(batch)})")
        if not batch:
            break
        guilds_raw.extend(batch)
        if len(batch) < 200:
            break
        last_id = batch[-1]["id"]
        time.sleep(0.5)

    guilds = [enrich(g) for g in guilds_raw]
    current_view = apply_sort(guilds)
    save_cache()
    print(f"\nDone — {len(guilds)} servers loaded. Member counts not fetched yet (run: counts)")


def cmd_counts(args):
    global guilds, current_view
    if not guilds:
        print("No servers loaded. Run: fetch")
        return
    reload = "--reload" in args

    if not reload:
        cached = load_cache()
        if cached and any(v is not None for v in cached.get("counts", {}).values()):
            counts = cached["counts"]
            for g in guilds:
                if g["members"] is None and g["id"] in counts:
                    g["members"] = counts[g["id"]]
            current_view = apply_sort(guilds)
            cached_at = cached.get("cached_at", "unknown")
            print(f"  Loaded counts from cache (cached at {cached_at} UTC)")
            print(f"  Use 'counts --reload' to re-fetch fresh counts")
            return

    total = len(guilds)
    print(f"Fetching member counts for {total} servers (this takes ~{total // 3}s)...")
    for i, g in enumerate(guilds):
        try:
            data = api_get(f"/guilds/{g['id']}", {"with_counts": "true"})
            g["members"] = data.get("approximate_member_count")
        except Exception:
            g["members"] = None
        if i % 5 == 0:
            print(f"  {i}/{total}", end="\r")
        time.sleep(0.3)
    print(f"  {total}/{total} done          ")
    save_cache()
    current_view = apply_sort(guilds)
    print("Member counts updated. Run: list")


def cmd_list(args):
    global current_view
    if not guilds:
        print("No servers loaded. Run: fetch")
        return

    # optional slice e.g. "list 1-50"
    subset = current_view
    if args:
        try:
            a, b = args[0].split("-")
            subset = current_view[int(a)-1:int(b)]
        except Exception:
            print(f"  Invalid range '{args[0]}', showing all")

    if HAS_RICH:
        table = Table(title=f"Servers ({len(subset)} shown / {len(guilds)} total) — sorted by {sort_by}", show_lines=False)
        table.add_column("#", style="dim", width=4)
        table.add_column("Name", min_width=28, max_width=42)
        table.add_column("Created", width=11)
        table.add_column("Age", width=6)
        table.add_column("Members", width=9)
        table.add_column("Flags", width=18)
        for i, g in enumerate(subset, 1):
            # find true index in current_view
            true_idx = current_view.index(g) + 1
            flags = []
            if g["owner"]: flags.append("[blue]OWNER[/]")
            if g["partnered"]: flags.append("[green]PARTNER[/]")
            if g["verified"]: flags.append("[cyan]VERIFIED[/]")
            table.add_row(
                str(true_idx),
                g["name"],
                str(g["created"]),
                f"{g['age_years']}y",
                str(g["members"]) if g["members"] is not None else "?",
                " ".join(flags) if flags else "",
            )
        console.print(table)
    else:
        print(f"\n{'#':<4} {'Name':<42} {'Created':<12} {'Age':<7} {'Members':<10} Flags")
        print("-" * 95)
        for g in subset:
            true_idx = current_view.index(g) + 1
            flags = " ".join(f for f, v in [("OWNER", g["owner"]), ("PARTNER", g["partnered"]), ("VERIFIED", g["verified"])] if v)
            members_str = str(g["members"]) if g["members"] is not None else "?"
            print(f"{true_idx:<4} {g['name'][:41]:<42} {str(g['created']):<12} {g['age_years']:<7} {members_str:<10} {flags}")


def cmd_stats(args):
    if not guilds:
        print("No servers loaded. Run: fetch")
        return
    owned = sum(1 for g in guilds if g["owner"])
    partnered = sum(1 for g in guilds if g["partnered"])
    verified = sum(1 for g in guilds if g["verified"])
    has_counts = sum(1 for g in guilds if g["members"] is not None)
    ages = [g["age_years"] for g in guilds]
    print(f"\n  Total servers : {len(guilds)}")
    print(f"  You own       : {owned}")
    print(f"  Partnered     : {partnered}")
    print(f"  Verified      : {verified}")
    print(f"  Oldest        : {max(ages)}y")
    print(f"  Newest        : {min(ages)}y")
    print(f"  Avg age       : {round(sum(ages)/len(ages), 1)}y")
    if has_counts:
        counts = [g["members"] for g in guilds if g["members"] is not None]
        print(f"  Member counts : fetched for {has_counts}/{len(guilds)}")
        print(f"  Smallest      : {min(counts):,}")
        print(f"  Largest       : {max(counts):,}")
    else:
        print(f"  Member counts : not fetched (run: counts)")
    print()


def cmd_sort(args):
    global sort_by, current_view
    options = {"age": "age", "members": "members", "name": "name",
               "a": "age", "m": "members", "n": "name"}
    if not args or args[0] not in options:
        print(f"  Current sort: {sort_by}")
        print("  Usage: sort age | sort members | sort name")
        return
    sort_by = options[args[0]]
    current_view = apply_sort(guilds)
    print(f"  Sorted by {sort_by}. Run: list")


def cmd_filter(args):
    global current_view
    if not args:
        print("  Usage: filter owned | filter small | filter old | filter <text> | filter clear")
        return
    f = args[0].lower()
    rest = " ".join(args[1:]).lower()
    if f == "clear":
        current_view = apply_sort(guilds)
        print(f"  Filter cleared — showing all {len(guilds)} servers")
    elif f == "owned":
        current_view = apply_sort([g for g in guilds if g["owner"]])
        print(f"  Showing {len(current_view)} owned servers")
    elif f == "small":
        threshold = int(args[1]) if len(args) > 1 and args[1].isdigit() else 50
        current_view = apply_sort([g for g in guilds if (g["members"] or 9999) < threshold])
        print(f"  Showing {len(current_view)} servers with <{threshold} members")
    elif f == "old":
        threshold = float(args[1]) if len(args) > 1 else 3.0
        current_view = apply_sort([g for g in guilds if g["age_years"] > threshold])
        print(f"  Showing {len(current_view)} servers older than {threshold}y")
    else:
        # text search
        query = f if not rest else f"{f} {rest}"
        current_view = apply_sort([g for g in guilds if query in g["name"].lower()])
        print(f"  Showing {len(current_view)} servers matching '{query}'")


def cmd_leave(args):
    global guilds_raw
    if not current_view:
        print("No servers in view. Run: fetch then list")
        return

    print("Enter server numbers to leave (comma-separated, ranges ok, e.g. 3, 7, 12-15)")
    print("Tip: use 'filter' + 'list' first to narrow down what you see")
    raw = input("> ").strip()
    if not raw:
        print("Cancelled.")
        return

    indices = set()
    for part in raw.split(","):
        part = part.strip()
        if "-" in part:
            try:
                a, b = part.split("-")
                indices.update(range(int(a), int(b) + 1))
            except ValueError:
                print(f"  Skipping invalid range: {part}")
        else:
            try:
                indices.add(int(part))
            except ValueError:
                print(f"  Skipping: {part}")

    to_leave, skipped = [], []
    for idx in sorted(indices):
        if 1 <= idx <= len(current_view):
            g = current_view[idx - 1]
            if g["owner"]:
                skipped.append(g["name"])
            else:
                to_leave.append(g)

    if skipped:
        print(f"\nSkipping (you own): {', '.join(skipped)}")
    if not to_leave:
        print("Nothing to leave.")
        return

    print(f"\nAbout to leave {len(to_leave)} servers:")
    for g in to_leave:
        print(f"  - {g['name']}")
    confirm = input("\nType yes to confirm: ").strip().lower()
    if confirm != "yes":
        print("Cancelled.")
        return

    left = 0
    for g in to_leave:
        headers = {"Authorization": token}
        resp = requests.delete(f"{BASE_URL}/users/@me/guilds/{g['id']}", headers=headers)
        if resp.status_code == 429:
            time.sleep(resp.json().get("retry_after", 1))
            resp = requests.delete(f"{BASE_URL}/users/@me/guilds/{g['id']}", headers=headers)
        ok = resp.status_code == 204
        print(f"  {'✓' if ok else '✗ FAILED'} {g['name']}")
        if ok:
            guilds.remove(g)
            guilds_raw = [raw for raw in guilds_raw if raw["id"] != g["id"]]
            left += 1
        time.sleep(0.5)

    current_view[:] = apply_sort(guilds)
    if left:
        save_cache()
    print(f"\nLeft {left} servers. {len(guilds)} remaining.")


def cmd_help(args):
    print("""
  Commands:
    fetch              — load servers from cache (or API if no cache)
    fetch --reload     — force fresh API call and update cache
    counts             — load member counts from cache
    counts --reload    — re-fetch all member counts and update cache
    list           — show current view
    list 1-50      — show a range
    stats          — summary stats
    sort age       — sort by age (default)
    sort members   — sort by member count
    sort name      — sort alphabetically
    filter owned   — only servers you own
    filter small   — servers with <50 members (or: filter small 100)
    filter old     — servers older than 3y (or: filter old 5)
    filter ffxiv   — text search by name
    filter clear   — reset filter
    leave          — interactively leave marked servers
    quit / q       — exit
""")


COMMANDS = {
    "fetch": cmd_fetch,
    "counts": cmd_counts,
    "list": cmd_list,
    "ls": cmd_list,
    "stats": cmd_stats,
    "sort": cmd_sort,
    "filter": cmd_filter,
    "leave": cmd_leave,
    "help": cmd_help,
    "?": cmd_help,
    "quit": lambda a: sys.exit(0),
    "q": lambda a: sys.exit(0),
    "exit": lambda a: sys.exit(0),
}


def main():
    global token
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        print("Error: DISCORD_TOKEN not found.")
        print("  Add it to a .env file: DISCORD_TOKEN=your_token_here")
        sys.exit(1)

    print("DiscordTools ready. Type 'help' for commands, or start with: fetch")
    print()

    while True:
        try:
            line = input("discord> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        parts = line.split()
        cmd, args = parts[0].lower(), parts[1:]
        if cmd in COMMANDS:
            try:
                COMMANDS[cmd](args)
            except Exception as e:
                print(f"  Error: {e}")
        else:
            print(f"  Unknown command '{cmd}'. Type 'help' for commands.")


if __name__ == "__main__":
    main()