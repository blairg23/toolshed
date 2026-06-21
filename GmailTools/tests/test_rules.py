from __future__ import annotations

import pytest

from src.rules import match_rule, add_rule


RULES = [
    {"sender": "Live Nation", "action": "delete"},
    {"sender": "Scheels", "domain": "scheels.com", "action": "unsubscribe_delete"},
    {"sender": "Sweatcoin", "action": "label", "label": "Sweatcoin"},
    {"sender": "Kickstarter", "action": "conditional", "conditions": []},
    {"sender": "Esox", "action": "keep"},
]


def test_match_by_display_name():
    rule = match_rule("Live Nation <promos@livenation.com>", RULES)
    assert rule is not None
    assert rule["action"] == "delete"


def test_match_case_insensitive():
    rule = match_rule("live nation <promos@livenation.com>", RULES)
    assert rule is not None
    assert rule["action"] == "delete"


def test_match_by_domain():
    rule = match_rule("Scheels Store <noreply@scheels.com>", RULES)
    assert rule is not None
    assert rule["action"] == "unsubscribe_delete"


def test_match_domain_only_when_name_absent():
    rule = match_rule("<noreply@scheels.com>", RULES)
    assert rule is not None
    assert rule["action"] == "unsubscribe_delete"


def test_no_match_returns_none():
    rule = match_rule("Unknown Sender <unknown@example.com>", RULES)
    assert rule is None


def test_first_rule_wins():
    rules = [
        {"sender": "Kickstarter", "action": "delete"},
        {"sender": "Kickstarter", "action": "keep"},
    ]
    rule = match_rule("Kickstarter <noreply@kickstarter.com>", rules)
    assert rule["action"] == "delete"


def test_add_rule_appends_to_account():
    config = {"accounts": [{"name": "personal", "email": "x@gmail.com", "rules": []}]}
    new_rule = {"sender": "New Sender", "action": "delete"}
    add_rule(config, "personal", new_rule)
    assert config["accounts"][0]["rules"] == [new_rule]


def test_add_rule_unknown_account_raises():
    config = {"accounts": []}
    with pytest.raises(ValueError, match="not found"):
        add_rule(config, "missing", {"sender": "X", "action": "delete"})
