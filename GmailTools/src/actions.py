from __future__ import annotations

import base64
import urllib.parse
from email.mime.text import MIMEText
from typing import Any

import requests


class LabelCache:
    """Lazy cache of Gmail label name to id mappings for a single account."""

    def __init__(self, service):
        self._service = service
        self._cache: dict[str, str] | None = None

    def _load(self) -> None:
        if self._cache is None:
            result = self._service.users().labels().list(userId="me").execute()
            self._cache = {lbl["name"]: lbl["id"] for lbl in result.get("labels", [])}

    def get_id(self, name: str) -> str | None:
        self._load()
        return self._cache.get(name)

    def create(self, name: str, dry_run: bool = False) -> str:
        """Create a label (and any missing parent for nested labels), return its id."""
        if "/" in name:
            parent = name.rsplit("/", 1)[0]
            if not self.get_id(parent):
                self.create(parent, dry_run)
        existing = self.get_id(name)
        if existing:
            return existing
        if dry_run:
            return f"<dry-run:{name}>"
        result = self._service.users().labels().create(
            userId="me", body={"name": name}
        ).execute()
        label_id: str = result["id"]
        assert self._cache is not None
        self._cache[name] = label_id
        return label_id


def get_header(msg: dict, name: str) -> str:
    headers = msg.get("payload", {}).get("headers", [])
    name_lower = name.lower()
    for h in headers:
        if h["name"].lower() == name_lower:
            return h["value"]
    return ""


def get_snippet(msg: dict) -> str:
    return msg.get("snippet", "")


def apply_action(
    service,
    label_cache: LabelCache,
    msg: dict,
    rule: dict,
    sender_messages: list[dict] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply a rule's action to msg. Returns a result dict with at minimum 'action' and 'msg_id'."""
    action = rule["action"]
    msg_id: str = msg["id"]

    if action == "keep":
        return {"action": "keep", "msg_id": msg_id}

    if action == "archive":
        if not dry_run:
            service.users().messages().modify(
                userId="me", id=msg_id, body={"removeLabelIds": ["INBOX"]}
            ).execute()
        return {"action": "archive", "msg_id": msg_id}

    if action == "delete":
        if not dry_run:
            service.users().messages().trash(userId="me", id=msg_id).execute()
        return {"action": "delete", "msg_id": msg_id}

    if action in ("label", "label_create"):
        return _apply_label(service, label_cache, msg_id, rule, dry_run)

    if action == "unsubscribe_delete":
        return _apply_unsubscribe_delete(service, msg, dry_run)

    if action == "keep_latest":
        return _apply_keep_latest(
            service, label_cache, msg_id, rule, sender_messages or [msg], dry_run
        )

    if action == "conditional":
        return _apply_conditional(service, label_cache, msg, rule, dry_run)

    return {"action": "error", "msg_id": msg_id, "reason": f"unknown action: {action}"}


def _apply_label(
    service, label_cache: LabelCache, msg_id: str, rule: dict, dry_run: bool
) -> dict[str, Any]:
    label_name: str = rule.get("label", "")
    action = rule["action"]

    if action == "label_create":
        label_id = label_cache.create(label_name, dry_run)
    else:
        label_id = label_cache.get_id(label_name)
        if not label_id:
            return {
                "action": "error",
                "msg_id": msg_id,
                "reason": f"label not found: {label_name}",
            }

    body: dict = {"addLabelIds": [label_id]}
    if rule.get("archive"):
        body["removeLabelIds"] = ["INBOX"]

    if not dry_run:
        service.users().messages().modify(userId="me", id=msg_id, body=body).execute()

    return {"action": action, "msg_id": msg_id, "label": label_name}


def _apply_unsubscribe_delete(service, msg: dict, dry_run: bool) -> dict[str, Any]:
    msg_id: str = msg["id"]
    header = get_header(msg, "List-Unsubscribe")

    if header and not dry_run:
        _fire_unsubscribe(header, service)

    if not dry_run:
        service.users().messages().trash(userId="me", id=msg_id).execute()

    return {"action": "unsubscribe_delete", "msg_id": msg_id}


def _fire_unsubscribe(header: str, service) -> None:
    entries = [e.strip().strip("<>") for e in header.split(",")]
    for entry in entries:
        if entry.startswith("http"):
            try:
                resp = requests.post(entry, timeout=10)
                if not resp.ok:
                    requests.get(entry, timeout=10)
            except Exception:
                try:
                    requests.get(entry, timeout=10)
                except Exception:
                    pass
        elif entry.startswith("mailto:"):
            parsed = urllib.parse.urlparse(entry)
            params = dict(urllib.parse.parse_qsl(parsed.query))
            _send_email(service, parsed.path, params.get("subject", "Unsubscribe"), "")


def _send_email(service, to: str, subject: str, body: str) -> None:
    mime = MIMEText(body)
    mime["to"] = to
    mime["subject"] = subject
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


def _apply_keep_latest(
    service,
    label_cache: LabelCache,
    msg_id: str,
    rule: dict,
    sender_messages: list[dict],
    dry_run: bool,
) -> dict[str, Any]:
    keep_n: int = rule.get("keep", 1)
    label_name: str | None = rule.get("label")

    sorted_msgs = sorted(
        sender_messages, key=lambda m: int(m.get("internalDate", 0)), reverse=True
    )
    to_archive = sorted_msgs[keep_n:]

    label_id: str | None = None
    if label_name:
        label_id = label_cache.get_id(label_name) or label_cache.create(label_name, dry_run)

    for old_msg in to_archive:
        body: dict = {"removeLabelIds": ["INBOX"]}
        if label_id:
            body["addLabelIds"] = [label_id]
        if not dry_run:
            service.users().messages().modify(userId="me", id=old_msg["id"], body=body).execute()

    return {"action": "keep_latest", "msg_id": msg_id, "archived": len(to_archive)}


def _apply_conditional(
    service, label_cache: LabelCache, msg: dict, rule: dict, dry_run: bool
) -> dict[str, Any]:
    msg_id: str = msg["id"]
    subject = get_header(msg, "subject").lower()
    snippet = get_snippet(msg).lower()
    text = subject + " " + snippet

    for condition in rule.get("conditions", []):
        if condition["match"].lower() in text:
            label_name: str = condition["label"]
            label_id = label_cache.get_id(label_name) or label_cache.create(label_name, dry_run)
            body: dict = {"addLabelIds": [label_id], "removeLabelIds": ["INBOX"]}
            if not dry_run:
                service.users().messages().modify(userId="me", id=msg_id, body=body).execute()
            return {"action": "conditional:label", "msg_id": msg_id, "label": label_name}

    fallback = rule.get("fallback", "keep")
    if fallback == "delete" and not dry_run:
        service.users().messages().trash(userId="me", id=msg_id).execute()
    return {"action": f"conditional:{fallback}", "msg_id": msg_id}
