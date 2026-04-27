"""Shared utilities: size helpers, path stats, process check, UI glyphs.

Everything here is small, stdlib-only, and has no Maidbook-internal imports —
other modules depend on this one, not the other way around.
"""

from __future__ import annotations

import locale
import os
import shutil
import subprocess
from pathlib import Path

# Required on macOS for UTF-8 in curses (locale must be set BEFORE curses
# initialization). Setting it at import time is intentional.
locale.setlocale(locale.LC_ALL, "")

HOME = Path.home()
APP_NAME = "Maidbook"
APP_TAGLINE = "the tidy Mac keeper"

# Box drawing — Claude Code uses rounded corners for cards.
BOX_TL, BOX_TR, BOX_BL, BOX_BR = "╭", "╮", "╰", "╯"
BOX_H, BOX_V = "─", "│"

# Bullet markers
MARK_SELECTED = "●"
MARK_UNSELECTED = "○"
MARK_CURSOR = "❯"
BULLET = "●"

# Braille spinner — same as Claude Code.
SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


# ---------------------------------------------------------------------------
# Size / count formatting
# ---------------------------------------------------------------------------


def human(n: int) -> str:
    """Format a byte count as 1.2 MB / 3.4 GB / etc."""
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    for u in units:
        if size < 1024 or u == units[-1]:
            return f"{int(size)} {u}" if u == "B" else f"{size:.1f} {u}"
        size /= 1024
    return f"{size:.1f} TB"


# ---------------------------------------------------------------------------
# Filesystem size
# ---------------------------------------------------------------------------


def path_size(p: Path) -> int:
    """Disk usage of ``p`` in bytes, via macOS ``du -sk``.

    ``du`` is a native BSD tool that walks the tree using ``getdirentries64``
    in batches and returns block-rounded disk usage — faster than any pure
    Python implementation on trees with millions of files. A missing path
    returns ``0``.

    Note: this reports *disk usage* (4 KB block granularity on APFS), not
    logical file size. For a cache cleaner, disk usage is the correct number
    — it's what gets freed on deletion.
    """
    if not p.exists():
        return 0
    try:
        # ``--`` is a POSIX end-of-options sentinel: everything after it is
        # treated as a positional arg, so a cache folder named like ``-H`` or
        # ``--si`` can't be mis-parsed by ``du`` as a flag.
        r = subprocess.run(
            ["du", "-sk", "--", str(p)],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return 0
    if r.returncode != 0 or not r.stdout.strip():
        return 0
    try:
        return int(r.stdout.split()[0]) * 1024
    except (ValueError, IndexError):
        return 0


def fmt_path(p: Path | str) -> str:
    """Render a path with ``$HOME`` abbreviated to ``~``."""
    s = str(p)
    home = str(HOME)
    if s.startswith(home):
        return "~" + s[len(home):]
    return s


def rm_path(p: Path) -> tuple[int, int]:
    """Delete a file or directory. Returns ``(bytes_freed, errors)``.

    The numbers are honest: ``bytes_freed`` reflects what was *actually*
    removed (size_before - size_after), not what the deletion was asked to
    free. ``errors`` counts every per-entry failure inside ``shutil.rmtree``
    plus any top-level ``OSError``. This matters when a tree contains
    protected, read-only, or busy files — the user must not be told space
    was reclaimed when some of it is still on disk.
    """
    if not p.exists() and not p.is_symlink():
        return 0, 0

    # File or symlink path — straight unlink, single failure point.
    if p.is_file() or p.is_symlink():
        try:
            size_before = p.lstat().st_size
        except OSError:
            size_before = 0
        try:
            p.unlink()
            return size_before, 0
        except OSError:
            return 0, 1

    # Directory tree — measure before, count per-entry failures, measure after.
    size_before = path_size(p)
    errors = 0

    def _onerror(_func, _path, _excinfo):
        nonlocal errors
        errors += 1

    try:
        shutil.rmtree(p, onerror=_onerror)
    except OSError:
        errors += 1

    size_after = path_size(p) if p.exists() else 0
    freed = max(0, size_before - size_after)
    return freed, errors


# ---------------------------------------------------------------------------
# Process detection
# ---------------------------------------------------------------------------


def is_app_running(app_name: str) -> bool:
    """True if a process whose name contains app_name is live.

    Matches either ``<app_name>.app`` in the command line (GUI apps on macOS)
    or a binary path ending in ``/<app_name>``.
    """
    try:
        out = subprocess.run(
            ["pgrep", "-fl", app_name],
            capture_output=True, text=True, timeout=3,
        )
        for line in out.stdout.splitlines():
            if f"{app_name}.app" in line or line.rstrip().endswith(f"/{app_name}"):
                return True
    except (subprocess.SubprocessError, OSError):
        pass
    return False
