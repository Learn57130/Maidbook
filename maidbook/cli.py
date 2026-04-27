"""Plain CLI fallback for machines without a working curses terminal,
or when scripting output is preferred (``maidbook --cli --dry-run``)."""

from __future__ import annotations

import subprocess

from .cache import Category, build_categories
from .common import APP_NAME, APP_TAGLINE, human, is_app_running


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
    # Skip categories whose scan failed — don't try to clean what we
    # couldn't measure.
    selected = [c for c, _sz, err in rows if err is None] if clean_all else []
    if not selected:
        print("Use --all to clean, or run without --cli for the TUI.")
        return

    total_freed = 0
    total_errs = 0
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
    label = "Would free" if dry_run else "Freed"
    print(f"\n  {label}: {human(total_freed)}    Errors: {total_errs}")
