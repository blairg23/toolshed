from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from src.runner import run_plan, DEFAULT_CATEGORIES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_RULES = [
    {"sender": "Acme Newsletter", "action": "delete"},
    {"sender": "FitPoints App", "action": "label", "label": "FitPoints", "archive": True},
]

_CONFIG_YAML = """\
accounts:
  - name: personal
    email: test@gmail.com
    rules:
      - sender: "Acme Newsletter"
        action: delete
      - sender: "FitPoints App"
        action: label
        label: "FitPoints"
        archive: true
"""


def _msg(msg_id: str, from_hdr: str, subject: str = "", snippet: str = "") -> dict:
    return {
        "id": msg_id,
        "internalDate": "1000000000000",
        "payload": {
            "headers": [
                {"name": "From", "value": from_hdr},
                {"name": "Subject", "value": subject},
            ]
        },
        "snippet": snippet,
    }


def _make_svc(messages: list[dict]) -> MagicMock:
    svc = MagicMock()
    # messages.list returns stubs
    stubs = [{"id": m["id"]} for m in messages]
    svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": stubs
    }
    # messages.get returns full message by id
    msg_by_id = {m["id"]: m for m in messages}
    svc.users.return_value.messages.return_value.get.return_value.execute.side_effect = (
        lambda: None
    )

    def _get_execute(msg_id):
        class _Exec:
            def execute(self):
                return msg_by_id[msg_id]
        return _Exec()

    svc.users.return_value.messages.return_value.get.side_effect = (
        lambda userId, id, format, metadataHeaders: _get_execute(id)
    )
    svc.users.return_value.labels.return_value.list.return_value.execute.return_value = {
        "labels": [{"name": "FitPoints", "id": "Label_001"}]
    }
    svc.users.return_value.messages.return_value.modify.return_value.execute.return_value = {}
    svc.users.return_value.messages.return_value.trash.return_value.execute.return_value = {}
    return svc


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    p = tmp_path / "config.yml"
    p.write_text(_CONFIG_YAML, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# plan (dry_run=True)
# ---------------------------------------------------------------------------

def test_plan_prints_table_no_writes(config_file, capsys):
    msgs = [
        _msg("m1", "Acme Newsletter <n@acme.example>", "Sale today"),
        _msg("m2", "FitPoints App <fp@fitpoints.example>", "Your points"),
    ]
    svc = _make_svc(msgs)

    with patch("src.runner.build_service", return_value=svc):
        rc = run_plan("personal", config_file, ["promotions"], limit=50, dry_run=True)

    assert rc == 0
    out = capsys.readouterr().out
    assert "delete" in out
    assert "label" in out
    # No modify or trash calls
    svc.users.return_value.messages.return_value.trash.assert_not_called()
    svc.users.return_value.messages.return_value.modify.assert_not_called()


def test_plan_nothing_to_do_when_no_messages(config_file, capsys):
    svc = MagicMock()
    svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": []
    }
    svc.users.return_value.labels.return_value.list.return_value.execute.return_value = {
        "labels": []
    }

    with patch("src.runner.build_service", return_value=svc):
        rc = run_plan("personal", config_file, ["promotions"], limit=10, dry_run=True)

    assert rc == 0
    assert "Nothing to do" in capsys.readouterr().out


def test_plan_unknown_account_returns_1(config_file):
    with patch("src.runner.build_service", return_value=MagicMock()):
        rc = run_plan("nonexistent", config_file, ["promotions"], limit=10, dry_run=True)
    assert rc == 1


# ---------------------------------------------------------------------------
# run (dry_run=False)
# ---------------------------------------------------------------------------

def test_run_applies_actions_on_confirm(config_file, capsys):
    msgs = [_msg("m1", "Acme Newsletter <n@acme.example>", "Big sale")]
    svc = _make_svc(msgs)

    with patch("src.runner.build_service", return_value=svc):
        rc = run_plan(
            "personal", config_file, ["promotions"], limit=50,
            dry_run=False, input_fn=lambda _: "y",
        )

    assert rc == 0
    svc.users.return_value.messages.return_value.trash.assert_called_once_with(
        userId="me", id="m1"
    )


def test_run_aborts_on_no(config_file, capsys):
    msgs = [_msg("m1", "Acme Newsletter <n@acme.example>")]
    svc = _make_svc(msgs)

    with patch("src.runner.build_service", return_value=svc):
        rc = run_plan(
            "personal", config_file, ["promotions"], limit=50,
            dry_run=False, input_fn=lambda _: "n",
        )

    assert rc == 0
    assert "Aborted" in capsys.readouterr().out
    svc.users.return_value.messages.return_value.trash.assert_not_called()


# ---------------------------------------------------------------------------
# Novel senders trigger classifier
# ---------------------------------------------------------------------------

def test_plan_novel_sender_calls_classify(config_file, capsys):
    msgs = [_msg("m3", "Unknown Brand <x@unknown.example>", "Check this out")]
    svc = _make_svc(msgs)

    classify_calls = []

    def _fake_classify(from_hdr, messages, labels, input_fn=None):
        classify_calls.append(from_hdr)
        return None  # user skips

    with patch("src.runner.build_service", return_value=svc):
        with patch("src.runner.classify_sender", side_effect=_fake_classify):
            rc = run_plan("personal", config_file, ["promotions"], limit=50, dry_run=True)

    assert rc == 0
    assert len(classify_calls) == 1


def test_plan_novel_sender_classified_rule_added_to_config(config_file):
    msgs = [_msg("m4", "New Brand <x@newbrand.example>")]
    svc = _make_svc(msgs)

    new_rule = {"sender": "New Brand", "action": "delete"}

    with patch("src.runner.build_service", return_value=svc):
        with patch("src.runner.classify_sender", return_value=new_rule):
            run_plan("personal", config_file, ["promotions"], limit=50, dry_run=True)

    # Config should have been updated
    from src.rules import load_config
    cfg = load_config(config_file)
    account_rules = cfg["accounts"][0]["rules"]
    assert any(r.get("sender") == "New Brand" for r in account_rules)


# ---------------------------------------------------------------------------
# Multiple categories
# ---------------------------------------------------------------------------

def test_plan_scans_multiple_categories(config_file):
    msgs = [_msg("m1", "Acme Newsletter <n@acme.example>")]
    svc = _make_svc(msgs)

    with patch("src.runner.build_service", return_value=svc):
        rc = run_plan(
            "personal", config_file,
            categories=["promotions", "social", "updates"],
            limit=10, dry_run=True,
        )

    assert rc == 0
    # list() should have been called once per category
    assert svc.users.return_value.messages.return_value.list.call_count == 3
