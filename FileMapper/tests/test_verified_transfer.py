"""Tests for filemapper.verified_transfer().

Covers the exact failure mode that caused real data loss: a copy that gets
interrupted partway through must never result in the source being deleted,
and must never leave a corrupt file sitting under the final destination
name. Everything here uses tmp_path -- no real filesystems, no real media.
"""

from __future__ import annotations

import os
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


def test_same_path_move_is_a_noop(tmp_path: Path) -> None:
    """When the computed output name lands the file back at its own
    source path (output dir == source dir, name unchanged), the transfer
    must be a true no-op -- the previous shutil.move() treated this case
    as harmless, but naively running it through copy-verify-replace-unlink
    would os.replace() the file onto itself and then immediately unlink
    the path it was just replaced into, deleting it entirely."""
    d = tmp_path / "same"
    d.mkdir()
    f = d / "video.mp4"
    f.write_bytes(b"unchanged content")

    filemapper.verified_transfer(f, f, "move")

    assert f.exists()
    assert f.read_bytes() == b"unchanged content"
    assert not (d / "video.mp4.partial").exists()


def test_same_path_copy_is_a_noop(tmp_path: Path) -> None:
    d = tmp_path / "same"
    d.mkdir()
    f = d / "video.mp4"
    f.write_bytes(b"unchanged content")

    filemapper.verified_transfer(f, f, "copy")

    assert f.exists()
    assert f.read_bytes() == b"unchanged content"


def test_move_preserves_symlink_instead_of_dereferencing(tmp_path: Path) -> None:
    """shutil.copy2() follows symlinks by default and would silently turn
    a symlinked source into a real file copy at the destination while
    deleting the original link -- the previous shutil.move() preserved
    the link itself instead."""
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()
    real_file = tmp_path / "actual_target.mp4"
    real_file.write_bytes(b"real content")
    link = src_dir / "video.mp4"
    link.symlink_to(real_file)
    dest = dest_dir / "archived.mp4"

    filemapper.verified_transfer(link, dest, "move")

    assert not link.exists() and not link.is_symlink()
    assert dest.is_symlink()
    assert Path(os.readlink(dest)) == real_file
    assert real_file.read_bytes() == b"real content", "the link's target must be untouched"


def test_copy_preserves_symlink_and_leaves_source_link_intact(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()
    real_file = tmp_path / "actual_target.mp4"
    real_file.write_bytes(b"real content")
    link = src_dir / "video.mp4"
    link.symlink_to(real_file)
    dest = dest_dir / "archived.mp4"

    filemapper.verified_transfer(link, dest, "copy")

    assert link.is_symlink(), "copy must not remove the source link"
    assert dest.is_symlink()
    assert Path(os.readlink(dest)) == real_file


def test_ordinary_filesystem_error_does_not_abort_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real filesystem failure (disk full, permission denied, a
    disconnected share) raises a plain OSError from inside copy2()/stat()/
    os.replace() -- not filemapper's own TransferVerificationError. That
    must still be caught per-file and reported, not propagate up and
    abort every remaining pair in the batch."""
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()
    good_src = src_dir / "good.mp4"
    bad_src = src_dir / "bad.mp4"
    good_src.write_bytes(b"good content")
    bad_src.write_bytes(b"bad content")

    real_copy2 = shutil.copy2

    def _maybe_fail(src_path, dest_path):
        if "bad" in str(src_path):
            raise PermissionError("simulated disk-full / permission failure")
        real_copy2(src_path, dest_path)

    monkeypatch.setattr(filemapper.shutil, "copy2", _maybe_fail)

    failures = []
    for src in (good_src, bad_src):
        dest = dest_dir / src.name
        try:
            filemapper.verified_transfer(src, dest, "move")
        except OSError:
            failures.append(src.name)

    assert failures == ["bad.mp4"]
    assert not good_src.exists()
    assert (dest_dir / "good.mp4").exists()
    assert bad_src.exists(), "the failed transfer's source must survive"
    assert not (dest_dir / "bad.mp4").exists()
