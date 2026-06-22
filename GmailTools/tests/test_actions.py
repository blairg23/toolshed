from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.actions import LabelCache, apply_action, get_header, get_snippet


def _msg(msg_id: str = "msg001", headers: list | None = None, snippet: str = "") -> dict:
    return {
        "id": msg_id,
        "internalDate": "1000000000000",
        "payload": {"headers": headers or []},
        "snippet": snippet,
    }


def _header(name: str, value: str) -> dict:
    return {"name": name, "value": value}


def _svc_with_labels(labels: list[dict]) -> MagicMock:
    svc = MagicMock()
    svc.users.return_value.labels.return_value.list.return_value.execute.return_value = {
        "labels": labels
    }
    return svc


# ---------------------------------------------------------------------------
# LabelCache
# ---------------------------------------------------------------------------

def test_label_cache_returns_id_for_known_label():
    svc = _svc_with_labels([{"name": "FitPoints", "id": "Label_001"}])
    cache = LabelCache(svc)
    assert cache.get_id("FitPoints") == "Label_001"


def test_label_cache_returns_none_for_unknown_label():
    svc = _svc_with_labels([])
    cache = LabelCache(svc)
    assert cache.get_id("Nonexistent") is None


def test_label_cache_create_calls_api_and_caches():
    svc = _svc_with_labels([])
    svc.users.return_value.labels.return_value.create.return_value.execute.return_value = {
        "id": "Label_new", "name": "NewLabel"
    }
    cache = LabelCache(svc)
    label_id = cache.create("NewLabel")
    assert label_id == "Label_new"
    assert cache.get_id("NewLabel") == "Label_new"


def test_label_cache_create_skips_api_when_label_exists():
    svc = _svc_with_labels([{"name": "Existing", "id": "Label_existing"}])
    cache = LabelCache(svc)
    label_id = cache.create("Existing")
    assert label_id == "Label_existing"
    svc.users.return_value.labels.return_value.create.assert_not_called()


def test_label_cache_create_dry_run_returns_placeholder():
    svc = _svc_with_labels([])
    cache = LabelCache(svc)
    label_id = cache.create("Ghost Label", dry_run=True)
    assert label_id == "<dry-run:Ghost Label>"
    svc.users.return_value.labels.return_value.create.assert_not_called()


def test_label_cache_create_nested_creates_parent_first():
    svc = _svc_with_labels([])
    create_seq = [
        {"id": "Label_parent", "name": "Parent"},
        {"id": "Label_child", "name": "Parent/Child"},
    ]
    svc.users.return_value.labels.return_value.create.return_value.execute.side_effect = create_seq
    cache = LabelCache(svc)
    label_id = cache.create("Parent/Child")
    assert label_id == "Label_child"
    calls = svc.users.return_value.labels.return_value.create.call_args_list
    assert calls[0].kwargs["body"]["name"] == "Parent"
    assert calls[1].kwargs["body"]["name"] == "Parent/Child"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_get_header_returns_value():
    msg = _msg(headers=[_header("From", "Test <test@example.com>")])
    assert get_header(msg, "From") == "Test <test@example.com>"


def test_get_header_case_insensitive():
    msg = _msg(headers=[_header("List-Unsubscribe", "<https://example.com/unsub>")])
    assert get_header(msg, "list-unsubscribe") == "<https://example.com/unsub>"


def test_get_header_missing_returns_empty():
    msg = _msg()
    assert get_header(msg, "Subject") == ""


def test_get_snippet():
    msg = _msg(snippet="Hello world")
    assert get_snippet(msg) == "Hello world"


# ---------------------------------------------------------------------------
# apply_action: keep
# ---------------------------------------------------------------------------

def test_action_keep_is_noop():
    svc = MagicMock()
    cache = LabelCache(svc)
    result = apply_action(svc, cache, _msg(), {"action": "keep"})
    assert result["action"] == "keep"
    svc.users.return_value.messages.return_value.trash.assert_not_called()
    svc.users.return_value.messages.return_value.modify.assert_not_called()


