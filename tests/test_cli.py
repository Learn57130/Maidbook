import pytest
from unittest.mock import patch
from maidbook import cli

@patch("maidbook.cli.build_categories")
@patch("builtins.print")
def test_run_cli(mock_print, mock_build):
    import io
    from maidbook.cache import Category

    mock_scan = lambda: 1024
    mock_clean = lambda dry: (1024, 0, "cleaned")

    cat1 = Category("c1", "Test1", "I1", "Desc1", mock_scan, mock_clean)
    mock_build.return_value = [cat1]

    # Dry run, not clean all
    cli.run_cli(dry_run=True, clean_all=False)
    assert any("Test1" in str(c) for c in mock_print.mock_calls)

    mock_print.reset_mock()
    # Clean all
    cli.run_cli(dry_run=False, clean_all=True)
    assert any("cleaned" in str(c) for c in mock_print.mock_calls)
