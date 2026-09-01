"""Tests for discord_tools.py's pure helper functions.

Everything here is deterministic logic (snowflake decoding, age math,
guild enrichment, sorting) -- no real Discord API calls, no network.
"""

from __future__ import annotations

import datetime

import discord_tools


def _snowflake_for(dt: datetime.datetime) -> str:
    """Build a Discord snowflake ID whose embedded timestamp is dt."""
    ts_ms = int(dt.timestamp() * 1000)
    offset_ms = ts_ms - discord_tools.DISCORD_EPOCH
    return str(offset_ms << 22)


# ---------- snowflake_to_date ----------


def test_snowflake_to_date_at_discord_epoch():
    assert discord_tools.snowflake_to_date("0") == datetime.date(2015, 1, 1)


def test_snowflake_to_date_roundtrips_arbitrary_date():
    target = datetime.datetime(2020, 6, 15, tzinfo=datetime.timezone.utc)
    sid = _snowflake_for(target)
    assert discord_tools.snowflake_to_date(sid) == datetime.date(2020, 6, 15)


# ---------- years_ago ----------


def test_years_ago_today_is_zero():
    assert discord_tools.years_ago(datetime.date.today()) == 0.0


def test_years_ago_one_year_back_rounds_to_one():
    one_year_ago = datetime.date.today() - datetime.timedelta(days=365)
    assert discord_tools.years_ago(one_year_ago) == 1.0


# ---------- enrich ----------


def test_enrich_builds_expected_fields():
    guild = {
        "id": "0",  # snowflake at the Discord epoch
        "name": "Test Guild",
        "owner": True,
        "features": ["PARTNERED", "VERIFIED"],
    }
    result = discord_tools.enrich(guild, members=42)
    assert result["id"] == "0"
    assert result["name"] == "Test Guild"
    assert result["owner"] is True
    assert result["created"] == datetime.date(2015, 1, 1)
    assert result["members"] == 42
    assert result["partnered"] is True
    assert result["verified"] is True


def test_enrich_defaults_owner_and_features_when_absent():
    guild = {"id": "0", "name": "Minimal Guild"}
    result = discord_tools.enrich(guild)
    assert result["owner"] is False
    assert result["partnered"] is False
    assert result["verified"] is False
    assert result["members"] is None


# ---------- apply_sort ----------


def _guilds():
    return [
        {"name": "Zebra", "age_years": 1.0, "members": 300},
        {"name": "apple", "age_years": 5.0, "members": 10},
        {"name": "Mango", "age_years": 3.0, "members": 100},
    ]


def test_apply_sort_by_age_oldest_first(monkeypatch):
    monkeypatch.setattr(discord_tools, "sort_by", "age")
    result = discord_tools.apply_sort(_guilds())
    assert [g["name"] for g in result] == ["apple", "Mango", "Zebra"]


def test_apply_sort_by_members_smallest_first(monkeypatch):
    monkeypatch.setattr(discord_tools, "sort_by", "members")
    result = discord_tools.apply_sort(_guilds())
    assert [g["name"] for g in result] == ["apple", "Mango", "Zebra"]


def test_apply_sort_by_name_case_insensitive(monkeypatch):
    monkeypatch.setattr(discord_tools, "sort_by", "name")
    result = discord_tools.apply_sort(_guilds())
    assert [g["name"] for g in result] == ["apple", "Mango", "Zebra"]
