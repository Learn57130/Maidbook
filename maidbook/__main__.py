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
from .common import APP_NAME, APP_TAGLINE


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

    if args.cli:
        from .cli import run_cli
        run_cli(dry_run=args.dry_run, clean_all=args.all)
        return 0

    try:
        from .tui import run_tui
        run_tui()
    except KeyboardInterrupt:
        return 0
    except curses.error as e:
        print(f"curses error: {e}")
        print("Falling back to CLI. Use --cli to skip the TUI next time.")
        from .cli import run_cli
        run_cli(dry_run=args.dry_run, clean_all=args.all)
    return 0


if __name__ == "__main__":
    sys.exit(main())
