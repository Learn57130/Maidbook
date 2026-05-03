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
    # Belt-and-braces: prune the global list so cross-test residue doesn't
    # accumulate.
    with common._REAPER_LOCK:
        common._REAPER_THREADS[:] = [t for t in common._REAPER_THREADS if t.is_alive()]


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


def test_reap_pending_trash_async_does_not_block(tmp_path, monkeypatch):
    """[P2 regression] Startup orphan-reap must NOT block the caller.

    Pre-populate trash with leftover dirs and verify reap_pending_trash_async
    returns essentially instantly (well under the actual rmtree cost). The
    daemon thread continues in the background; the call site (main()) should
    be free to start the UI immediately.
    """
    import time as _time
    trash_root = tmp_path / ".trash"
    monkeypatch.setattr(common, "TRASH_BASE", trash_root)
    trash_root.mkdir()

    # Build a few orphan subdirs with enough files to make a sync rmtree
    # measurably slow.
    for i in range(3):
        sub = trash_root / f"orphan-{i}"
        sub.mkdir()
        for j in range(200):
            (sub / f"f_{j:03d}.bin").write_bytes(b"x" * 64)

    t0 = _time.monotonic()
    thread = common.reap_pending_trash_async()
    elapsed = _time.monotonic() - t0

    assert thread is not None, "should return a thread when trash exists"
    assert elapsed < 0.05, (
        f"async reap took {elapsed*1000:.0f}ms — must return immediately, "
        "the whole point is to not block startup"
    )

    # Verify it eventually does the work
    thread.join(timeout=10.0)
    assert not thread.is_alive(), "background reaper should finish in reasonable time"
    remaining = [p for p in trash_root.iterdir() if p.is_dir()]
    assert remaining == [], "background reaper should clear orphans like the sync version"


def test_reap_pending_trash_async_no_trash_dir_returns_none(tmp_path, monkeypatch):
    """When there's nothing to reap (clean install), don't even spawn a
    thread — just return None so callers can skip the work entirely."""
    monkeypatch.setattr(common, "TRASH_BASE", tmp_path / "never_created")

    assert common.reap_pending_trash_async() is None


def test_wait_for_pending_reaps_reports_pending_bytes(tmp_path, monkeypatch):
    """[P1 regression] wait_for_pending_reaps must surface bytes-still-in-trash
    so the post-clean summary can honestly distinguish "freed" from
    "scheduled for deletion".
    """
    trash_root = tmp_path / ".trash"
    monkeypatch.setattr(common, "TRASH_BASE", trash_root)
    trash_root.mkdir()

    # Simulate a clean that mv'd content into trash but the reaper hasn't
    # touched it yet.
    leftover = trash_root / "still_pending"
    leftover.mkdir()
    (leftover / "data.bin").write_bytes(b"x" * 1024)

    # No reaper threads scheduled; wait should return immediately and report
    # the bytes that are *still on disk*.
    alive, pending = common.wait_for_pending_reaps(timeout=0.1)

    assert alive == 0, "no in-flight reapers should be running"
    assert pending > 0, (
        "must report pending bytes when content is sitting in trash — "
        "this is the honesty-contract fix for the over-reporting bug"
    )


def test_wait_for_pending_reaps_zero_pending_when_trash_empty(tmp_path, monkeypatch):
    """When the reaper finished and the trash is empty, pending must be 0
    so callers can render the simple "Freed: X" message."""
    trash_root = tmp_path / ".trash"
    monkeypatch.setattr(common, "TRASH_BASE", trash_root)
    trash_root.mkdir()

    alive, pending = common.wait_for_pending_reaps(timeout=0.1)

    assert alive == 0
    assert pending == 0


