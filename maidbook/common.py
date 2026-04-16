"""Shared utilities: size helpers, path stats, process check, UI glyphs.

Everything here is small, stdlib-only, and has no Maidbook-internal imports —
other modules depend on this one, not the other way around.
"""

from __future__ import annotations

import locale
import os
import shutil
import stat as _stat
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


def short_count(n: int) -> str:
    """Compact count — 234, 1.2k, 3.4M."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return f"{n / 1_000_000:.1f}M".replace(".0M", "M")


# ---------------------------------------------------------------------------
# Filesystem walks
# ---------------------------------------------------------------------------


def path_size(p: Path) -> int:
    return path_stats(p)[0]


def path_stats(p: Path) -> tuple[int, int, int]:
    """Return (total_bytes, file_count, dir_count) for a path.

    Uses ``os.scandir`` — 3-5× faster than ``os.walk`` for deep cache trees
    because ``DirEntry`` objects cache their lstat info, saving one syscall
    per file. Counts every entry under ``p`` (excluding ``p`` itself).
    """
    try:
        st = os.lstat(p)
    except OSError:
        return 0, 0, 0
    if not _stat.S_ISDIR(st.st_mode):
        return st.st_size, 1, 0

    total, files_n, dirs_n = 0, 0, 0
    stack = [str(p)]
    while stack:
        current = stack.pop()
        try:
            it = os.scandir(current)
        except OSError:
            continue
        with it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        dirs_n += 1
                        stack.append(entry.path)
                    else:
                        files_n += 1
                        try:
                            total += entry.stat(follow_symlinks=False).st_size
                        except OSError:
                            pass
                except OSError:
                    pass
    return total, files_n, dirs_n


def sum_stats(items) -> tuple[int, int, int]:
    """Aggregate a stream of (bytes, files, dirs) triples."""
    b, f, d = 0, 0, 0
    for x in items:
        b += x[0]
        f += x[1]
        d += x[2]
    return b, f, d


def rm_path(p: Path) -> tuple[int, int]:
    """Delete a file or directory recursively. Returns (bytes_freed, errors)."""
    size = path_size(p)
    errors = 0
    if not p.exists():
        return 0, 0
    try:
        if p.is_file() or p.is_symlink():
            p.unlink()
        else:
            shutil.rmtree(p, onerror=lambda *_: None)
    except OSError:
        errors += 1
    return size, errors


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
