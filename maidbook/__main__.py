"""Command-line entry point. Installed by pyproject.toml as ``maidbook``.

Usage:
  maidbook              launch the TUI
  maidbook --cli        plain CLI (no curses)
  maidbook --dry-run    scan only, no deletion
  maidbook --all        (with --cli) clean every category — use with care
"""

from __future__ import annotations

import argparse
import curses
import sys

from . import __version__
from .cli import run_cli
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
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {__version__}")
    args = p.parse_args()

    # Reap any leftover trash from a previous session, in the background.
    # Cheap if the trash dir is empty (common case). Crucially, this MUST
    # NOT block startup — if the previous session left a 5 GB tree behind,
    # a synchronous rmtree here would freeze the UI for tens of seconds
    # before either CLI or TUI rendered, defeating the whole point of
    # async deletion. The daemon thread continues independently; if the
    # user quits before it finishes, next startup tries again.
    reap_pending_trash_async()

    try:
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
        # Give in-flight background reapers a moment to finish so small
        # cleans complete fully in-session. Anything still running gets
        # picked up by reap_pending_trash_async() at next startup.
        wait_for_pending_reaps(timeout=2.0)


if __name__ == "__main__":
    sys.exit(main())
