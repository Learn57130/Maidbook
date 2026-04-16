"""Plain CLI fallback for machines without a working curses terminal,
or when scripting output is preferred (``maidbook --cli --dry-run``)."""

from __future__ import annotations

from .common import APP_NAME, APP_TAGLINE, human, is_app_running, sum_stats
from .cache import build_categories


def run_cli(dry_run: bool, clean_all: bool) -> None:
    cats = build_categories()
    print(f"{APP_NAME} -- {APP_TAGLINE}\n")
    print("Scanning...\n")
    rows = [(c, c.scan()) for c in cats]
    rows.sort(key=lambda x: -x[1][0])

    print(f"  {'#':>3}  {'Size':>10}  {'Files':>7}  {'Dirs':>6}  Category")
    print(f"  {'-'*3}  {'-'*10}  {'-'*7}  {'-'*6}  {'-'*50}")
    for i, (c, (sz, fn, dn)) in enumerate(rows, 1):
        size_cell = human(sz) if sz else "--"
        print(f"  {i:>3}  {size_cell:>10}  {fn:>7,}  {dn:>6,}  "
              f"{c.name} -- {c.description}")
    total_b, total_f, total_d = sum_stats(s for _, s in rows)
    print(f"  Total: {human(total_b)}  "
          f"({total_f:,} files, {total_d:,} folders)\n")

    if dry_run and not clean_all:
        return
    selected = [c for c, _ in rows] if clean_all else []
    if not selected:
        print("Use --all to clean, or run without --cli for the TUI.")
        return

    total_freed = 0
    for c in selected:
        if c.requires_apps_closed and not dry_run:
            if any(is_app_running(a) for a in c.requires_apps_closed):
                print(f"  >>  {c.name}: app running, skipped")
                continue
        freed, _errs, msg = c.clean(dry_run)
        total_freed += freed
        print(f"  OK  {c.name:<22} {human(freed):>10}  {msg}")
    label = "Would free" if dry_run else "Freed"
    print(f"\n  {label}: {human(total_freed)}")
