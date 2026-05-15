"""Plain CLI fallback for machines without a working curses terminal,
or when scripting output is preferred (``maidbook --cli --dry-run``).

Also provides headless cron mode (``maidbook --cron``) and history
viewer (``maidbook --history``).
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime

import shutil
import sys

from .cache import Category, build_categories
from . import common as _common
from .common import (
    APP_NAME, APP_TAGLINE, human, is_app_running,
    load_whitelist, append_cron_log, record_session,
    LAUNCHD_LABEL, LAUNCHD_PLIST_PATH, LOG_DIR,
    load_schedule_config, save_schedule_config,
)


def _safe_scan(c: Category) -> tuple[int, str | None]:
    """Run a category's scan, isolating per-category failures.

    The TUI worker pool already isolates each scan future. The plain CLI
    used to evaluate every ``c.scan()`` inside one list comprehension, so a
    single misbehaving category took the whole run down before any output
    printed. This helper gives the CLI the same per-category resilience:
    return ``(size, error_message)`` and let the caller render an error row.
    """
    try:
        return c.scan(), None
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as e:
        return 0, f"scan error: {e}"


def run_cli(dry_run: bool, clean_all: bool) -> None:
    cats = build_categories()
    print(f"{APP_NAME} -- {APP_TAGLINE}\n")
    print("Scanning...\n")
    rows = [(c, *_safe_scan(c)) for c in cats]
    rows.sort(key=lambda x: -x[1])

    print(f"  {'#':>3}  {'Size':>10}  {'Safety':<8}  Name  --  Directory  --  Notes")
    print(f"  {'-'*3}  {'-'*10}  {'-'*8}  {'-'*70}")
    for i, (c, sz, err) in enumerate(rows, 1):
        if err is not None:
            size_cell = "?"
            note = f"{c.description}  [{err}]"
        elif sz:
            size_cell = human(sz)
            note = c.description
        else:
            size_cell = "--"
            note = c.description
        print(f"  {i:>3}  {size_cell:>10}  {c.safety:<8}  "
              f"{c.name}  --  {c.path_hint}  --  {note}")
    total_b = sum(sz for _, sz, _ in rows)
    print(f"\n  Total: {human(total_b)}\n")

    if dry_run and not clean_all:
        return
    whitelist = load_whitelist()
    selected = [c for c, _sz, err in rows
                if err is None and c.key not in whitelist] if clean_all else []
    if not selected:
        print("Use --all to clean, or run without --cli for the TUI.")
        return

    total_freed = 0
    total_errs = 0
    # async_batch() scopes pending-byte accounting to *this* clean's
    # trash subdirs only — orphans from a previous session that the
    # startup reaper is still draining don't get charged here.
    from .common import async_batch, wait_for_pending_reaps
    with async_batch() as batch_pending_bytes:
        for c in selected:
            if c.requires_apps_closed and not dry_run:
                if any(is_app_running(a) for a in c.requires_apps_closed):
                    print(f"  >>  {c.name}: app running, skipped")
                    continue
            try:
                freed, errs, msg = c.clean(dry_run)
            except (OSError, subprocess.SubprocessError, RuntimeError) as e:
                print(f"  !!  {c.name:<22} clean failed: {e}")
                total_errs += 1
                continue
            total_freed += freed
            total_errs += errs
            marker = "OK" if errs == 0 else "!!"
            print(f"  {marker}  {c.name:<22} {human(freed):>10}  {msg}")
        if dry_run:
            print(f"\n  Would free: {human(total_freed)}    Errors: {total_errs}")
        else:
            wait_for_pending_reaps(timeout=5.0)
            pending = batch_pending_bytes()
            actually_freed = max(0, total_freed - pending)
            if pending > 0:
                print(
                    f"\n  Freed: {human(actually_freed)}    "
                    f"({human(pending)} still finalizing in background)    "
                    f"Errors: {total_errs}"
                )
            else:
                print(f"\n  Freed: {human(total_freed)}    Errors: {total_errs}")


def run_cron(dry_run: bool) -> None:
    """Headless cron mode — scan, then clean the persisted category selection.

    Mirrors the TUI flow: same ``build_categories()`` scan, then filters to
    the ``selected_keys`` list that was saved when the user set up the schedule
    via *Schedule cleaning → scan → select*.

    If ``selected_keys`` is empty (no TUI setup run yet), falls back to
    cleaning every non-whitelisted category so plain ``maidbook --cron``
    still works out of the box.
    """
    t0 = time.monotonic()

    cats = build_categories()

    sched_cfg = load_schedule_config()
    selected_keys = set(sched_cfg.get("selected_keys", []))

    if selected_keys:
        # User configured a specific selection — honour it exactly.
        # Keys no longer present in build_categories() are silently skipped.
        whitelist = load_whitelist()
        selected = [
            c for c in cats
            if c.key in selected_keys and c.key not in whitelist
        ]
    else:
        # No schedule setup yet — clean everything not whitelisted.
        whitelist = load_whitelist()
        selected = [c for c in cats if c.key not in whitelist]

    cleaned: list[dict] = []
    total_freed = 0
    total_errs = 0

    from .common import async_batch, wait_for_pending_reaps
    with async_batch() as batch_pending_bytes:
        for c in selected:
            if c.requires_apps_closed and not dry_run:
                if any(is_app_running(a) for a in c.requires_apps_closed):
                    continue
            try:
                freed, errs, msg = c.clean(dry_run)
            except (OSError, subprocess.SubprocessError, RuntimeError):
                total_errs += 1
                continue
            if freed > 0 or errs > 0:
                cleaned.append({
                    "category": c.name,
                    "freed_bytes": freed,
                    "errors": errs,
                    "message": msg,
                })
            total_freed += freed
            total_errs += errs

        if not dry_run:
            wait_for_pending_reaps(timeout=5.0)
            pending = batch_pending_bytes()
            total_freed = max(0, total_freed - pending)

    duration = round(time.monotonic() - t0, 1)

    if not dry_run and total_freed > 0:
        record_session(total_freed, [e["category"] for e in cleaned], duration)

    result = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "categories_cleaned": cleaned,
        "total_freed_bytes": total_freed,
        "total_freed_human": human(total_freed),
        "errors": total_errs,
        "duration_seconds": duration,
    }

    output = json.dumps(result, indent=2)
    print(output)

    if not dry_run and cleaned:
        lines = [
            f"[{result['timestamp']}] Maidbook cron run",
            f"  Duration: {duration}s",
            f"  Freed: {human(total_freed)}  Errors: {total_errs}",
        ]
        for e in cleaned:
            lines.append(f"    {e['category']:<26} {human(e['freed_bytes']):>10}  {e['message']}")
        lines.append("")
        append_cron_log("\n".join(lines) + "\n")


def show_history() -> None:
    """Print the last 10 cron log entries."""
    log_dir = _common.LOG_DIR
    if not log_dir.exists():
        print("No history yet. Run `maidbook --cron` to start logging.")
        return

    log_files = sorted(log_dir.glob("*.log"), reverse=True)
    if not log_files:
        print("No history yet. Run `maidbook --cron` to start logging.")
        return

    print(f"{APP_NAME} — cleaning history\n")
    lines_shown = 0
    max_lines = 80
    for lf in log_files[:10]:
        try:
            text = lf.read_text()
        except OSError:
            continue
        for line in text.splitlines():
            if lines_shown >= max_lines:
                break
            print(line)
            lines_shown += 1
        if lines_shown >= max_lines:
            print(f"\n  (truncated — see {log_dir} for full logs)")
            break


def show_stats() -> None:
    """Print lifetime stats summary."""
    from .common import load_stats
    stats = load_stats()
    total = stats.get("total_freed_all_time", 0)
    sessions = stats.get("sessions", [])
    velocity = stats.get("bloat_velocity", [])

    print(f"{APP_NAME} — lifetime statistics\n")
    print(f"  Total freed (all time):  {human(total)}")
    print(f"  Sessions recorded:       {len(sessions)}")

    if sessions:
        avg_freed = sum(s.get("freed", 0) for s in sessions) // len(sessions)
        print(f"  Average freed / session: {human(avg_freed)}")
        last = sessions[-1]
        print(f"  Last session:            {last.get('date', '?')}  "
              f"freed {human(last.get('freed', 0))}")

    if velocity:
        latest = velocity[-1]
        print(f"\n  Latest cache footprint:  {human(latest.get('total_cache_size', 0))}")
        if len(velocity) >= 2:
            prev = velocity[-2]
            delta = latest.get("total_cache_size", 0) - prev.get("total_cache_size", 0)
            direction = "↑" if delta > 0 else "↓" if delta < 0 else "→"
            print(f"  Trend since last scan:   {direction} {human(abs(delta))}")

    sched = schedule_status()
    if sched:
        print(f"\n  Scheduled clean:         {sched}")
    print()


# ---------------------------------------------------------------------------
# Scheduled clean — macOS launchd
# ---------------------------------------------------------------------------

_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
{args}
    </array>
    <key>StartCalendarInterval</key>
    <dict>
{schedule}
    </dict>
    <key>StandardOutPath</key>
    <string>{log}</string>
    <key>StandardErrorPath</key>
    <string>{err_log}</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
"""

