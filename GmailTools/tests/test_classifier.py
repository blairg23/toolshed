from __future__ import annotations

import pytest

from src.classifier import classify_sender


def _msg(subject: str = "", snippet: str = "") -> dict:
    headers = [{"name": "Subject", "value": subject}] if subject else []
    return {
        "id": "msg001",
        "internalDate": "1000000000000",
        "payload": {"headers": headers},
        "snippet": snippet,
    }


def _inp(*responses: str):
    it = iter(responses)
    return lambda _prompt="": next(it)


# ---------------------------------------------------------------------------
# skip / quit
# ---------------------------------------------------------------------------

def test_skip_returns_none():
    rule = classify_sender("X <x@example.com>", [_msg()], [], input_fn=_inp("s"))
    assert rule is None


def test_unrecognized_choice_returns_none():
    rule = classify_sender("X <x@example.com>", [_msg()], [], input_fn=_inp("z"))
    assert rule is None


def test_quit_raises_system_exit():
    with pytest.raises(SystemExit):
        classify_sender("X <x@example.com>", [_msg()], [], input_fn=_inp("q"))


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

def test_delete_by_display_name():
    rule = classify_sender(
        "Acme Newsletter <news@acme.example>", [_msg()], [],
        input_fn=_inp("1", "1"),  # action=delete, match=display name
    )
    assert rule == {"sender": "Acme Newsletter", "action": "delete"}


def test_delete_by_domain():
    rule = classify_sender(
        "Widgets Shop <x@widgets-shop.example>", [_msg()], [],
        input_fn=_inp("1", "2"),  # action=delete, match=domain
    )
    assert rule == {"domain": "widgets-shop.example", "action": "delete"}


def test_delete_both_name_and_domain():
    rule = classify_sender(
        "Acme <x@acme.example>", [_msg()], [],
        input_fn=_inp("1", "3"),  # action=delete, match=both
    )
    assert rule == {"sender": "Acme", "domain": "acme.example", "action": "delete"}


# ---------------------------------------------------------------------------
# label
# ---------------------------------------------------------------------------

def test_label_with_archive():
    rule = classify_sender(
        "FitPoints App <noreply@fitpoints.example>", [_msg()], [],
        input_fn=_inp("3", "1", "FitPoints", "y"),
    )
    assert rule["action"] == "label"
    assert rule["label"] == "FitPoints"
    assert rule.get("archive") is True


def test_label_no_archive():
    rule = classify_sender(
        "FitPoints App <noreply@fitpoints.example>", [_msg()], [],
        input_fn=_inp("3", "1", "FitPoints", "n"),
    )
    assert rule["action"] == "label"
    assert "archive" not in rule


def test_label_default_name_when_blank_input():
    rule = classify_sender(
        "Example Sender <x@example.com>", [_msg()], [],
        input_fn=_inp("3", "1", "", "n"),  # blank label -> use display name
    )
    assert rule["label"] == "Example Sender"


def test_label_shows_existing_labels(capsys):
    classify_sender(
        "X <x@example.com>", [_msg()], ["Alpha", "Beta"],
        input_fn=_inp("3", "1", "Alpha", "n"),
    )
    out = capsys.readouterr().out
    assert "Alpha" in out


# ---------------------------------------------------------------------------
# label_create
# ---------------------------------------------------------------------------

def test_label_create_by_domain():
    rule = classify_sender(
        "Example <x@test.example>", [_msg()], [],
        input_fn=_inp("4", "2", "New/Nested Label", "y"),
    )
    assert rule["action"] == "label_create"
    assert rule["domain"] == "test.example"
    assert rule["label"] == "New/Nested Label"
    assert rule.get("archive") is True


# ---------------------------------------------------------------------------
# keep_latest
# ---------------------------------------------------------------------------

def test_keep_latest_with_count_and_label():
    rule = classify_sender(
        "Example Service <x@example.com>", [_msg()], [],
        input_fn=_inp("5", "1", "2", "Service Label"),
    )
    assert rule["action"] == "keep_latest"
    assert rule["keep"] == 2
    assert rule["label"] == "Service Label"


def test_keep_latest_defaults_to_1_on_blank():
    rule = classify_sender(
        "Example Service <x@example.com>", [_msg()], [],
        input_fn=_inp("5", "1", "", ""),  # blank keep -> 1, blank label -> display name
    )
    assert rule["keep"] == 1


# ---------------------------------------------------------------------------
# keep
# ---------------------------------------------------------------------------

def test_keep():
    rule = classify_sender(
        "Trusted <x@trusted.example>", [_msg()], [],
        input_fn=_inp("6", "1"),
    )
    assert rule == {"sender": "Trusted", "action": "keep"}


# ---------------------------------------------------------------------------
# conditional
# ---------------------------------------------------------------------------

def test_conditional_with_conditions_and_fallback():
    rule = classify_sender(
        "Crowdfund Co <x@crowdfund.example>", [_msg()], [],
        input_fn=_inp("7", "1", "Alpha", "Crowdfunding/Alpha", "", "delete"),
    )
    assert rule["action"] == "conditional"
    assert rule["conditions"] == [{"match": "Alpha", "label": "Crowdfunding/Alpha"}]
    assert rule["fallback"] == "delete"


def test_conditional_default_fallback_keep():
    rule = classify_sender(
        "Crowdfund Co <x@crowdfund.example>", [_msg()], [],
        input_fn=_inp("7", "1", "", ""),  # no conditions, blank keyword ends loop, blank fallback -> keep
    )
    assert rule["fallback"] == "keep"
    assert rule["conditions"] == []


# ---------------------------------------------------------------------------
# unsubscribe_delete
# ---------------------------------------------------------------------------

def test_unsubscribe_delete():
    rule = classify_sender(
        "Spam <x@spam.example>", [_msg()], [],
        input_fn=_inp("2", "1"),
    )
    assert rule == {"sender": "Spam", "action": "unsubscribe_delete"}


# ---------------------------------------------------------------------------
# multiple messages
# ---------------------------------------------------------------------------

def test_multiple_messages_shows_count(capsys):
    msgs = [_msg(subject=f"Subject {i}", snippet=f"Snippet {i}") for i in range(3)]
    classify_sender("Sender <x@example.com>", msgs, [], input_fn=_inp("s"))
    out = capsys.readouterr().out
    assert "3" in out
