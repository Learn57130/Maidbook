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

    # Check that subprocess was called correctly (list format, no shell=True)
    mock_run.assert_called_with(
        ["du", "-sk", ";/bin/sh"],
        capture_output=True, text=True, timeout=120
    )

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
