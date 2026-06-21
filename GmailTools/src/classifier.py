from __future__ import annotations

import email.utils
from typing import Callable

_InputFn = Callable[[str], str]


def _get_header(msg: dict, name: str) -> str:
    headers = msg.get("payload", {}).get("headers", [])
    name_lower = name.lower()
    for h in headers:
        if h["name"].lower() == name_lower:
            return h["value"]
    return ""


def _get_snippet(msg: dict) -> str:
    return msg.get("snippet", "")

_ACTION_MAP = {
    "1": "delete",
    "2": "unsubscribe_delete",
    "3": "label",
    "4": "label_create",
    "5": "keep_latest",
    "6": "keep",
    "7": "conditional",
}


def classify_sender(
    from_header: str,
    messages: list[dict],
    existing_labels: list[str],
    input_fn: _InputFn = input,
) -> dict | None:
    """
    Prompt the user to create a rule for a sender with no existing rule.

    Returns a rule dict ready to pass to add_rule(), or None if the user
    chose to skip. Raises SystemExit(0) if the user chose to quit.

    messages: all emails from this sender (pre-grouped by the caller).
    existing_labels: current Gmail label names shown as reference.
    input_fn: injectable callable for reads (replaces builtins.input in tests).
    """
    display_name, address = email.utils.parseaddr(from_header)
    domain = address.split("@")[-1].lower() if "@" in address else ""

    print(f"\n{'=' * 60}")
    print(f"  Sender:  {display_name or '(no display name)'}")
    print(f"  Address: {address}")
    print(f"  Count:   {len(messages)} email(s)")

    subjects = list(
        {_get_header(m, "subject") for m in messages[:5] if _get_header(m, "subject")}
    )
    if subjects:
        print("  Subject(s):")
        for s in subjects[:3]:
            print(f"    - {s[:80]}")

    snippets = [_get_snippet(m) for m in messages if _get_snippet(m)]
    if snippets:
        print(f"  Snippet:  {snippets[0][:100]}")

    print("\nAction:")
    print("  1) delete              2) unsubscribe_delete")
    print("  3) label               4) label_create")
    print("  5) keep_latest         6) keep")
    print("  7) conditional         s) skip    q) quit")

    choice = input_fn("Choice: ").strip().lower()
    if choice == "q":
        raise SystemExit(0)
    if choice == "s" or choice not in _ACTION_MAP:
        return None

    action = _ACTION_MAP[choice]

    # --- Match key ---
    print("\nMatch on:")
    if display_name:
        print(f'  1) display name: "{display_name}"')
    if domain:
        print(f'  2) domain: "{domain}"')
    if display_name and domain:
        print("  3) both")

    match_choice = input_fn("Choice [1]: ").strip() or "1"

    rule: dict = {}
    if match_choice in ("1", "3") and display_name:
        rule["sender"] = display_name
    if match_choice in ("2", "3") and domain:
        rule["domain"] = domain
    if not rule:
        rule["domain"] = domain if domain else (display_name or "unknown")

    rule["action"] = action

    # --- Action-specific prompts ---
    if action in ("label", "label_create"):
        if existing_labels:
            sample = sorted(existing_labels)[:10]
            print("\nExisting labels (sample):")
            for lbl in sample:
                print(f"  - {lbl}")
        default_label = display_name or domain
        label = input_fn(f"Label name [{default_label}]: ").strip() or default_label
        rule["label"] = label
        archive_raw = input_fn("Archive (remove from inbox)? [Y/n]: ").strip().lower()
        if archive_raw != "n":
            rule["archive"] = True

    elif action == "keep_latest":
        n_raw = input_fn("Keep how many? [1]: ").strip()
        rule["keep"] = int(n_raw) if n_raw.isdigit() else 1
        default_label = display_name or domain
        label = input_fn(f"Label name (blank to skip) [{default_label}]: ").strip()
        if label or default_label:
            rule["label"] = label or default_label

    elif action == "conditional":
        conditions: list[dict] = []
        print("\nAdd conditions (keyword + label pairs). Leave keyword blank to finish.")
        while True:
            kw = input_fn("  Keyword (blank to finish): ").strip()
            if not kw:
                break
            if existing_labels:
                print("  Labels:", ", ".join(sorted(existing_labels)[:5]))
            lbl = input_fn("  Label: ").strip()
            if lbl:
                conditions.append({"match": kw, "label": lbl})
        rule["conditions"] = conditions
        fallback_raw = input_fn("Fallback action (keep/delete) [keep]: ").strip().lower()
        rule["fallback"] = fallback_raw if fallback_raw in ("keep", "delete") else "keep"

    return rule
