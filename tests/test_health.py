import pytest
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
from maidbook import health

@patch("maidbook.health.Path.exists")
@patch("maidbook.health.plistlib.load")
@patch("maidbook.health.Path.stat")
@patch("maidbook.health.time.time")
def test_scan_xprotect_ok(mock_time, mock_stat, mock_load, mock_exists):
    mock_exists.return_value = True
    mock_load.return_value = {"CFBundleShortVersionString": "2166"}

    mock_stat_result = MagicMock()
    mock_stat_result.st_mtime = 1000000.0
    mock_stat.return_value = mock_stat_result

    # age = 10 days
    mock_time.return_value = 1000000.0 + (86400 * 10)

    # We must patch open() too, but just patch the whole method flow
    with patch("builtins.open", MagicMock()):
        findings = health.scan_xprotect()

    assert len(findings) == 1
    assert findings[0].severity == "ok"
    assert "2166" in findings[0].detail

@patch("maidbook.health.Path.exists")
@patch("maidbook.health.plistlib.load")
@patch("maidbook.health.Path.stat")
@patch("maidbook.health.time.time")
def test_scan_xprotect_stale(mock_time, mock_stat, mock_load, mock_exists):
    mock_exists.return_value = True
    mock_load.return_value = {"CFBundleShortVersionString": "2160"}

    mock_stat_result = MagicMock()
    mock_stat_result.st_mtime = 1000000.0
    mock_stat.return_value = mock_stat_result

    # age = 50 days (> 45 days)
    mock_time.return_value = 1000000.0 + (86400 * 50)

    with patch("builtins.open", MagicMock()):
        findings = health.scan_xprotect()

    assert len(findings) == 1
    assert findings[0].severity == "caution"
    assert "stale" in findings[0].title

def test_scan_malware_heuristics(monkeypatch, tmp_path):
    monkeypatch.setattr(health, "HOME", tmp_path)
    monkeypatch.setattr(health, "KNOWN_ADWARE_SIGNS", [
        ("FakeMalware", [str(tmp_path / "FakeMalware.app")])
    ])

    # Setup adware
    (tmp_path / "FakeMalware.app").mkdir()

    # Setup unknown launch agent
    la_dir = tmp_path / "Library/LaunchAgents"
    la_dir.mkdir(parents=True)
    (la_dir / "com.unknown.evil.plist").touch()

    # Setup known launch agent
    (la_dir / "com.apple.good.plist").touch()
    (la_dir / "com.google.chrome.plist").touch()

    findings = health.scan_malware_heuristics(agent_dirs=[la_dir])

    assert len(findings) == 2

    risk = next(f for f in findings if f.severity == "risk")
    assert "FakeMalware" in risk.title

    review = next(f for f in findings if f.severity == "review")
    assert "com.unknown.evil" in review.title

@patch("maidbook.health._has_quarantine_xattr")
def test_scan_quarantine(mock_xattr, tmp_path, monkeypatch):
    monkeypatch.setattr(health, "HOME", tmp_path)

    dl = tmp_path / "Downloads"
    dl.mkdir()
    f1 = dl / "file1.txt"
    f1.write_text("a")
    f2 = dl / "file2.txt"
    f2.write_text("b")

    def _xattr(p):
        return p == f1
    mock_xattr.side_effect = _xattr

    findings = health.scan_quarantine()
    assert len(findings) == 1
    assert "file1.txt" in findings[0].title
    assert findings[0].severity == "info"

@patch("maidbook.health._run_quiet")
def test_scan_vulnerabilities(mock_run):
    def run_side_effect(cmd, timeout=60):
        if "pip-audit" in cmd[0]:
            # Vulnerable python package
            return 0, '{"vulnerabilities": [{"name": "requests"}]}', ""
        if "brew" in cmd[0]:
            # Clean brew
            return 0, "", ""
        if "npm" in cmd[0]:
            # Clean npm
            return 0, "{}", ""
        return 127, "", ""

    mock_run.side_effect = run_side_effect

    findings = health.scan_vulnerabilities()
    assert len(findings) == 3

    vuln = next(f for f in findings if "pip-audit" in f.title)
    assert vuln.severity == "caution"
    assert "1 known" in vuln.title

    brew = next(f for f in findings if "brew" in f.title)
    assert brew.severity == "ok"

    npm = next(f for f in findings if "npm" in f.title)
    assert npm.severity == "ok"