def _build_schedule_block(interval: str, hour: int, minute: int) -> str:
    """Return the launchd StartCalendarInterval XML block."""
    h = f"        <key>Hour</key>\n        <integer>{hour}</integer>"
    m = f"        <key>Minute</key>\n        <integer>{minute}</integer>"
    if interval == "weekly":
        w = "        <key>Weekday</key>\n        <integer>0</integer>"
        return f"{w}\n{h}\n{m}"
    return f"{h}\n{m}"


def _maidbook_argv() -> str:
    """Return the <string> XML lines for the maidbook binary + --cron arg."""
    binary = shutil.which("maidbook")
    if binary:
        return f"        <string>{binary}</string>\n        <string>--cron</string>"
    # Fallback: run as a Python module
    exe = sys.executable
    return (
        f"        <string>{exe}</string>\n"
        "        <string>-m</string>\n"
        "        <string>maidbook</string>\n"
        "        <string>--cron</string>"
    )


def print_schedule_summary(interval: str, hour: int, minute: int,
                           n_keys: int) -> None:
    """Print a structured schedule-installed summary to stdout."""
    from pathlib import Path as _Path
    home = str(_Path("~").expanduser())
    plist_short = str(LAUNCHD_PLIST_PATH).replace(home, "~")
    log_short   = str(LOG_DIR / "launchd.log").replace(home, "~")

    time_str = f"{hour:02d}:{minute:02d}"
    when = "daily" if interval == "daily" else "weekly · every Sunday"
    cats_str = f"{n_keys} selected" if n_keys else "all non-whitelisted"

    W = 46
    sep = "─" * W
    print(f"\n  ┌{sep}┐")
    print(f"  │  {'✓  Scheduled clean installed':<{W - 2}}  │")
    print(f"  ├{sep}┤")
    print(f"  │  {'Interval':<12}  {when:<{W - 16}}│")
    print(f"  │  {'Time':<12}  {time_str:<{W - 16}}│")
    print(f"  │  {'Categories':<12}  {cats_str:<{W - 16}}│")
    print(f"  ├{sep}┤")
    print(f"  │  {'Plist':<12}  {plist_short:<{W - 16}}│")
    print(f"  │  {'Logs':<12}  {log_short:<{W - 16}}│")
    print(f"  ├{sep}┤")
    print(f"  │  {'To remove':<12}  {'maidbook --unschedule':<{W - 16}}│")
    print(f"  └{sep}┘\n")


