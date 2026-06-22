from __future__ import annotations

import email.utils
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

from .actions import LabelCache, apply_action, get_header, get_snippet
from .auth import build_service
from .classifier import classify_sender
from .rules import add_rule, get_account, load_config, match_rule, save_config

_InputFn = Callable[[str], str]

CATEGORY_LABELS: dict[str, str] = {
    "promotions": "CATEGORY_PROMOTIONS",
    "social": "CATEGORY_SOCIAL",
    "updates": "CATEGORY_UPDATES",
}

DEFAULT_CATEGORIES = ["promotions", "social", "updates"]


def run_plan(
    account_name: str,
    config_path: Path,
    categories: list[str],
    limit: int,
    dry_run: bool,
    input_fn: _InputFn = input,
) -> int:
    """
    Core plan/run pipeline. dry_run=True prints the plan with no Gmail writes.
    dry_run=False confirms with the user then executes all actions.
    Returns an exit code (0 = success).
    """
    config = load_config(config_path)
    account = get_account(config, account_name)
    if not account:
        print(f"Account '{account_name}' not found in {config_path}.", file=sys.stderr)
        return 1

    rules: list[dict] = account.get("rules", [])
    service = build_service(account_name)

    # --- Fetch and match ---
    matched: list[tuple[dict, dict]] = []
    # key -> (representative From header, [msgs])
    unmatched: dict[str, tuple[str, list[dict]]] = {}

    total_fetched = 0
    for cat in categories:
        label = CATEGORY_LABELS.get(cat.lower())
        if not label:
            print(f"Unknown category '{cat}' -- skipping.", file=sys.stderr)
            continue
        msgs = _fetch_messages(service, label, limit)
        total_fetched += len(msgs)
        for msg in msgs:
            from_hdr = get_header(msg, "From")
            rule = match_rule(from_hdr, rules)
            if rule:
                matched.append((msg, rule))
            else:
                key = _sender_key(from_hdr)
                if key not in unmatched:
                    unmatched[key] = (from_hdr, [])
                unmatched[key][1].append(msg)

    print(f"Fetched {total_fetched} message(s) across {len(categories)} category(ies).")
    print(f"  Matched: {len(matched)}  |  Novel senders: {len(unmatched)}")

    # --- Interactive classifier for novel senders ---
    if unmatched:
        label_list = _list_labels(service)
        print(f"\n{len(unmatched)} sender(s) have no rule -- starting classifier.")
        for key, (from_hdr, msgs) in unmatched.items():
            rule = classify_sender(from_hdr, msgs, label_list, input_fn=input_fn)
            if rule is not None:
                add_rule(config, account_name, rule)
                save_config(config, config_path)
                for msg in msgs:
                    matched.append((msg, rule))

    if not matched:
        print("\nNothing to do.")
        return 0

    # Group matched messages by sender (needed for keep_latest)
    sender_msgs: dict[str, list[dict]] = defaultdict(list)
    for msg, _rule in matched:
        sender_msgs[_sender_key(get_header(msg, "From"))].append(msg)

    _print_plan(matched)

    if dry_run:
        return 0

    # --- Confirm and execute ---
    answer = input_fn("\nApply these changes? [y/N]: ").strip().lower()
    if answer != "y":
        print("Aborted.")
        return 0

    label_cache = LabelCache(service)
    results: list[dict[str, Any]] = []
    keep_latest_done: set[str] = set()

    for msg, rule in matched:
        key = _sender_key(get_header(msg, "From"))

        # keep_latest must run once per sender, not once per message
        if rule["action"] == "keep_latest":
            if key in keep_latest_done:
                continue
            keep_latest_done.add(key)

        result = apply_action(
            service, label_cache, msg, rule,
            sender_messages=sender_msgs.get(key, [msg]),
            dry_run=False,
        )
        results.append(result)

    _print_summary(results)
    return 0


def _fetch_messages(service, label: str, limit: int) -> list[dict]:
    result = service.users().messages().list(
        userId="me", labelIds=[label], maxResults=limit
    ).execute()
    msg_stubs = result.get("messages", [])
    messages = []
    for stub in msg_stubs:
        msg = service.users().messages().get(
            userId="me",
            id=stub["id"],
            format="metadata",
            metadataHeaders=["From", "Subject", "List-Unsubscribe"],
        ).execute()
        messages.append(msg)
    return messages


def _list_labels(service) -> list[str]:
    result = service.users().labels().list(userId="me").execute()
    return [lbl["name"] for lbl in result.get("labels", [])]


def _sender_key(from_hdr: str) -> str:
    _, address = email.utils.parseaddr(from_hdr)
    return address.lower() or from_hdr.lower()


def _effective_action(msg: dict, rule: dict) -> str:
    """Resolve the actual action a message will receive, evaluating conditional branches."""
    action = rule["action"]
    if action != "conditional":
        return action
    subject = get_header(msg, "subject").lower()
    snippet = get_snippet(msg).lower()
    text = subject + " " + snippet
    for condition in rule.get("conditions", []):
        if condition["match"].lower() in text:
            return f"conditional:label:{condition['label']}"
    fallback = rule.get("fallback", "keep")
    return f"conditional:{fallback}"


def _print_plan(matched: list[tuple[dict, dict]]) -> None:
    action_counts: Counter = Counter()
    action_senders: dict[str, list[str]] = defaultdict(list)

    for msg, rule in matched:
        action = _effective_action(msg, rule)
        action_counts[action] += 1
        display, _ = email.utils.parseaddr(get_header(msg, "From"))
        if display and display not in action_senders[action]:
            action_senders[action].append(display)

    print(f"\n{'Action':<30} {'Count':>5}  Examples")
    print(f"{'-' * 30} {'-' * 5}  {'-' * 40}")
    for action in sorted(action_counts):
        examples = ", ".join(action_senders[action][:3])
        print(f"{action:<30} {action_counts[action]:>5}  {examples}")
    print(f"{'-' * 30} {'-' * 5}")
    print(f"{'TOTAL':<30} {sum(action_counts.values()):>5}")


def _print_summary(results: list[dict[str, Any]]) -> None:
    counts: Counter = Counter(r["action"] for r in results)
    errors = [r for r in results if r["action"] == "error"]

    print(f"\n{'Action':<30} {'Count':>5}")
    print(f"{'-' * 30} {'-' * 5}")
    for action in sorted(counts):
        print(f"{action:<30} {counts[action]:>5}")
    print(f"{'-' * 30} {'-' * 5}")
    print(f"{'TOTAL':<30} {sum(counts.values()):>5}")

    if errors:
        print(f"\n{len(errors)} error(s):")
        for e in errors:
            print(f"  {e['msg_id']}: {e.get('reason', '?')}")
