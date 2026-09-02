"""Tests for filemapper.verified_transfer().

Covers the exact failure mode that caused real data loss: a copy that gets
interrupted partway through must never result in the source being deleted,
and must never leave a corrupt file sitting under the final destination
name. Everything here uses tmp_path -- no real filesystems, no real media.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import filemapper


def test_move_succeeds_source_gone_dest_present(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()
    src = src_dir / "video.mp4"
    src.write_bytes(b"complete video content")
    dest = dest_dir / "archived.mp4"

    filemapper.verified_transfer(src, dest, "move")

    assert not src.exists()
    assert dest.read_bytes() == b"complete video content"
    assert not (dest_dir / "archived.mp4.partial").exists()


def test_copy_succeeds_source_preserved(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()
    src = src_dir / "video.mp4"
    src.write_bytes(b"complete video content")
    dest = dest_dir / "archived.mp4"

    filemapper.verified_transfer(src, dest, "copy")

    assert src.read_bytes() == b"complete video content"
    assert dest.read_bytes() == b"complete video content"


def test_interrupted_move_preserves_source_and_leaves_no_corrupt_final_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the actual incident: a copy that writes fewer
    bytes than the source (simulating an interrupted transfer) must not
    delete the source, and must not leave anything under the real
    destination filename."""
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()
    src = src_dir / "video.mp4"
    src.write_bytes(b"x" * 1000)  # pretend this is a multi-GB recording
    dest = dest_dir / "archived.mp4"

    def _truncated_copy(src_path, dest_path):
        # Simulate an interrupted write: only part of the data lands.
        Path(dest_path).write_bytes(b"x" * 400)

    monkeypatch.setattr(filemapper.shutil, "copy2", _truncated_copy)

    with pytest.raises(filemapper.TransferVerificationError, match="size mismatch"):
        filemapper.verified_transfer(src, dest, "move")

    assert src.exists(), "source must survive a failed verification"
    assert src.read_bytes() == b"x" * 1000
    assert not dest.exists(), "no corrupt file under the final destination name"
    assert not (dest_dir / "archived.mp4.partial").exists(), "temp file must be cleaned up"


def test_interrupted_copy_preserves_source_and_leaves_no_corrupt_final_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()
    src = src_dir / "video.mp4"
    src.write_bytes(b"x" * 1000)
    dest = dest_dir / "archived.mp4"

    def _truncated_copy(src_path, dest_path):
        Path(dest_path).write_bytes(b"x" * 400)

    monkeypatch.setattr(filemapper.shutil, "copy2", _truncated_copy)

    with pytest.raises(filemapper.TransferVerificationError):
        filemapper.verified_transfer(src, dest, "copy")

    assert src.exists()
    assert src.read_bytes() == b"x" * 1000
    assert not dest.exists()


def test_one_failed_transfer_does_not_abort_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run()'s loop must keep processing remaining pairs after one failure,
    rather than aborting the whole batch on the first bad transfer."""
    src_dir = tmp_path / "src"
    target_dir = tmp_path / "targets"
    out_dir = tmp_path / "out"
    src_dir.mkdir()
    target_dir.mkdir()
    (target_dir / "2025-01-01_good").mkdir()
    (target_dir / "2025-01-02_bad").mkdir()

    good_src = src_dir / "2025-01-01 10-00-00.mp4"
    bad_src = src_dir / "2025-01-02 10-00-00.mp4"
    good_src.write_bytes(b"good content")
    bad_src.write_bytes(b"x" * 1000)

    config_path = tmp_path / "config.yml"
    config_path.write_text(
        f"""
vods:
  source:
    path: "{src_dir.as_posix()}"
    pattern: "{{date}} {{time}}.{{ext}}"
  match:
    strategy: date_prefix
    fallback: interactive
    reference:
      path: "{target_dir.as_posix()}"
      name_template: "{{date}}_{{title}}.{{ext}}"
      fields:
        title:
          source: folder_name
  output:
    path: "{out_dir.as_posix()}"
    operation: move
"""
    )

    real_copy2 = shutil.copy2

    def _maybe_truncate(src_path, dest_path):
        if "2025-01-02" in str(src_path):
            Path(dest_path).write_bytes(b"x" * 400)
        else:
            real_copy2(src_path, dest_path)

    monkeypatch.setattr(filemapper.shutil, "copy2", _maybe_truncate)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")

    filemapper.run(config_path, dry_run=False, section="vods")

    assert not good_src.exists(), "the good pair should have moved successfully"
    assert bad_src.exists(), "the bad pair's source must survive"
    assert (out_dir / "2025-01-01_good.mp4").exists()
    assert not (out_dir / "2025-01-02_bad.mp4").exists()