def schedule_cron(interval: str = "weekly",
                  hour: int = 3, minute: int = 0,
                  selected_keys: list | None = None,
                  quiet: bool = False) -> None:
    """Install a launchd job to run ``maidbook --cron`` automatically.

    interval      : ``"daily"`` or ``"weekly"`` (Sunday).
    hour          : 0–23 (default 3).
    minute        : 0–59 (default 0).
    selected_keys : category keys chosen during TUI scan → select step.
                    Persisted to ``schedule.json`` so cron cleans exactly
                    those categories.  Empty list → clean all non-whitelisted.
    quiet         : suppress all stdout (use when called from the TUI, which
                    shows its own flash message instead).

    Writes ``~/Library/LaunchAgents/com.maidbook.cron.plist`` and loads it.
    """
    if interval not in ("daily", "weekly"):
        if not quiet:
            print(f"Unknown interval '{interval}'. Choose: daily, weekly")
        return

    log_dir = LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    plist = _PLIST_TEMPLATE.format(
        label=LAUNCHD_LABEL,
        args=_maidbook_argv(),
        schedule=_build_schedule_block(interval, hour, minute),
        log=log_dir / "launchd.log",
        err_log=log_dir / "launchd-err.log",
    )

    LAUNCHD_PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Unload any existing job first (ignore errors — may not be loaded)
    subprocess.run(
        ["launchctl", "unload", str(LAUNCHD_PLIST_PATH)],
        capture_output=True,
    )

    LAUNCHD_PLIST_PATH.write_text(plist)

    # Persist schedule config: interval + time + category selection.
    save_schedule_config({
        "interval": interval,
        "hour": hour,
        "minute": minute,
        "selected_keys": list(selected_keys or []),
    })

    result = subprocess.run(
        ["launchctl", "load", str(LAUNCHD_PLIST_PATH)],
        capture_output=True, text=True,
    )

    if quiet:
        return  # TUI shows its own flash message — summary printed after curses exits

    if result.returncode != 0:
        print(f"  ✗  launchctl load failed — {result.stderr.strip()}")
        print(f"  Plist written to {LAUNCHD_PLIST_PATH}")
        print(f"  Load manually: launchctl load {LAUNCHD_PLIST_PATH}")
    else:
        n = len(selected_keys) if selected_keys else 0
        print_schedule_summary(interval, hour, minute, n)


