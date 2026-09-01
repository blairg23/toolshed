"""Tests for gdrive.py's audit subcommand logic.

Everything here exercises pure, deterministic functions operating on
synthetic {rel_path: {"md5":..., "size":...}} entries dicts -- never a real
rclone invocation or real Google Drive account.
"""

from __future__ import annotations

import time

import gdrive


def _entries(**paths):
    """Build a synthetic entries dict from path=(md5, size) kwargs-friendly
    positional tuples, e.g. _entries(**{"a/b.jpg": ("hash1", 100)})."""
    return {path: {"md5": md5, "size": size} for path, (md5, size) in paths.items()}


# ---------- format_size ----------


def test_format_size_bytes():
    assert gdrive.format_size(500) == "500 B"


def test_format_size_kb():
    assert gdrive.format_size(2048) == "2.00 KB"


def test_format_size_mb():
    assert gdrive.format_size(1536 * 1024) == "1.50 MB"


def test_format_size_zero_or_none():
    assert gdrive.format_size(0) == "0 B"
    assert gdrive.format_size(None) == "0 B"


# ---------- is_junk_path ----------


def test_junk_exact_names_case_insensitive():
    assert gdrive.is_junk_path("folder/Contents.csv")
    assert gdrive.is_junk_path("folder/desktop.ini")
    assert gdrive.is_junk_path("folder/DESKTOP.INI")


def test_junk_trashed_prefix():
    assert gdrive.is_junk_path(".trashed-1234/photo.jpg")
    assert gdrive.is_junk_path("album/.trashed-5678")


def test_junk_thumbnails_dir():
    assert gdrive.is_junk_path("album/.thumbnails/thumb.jpg")


def test_junk_conflicts_dir():
    assert gdrive.is_junk_path("Camera/_conflicts/photo.jpg")


def test_junk_1_suffix_file_and_dir():
    assert gdrive.is_junk_path("Camera/photo_1.jpg")
    assert gdrive.is_junk_path("Camera_1/photo.jpg")


def test_non_junk_path_is_not_flagged():
    assert not gdrive.is_junk_path("Camera/2025-06-15 14.30.22.jpg")
    assert not gdrive.is_junk_path("Music Recordings/set_10.flac")


# ---------- digest_to_paths ----------


def test_digest_to_paths_groups_by_hash():
    entries = _entries(**{
        "a.jpg": ("hash1", 100),
        "b.jpg": ("hash1", 100),
        "c.jpg": ("hash2", 200),
    })
    by_digest = gdrive.digest_to_paths(entries)
    assert by_digest == {"hash1": ["a.jpg", "b.jpg"], "hash2": ["c.jpg"]}


def test_digest_to_paths_excludes_missing_hash():
    entries = _entries(**{
        "doc.gdoc": (None, None),
        "a.jpg": ("hash1", 100),
    })
    by_digest = gdrive.digest_to_paths(entries)
    assert by_digest == {"hash1": ["a.jpg"]}


# ---------- audit_duplicates ----------


def test_audit_duplicates_finds_multi_copy_hashes():
    entries = _entries(**{
        "a.jpg": ("hash1", 100),
        "b.jpg": ("hash1", 100),
        "c.jpg": ("hash1", 100),
        "unique.jpg": ("hash2", 500),
    })
    groups = gdrive.audit_duplicates(entries)
    assert len(groups) == 1
    digest, size, reclaimable, paths = groups[0]
    assert digest == "hash1"
    assert size == 100
    assert reclaimable == 200  # 100 * (3 copies - 1)
    assert paths == ["a.jpg", "b.jpg", "c.jpg"]


def test_audit_duplicates_excludes_unique_files():
    entries = _entries(**{"a.jpg": ("hash1", 100), "b.jpg": ("hash2", 200)})
    assert gdrive.audit_duplicates(entries) == []


def test_audit_duplicates_sorted_by_reclaimable_descending():
    entries = _entries(**{
        "small1.jpg": ("s", 10),
        "small2.jpg": ("s", 10),
        "big1.jpg": ("b", 1000),
        "big2.jpg": ("b", 1000),
    })
    groups = gdrive.audit_duplicates(entries)
    assert [g[0] for g in groups] == ["b", "s"]


# ---------- audit_junk ----------


def test_audit_junk_counts_and_sizes_matching_entries():
    entries = _entries(**{
        "Camera/photo.jpg": (None, 500),
        "Camera/desktop.ini": (None, 10),
        "Camera/.trashed-1/old.jpg": (None, 300),
    })
    count, total_size, paths = gdrive.audit_junk(entries)
    assert count == 2
    assert total_size == 310
    assert paths == ["Camera/.trashed-1/old.jpg", "Camera/desktop.ini"]


def test_audit_junk_empty_when_nothing_matches():
    entries = _entries(**{"Camera/photo.jpg": (None, 500)})
    assert gdrive.audit_junk(entries) == (0, 0, [])


# ---------- audit_space_breakdown ----------


def test_audit_space_breakdown_totals_by_top_level_folder():
    entries = _entries(**{
        "Mobile/Camera/a.jpg": (None, 100),
        "Mobile/Camera/b.jpg": (None, 200),
        "Music Recordings/set.flac": (None, 5000),
        "root_file.txt": (None, 50),
    })
    folder_sizes, _largest = gdrive.audit_space_breakdown(entries, top_n=10)
    assert dict(folder_sizes) == {
        "Mobile": 300,
        "Music Recordings": 5000,
        "(root)": 50,
    }
    assert folder_sizes[0] == ("Music Recordings", 5000)


def test_audit_space_breakdown_largest_files_respects_top_n():
    entries = _entries(**{
        "a.jpg": (None, 100),
        "b.jpg": (None, 300),
        "c.jpg": (None, 200),
    })
    _folders, largest = gdrive.audit_space_breakdown(entries, top_n=2)
    assert largest == [("b.jpg", 300), ("c.jpg", 200)]


# ---------- drive cache round-trip (rich entries shape) ----------


def test_drive_cache_round_trips_rich_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(gdrive, "CACHE_DIR", tmp_path)
    entries = {"a.jpg": {"md5": "hash1", "size": 100}}

    gdrive.save_drive_cache("remote:path", entries)
    loaded = gdrive.load_drive_cache("remote:path", ttl_seconds=3600)

    assert loaded == entries


def test_drive_cache_returns_none_when_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(gdrive, "CACHE_DIR", tmp_path)
    entries = {"a.jpg": {"md5": "hash1", "size": 100}}
    gdrive.save_drive_cache("remote:path", entries)

    cache_file = gdrive.cache_path_for("remote:path")
    data = cache_file.read_text(encoding="utf-8")
    import json

    stale = json.loads(data)
    stale["hashed_at"] = time.time() - 999999
    cache_file.write_text(json.dumps(stale), encoding="utf-8")

    assert gdrive.load_drive_cache("remote:path", ttl_seconds=3600) is None


def test_drive_cache_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(gdrive, "CACHE_DIR", tmp_path)
    assert gdrive.load_drive_cache("remote:never-cached", ttl_seconds=3600) is None