# ---------------------------------------------------------------------------
# apply_action: delete
# ---------------------------------------------------------------------------

def test_action_delete_trashes_message():
    svc = MagicMock()
    cache = LabelCache(svc)
    result = apply_action(svc, cache, _msg("msg001"), {"action": "delete"})
    assert result == {"action": "delete", "msg_id": "msg001"}
    svc.users().messages().trash.assert_called_once_with(userId="me", id="msg001")


def test_action_delete_dry_run_skips_trash():
    svc = MagicMock()
    cache = LabelCache(svc)
    result = apply_action(svc, cache, _msg("msg001"), {"action": "delete"}, dry_run=True)
    assert result["action"] == "delete"
    svc.users.return_value.messages.return_value.trash.assert_not_called()


# ---------------------------------------------------------------------------
# apply_action: label
# ---------------------------------------------------------------------------

def test_action_label_applies_label():
    svc = _svc_with_labels([{"name": "FitPoints", "id": "Label_001"}])
    cache = LabelCache(svc)
    result = apply_action(svc, cache, _msg("msg002"), {"action": "label", "label": "FitPoints"})
    assert result["action"] == "label"
    assert result["label"] == "FitPoints"
    svc.users().messages().modify.assert_called_once_with(
        userId="me", id="msg002", body={"addLabelIds": ["Label_001"]}
    )


def test_action_label_with_archive_removes_inbox():
    svc = _svc_with_labels([{"name": "FitPoints", "id": "Label_001"}])
    cache = LabelCache(svc)
    apply_action(svc, cache, _msg("msg003"), {"action": "label", "label": "FitPoints", "archive": True})
    body = svc.users().messages().modify.call_args.kwargs["body"]
    assert "INBOX" in body.get("removeLabelIds", [])


def test_action_label_missing_label_returns_error():
    svc = _svc_with_labels([])
    cache = LabelCache(svc)
    result = apply_action(svc, cache, _msg(), {"action": "label", "label": "Ghost"})
    assert result["action"] == "error"
    assert "Ghost" in result["reason"]


# ---------------------------------------------------------------------------
# apply_action: label_create
# ---------------------------------------------------------------------------

def test_action_label_create_creates_and_applies():
    svc = _svc_with_labels([])
    svc.users.return_value.labels.return_value.create.return_value.execute.return_value = {
        "id": "Label_new", "name": "NewLabel"
    }
    cache = LabelCache(svc)
    result = apply_action(svc, cache, _msg("msg004"), {"action": "label_create", "label": "NewLabel"})
    assert result["action"] == "label_create"
    svc.users().messages().modify.assert_called_once()


# ---------------------------------------------------------------------------
# apply_action: unsubscribe_delete
# ---------------------------------------------------------------------------

def test_action_unsubscribe_delete_trashes_message():
    svc = MagicMock()
    cache = LabelCache(svc)
    msg = _msg("msg005", headers=[_header("List-Unsubscribe", "")])
    result = apply_action(svc, cache, msg, {"action": "unsubscribe_delete"})
    assert result == {"action": "unsubscribe_delete", "msg_id": "msg005"}
    svc.users().messages().trash.assert_called_once_with(userId="me", id="msg005")


def test_action_unsubscribe_delete_fires_http():
    svc = MagicMock()
    cache = LabelCache(svc)
    msg = _msg(headers=[_header("List-Unsubscribe", "<https://example.com/unsub?t=abc>")])
    with patch("src.actions.requests.post") as mock_post:
        apply_action(svc, cache, msg, {"action": "unsubscribe_delete"})
    mock_post.assert_called_once_with("https://example.com/unsub?t=abc", timeout=10)


def test_action_unsubscribe_delete_dry_run_skips_trash_and_http():
    svc = MagicMock()
    cache = LabelCache(svc)
    msg = _msg(headers=[_header("List-Unsubscribe", "<https://example.com/unsub>")])
    with patch("src.actions.requests.post") as mock_post:
        result = apply_action(svc, cache, msg, {"action": "unsubscribe_delete"}, dry_run=True)
    mock_post.assert_not_called()
    svc.users.return_value.messages.return_value.trash.assert_not_called()
    assert result["action"] == "unsubscribe_delete"


