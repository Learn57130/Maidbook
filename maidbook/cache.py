"""Cache categories: pip / npm / brew, browser caches, XDG ~/.cache,
Xcode DerivedData, dev build artifacts, a curated ``SAFE_CACHE_ITEMS``
list, plus auto-discovery of whatever else is sitting in
``~/Library/Caches``.

Each :class:`Category` has:
  * a ``scan`` function returning ``(bytes, file_count, dir_count)``
  * a ``clean(dry)`` function returning ``(bytes_freed, errors, message)``
  * a ``safety`` label — ``"safe" | "caution" | "review"`` — used by the TUI
"""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .common import HOME, fmt_path, is_app_running, path_size, rm_path, rm_path_async


@dataclass
class Category:
    key: str
    name: str
    icon: str
    description: str
    # scan() returns disk usage in bytes (via ``du -sk``).
    scan: Callable[[], int]
    clean: Callable[[bool], tuple[int, int, str]]
    tags: set[str] = field(default_factory=set)
    requires_apps_closed: list[str] = field(default_factory=list)
    # safety: "safe"    = very fine to delete, rebuilds automatically
    #         "caution" = fine, but costs time/inconvenience (rebuild, re-login)
    #         "review"  = unknown / unverified — user should decide
    safety: str = "review"
    safety_note: str = ""
    # Short, display-friendly directory path (home abbreviated to ``~``).
    path_hint: str = ""


# ---------------------------------------------------------------------------
# Classifier for auto-discovered items in ~/Library/Caches/
# ---------------------------------------------------------------------------

_APPLE_SAFE_PREFIXES = ("com.apple.",)
_VERY_SAFE_NAMES = {
    # Cache dirs that everyone agrees rebuild cleanly.
    "Adobe Camera Raw 2", "node-gyp", "typescript", "Homebrew",
    "vscode-cpptools", "ms-playwright-go", "Jedi", "Mozilla",
    "GeoServices", "puccinialin", "SiriTTS",
}


def classify_discovered(name: str) -> tuple[str, str]:
    """Return (safety_level, short_note) for an auto-discovered cache folder."""
    if name in _VERY_SAFE_NAMES:
        return "safe", "known cache dir"
    if any(name.startswith(p) for p in _APPLE_SAFE_PREFIXES):
        return "safe", "Apple system cache — regenerated on demand"
    # Reverse-DNS bundle IDs (com.xxx.yyy) → app cache, probably fine
    if "." in name and name.count(".") >= 1 and name[0].islower():
        return "caution", "app cache — unknown behavior, low risk"
    return "review", "unverified — review before cleaning"


# ---------------------------------------------------------------------------
# Vendor-tool cleaners (pip / npm / brew)
# ---------------------------------------------------------------------------


