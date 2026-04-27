import pytest
import subprocess
from pathlib import Path
from unittest.mock import patch

# Attempting some injection attacks
def test_path_size_injection():
    from maidbook.common import path_size
    malicious_path = Path("-h")
    # path_size checks if p.exists(), so this returns 0
    assert path_size(malicious_path) == 0

@patch("subprocess.run")
def test_path_size_subprocess_injection(mock_run):
    from maidbook.common import path_size

    # Bypass exists check
    # Note: `Path("; rm -rf /")` normalizes away the trailing slash!
    # Let's use something simple
    path = Path(";/bin/sh")
    with patch.object(Path, 'exists', return_value=True):
        path_size(path)

    # Check that subprocess was called correctly: list format, no shell=True,
    # and a ``--`` separator so the path can never be mis-parsed as a flag.
    mock_run.assert_called_with(
        ["du", "-sk", "--", ";/bin/sh"],
        capture_output=True, text=True, timeout=120
    )


@patch("subprocess.run")
def test_path_size_dashed_filename_not_parsed_as_flag(mock_run):
    """Regression test for audit finding M3.

    A cache folder literally named like a flag (``-rf``, ``--si``) must NOT
    be consumed by ``du`` as an option. The ``--`` end-of-options sentinel
    guarantees this.
    """
    from maidbook.common import path_size
    for hostile in ("-rf", "--si", "-H"):
        mock_run.reset_mock()
        mock_run.return_value = type("R", (), {
            "returncode": 0, "stdout": "42\t/x\n", "stderr": "",
        })()
        with patch.object(Path, "exists", return_value=True):
            path_size(Path(hostile))
        args, _ = mock_run.call_args
        assert args[0] == ["du", "-sk", "--", hostile], (
            f"du must be called with -- separator before {hostile!r}"
        )


def test_rm_path_on_symlink_removes_link_not_target(tmp_path):
    """Core safety invariant: deleting a symlinked cache entry must NOT
    delete the link's target. A malicious or misconfigured symlink inside
    ``~/Library/Caches`` pointing to something important (e.g. ``/Users``,
    ``~/Documents``) must be handled as a link-only unlink.
    """
    from maidbook.common import rm_path

    target = tmp_path / "important_data"
    target.mkdir()
    (target / "file.txt").write_text("precious")

    link = tmp_path / "cache_link"
    link.symlink_to(target)

    rm_path(link)

    assert not link.is_symlink() and not link.exists(), \
        "the symlink itself should have been removed"
    assert target.exists(), "target directory was deleted via the symlink"
    assert (target / "file.txt").exists(), \
        "target contents were deleted via the symlink"


def test_browser_cleaner_preserves_symlinked_cache_target(tmp_path, monkeypatch):
    """Core safety invariant: even if an attacker plants a symlink inside
    a browser profile at the literal name ``Cache`` and points it at a
    sensitive directory, the cleaner must not follow the link.
    """
    from maidbook import cache

    monkeypatch.setattr(cache, "HOME", tmp_path)

    profile = tmp_path / "Library/Caches/BraveSoftware/Brave-Browser/Default"
    profile.mkdir(parents=True)

    docs = tmp_path / "Documents"
    docs.mkdir()
    (docs / "diary.txt").write_text("private")
    (profile / "Cache").symlink_to(docs)

    _scan, clean = cache.make_browser_cleaner(
        "Brave", "Brave Browser", "Library/Caches/BraveSoftware/Brave-Browser",
    )
    monkeypatch.setattr(cache, "is_app_running", lambda _name: False)
    clean(False)

    assert docs.exists(), "symlink target (Documents) was removed"
    assert (docs / "diary.txt").exists(), \
        "symlink target contents were removed"


