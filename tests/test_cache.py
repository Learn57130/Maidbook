import pytest
import os
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
from maidbook import cache

def test_classify_discovered():
    assert cache.classify_discovered("com.apple.Safari")[0] == "safe"
    assert cache.classify_discovered("node-gyp")[0] == "safe"
    assert cache.classify_discovered("com.spotify.client")[0] == "caution"
    assert cache.classify_discovered("UnknownApp")[0] == "review"

@patch("subprocess.run")
def test_clean_pip(mock_run, monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "HOME", tmp_path)
    mock_run.return_value = MagicMock(returncode=0)

    # dry run
    freed, errs, msg = cache.clean_pip(True)
    assert errs == 0
    assert "would run" in msg

    # real run
    freed, errs, msg = cache.clean_pip(False)
    assert errs == 0
    assert "pip cache purged" in msg

@patch("subprocess.run")
def test_clean_brew(mock_run, monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "HOME", tmp_path)

    mock_proc = MagicMock(returncode=0)
    mock_proc.stdout = "This operation has freed approximately 50MB of disk space.\n"
    mock_run.return_value = mock_proc

    freed, errs, msg = cache.clean_brew(False)
    assert freed == int(50.0 * (1 << 20))
    assert errs == 0

@patch("maidbook.cache.path_size")
@patch("maidbook.cache.is_app_running")
def test_browser_cleaner(mock_is_app, mock_cache_size, monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "HOME", tmp_path)
    mock_cache_size.return_value = 0

    # create fake browser profile
    profile = tmp_path / "Library/Caches/BraveSoftware/Brave-Browser"
    profile.mkdir(parents=True)
    (profile / "Cache").mkdir()
    (profile / "Code Cache").mkdir()
    (profile / "ImportantProfileData").mkdir()

    scan, clean = cache.make_browser_cleaner("Brave", "Brave Browser", "Library/Caches/BraveSoftware/Brave-Browser")

    # test scan
    # with 2 empty dirs
    assert scan() == 0

    # test clean running app
    mock_is_app.return_value = True
    freed, errs, msg = clean(False)
    assert "is running -- skipped" in msg
    assert (profile / "Cache").exists()

    # test clean
    mock_is_app.return_value = False
    freed, errs, msg = clean(False)
    assert not (profile / "Cache").exists()
    assert not (profile / "Code Cache").exists()
    assert (profile / "ImportantProfileData").exists()

def test_discover_other_caches(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "HOME", tmp_path)

    caches = tmp_path / "Library/Caches"
    caches.mkdir(parents=True)

    (caches / "pip").mkdir() # Covered
    (caches / "Homebrew").mkdir() # Covered
    (caches / "com.spotify.client").mkdir() # Discovered
    (caches / ".hidden").mkdir() # Ignored

    found = cache.discover_other_caches()
    assert len(found) == 1
    assert found[0][0] == "com.spotify.client"


# ---------------------------------------------------------------------------
# Dev artifact discovery tests
# ---------------------------------------------------------------------------

def test_discover_node_modules(tmp_path):
    project = tmp_path / "my-app"
    project.mkdir()
    nm = project / "node_modules"
    nm.mkdir()
    (nm / "react").mkdir()

    found = cache.discover_dev_artifacts(roots=[tmp_path])
    types = [f[0] for f in found]
    assert "node_modules" in types
    match = [f for f in found if f[0] == "node_modules"][0]
    assert match[1] == nm
    assert "my-app" in match[2]


def test_discover_pycache(tmp_path):
    project = tmp_path / "pyproject"
    project.mkdir()
    pc = project / "__pycache__"
    pc.mkdir()

    found = cache.discover_dev_artifacts(roots=[tmp_path])
    types = [f[0] for f in found]
    assert "__pycache__" in types
    match = [f for f in found if f[0] == "__pycache__"][0]
    assert match[3] == "safe"


def test_discover_venv(tmp_path):
    project = tmp_path / "ml-project"
    project.mkdir()
    (project / "venv").mkdir()
    (project / ".venv").mkdir()

    found = cache.discover_dev_artifacts(roots=[tmp_path])
    types = [f[0] for f in found]
    assert "venv" in types
    assert ".venv" in types


def test_discover_build_needs_sibling_marker(tmp_path):
    """build/ and dist/ are only flagged if a project marker file exists."""
    project = tmp_path / "generic-folder"
    project.mkdir()
    (project / "build").mkdir()
    (project / "dist").mkdir()

    found = cache.discover_dev_artifacts(roots=[tmp_path])
    types = [f[0] for f in found]
    assert "build" not in types
    assert "dist" not in types

    (project / "package.json").write_text("{}")
    found = cache.discover_dev_artifacts(roots=[tmp_path])
    types = [f[0] for f in found]
    assert "build" in types
    assert "dist" in types


def test_discover_respects_maidbook_keep(tmp_path):
    project = tmp_path / "keep-me"
    project.mkdir()
    (project / "node_modules").mkdir()
    (project / ".maidbook-keep").write_text("")

    found = cache.discover_dev_artifacts(roots=[tmp_path])
    paths = [f[1] for f in found]
    assert not any("keep-me" in str(p) for p in paths)


def test_discover_skips_symlinks(tmp_path):
    project = tmp_path / "real"
    project.mkdir()
    (project / "node_modules").mkdir()
    link = tmp_path / "linked"
    link.symlink_to(project)

    found = cache.discover_dev_artifacts(roots=[tmp_path])
    real_paths = [f[1] for f in found if not f[1].is_symlink()]
    assert len(real_paths) == 1


def test_discover_max_depth(tmp_path):
    """Artifacts nested beyond _MAX_ARTIFACT_DEPTH are not found."""
    d = tmp_path
    for i in range(cache._MAX_ARTIFACT_DEPTH + 2):
        d = d / f"level{i}"
        d.mkdir()
    (d / "node_modules").mkdir()

    found = cache.discover_dev_artifacts(roots=[tmp_path])
    assert not any(f[0] == "node_modules" and "level" in str(f[1]) and
                   str(f[1]).count("level") > cache._MAX_ARTIFACT_DEPTH
                   for f in found)


def test_discover_nonexistent_root(tmp_path):
    found = cache.discover_dev_artifacts(roots=[tmp_path / "does-not-exist"])
    assert found == []


def test_artifact_cleaner_dry_run(tmp_path):
    target = tmp_path / "node_modules"
    target.mkdir()
    (target / "pkg").mkdir()

    scan_fn, clean_fn = cache.make_artifact_cleaner(target)
    freed, errs, msg = clean_fn(True)
    assert "would remove" in msg
    assert target.exists()


def test_build_categories_includes_artifacts(monkeypatch, tmp_path):
    # Pin ARTIFACT_SCAN_ROOTS to the tmp tree so the test is fully isolated
    # from the real filesystem.  Without this, the test relied on the user
    # having real node_modules dirs in ~/Developer — fragile and now broken
    # after a cron clean.
    monkeypatch.setattr(cache, "HOME", tmp_path)
    monkeypatch.setattr(cache, "ARTIFACT_SCAN_ROOTS", [tmp_path / "Developer"])
    project = tmp_path / "Developer" / "test-project"
    project.mkdir(parents=True)
    (project / "node_modules").mkdir()

    cats = cache.build_categories()
    artifact_cats = [c for c in cats if "dev-artifacts" in c.tags]
    assert len(artifact_cats) >= 1
    assert any("node_modules" in c.name for c in artifact_cats)
