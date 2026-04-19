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
