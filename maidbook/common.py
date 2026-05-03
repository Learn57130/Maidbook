"""Shared utilities: size helpers, path stats, process check, UI glyphs.

Everything here is small, stdlib-only, and has no Maidbook-internal imports —
other modules depend on this one, not the other way around.
"""

from __future__ import annotations

import contextlib
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

# Thread-local batch tracker. When a thread enters async_batch(), every
# rm_path_async call on that thread also records its trash subdir into
# the batch so the caller can later ask "of all the trash dirs my code
# created in this batch, how many bytes are still pending?". This avoids
# charging the current batch with bytes that belong to orphan subdirs
# the startup reaper happens to be draining at the same time.
_CURRENT_BATCH = threading.local()


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

    # If a batch is active on this thread, record the trash subdir so
    # the caller can later sum just this batch's pending bytes (rather
    # than the whole TRASH_BASE which may include unrelated orphans).
    batch_subdirs = getattr(_CURRENT_BATCH, "subdirs", None)
    if batch_subdirs is not None:
        batch_subdirs.append(trash)

    return size_before, 0


@contextlib.contextmanager
def async_batch():
    """Context manager: track per-batch trash subdirs for honest reporting.

    Yields a zero-arg callable. Calling it returns the total disk usage
    of the trash subdirs created by ``rm_path_async`` calls inside this
    context (and on this thread). Subdirs already drained by the reaper
    contribute 0.

    This is the right number to subtract from a batch's claimed
    ``total_freed`` to get a truly-reclaimed figure — using
    :func:`trash_pending_bytes` for the same purpose would incorrectly
    charge the current batch for orphans being drained by the startup
    reaper.

    Usage::

        with async_batch() as batch_pending:
            for c in selected:
                freed, errs, msg = c.clean(...)
                ...
            wait_for_pending_reaps(timeout=5.0)
            actually_freed = max(0, total_freed - batch_pending())
    """
    previous = getattr(_CURRENT_BATCH, "subdirs", None)
    subdirs: list[Path] = []
    _CURRENT_BATCH.subdirs = subdirs

    def _pending() -> int:
        return sum(path_size(d) for d in subdirs if d.exists())

    try:
        yield _pending
    finally:
        # Restore previous batch context so nested cleans still work.
        _CURRENT_BATCH.subdirs = previous


def trash_pending_bytes() -> int:
    """Total disk usage currently sitting in :data:`TRASH_BASE`.

    Used by the post-clean summary to honestly distinguish between bytes
    that were actually reclaimed and bytes that have been renamed into
    the trash but not yet ``rmtree``'d by the background reaper.
    """
    if not TRASH_BASE.exists():
        return 0
    return path_size(TRASH_BASE)


def reap_pending_trash() -> int:
    """Remove any leftover trash subdirs from prior sessions, synchronously.

    Returns the number of subdirs reaped. Caller is responsible for
    deciding whether to invoke this directly (small leftover, fast) or via
    :func:`reap_pending_trash_async` so it doesn't block UI startup.
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


def reap_pending_trash_async() -> threading.Thread | None:
    """Spawn a daemon thread that reaps leftover trash from prior sessions.

    Returns the thread (so callers / tests can introspect) or ``None`` if
    the trash dir doesn't exist yet (clean install, common case — cheap
    to skip).

    Intended to be called once at app startup. The previous synchronous
    version of this call could freeze the app for tens of seconds before
    any UI rendered, in the exact failure mode async deletion was meant
    to fix. By going through a daemon thread, the UI renders immediately
    and the reap continues in the background; if the user quits before
    it finishes, next startup tries again.
    """
    if not TRASH_BASE.exists():
        return None
    t = threading.Thread(
        target=reap_pending_trash,
        name="maidbook-startup-reaper", daemon=True,
    )
    t.start()
    with _REAPER_LOCK:
        _REAPER_THREADS.append(t)
    return t


def wait_for_pending_reaps(timeout: float = 2.0) -> tuple[int, int]:
    """Wait briefly for in-flight background reapers to finish.

    Returns ``(threads_still_alive, bytes_still_in_trash)``:

    - ``threads_still_alive`` — number of background reapers that did NOT
      finish within ``timeout``.
    - ``bytes_still_in_trash`` — disk usage of :data:`TRASH_BASE` after
      the wait. Used by the clean summary to honestly distinguish between
      "freed" and "scheduled but not yet finalized".

    Intended to be called once after a batch of cleans (and again at exit)
    so small cleans complete in-session and the user sees an honest report
    of what was actually reclaimed vs what's still pending.
    """
    deadline = time.monotonic() + timeout
    with _REAPER_LOCK:
        threads = list(_REAPER_THREADS)
    for t in threads:
        remaining = max(0.0, deadline - time.monotonic())
        t.join(timeout=remaining)
    with _REAPER_LOCK:
        still_alive = sum(1 for t in _REAPER_THREADS if t.is_alive())
        # Prune dead threads so the list doesn't grow unbounded over a
        # long session (one of the things a careful reviewer was right
        # to flag).
        _REAPER_THREADS[:] = [t for t in _REAPER_THREADS if t.is_alive()]
    return still_alive, trash_pending_bytes()


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
