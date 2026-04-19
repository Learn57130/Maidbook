import pytest
import os
import subprocess
from pathlib import Path
from maidbook import __main__
from unittest.mock import patch, MagicMock

def test_argparse_cli_mode(monkeypatch):
    monkeypatch.setattr("sys.argv", ["maidbook", "--cli", "--dry-run"])
    with patch("maidbook.__main__.run_cli") as mock_cli:
        assert __main__.main() == 0
        mock_cli.assert_called_with(dry_run=True, clean_all=False)

def test_argparse_version(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["maidbook", "--version"])
    with pytest.raises(SystemExit) as e:
        __main__.main()
    assert e.value.code == 0
    out, err = capsys.readouterr()
    from maidbook import __version__
    assert __version__ in out

@patch("maidbook.__main__.run_tui")
def test_argparse_tui_mode(mock_tui, monkeypatch):
    monkeypatch.setattr("sys.argv", ["maidbook"])
    assert __main__.main() == 0
    mock_tui.assert_called_once()