def test_browser_cleaner_preserves_profile_data_files(tmp_path, monkeypatch):
    """Core safety invariant: the browser cleaner matches only the literal
    directory names ``Cache``, ``Code Cache``, ``GPUCache``. Profile data
    files (``Cookies``, ``History``, ``Login Data``, ``Bookmarks``) must
    never be touched.
    """
    from maidbook import cache

    monkeypatch.setattr(cache, "HOME", tmp_path)

    profile = tmp_path / "Library/Caches/BraveSoftware/Brave-Browser/Default"
    profile.mkdir(parents=True)
    (profile / "Cache").mkdir()
    (profile / "Code Cache").mkdir()
    (profile / "GPUCache").mkdir()
    (profile / "Cookies").write_bytes(b"session=xyz")
    (profile / "History").write_bytes(b"history db")
    (profile / "Login Data").write_bytes(b"login db")
    (profile / "Bookmarks").write_text("{}")

    _scan, clean = cache.make_browser_cleaner(
        "Brave", "Brave Browser", "Library/Caches/BraveSoftware/Brave-Browser",
    )
    monkeypatch.setattr(cache, "is_app_running", lambda _name: False)
    clean(False)

    assert not (profile / "Cache").exists()
    assert not (profile / "Code Cache").exists()
    assert not (profile / "GPUCache").exists()
    assert (profile / "Cookies").exists(), "Cookies file was removed"
    assert (profile / "History").exists(), "History file was removed"
    assert (profile / "Login Data").exists(), "Login Data was removed"
    assert (profile / "Bookmarks").exists(), "Bookmarks was removed"

@patch("subprocess.run")
def test_is_app_running_injection(mock_run):
    from maidbook.common import is_app_running

    # Try to inject via app name
    is_app_running("; echo hacked")

    mock_run.assert_called_with(
        ["pgrep", "-fl", "; echo hacked"],
        capture_output=True, text=True, timeout=3
    )

@patch("subprocess.run")
def test_health_run_quiet_injection(mock_run):
    from maidbook.health import _run_quiet

    _run_quiet(["pip-audit", "--format=json", ";", "ls"])

    mock_run.assert_called_with(
        ["pip-audit", "--format=json", ";", "ls"],
        capture_output=True, text=True, timeout=60
    )


def test_rm_path_reports_partial_deletion_honestly(tmp_path):
    """Regression test for codex P1.

    If part of a tree cannot be removed, ``rm_path`` must NOT claim the
    full pre-deletion size as bytes_freed. It must report only what
    actually went away, and surface the failure count via ``errors``.
    """
    import os
    from maidbook.common import rm_path

    root = tmp_path / "cache_root"
    root.mkdir()
    deletable = root / "regular.bin"
    deletable.write_bytes(b"x" * 4096)

    # A read-only subdir whose unlink will fail — simulates the kind of
    # protected file (system-owned cache, locked DB, etc.) the cleaner
    # is expected to walk past honestly rather than over-report.
    locked_dir = root / "locked"
    locked_dir.mkdir()
    locked_file = locked_dir / "stuck.bin"
    locked_file.write_bytes(b"y" * 4096)
    # Strip write perm on the parent so the file inside cannot be unlinked.
    os.chmod(locked_dir, 0o500)
    try:
        freed, errors = rm_path(root)

        # Honest reporting: errors > 0, and freed cannot exceed what
        # actually went away.
        assert errors > 0, "rmtree onerror callback must propagate"
        assert locked_file.exists(), "fixture sanity: locked file should still be present"
        # The locked file's bytes must NOT be counted as freed.
        assert freed < 4096 * 2, (
            f"freed={freed} but the locked file's bytes are still on disk"
        )
    finally:
        # Restore perms so pytest can clean up tmp_path.
        os.chmod(locked_dir, 0o700)


def test_cli_run_isolates_per_category_scan_failures(tmp_path, monkeypatch, capsys):
    """Regression test for codex P3.

    A single scan() raising must NOT take down the rest of the CLI run.
    The row should appear with a ``?`` size and an error note, and other
    categories should continue to print normally.
    """
    from maidbook import cli
    from maidbook.cache import Category

    def good_scan() -> int:
        return 2048

    def bad_scan() -> int:
        raise RuntimeError("simulated scan failure")

    def noop_clean(_dry):
        return 0, 0, "noop"

    cats = [
        Category("ok", "good-cat", "OK", "good description",
                 good_scan, noop_clean, safety="safe", path_hint="~/ok"),
        Category("bad", "bad-cat", "!!", "bad description",
                 bad_scan, noop_clean, safety="review", path_hint="~/bad"),
    ]
    monkeypatch.setattr(cli, "build_categories", lambda: cats)

    cli.run_cli(dry_run=True, clean_all=False)

    out = capsys.readouterr().out
    assert "good-cat" in out, "the good category must still print"
    assert "bad-cat" in out, "the failing category must still appear as a row"
    assert "?" in out, "failing row should render `?` for size"
    assert "scan error" in out, "failing row should annotate the error inline"
