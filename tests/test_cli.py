import json
import pytest
from unittest.mock import patch
from maidbook import cli, common

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


def test_tui_scan_worker_isolates_runtime_errors(monkeypatch, tmp_path):
    from maidbook import common
    from maidbook.cache import Category
    from maidbook.tui import TUI

    # Pin stats to a tmp file so scan_worker's bloat snapshot doesn't
    # touch the user's real ~/.maidbook/stats.json.
    monkeypatch.setattr(common, "MAIDBOOK_DIR", tmp_path)
    monkeypatch.setattr(common, "STATS_PATH", tmp_path / "stats.json")

    good = Category(
        "good", "Good", "G", "good",
        lambda: 2048,
        lambda dry: (0, 0, "noop"),
    )
    bad = Category(
        "bad", "Bad", "B", "bad",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        lambda dry: (0, 0, "noop"),
    )

    tui = TUI(None, [good, bad])
    tui.mode = "scan"

    tui.scan_worker()

    assert tui.scan_done is True
    assert tui.mode == "select"
    assert tui.sizes["good"] == 2048
    assert tui.sizes["bad"] == 0

    # And the bloat snapshot landed in the tmp stats file.
    stats = common.load_stats()
    assert len(stats["bloat_velocity"]) == 1
    assert stats["bloat_velocity"][0]["total_cache_size"] == 2048


def test_tui_clean_worker_records_session(monkeypatch, tmp_path):
    """Regression: clean_worker must call record_session so Stats screen
    actually shows freed bytes after a TUI clean (not just --cron)."""
    from maidbook import common
    from maidbook.cache import Category
    from maidbook.tui import TUI

    monkeypatch.setattr(common, "MAIDBOOK_DIR", tmp_path)
    monkeypatch.setattr(common, "STATS_PATH", tmp_path / "stats.json")
    monkeypatch.setattr(common, "TRASH_BASE", tmp_path / "trash")

    cat = Category(
        "test-cat", "TestCat", "T", "test category",
        lambda: 4096,
        lambda dry: (4096, 0, "cleaned"),
    )
    tui = TUI(None, [cat])
    tui.sizes = {"test-cat": 4096}
    tui.selected = {"test-cat"}
    tui.dry_run = False
    tui.mode = "clean"

    tui.clean_worker()

    assert tui.mode == "done"
    stats = common.load_stats()
    assert stats["total_freed_all_time"] == 4096
    assert len(stats["sessions"]) == 1
    assert stats["sessions"][0]["freed"] == 4096
    assert "TestCat" in stats["sessions"][0]["categories"]


def test_tui_clean_worker_dry_run_does_not_record(monkeypatch, tmp_path):
    """Dry-run cleans must NOT pollute the stats file."""
    from maidbook import common
    from maidbook.cache import Category
    from maidbook.tui import TUI

    monkeypatch.setattr(common, "MAIDBOOK_DIR", tmp_path)
    monkeypatch.setattr(common, "STATS_PATH", tmp_path / "stats.json")
    monkeypatch.setattr(common, "TRASH_BASE", tmp_path / "trash")

    cat = Category(
        "test-cat", "TestCat", "T", "test category",
        lambda: 4096,
        lambda dry: (4096, 0, "would clean"),
    )
    tui = TUI(None, [cat])
    tui.sizes = {"test-cat": 4096}
    tui.selected = {"test-cat"}
    tui.dry_run = True
    tui.mode = "clean"

    tui.clean_worker()

    stats = common.load_stats()
    assert stats["total_freed_all_time"] == 0
    assert stats["sessions"] == []


# ---------------------------------------------------------------------------
# Cron mode tests
# ---------------------------------------------------------------------------

