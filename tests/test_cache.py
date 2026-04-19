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
