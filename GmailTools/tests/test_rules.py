from __future__ import annotations

import pytest

from src.rules import match_rule, add_rule


RULES = [
    {"sender": "Acme Newsletter", "action": "delete"},
    {"sender": "Widgets Shop", "domain": "widgets-shop.example", "action": "unsubscribe_delete"},
    {"sender": "FitPoints", "action": "label", "label": "FitPoints"},
    {"sender": "CrowdFund Co", "action": "conditional", "conditions": []},
    {"sender": "Trusted Sender", "action": "keep"},
]


def test_match_by_display_name():
    rule = match_rule("Acme Newsletter <promos@acme.example>", RULES)
    assert rule is not None
    assert rule["action"] == "delete"


def test_match_case_insensitive():
    rule = match_rule("acme newsletter <promos@acme.example>", RULES)
    assert rule is not None
    assert rule["action"] == "delete"


def test_match_by_domain():
    rule = match_rule("Widgets Shop <noreply@widgets-shop.example>", RULES)
    assert rule is not None
    assert rule["action"] == "unsubscribe_delete"


def test_match_domain_only_when_name_absent():
    rule = match_rule("<noreply@widgets-shop.example>", RULES)
    assert rule is not None
    assert rule["action"] == "unsubscribe_delete"


def test_no_match_returns_none():
    rule = match_rule("Unknown Sender <unknown@unknown.example>", RULES)
    assert rule is None


def test_first_rule_wins():
    rules = [
        {"sender": "CrowdFund Co", "action": "delete"},
        {"sender": "CrowdFund Co", "action": "keep"},
    ]
    rule = match_rule("CrowdFund Co <noreply@crowdfund.example>", rules)
    assert rule["action"] == "delete"


def test_add_rule_appends_to_account():
    config = {"accounts": [{"name": "test-account", "email": "x@example.com", "rules": []}]}
    new_rule = {"sender": "New Sender", "action": "delete"}
    add_rule(config, "test-account", new_rule)
    assert config["accounts"][0]["rules"] == [new_rule]


def test_add_rule_unknown_account_raises():
    config = {"accounts": []}
    with pytest.raises(ValueError, match="not found"):
        add_rule(config, "missing", {"sender": "X", "action": "delete"})