def test_async_batch_excludes_orphans_from_other_sessions(tmp_path, monkeypatch):
    """[P3 regression] async_batch() must only count trash subdirs created
    inside its own context — orphans the startup reaper is draining must
    NOT be charged against the current clean batch.

    Failure mode being prevented:
      previous session crashed, left 5 GB orphans in trash;
      startup reaper still draining them;
      current clean mvs 1 GB into trash;
      naive `trash_pending_bytes()` would return 6 GB,
      making the current clean appear to have freed -4 GB → 0.
    """
    trash_root = tmp_path / ".trash"
    monkeypatch.setattr(common, "TRASH_BASE", trash_root)
    trash_root.mkdir()

    # Simulate orphans from a previous session sitting in trash, mid-reap.
    orphan = trash_root / "orphan_from_previous_session"
    orphan.mkdir()
    (orphan / "leftover.bin").write_bytes(b"x" * 8192)

    # Sanity: aggregate trash_pending_bytes sees the orphan.
    aggregate_before = common.trash_pending_bytes()
    assert aggregate_before > 0

    # Now do an async-batched "current clean" of a fresh dir.
    fresh = tmp_path / "fresh_cache"
    fresh.mkdir()
    (fresh / "f.bin").write_bytes(b"y" * 4096)

    # Mock _schedule_reap to a no-op so the trash subdir stays on disk
    # long enough for the test to measure it. (In production the daemon
    # reaper would race the assertion.)
    with patch("maidbook.common._schedule_reap"):
        with common.async_batch() as batch_pending_bytes:
            moved, errors = common.rm_path_async(fresh)
            batch_pending = batch_pending_bytes()

    assert errors == 0
    # Critical assertion: batch_pending ONLY reflects the fresh batch,
    # not the orphan. The orphan's bytes belong to the previous session
    # and the startup reaper's responsibility, not this batch's.
    assert batch_pending < aggregate_before, (
        f"async_batch() must exclude orphans (aggregate={aggregate_before}, "
        f"batch={batch_pending}). Charging this batch with old orphans "
        f"causes Freed: 0 reports for cleans that actually reclaimed space."
    )
    assert batch_pending > 0, (
        "the fresh dir was just mv'd into trash and not yet reaped, "
        "so its bytes should still register as pending for this batch"
    )

    _wait_for_reapers()


def test_async_batch_yields_zero_when_no_async_calls(tmp_path, monkeypatch):
    """An async_batch with no rm_path_async calls inside it has zero
    pending bytes — even if other content sits in trash from elsewhere."""
    trash_root = tmp_path / ".trash"
    monkeypatch.setattr(common, "TRASH_BASE", trash_root)
    trash_root.mkdir()
    (trash_root / "unrelated").mkdir()
    (trash_root / "unrelated" / "x.bin").write_bytes(b"z" * 4096)

    with common.async_batch() as batch_pending_bytes:
        # No rm_path_async calls inside.
        pass
    # batch_pending callable still works after exit (closure over subdirs)
    # but should report 0 because no async calls happened in this batch.
    assert batch_pending_bytes() == 0


def test_async_batch_nesting_restores_outer_context(tmp_path, monkeypatch):
    """Nested async_batch() calls must restore the outer context on exit
    so the outer batch keeps tracking new rm_path_async calls correctly."""
    trash_root = tmp_path / ".trash"
    monkeypatch.setattr(common, "TRASH_BASE", trash_root)

    outer = tmp_path / "outer_dir"
    outer.mkdir()
    (outer / "f.bin").write_bytes(b"o" * 4096)
    inner = tmp_path / "inner_dir"
    inner.mkdir()
    (inner / "f.bin").write_bytes(b"i" * 4096)

    # Mock _schedule_reap so the trash subdirs stay on disk long enough
    # to measure (otherwise the daemon reaper races the assertions).
    with patch("maidbook.common._schedule_reap"):
        with common.async_batch() as outer_pending:
            common.rm_path_async(outer)
            with common.async_batch() as inner_pending:
                common.rm_path_async(inner)
                assert inner_pending() > 0
            # After inner exits, the outer batch should still track 'outer'
            # (and only 'outer' — not the inner one).
            assert outer_pending() > 0

    _wait_for_reapers()


def test_trash_pending_bytes_reflects_disk(tmp_path, monkeypatch):
    """trash_pending_bytes is what powers the 'still finalizing' UI hint;
    ensure it tracks what's actually on disk."""
    trash_root = tmp_path / ".trash"
    monkeypatch.setattr(common, "TRASH_BASE", trash_root)

    # No trash dir -> 0
    assert common.trash_pending_bytes() == 0

    # Create some content -> non-zero (du gives block-rounded, just check >0)
    trash_root.mkdir()
    sub = trash_root / "queued"
    sub.mkdir()
    (sub / "f.bin").write_bytes(b"x" * 4096)
    assert common.trash_pending_bytes() > 0


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
