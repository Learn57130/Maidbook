# Changelog

All notable changes to Maidbook. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/), versioning per
[SemVer](https://semver.org/).

## [Unreleased]

## [0.1.0] — 2026-04-16

Initial public release.

### Added

**Cache cleaner**
- Curses TUI with a dedicated scan screen, 15-row category list, and rounded-card layout.
- Vendor-tool cleaners for pip (`pip cache purge`), npm (`npm cache clean --force`), and brew (`brew cleanup -s --prune=all`).
- Browser cleaners for Brave, Chrome, Edge, Opera, Firefox, Comet, ChatGPT Atlas — only `Cache/`, `Code Cache/`, `GPUCache/` subfolders are touched; profile data is preserved.
- Skips browsers whose process is live via `pgrep -fl`.
- Curated 19-item `SAFE_CACHE_ITEMS` bundle + `~/.cache` + Xcode `DerivedData`.
- Auto-discovery of every folder under `~/Library/Caches/` not already handled, each tagged `safe` / `caution` / `review` via a conservative classifier.
- Per-row columns: size, files / dirs count, safety verdict, description. Hover line shows exact counts + safety note.
- Group totals in the footer (dev · safe · browsers · other) plus selection summary with dry-run indicator.
- `[A]` select all, `[N]` deselect all, `[s/b/o]` tag-based selection.
- Dry-run toggle (`d`) and rescan (`r`).

**Health check**
- Five read-only modules, run concurrently:
  - `xprotect` — reads `/Library/Apple/System/Library/CoreServices/XProtect.bundle/Contents/Info.plist`, flags if > 45 days stale.
  - `malware` — known adware path signatures (MacKeeper / Genieo / Pirrit / Shlayer / Silver Sparrow) + LaunchAgents from non-Apple, non-well-known vendors.
  - `codesign` — parallel `codesign --verify --strict` across `/Applications` and `~/Applications`.
  - `quarantine` — `xattr`-based lookup for `com.apple.quarantine` flag in `~/Downloads` / `~/Desktop`.
  - `vulns` — wraps `pip-audit`, `brew outdated --quiet`, `npm outdated -g --json` when available.
- Landing menu on startup: Cache cleaner / Health check / Both.
- Findings sorted by severity (risk → caution → review → info → ok), grouped by module.
- `[C]` copies a plain-text report to the clipboard via `pbcopy`.

**Performance**
- `path_stats()` uses `os.scandir` instead of `os.walk` (3–5× faster).
- Top-level scan pool at 16 workers; composite scans (safe-caches, browsers) fan out internally.

### Safety
- Confirmation prompt before any deletion in the TUI.
- Auto-discovered rows default to `review`; never automatically selected by `[s]`.
- Health check is read-only — no files are modified, ever.
