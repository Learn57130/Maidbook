"""Shared utilities: size helpers, path stats, process check, UI glyphs.

Everything here is small, stdlib-only, and has no Maidbook-internal imports —
other modules depend on this one, not the other way around.
"""

from __future__ import annotations

import locale
import os
import shutil
import subprocess
import threading
import time
import uuid
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


def redact_home(text: str) -> str:
    """Replace any literal ``$HOME`` substring inside an arbitrary string with ``~``.

    Use this when sanitising free-form text that may *contain* paths but isn't
    a path itself — error strings from external tools (``codesign --verify``
    stderr, ``launchctl unload`` remediation messages, etc.). For path values
    that come in already-canonical form, prefer :func:`fmt_path`.
    """
    if not text:
        return text
    home = str(HOME)
    if home not in text:
        return text
    return text.replace(home, "~")


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
# Async deletion via mv-then-rmtree
# ---------------------------------------------------------------------------
#
# Cache trees with tens of thousands of inodes (npm, ~/.cache, Xcode
# DerivedData) take seconds-to-minutes to ``shutil.rmtree`` directly. The
# user perceives that as a frozen UI even though the worker thread is making
# real progress.
#
# The trick: an ``os.rename`` of a directory within the same filesystem is
# essentially free — APFS just updates a single inode entry. Move the cache
# tree into ``~/.maidbook/trash/<unique>/``, return immediately with the
# moved size, and let a background daemon thread handle the actual
# ``shutil.rmtree`` on its own time.
#
# Honesty contract preserved: the bytes reported are the bytes that were
# *moved out of the original location*. From the user's perspective the
# cache is gone (the original path no longer exists). The disk-space
# reclamation lags by however long the background reaper takes — usually
# seconds, occasionally a minute or two for huge trees. Any orphan trash
# left from a previous session is reaped at next startup.
#
# If the rename fails (cross-filesystem move, permissions), we fall back
# to synchronous ``rm_path`` so the contract still holds.

TRASH_BASE = HOME / ".maidbook" / "trash"

_REAPER_THREADS: list[threading.Thread] = []
_REAPER_LOCK = threading.Lock()


def _new_trash_dir() -> Path:
    """Create and return a unique subdir under :data:`TRASH_BASE`."""
    TRASH_BASE.mkdir(parents=True, exist_ok=True)
    # Timestamp + short uuid keeps multiple parallel cleans from colliding.
    name = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    d = TRASH_BASE / name
    d.mkdir()
    return d


def _reap_one(trash_subdir: Path) -> None:
    """Background worker: ``shutil.rmtree`` a trash subdir, swallow errors.

    Errors are intentionally silent — by the time the reaper runs, the user
    has already moved on. The orphan-reaper at next startup will retry any
    paths this thread couldn't finish.
    """
    try:
        shutil.rmtree(trash_subdir, ignore_errors=True)
    except OSError:
        # Defensive: shutil.rmtree(..., ignore_errors=True) shouldn't raise,
        # but a permission error during the recursive walk has been seen on
        # some macOS configs. Either way, leave it for next-startup reap.
        pass


def _schedule_reap(trash_subdir: Path) -> None:
    """Spawn a daemon thread to delete ``trash_subdir`` in the background."""
    t = threading.Thread(
        target=_reap_one, args=(trash_subdir,),
        name=f"maidbook-reaper-{trash_subdir.name}", daemon=True,
    )
    t.start()
    with _REAPER_LOCK:
        _REAPER_THREADS.append(t)


def rm_path_async(p: Path) -> tuple[int, int]:
    """Delete a path with perceived-speed optimisation.

    Returns ``(bytes_moved, errors)`` — same shape as :func:`rm_path`.

    For a **directory**: rename it into ``~/.maidbook/trash/<unique>/`` and
    spawn a background daemon thread to ``shutil.rmtree`` the trash entry.
    The rename is essentially instant (single APFS metadata update), so the
    caller returns immediately.

    For a **file or symlink**: delegates to :func:`rm_path` — single-file
    deletion is already fast and gains nothing from being deferred.

    Falls back to :func:`rm_path` if the rename fails (cross-filesystem
    move, permission error). The honesty contract from :func:`rm_path` is
    preserved end-to-end.
    """
    if not p.exists() and not p.is_symlink():
        return 0, 0

    # Files / symlinks: no perceived-speed win from going async.
    if p.is_file() or p.is_symlink():
        return rm_path(p)

    # Directory: try the rename trick.
    size_before = path_size(p)
    try:
        trash = _new_trash_dir()
        target = trash / p.name
        os.rename(str(p), str(target))
    except OSError:
        # Cross-filesystem, permission denied, or anything else — fall back
        # to the synchronous honest path so the caller still gets accurate
        # numbers.
        return rm_path(p)

    _schedule_reap(trash)
    return size_before, 0


def reap_pending_trash() -> int:
    """Remove any leftover trash subdirs from prior sessions.

    Returns the number of subdirs reaped. Intended to be called once at
    startup so a crash or force-quit during a previous clean doesn't leave
    space unreclaimed indefinitely.
    """
    if not TRASH_BASE.exists():
        return 0
    count = 0
    try:
        entries = list(TRASH_BASE.iterdir())
    except OSError:
        return 0
    for d in entries:
        if not d.is_dir():
            continue
        try:
            shutil.rmtree(d, ignore_errors=True)
            count += 1
        except OSError:
            continue
    return count


def wait_for_pending_reaps(timeout: float = 2.0) -> int:
    """Wait briefly for in-flight background reapers to finish.

    Returns the number of reapers still running after the timeout. Intended
    to be called at app exit so small cleans finish synchronously while big
    ones are left for the next-startup reaper.
    """
    deadline = time.monotonic() + timeout
    with _REAPER_LOCK:
        threads = list(_REAPER_THREADS)
    for t in threads:
        remaining = max(0.0, deadline - time.monotonic())
        t.join(timeout=remaining)
    with _REAPER_LOCK:
        still_alive = sum(1 for t in _REAPER_THREADS if t.is_alive())
    return still_alive


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
