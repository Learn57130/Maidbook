# Changelog

All notable changes to Maidbook. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/), versioning per
[SemVer](https://semver.org/).

## [Unreleased] — v0.2 planned

### Planned

- Intelligent cache discovery — signature-based scan for `node_modules`,
  `target/`, `docker`, `__pycache__` across the user's projects dir.
- Risk grading — finer tiering (low / medium / high) to distinguish safe
  log deletion from time-costly build re-compilation.
- Headless cron mode via `--cron` flag with user-defined TTL rules.
- Smart whitelisting — toggle-based "pinning" in the TUI to protect
  specific active projects from automated cleanup.
- Quantitative analytics — persistent JSON / SQLite tracking of cumulative
  space saved and "bloat velocity" (GB growth over time).
- Post-action reporting — cron summary logs for full transparency.
- ASCII mascot integration — reactive minimalist mascot in the TUI that
  changes state based on system cleanliness.
- Async deletion (mv-then-rmtree) for ~50–100× perceived cleanup speed.
- Graceful Ctrl+C during clean (threading.Event stop signal).
- Within-category progress (current path / file count) during cleanup.
- Vim-style end-of-list (`G` / `gg`) and selection wrap-around.

## [0.1.2] — 2026-04-30

A small honesty patch shipped from a Codex PR review. Three real bugs caught
post-v0.1.1 by Codex, all in `health.py` / `tui.py` territory.

### Fixed

- **`scan_vulnerabilities` now treats `pip-audit` rc=1 as a successful scan
  with vulnerabilities found.** `pip-audit` documents an exit code of `1`
  when CVEs are present in the dependency set; the previous code only
  entered the JSON-parse branch on rc=0, so vulnerable Python packages
  were never surfaced and the Health Check would silently report "clean".
  This is an honesty-class bug — same family as the `rm_path` partial-
  deletion fix in v0.1.1. True scan failures now emit an `info` finding
  ("pip-audit scan failed") rather than passing through as silently clean.
- **`scan_quarantine` now flags quarantined `.app` bundles.** `.app`
  bundles are directories on APFS, so the prior `if not p.is_dir()`
  filter was excluding them entirely. A quarantined app downloaded into
  `~/Downloads` or `~/Desktop` would never be reported. Fixed by allowing
  `.app`-suffixed directories through the candidate gate.
- **TUI scan worker now isolates a wider class of per-category failures.**
  Previously caught only `OSError`; a `RuntimeError` / `ValueError` /
  `subprocess.SubprocessError` raised by one cache scanner would kill the
  whole worker thread and leave the scan stuck. Broadened the exception
  set so a single broken category cannot take down the whole scan.

### Tests

Two new regression tests, plus a fix to a pre-existing test that was
asserting the wrong `pip-audit` return code:
- `test_tui_scan_worker_isolates_runtime_errors`
- `test_scan_quarantine_includes_app_bundles`
- `test_scan_vulnerabilities` — corrected the vulnerable-case fixture from
  `rc=0` to `rc=1` to match `pip-audit`'s actual contract

37/37 pytest passing.

### Workflow note

This patch is the first one shipped via a real PR-review loop rather than
direct push to `main`. Codex opened the PR (`codex/fix-review-findings`),
the fixes were verified locally on the branch (`pytest -v` clean), then
merged via the GitHub UI. A duplicate Jules-driven PR forked off a stale
commit was closed without merging — the canonical "stale-branch duplicate-PR"
anti-pattern.

## [0.1.1] — 2026-04-29

A "post-journey" patch addressing 7 issues surfaced during a full live
user-journey test of v0.1.0.

### Fixed

- **N1 — Full path redaction in clipboard + display.** Codex's M1 fix had
  only piped `f.path` through `fmt_path`; the codesign-finding `f.detail`
  string still emitted `/Users/<name>/Applications/Foo.app: invalid Info.plist`.
  Username leaks via `f.detail` and `f.remediation` are now redacted via a
  new `common.redact_home` helper applied at every emission site.
- **N4 — `s` filter now matches the visible safety column.** Pressing `s`
  used to select only the 5 hand-tagged "safe" categories. It now selects
  every row where `c.safety == "safe"`, including browser caches and
  Apple-prefixed auto-discovered rows that classify as safe — matching
  what users see in the column.
- **N2 — Filter keys (`s`/`b`/`o`) are now consistently replacing.** `b`
  and `o` used to be additive while `s` cleared first; same UI affordance,
  different semantics. All three now clear-then-select.
- **N7 — `codesign --verify` timeouts no longer misclassified as caution.**
  When `codesign --verify` exceeds 20 s (e.g. on Xcode), the finding now
  emits severity `info` with title "Signature scan inconclusive: …",
  rather than mislabelling a slow scan as "Signature issue".

### Added

- **N5 — Confirm box itemises the top selected categories.** Users who
  built selections across `s`/`b`/`o`/Space sometimes saw a different
  selection at confirm than they expected. The confirm dialog now lists
  the top 5 selected categories with their sizes plus a `+ N more …`
  spillover line.
- **N9 — `Home`/`End`/`PgUp`/`PgDn` keybindings documented in README.**
  These already worked in the TUI; they're now in the keybindings tables
  for both the cache selector and the health-check results, with a
  `Fn+→` note for Macbook keyboards that lack a dedicated End key.
- **M2 — Network-use disclosure in README.** Added a paragraph clarifying
  that Maidbook itself is fully offline, while the optional `pip-audit`
  wrapper does fetch CVE data over HTTPS from the PyPA Advisory Database.

### Tests

Five new regression tests:
- `test_redact_home_replaces_username_anywhere`
- `test_format_findings_redacts_username_in_detail`
- `test_s_filter_selects_by_safety_column`
- `test_filter_keys_are_replacing_not_additive`
- `test_codesign_timeout_is_info_not_caution`

35/35 pytest passing.

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