@patch("maidbook.cli.build_categories")
def test_run_cron_dry_run(mock_build, capsys, monkeypatch, tmp_path):
    from maidbook.cache import Category

    monkeypatch.setattr(common, "MAIDBOOK_DIR", tmp_path)
    monkeypatch.setattr(common, "STATS_PATH", tmp_path / "stats.json")
    monkeypatch.setattr(common, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(common, "WHITELIST_PATH", tmp_path / "whitelist.json")
    monkeypatch.setattr(common, "SCHEDULE_CONFIG_PATH", tmp_path / "schedule.json")

    cat = Category("c1", "Test1", "I1", "Desc1",
                    lambda: 1024, lambda dry: (1024, 0, "would remove"),
                    safety="safe")
    mock_build.return_value = [cat]

    cli.run_cron(dry_run=True)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["dry_run"] is True
    assert data["total_freed_bytes"] == 1024


@patch("maidbook.cli.build_categories")
def test_run_cron_skips_whitelisted(mock_build, capsys, monkeypatch, tmp_path):
    from maidbook.cache import Category

    monkeypatch.setattr(common, "MAIDBOOK_DIR", tmp_path)
    monkeypatch.setattr(common, "STATS_PATH", tmp_path / "stats.json")
    monkeypatch.setattr(common, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(common, "WHITELIST_PATH", tmp_path / "whitelist.json")
    monkeypatch.setattr(common, "SCHEDULE_CONFIG_PATH", tmp_path / "schedule.json")

    cat1 = Category("c1", "Keep", "I1", "Desc1",
                     lambda: 1024, lambda dry: (1024, 0, "cleaned"),
                     safety="safe")
    cat2 = Category("c2", "Clean", "I2", "Desc2",
                     lambda: 2048, lambda dry: (2048, 0, "cleaned"),
                     safety="safe")
    mock_build.return_value = [cat1, cat2]
    common.save_whitelist({"c1"})

    cli.run_cron(dry_run=True)
    out = capsys.readouterr().out
    data = json.loads(out)
    cat_names = [e["category"] for e in data["categories_cleaned"]]
    assert "Keep" not in cat_names
    assert "Clean" in cat_names


@patch("maidbook.cli.build_categories")
def test_run_cron_uses_selected_keys_when_configured(mock_build, capsys, monkeypatch, tmp_path):
    """When selected_keys is saved, cron cleans only those categories."""
    from maidbook.cache import Category

    monkeypatch.setattr(common, "MAIDBOOK_DIR", tmp_path)
    monkeypatch.setattr(common, "STATS_PATH", tmp_path / "stats.json")
    monkeypatch.setattr(common, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(common, "WHITELIST_PATH", tmp_path / "whitelist.json")
    monkeypatch.setattr(common, "SCHEDULE_CONFIG_PATH", tmp_path / "schedule.json")

    cat1 = Category("pip", "pip cache", "P", "pip",
                     lambda: 1024, lambda dry: (1024, 0, "cleaned"),
                     safety="safe")
    cat2 = Category("npm", "npm cache", "N", "npm",
                     lambda: 2048, lambda dry: (2048, 0, "cleaned"),
                     safety="safe")
    cat3 = Category("art", "node_modules", "A", "artifact",
                     lambda: 65536, lambda dry: (65536, 0, "removed"),
                     safety="caution")
    mock_build.return_value = [cat1, cat2, cat3]

    # No schedule configured → clean all non-whitelisted
    cli.run_cron(dry_run=True)
    out = capsys.readouterr().out
    data = json.loads(out)
    cat_names = [e["category"] for e in data["categories_cleaned"]]
    assert "pip cache" in cat_names
    assert "npm cache" in cat_names
    assert "node_modules" in cat_names

    # Configure a specific selection (pip + art only, not npm)
    common.save_schedule_config({
        "interval": "weekly", "hour": 3, "minute": 0,
        "selected_keys": ["pip", "art"],
    })
    cli.run_cron(dry_run=True)
    out = capsys.readouterr().out
    data = json.loads(out)
    cat_names = [e["category"] for e in data["categories_cleaned"]]
    assert "pip cache" in cat_names
    assert "node_modules" in cat_names
    assert "npm cache" not in cat_names   # not in selected_keys

    # Whitelist still overrides within the selection
    common.save_whitelist({"art"})
    cli.run_cron(dry_run=True)
    out = capsys.readouterr().out
    data = json.loads(out)
    cat_names = [e["category"] for e in data["categories_cleaned"]]
    assert "pip cache" in cat_names
    assert "node_modules" not in cat_names  # whitelisted


def test_show_history_empty(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(common, "LOG_DIR", tmp_path / "no-logs")
    cli.show_history()
    out = capsys.readouterr().out
    assert "No history" in out


def test_show_history_with_data(capsys, monkeypatch, tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "2026-05-13.log").write_text("[2026-05-13] test entry\n  Freed: 1.0 MB\n")
    monkeypatch.setattr(common, "LOG_DIR", log_dir)

    cli.show_history()
    out = capsys.readouterr().out
    assert "test entry" in out


def test_show_stats_empty(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(common, "STATS_PATH", tmp_path / "stats.json")
    cli.show_stats()
    out = capsys.readouterr().out
    assert "0 B" in out


def test_show_stats_with_data(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(common, "MAIDBOOK_DIR", tmp_path)
    monkeypatch.setattr(common, "STATS_PATH", tmp_path / "stats.json")

    common.record_session(1_048_576, ["pip"], 3.0)
    cli.show_stats()
    out = capsys.readouterr().out
    assert "1.0 MB" in out


def test_unschedule_clears_schedule_config(monkeypatch, tmp_path):
    """unschedule_cron must remove schedule.json so stale selected_keys
    don't drive the next cron run."""
    from unittest.mock import patch
    from maidbook import cli, common

    monkeypatch.setattr(common, "MAIDBOOK_DIR", tmp_path)
    monkeypatch.setattr(common, "STATS_PATH", tmp_path / "stats.json")
    monkeypatch.setattr(common, "SCHEDULE_CONFIG_PATH", tmp_path / "schedule.json")

    # Write a fake plist and schedule config
    plist_path = tmp_path / "fake.plist"
    plist_path.write_text("<plist/>")
    monkeypatch.setattr(cli, "LAUNCHD_PLIST_PATH", plist_path)

    common.save_schedule_config({"interval": "weekly", "hour": 3, "minute": 0,
                                  "selected_keys": ["pip", "npm"]})
    assert common.SCHEDULE_CONFIG_PATH.exists()

    with patch("subprocess.run"):
        cli.unschedule_cron()

    assert not plist_path.exists()
    assert not common.SCHEDULE_CONFIG_PATH.exists()


def test_schedule_status_returns_empty_when_no_plist(monkeypatch, tmp_path):
    """schedule_status returns '' when no plist exists."""
    monkeypatch.setattr(cli, "LAUNCHD_PLIST_PATH", tmp_path / "nonexistent.plist")
    assert cli.schedule_status() == ""


def test_unschedule_no_plist_is_noop(capsys, monkeypatch, tmp_path):
    """unschedule_cron prints 'No scheduled clean' when plist is absent."""
    monkeypatch.setattr(cli, "LAUNCHD_PLIST_PATH", tmp_path / "nonexistent.plist")
    cli.unschedule_cron()
    out = capsys.readouterr().out
    assert "No scheduled clean" in out
