from __future__ import annotations

import email.utils
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

_yaml = YAML()
_yaml.preserve_quotes = True

VALID_ACTIONS = {
    "delete",
    "unsubscribe_delete",
    "label",
    "label_create",
    "keep_latest",
    "conditional",
    "keep",
}


def load_config(config_path: Path) -> dict:
    with config_path.open() as f:
        return _yaml.load(f)


def save_config(config: dict, config_path: Path) -> None:
    with config_path.open("w") as f:
        _yaml.dump(config, f)


def get_account(config: dict, name: str) -> dict | None:
    for account in config.get("accounts", []):
        if account["name"] == name:
            return account
    return None


def match_rule(from_header: str, rules: list[dict]) -> dict | None:
    """Return the first rule matching the from_header, or None."""
    display_name, address = email.utils.parseaddr(from_header)
    display_lower = display_name.lower()
    domain = address.split("@")[-1].lower() if "@" in address else ""

    for rule in rules:
        sender_pattern = rule.get("sender", "").lower()
        domain_pattern = rule.get("domain", "").lower()

        name_match = sender_pattern and sender_pattern in display_lower
        domain_match = domain_pattern and domain_pattern == domain

        if name_match or domain_match:
            return rule

    return None


def add_rule(config: dict, account_name: str, rule: dict[str, Any]) -> None:
    """Append a new rule to the named account's rules list."""
    for account in config.get("accounts", []):
        if account["name"] == account_name:
            if account.get("rules") is None:
                account["rules"] = []
            account["rules"].append(rule)
            return
    raise ValueError(f"Account '{account_name}' not found in config")