def unschedule_cron() -> None:
    """Unload and remove the launchd job."""
    if not LAUNCHD_PLIST_PATH.exists():
        print("No scheduled clean found.")
        return

    subprocess.run(
        ["launchctl", "unload", str(LAUNCHD_PLIST_PATH)],
        capture_output=True,
    )
    LAUNCHD_PLIST_PATH.unlink(missing_ok=True)
    from .common import SCHEDULE_CONFIG_PATH
    SCHEDULE_CONFIG_PATH.unlink(missing_ok=True)
    print(f"{APP_NAME} — scheduled clean removed.")


def schedule_status() -> str:
    """Return a human-readable status string, or empty string if not scheduled."""
    if not LAUNCHD_PLIST_PATH.exists():
        return ""
    try:
        import re
        content = LAUNCHD_PLIST_PATH.read_text()
        # Parse hour and minute from plist XML
        hour_m   = re.search(r"<key>Hour</key>\s*<integer>(\d+)</integer>",   content)
        minute_m = re.search(r"<key>Minute</key>\s*<integer>(\d+)</integer>", content)
        h = int(hour_m.group(1))   if hour_m   else 3
        m = int(minute_m.group(1)) if minute_m else 0
        time_str = f"{h:02d}:{m:02d}"
        if "Weekday" in content:
            return f"weekly · Sunday {time_str}"
        return f"daily · {time_str}"
    except OSError:
        return "installed (unreadable)"
