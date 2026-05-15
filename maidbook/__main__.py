"""Command-line entry point. Installed by pyproject.toml as ``maidbook``.

Usage:
  maidbook              launch the TUI
  maidbook --cli        plain CLI (no curses)
  maidbook --dry-run    scan only, no deletion
  maidbook --all        (with --cli) clean every category — use with care
  maidbook --cron       headless mode: clean all, JSON output, log to file
  maidbook --history    print last 10 cleaning sessions
  maidbook --stats      print lifetime statistics
"""

from __future__ import annotations

import argparse
import curses
import sys

from . import __version__
from .cli import run_cli, run_cron, show_history, show_stats, schedule_cron, unschedule_cron
from .common import (
    APP_NAME, APP_TAGLINE,
    reap_pending_trash_async, wait_for_pending_reaps,
)
from .tui import run_tui


def main() -> int:
    p = argparse.ArgumentParser(
        prog="maidbook",
        description=f"{APP_NAME} {__version__} — {APP_TAGLINE}",
    )
    p.add_argument("--cli", action="store_true",
                   help="plain CLI instead of TUI")
    p.add_argument("--dry-run", action="store_true",
                   help="scan only, no deletion")
    p.add_argument("--all", action="store_true",
                   help="(with --cli) clean all categories")
    p.add_argument("--cron", action="store_true",
                   help="headless mode: clean all non-whitelisted categories, JSON output")
    p.add_argument("--history", action="store_true",
                   help="print last 10 cleaning sessions from cron logs")
    p.add_argument("--stats", action="store_true",
                   help="print lifetime cleaning statistics")
    p.add_argument("--schedule", nargs="?", const="weekly",
                   metavar="INTERVAL",
                   help="install scheduled cron clean: daily or weekly (default: weekly)")
    p.add_argument("--unschedule", action="store_true",
                   help="remove the scheduled cron clean")
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {__version__}")
    args = p.parse_args()

    if args.unschedule:
        unschedule_cron()
        return 0

    if args.schedule is not None:
        schedule_cron(args.schedule)
        return 0

    if args.history:
        show_history()
        return 0

    if args.stats:
        show_stats()
        return 0

    reap_pending_trash_async()

    try:
        if args.cron:
            run_cron(dry_run=args.dry_run)
            return 0

        if args.cli:
            run_cli(dry_run=args.dry_run, clean_all=args.all)
            return 0

        try:
            run_tui()
        except KeyboardInterrupt:
            return 0
        except curses.error as e:
            print(f"curses error: {e}")
            print("Falling back to CLI. Use --cli to skip the TUI next time.")
            run_cli(dry_run=args.dry_run, clean_all=args.all)
        return 0
    finally:
        wait_for_pending_reaps(timeout=2.0)


if __name__ == "__main__":
    sys.exit(main())
