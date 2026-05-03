import pytest
import os
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
from maidbook import common

def test_human():
    assert common.human(0) == "0 B"
    assert common.human(500) == "500 B"
    assert common.human(1024) == "1.0 KB"
    assert common.human(int(1024 * 1.5)) == "1.5 KB"
    assert common.human(1024**2) == "1.0 MB"
    assert common.human(1024**3) == "1.0 GB"
    assert common.human(1024**4) == "1.0 TB"
    assert common.human(1024**5) == "1024.0 TB"

def test_fmt_path(monkeypatch):
    monkeypatch.setattr(common, "HOME", Path("/Users/test"))

    assert common.fmt_path(Path("/Users/test/Library/Caches")) == "~/Library/Caches"
    assert common.fmt_path("/Users/test/Library/Caches") == "~/Library/Caches"
    assert common.fmt_path(Path("/Library/Caches")) == "/Library/Caches"

@patch("subprocess.run")
def test_path_size_exists_but_fails(mock_run, tmp_path):
    # if du fails, it should return 0
    mock_run.side_effect = subprocess.SubprocessError("du failed")

    # We must provide a path that actually exists because `path_size` checks `.exists()`
    assert common.path_size(tmp_path) == 0

@patch("subprocess.run")
def test_path_size_success(mock_run, tmp_path):
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "42\t/some/path\n"
    mock_run.return_value = mock_proc

    # 42 blocks * 1024 = 43008 bytes
    assert common.path_size(tmp_path) == 43008

def test_rm_path(tmp_path):
    # Test directory
    d = tmp_path / "dir"
    d.mkdir()
    f = d / "file.txt"
    f.write_text("hello")

    # Should delete cleanly
    with patch("maidbook.common.path_size", return_value=100):
        size, errors = common.rm_path(d)
        assert size == 100
        assert errors == 0
        assert not d.exists()

    # Test missing
    size, errors = common.rm_path(tmp_path / "missing")
    assert size == 0
    assert errors == 0

# ---------------------------------------------------------------------------
# rm_path_async + trash reaper
# ---------------------------------------------------------------------------

def _wait_for_reapers():
    """Block until any in-flight reaper threads finish (test hygiene)."""
    common.wait_for_pending_reaps(timeout=5.0)


def test_rm_path_async_directory_moves_to_trash(tmp_path, monkeypatch):
    """A directory deletion via rm_path_async leaves the original gone and
    returns its pre-delete size as bytes_moved."""
    monkeypatch.setattr(common, "TRASH_BASE", tmp_path / ".trash")

    target = tmp_path / "cache_dir"
    target.mkdir()
    (target / "a.txt").write_text("x" * 50)
    (target / "b.txt").write_text("y" * 50)

    with patch("maidbook.common.path_size", return_value=4096):
        moved, errors = common.rm_path_async(target)

    assert errors == 0
    assert moved == 4096
    assert not target.exists(), "original path should be gone after async delete"
    _wait_for_reapers()


def test_rm_path_async_file_delegates_to_rm_path(tmp_path, monkeypatch):
    """Files are not worth async-ing — delegate to rm_path so the honesty
    contract for single-file deletion stays untouched."""
    monkeypatch.setattr(common, "TRASH_BASE", tmp_path / ".trash")

    f = tmp_path / "single.txt"
    f.write_text("hello world")
    expected_size = f.lstat().st_size

    moved, errors = common.rm_path_async(f)

    assert errors == 0
    assert moved == expected_size
    assert not f.exists()
    # Trash dir should NOT have been used for a single-file delete.
    assert not (tmp_path / ".trash").exists()


def test_rm_path_async_missing_path_returns_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "TRASH_BASE", tmp_path / ".trash")

    moved, errors = common.rm_path_async(tmp_path / "nope")

    assert (moved, errors) == (0, 0)


def test_rm_path_async_falls_back_when_rename_fails(tmp_path, monkeypatch):
    """If os.rename raises OSError (cross-fs, perms, etc.), rm_path_async
    must fall back to synchronous rm_path so the bytes_freed value is
    still honestly the bytes that left disk."""
    monkeypatch.setattr(common, "TRASH_BASE", tmp_path / ".trash")

    target = tmp_path / "cache"
    target.mkdir()
    (target / "x.txt").write_text("payload")

    def _boom(*_args, **_kwargs):
        raise OSError("simulated cross-filesystem rename failure")

    with patch("maidbook.common.os.rename", side_effect=_boom), \
         patch("maidbook.common.path_size", return_value=2048):
        moved, errors = common.rm_path_async(target)

    assert moved == 2048
    assert errors == 0
    assert not target.exists(), "fallback rm_path should still delete the tree"


def test_reap_pending_trash_clears_orphans(tmp_path, monkeypatch):
    """Pre-populate trash with leftover dirs from a 'crashed previous
    session' and verify reap_pending_trash deletes them all."""
    trash_root = tmp_path / ".trash"
    monkeypatch.setattr(common, "TRASH_BASE", trash_root)
    trash_root.mkdir()

    for i in range(3):
        sub = trash_root / f"orphan-{i}"
        sub.mkdir()
        (sub / "leftover.txt").write_text("from a prior session")

    reaped = common.reap_pending_trash()

    assert reaped == 3
    # All orphans gone, trash root itself can stay (cheap to recreate)
    remaining = [p for p in trash_root.iterdir() if p.is_dir()]
    assert remaining == []


def test_reap_pending_trash_no_trash_dir_is_no_op(tmp_path, monkeypatch):
    """If TRASH_BASE doesn't exist (clean install), reap returns 0 quietly."""
    monkeypatch.setattr(common, "TRASH_BASE", tmp_path / "never_created")

    assert common.reap_pending_trash() == 0


@patch("subprocess.run")
def test_is_app_running(mock_run):
    # App running match .app
    mock_proc = MagicMock()
    mock_proc.stdout = "123 /Applications/Safari.app/Contents/MacOS/Safari\n"
    mock_run.return_value = mock_proc
    assert common.is_app_running("Safari")

    # App running match binary
    mock_proc.stdout = "456 /usr/local/bin/node\n"
    assert common.is_app_running("node")

    # App not running
    mock_proc.stdout = "789 /some/other/app\n"
    assert not common.is_app_running("Safari")

    # Subprocess error
    mock_run.side_effect = subprocess.SubprocessError
    assert not common.is_app_running("Safari")