# ---------------------------------------------------------------------------
# apply_action: keep_latest
# ---------------------------------------------------------------------------

def _ts_msg(msg_id: str, ts: int) -> dict:
    return {"id": msg_id, "internalDate": str(ts), "payload": {"headers": []}, "snippet": ""}


def test_action_keep_latest_archives_oldest():
    svc = _svc_with_labels([{"name": "Example Service", "id": "Label_svc"}])
    cache = LabelCache(svc)
    msgs = [_ts_msg("old1", 1000), _ts_msg("new1", 3000), _ts_msg("mid1", 2000)]
    rule = {"action": "keep_latest", "keep": 1, "label": "Example Service", "archive": True}
    result = apply_action(svc, cache, msgs[0], rule, sender_messages=msgs)
    assert result["action"] == "keep_latest"
    assert result["archived"] == 2
    modify_calls = svc.users().messages().modify.call_args_list
    archived_ids = {c.kwargs["id"] for c in modify_calls}
    assert "new1" not in archived_ids
    assert {"old1", "mid1"} == archived_ids


def test_action_keep_latest_nothing_to_archive_when_count_within_limit():
    svc = _svc_with_labels([])
    cache = LabelCache(svc)
    msgs = [_ts_msg("msg1", 1000)]
    rule = {"action": "keep_latest", "keep": 2}
    result = apply_action(svc, cache, msgs[0], rule, sender_messages=msgs)
    assert result["archived"] == 0
    svc.users.return_value.messages.return_value.modify.assert_not_called()


# ---------------------------------------------------------------------------
# apply_action: conditional
# ---------------------------------------------------------------------------

def test_action_conditional_matches_subject():
    svc = _svc_with_labels([{"name": "Crowdfunding/Alpha", "id": "Label_alpha"}])
    cache = LabelCache(svc)
    msg = _msg("msg006", headers=[_header("Subject", "Update on Project Alpha launch")])
    rule = {
        "action": "conditional",
        "conditions": [{"match": "Project Alpha", "label": "Crowdfunding/Alpha"}],
        "fallback": "keep",
    }
    result = apply_action(svc, cache, msg, rule)
    assert result["action"] == "conditional:label"
    assert result["label"] == "Crowdfunding/Alpha"


def test_action_conditional_matches_snippet():
    svc = _svc_with_labels([{"name": "Crowdfunding/Beta", "id": "Label_beta"}])
    cache = LabelCache(svc)
    msg = _msg("msg007", snippet="Project Beta just launched!")
    rule = {
        "action": "conditional",
        "conditions": [{"match": "Project Beta", "label": "Crowdfunding/Beta"}],
        "fallback": "keep",
    }
    result = apply_action(svc, cache, msg, rule)
    assert result["action"] == "conditional:label"


def test_action_conditional_fallback_delete():
    svc = MagicMock()
    cache = LabelCache(svc)
    msg = _msg("msg008", snippet="Nothing special here")
    rule = {"action": "conditional", "conditions": [], "fallback": "delete"}
    result = apply_action(svc, cache, msg, rule)
    assert result["action"] == "conditional:delete"
    svc.users().messages().trash.assert_called_once_with(userId="me", id="msg008")


def test_action_conditional_fallback_keep_no_trash():
    svc = MagicMock()
    cache = LabelCache(svc)
    msg = _msg("msg009", snippet="Nothing matches")
    rule = {"action": "conditional", "conditions": [], "fallback": "keep"}
    result = apply_action(svc, cache, msg, rule)
    assert result["action"] == "conditional:keep"
    svc.users.return_value.messages.return_value.trash.assert_not_called()


# ---------------------------------------------------------------------------
# Unknown action
# ---------------------------------------------------------------------------

def test_unknown_action_returns_error():
    svc = MagicMock()
    cache = LabelCache(svc)
    result = apply_action(svc, cache, _msg(), {"action": "teleport"})
    assert result["action"] == "error"
    assert "teleport" in result["reason"]