def clean_pip(dry: bool) -> tuple[int, int, str]:
    cache_dir = HOME / "Library/Caches/pip"
    before = path_size(cache_dir)
    if dry:
        return before, 0, "would run: pip cache purge"
    for cmd in (["pip", "cache", "purge"], ["pip3", "cache", "purge"]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                after = path_size(cache_dir)
                return max(before - after, 0), 0, "pip cache purged"
        except (subprocess.SubprocessError, FileNotFoundError):
            continue
    return 0, 1, "pip not found"


def clean_npm(dry: bool) -> tuple[int, int, str]:
    cache_dir = HOME / ".npm"
    before = path_size(cache_dir)
    if dry:
        return before, 0, "would run: npm cache clean --force"
    try:
        r = subprocess.run(
            ["npm", "cache", "clean", "--force"],
            capture_output=True, text=True, timeout=120,
        )
        after = path_size(cache_dir)
        if r.returncode != 0:
            return 0, 1, "npm failed"
        return max(before - after, 0), 0, "npm cache cleaned"
    except (subprocess.SubprocessError, FileNotFoundError):
        return 0, 1, "npm not found"


def clean_brew(dry: bool) -> tuple[int, int, str]:
    if dry:
        return path_size(HOME / "Library/Caches/Homebrew"), 0, "would run: brew cleanup"
    try:
        r = subprocess.run(
            ["brew", "cleanup", "-s", "--prune=all"],
            capture_output=True, text=True, timeout=300,
        )
        if r.returncode != 0:
            return 0, 1, "brew failed"
        freed = 0
        for line in r.stdout.splitlines():
            if "freed approximately" in line:
                parts = line.split()
                for i, p in enumerate(parts):
                    if p == "approximately" and i + 1 < len(parts):
                        val = parts[i + 1].rstrip(".")
                        for unit, mult in (("TB", 1 << 40), ("GB", 1 << 30),
                                           ("MB", 1 << 20), ("KB", 1 << 10),
                                           ("B", 1)):
                            if val.upper().endswith(unit):
                                try:
                                    freed = int(float(val[: -len(unit)]) * mult)
                                except ValueError:
                                    pass
                                break
        return freed, 0, "brew cleaned"
    except (subprocess.SubprocessError, FileNotFoundError):
        return 0, 1, "brew not found"


# ---------------------------------------------------------------------------
# Browser caches — only Cache/, Code Cache/, GPUCache/ subfolders are cleared.
# Profile data (cookies, history, logins, bookmarks) is never touched.
# ---------------------------------------------------------------------------

BROWSERS = [
    ("Brave",   "Brave Browser",  "Library/Caches/BraveSoftware/Brave-Browser"),
    ("Chrome",  "Google Chrome",  "Library/Caches/Google/Chrome"),
    ("Edge",    "Microsoft Edge", "Library/Caches/Microsoft Edge"),
    ("Opera",   "Opera",          "Library/Caches/com.operasoftware.Opera"),
    ("Firefox", "firefox",        "Library/Caches/Firefox"),
    ("Comet",   "Comet",          "Library/Caches/Comet"),
    ("Atlas",   "ChatGPT",        "Library/Caches/com.openai.atlas"),
]


def find_browser_cache_dirs(root: Path) -> list[Path]:
    targets: list[Path] = []
    if not root.exists():
        return targets
    for dirpath, dirnames, _files in os.walk(root):
        for name in list(dirnames):
            if name in ("Cache", "Code Cache", "GPUCache"):
                targets.append(Path(dirpath) / name)
    return targets


def make_browser_cleaner(display: str, proc: str, rel: str):
    root = HOME / rel

    def scan() -> int:
        targets = find_browser_cache_dirs(root)
        if not targets:
            return 0
        # ``du`` natively walks each subdir fast; parallelizing short calls
        # adds more subprocess overhead than it saves. Sum inline.
        return sum(path_size(t) for t in targets)

    def clean(dry: bool) -> tuple[int, int, str]:
        if is_app_running(proc) and not dry:
            return 0, 0, f"{display} is running -- skipped"
        freed, errs, count = 0, 0, 0
        for t in find_browser_cache_dirs(root):
            count += 1
            if dry:
                freed += path_size(t)
            else:
                s, e = rm_path_async(t)
                freed += s
                errs += e
        if count == 0:
            return 0, 0, "nothing to clean"
        verb = "would clear" if dry else "cleared"
        return freed, errs, f"{verb} {count} subdirs"

    return scan, clean


# ---------------------------------------------------------------------------
# Curated safe caches + ~/.cache + Xcode DerivedData
# ---------------------------------------------------------------------------

SAFE_CACHE_ITEMS = [
    "puccinialin", "SiriTTS", "ms-playwright-go", "Jedi", "Mozilla",
    "com.todesktop.230313mzl4w4u92.ShipIt", "com.apple.CharacterPaletteIM",
    "com.adobe.lightroomCC", "Adobe Camera Raw 2", "node-gyp", "vscode-cpptools",
    "typescript", "com.apple.helpd", "com.apple.tipsd", "com.apple.parsecd",
    "com.apple.e5rt.e5bundlecache", "com.apple.CloudTelemetry", "GeoServices",
]


def scan_safe_caches() -> int:
    base = HOME / "Library/Caches"
    targets = [base / i for i in SAFE_CACHE_ITEMS if (base / i).exists()]
    if not targets:
        return 0
    with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
        return sum(pool.map(path_size, targets))


def clean_safe_caches(dry: bool) -> tuple[int, int, str]:
    base = HOME / "Library/Caches"
    freed, errs, count = 0, 0, 0
    for item in SAFE_CACHE_ITEMS:
        p = base / item
        if not p.exists():
            continue
        count += 1
        if dry:
            freed += path_size(p)
        else:
            s, e = rm_path_async(p)
            freed += s
            errs += e
    if count == 0:
        return 0, 0, "nothing to clean"
    verb = "would clear" if dry else "cleared"
    return freed, errs, f"{verb} {count} items"


def scan_dotcache() -> int:
    return path_size(HOME / ".cache")


def clean_dotcache(dry: bool) -> tuple[int, int, str]:
    cache = HOME / ".cache"
    if not cache.exists():
        return 0, 0, "no ~/.cache"
    freed, errs, count = 0, 0, 0
    for child in cache.iterdir():
        count += 1
        if dry:
            freed += path_size(child)
        else:
            s, e = rm_path_async(child)
            freed += s
            errs += e
    verb = "would clear" if dry else "cleared"
    return freed, errs, f"{verb} {count} items"


def scan_xcode() -> int:
    return path_size(HOME / "Library/Developer/Xcode/DerivedData")


def clean_xcode(dry: bool) -> tuple[int, int, str]:
    dd = HOME / "Library/Developer/Xcode/DerivedData"
    if not dd.exists():
        return 0, 0, "no DerivedData"
    freed, errs = 0, 0
    for child in dd.iterdir():
        if dry:
            freed += path_size(child)
        else:
            s, e = rm_path_async(child)
            freed += s
            errs += e
    verb = "would clear" if dry else "cleared"
    return freed, errs, f"{verb} DerivedData"


# ---------------------------------------------------------------------------
# Dev build artifacts — node_modules, target/, venv, __pycache__, etc.
# ---------------------------------------------------------------------------

ARTIFACT_SCAN_ROOTS = [
    HOME / "Developer",
    HOME / "Projects",
    HOME / "repos",
    HOME / "code",
    HOME / "Desktop",
    HOME / "Documents",
]

_ARTIFACT_NAMES = frozenset({
    "node_modules", "target", ".build", "venv", ".venv", "__pycache__",
})

_ARTIFACT_NEED_SIBLING = frozenset({"build", "dist"})

_PROJECT_MARKERS = frozenset({
    "package.json", "setup.py", "pyproject.toml", "Cargo.toml",
    "build.gradle", "pom.xml", "Makefile", "CMakeLists.txt",
})

_ARTIFACT_LABELS: dict[str, tuple[str, str]] = {
    "node_modules": ("npm/yarn install artifacts", "caution"),
    "target":       ("Rust/Java/Scala build output", "caution"),
    ".build":       ("Swift Package Manager build", "caution"),
    "build":        ("Build output", "caution"),
    "dist":         ("Distribution output", "caution"),
    "venv":         ("Python virtual environment", "caution"),
    ".venv":        ("Python virtual environment", "caution"),
    "__pycache__":  ("Python bytecode cache", "safe"),
}

_MAX_ARTIFACT_DEPTH = 5
_MAX_ARTIFACTS = 200


def discover_dev_artifacts(
    roots: list[Path] | None = None,
) -> list[tuple[str, Path, str, str]]:
    """Walk project roots and find dev build artifact directories.

    Returns ``(artifact_type, path, description, safety)`` for each hit.
    Skips projects containing a ``.maidbook-keep`` sentinel.
    """
    if roots is None:
        roots = ARTIFACT_SCAN_ROOTS
    found: list[tuple[str, Path, str, str]] = []

    def _has_project_marker(parent: Path) -> bool:
        try:
            return any((parent / m).exists() for m in _PROJECT_MARKERS)
        except OSError:
            return False

    for root in roots:
        if not root.is_dir():
            continue
        try:
            _walk_for_artifacts(root, found, 0, _has_project_marker)
        except OSError:
            continue
        if len(found) >= _MAX_ARTIFACTS:
            break
    return found[:_MAX_ARTIFACTS]


def _walk_for_artifacts(
    directory: Path,
    found: list[tuple[str, Path, str, str]],
    depth: int,
    has_marker: callable,
) -> None:
    if depth > _MAX_ARTIFACT_DEPTH or len(found) >= _MAX_ARTIFACTS:
        return
    if (directory / ".maidbook-keep").exists():
        return
    try:
        entries = list(directory.iterdir())
    except (OSError, PermissionError):
        return
    subdirs: list[Path] = []
    for entry in entries:
        if not entry.is_dir() or entry.is_symlink():
            continue
        name = entry.name
        if name.startswith(".") and name not in (".build", ".venv"):
            continue
        if name in _ARTIFACT_NAMES:
            desc, safety = _ARTIFACT_LABELS.get(name, ("build artifact", "caution"))
            project = directory.name
            found.append((name, entry, f"{project}/{name} — {desc}", safety))
            continue
        if name in _ARTIFACT_NEED_SIBLING:
            if has_marker(directory):
                desc, safety = _ARTIFACT_LABELS.get(name, ("build artifact", "caution"))
                project = directory.name
                found.append((name, entry, f"{project}/{name} — {desc}", safety))
                continue
        subdirs.append(entry)
    for sub in subdirs:
        if sub.name in _ARTIFACT_NAMES or sub.name in _ARTIFACT_NEED_SIBLING:
            continue
        _walk_for_artifacts(sub, found, depth + 1, has_marker)


def make_artifact_cleaner(path: Path):
    def scan() -> int:
        return path_size(path)

    def clean(dry: bool) -> tuple[int, int, str]:
        if not path.exists():
            return 0, 0, "already gone"
        if dry:
            return path_size(path), 0, "would remove"
        s, e = rm_path_async(path)
        return s, e, "removed" if e == 0 else f"errors: {e}"

    return scan, clean


# ---------------------------------------------------------------------------
# Auto-discovery of uncovered ~/Library/Caches/* folders
# ---------------------------------------------------------------------------


def discover_other_caches() -> list[tuple[str, Path]]:
    """Return (name, path) for items in ~/Library/Caches not already covered
    by a hardcoded category."""
    base = HOME / "Library/Caches"
    if not base.exists():
        return []
    covered: set[str] = {"pip", "Homebrew"}
    covered.update(SAFE_CACHE_ITEMS)
    for _display, _proc, rel in BROWSERS:
        parts = rel.split("/")
        if len(parts) >= 3 and parts[0] == "Library" and parts[1] == "Caches":
            covered.add(parts[2])
    try:
        entries = sorted(base.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    found: list[tuple[str, Path]] = []
    for child in entries:
        if child.name.startswith(".") or child.name in covered:
            continue
        found.append((child.name, child))
    return found


def make_discovered_cleaner(path: Path):
    def scan() -> int:
        return path_size(path)

    def clean(dry: bool) -> tuple[int, int, str]:
        if not path.exists():
            return 0, 0, "missing"
        if dry:
            return path_size(path), 0, "would remove"
        s, e = rm_path_async(path)
        return s, e, "removed" if e == 0 else f"errors: {e}"

    return scan, clean


# ---------------------------------------------------------------------------
# Build the full category list (hardcoded + browsers + auto-discovered)
# ---------------------------------------------------------------------------


def build_categories() -> list[Category]:
    cats: list[Category] = [
        Category("pip", "pip", "py", "Python pip download cache",
                 lambda: path_size(HOME / "Library/Caches/pip"),
                 clean_pip, tags={"safe", "dev"},
                 safety="safe", safety_note="rebuilt on next pip install",
                 path_hint="~/Library/Caches/pip"),
        Category("npm", "npm", "js", "Node npm cache",
                 lambda: path_size(HOME / ".npm"),
                 clean_npm, tags={"safe", "dev"},
                 safety="safe", safety_note="rebuilt on next npm install",
                 path_hint="~/.npm"),
        Category("brew", "brew", "br", "Homebrew downloads & old versions",
                 lambda: path_size(HOME / "Library/Caches/Homebrew"),
                 clean_brew, tags={"safe", "dev"},
                 safety="safe", safety_note="re-downloaded if needed",
                 path_hint="~/Library/Caches/Homebrew"),
        Category("safe-caches", "safe-caches", "..",
                 "Misc app caches bundle (curated)",
                 scan_safe_caches, clean_safe_caches, tags={"safe"},
                 safety="safe",
                 safety_note="hand-picked list of rebuildable caches",
                 path_hint=f"~/Library/Caches/* ({len(SAFE_CACHE_ITEMS)} items)"),
        Category("dotcache", "dotcache", "~.", "~/.cache contents",
                 scan_dotcache, clean_dotcache, tags={"safe"},
                 safety="safe",
                 safety_note="XDG cache dir — rebuilds on demand",
                 path_hint="~/.cache"),
        Category("xcode", "xcode", "xc", "Xcode DerivedData",
                 scan_xcode, clean_xcode, tags={"dev"},
                 safety="caution",
                 safety_note="next Xcode build will be slow",
                 path_hint="~/Library/Developer/Xcode/DerivedData"),
    ]
    for display, proc, rel in BROWSERS:
        scan_fn, clean_fn = make_browser_cleaner(display, proc, rel)
        cats.append(Category(
            f"browser-{display.lower()}",
            f"browser-{display.lower()}",
            "()",
            f"{display} cache (profile data preserved)",
            scan_fn, clean_fn,
            tags={"browser"},
            requires_apps_closed=[proc],
            safety="safe",
            safety_note="cookies, history, logins preserved",
            path_hint=fmt_path(HOME / rel),
        ))
    for atype, apath, adesc, asafety in discover_dev_artifacts():
        scan_fn, clean_fn = make_artifact_cleaner(apath)
        note = "rebuilds on next install/build" if asafety == "caution" else "regenerated automatically"
        cats.append(Category(
            key=f"artifact-{apath.parent.name}-{atype}",
            name=f"{apath.parent.name}/{atype}",
            icon=">>",
            description=adesc,
            scan=scan_fn,
            clean=clean_fn,
            tags={"dev-artifacts"},
            safety=asafety,
            safety_note=note,
            path_hint=fmt_path(apath),
        ))
    for name, path in discover_other_caches():
        scan_fn, clean_fn = make_discovered_cleaner(path)
        safety_level, safety_note = classify_discovered(name)
        cats.append(Category(
            key=f"other-{name}",
            name=name,
            icon="??",
            description=f"auto-discovered cache folder",
            scan=scan_fn,
            clean=clean_fn,
            tags={"other"},
            safety=safety_level,
            safety_note=safety_note,
            path_hint=fmt_path(path),
        ))
    return cats
