# CLAUDE.md — developer notes

Guidance for Claude (and humans) picking up this codebase. Read this before
suggesting changes.

## Project shape

```
maidbook/
├── maidbook/
│   ├── __init__.py        version string, a couple of re-exports
│   ├── __main__.py        argparse entry, `maidbook` command
│   ├── common.py          utilities, constants, glyphs — ZERO internal deps
│   ├── cache.py           Category + all cache scanners/cleaners
│   ├── health.py          Finding + HealthModule + 7 read-only scanners
│   ├── agents.py          AI skill + MCP server discovery and management
│   ├── tui.py             curses UI, TUI class, state machine
│   └── cli.py             plain-text fallback for --cli
├── pyproject.toml         setuptools-based, stdlib-only runtime deps
├── README.md              user-facing docs
├── CHANGELOG.md           releases
├── CLAUDE.md              this file
├── LICENSE                MIT
└── .github/workflows/
    └── ci.yml             macOS smoke test
```

**Dependency graph (one-way):**

```
common  ←  cache   ←  tui  →  cli
        ←  health  ←  agents  ←
        ←  agents  ←
```

i.e., `health` depends on `agents` (for `scan_skills` / `scan_mcp_configs`).

`common.py` has no internal imports. If you add a helper, start there.

## Design principles

1. **Stdlib only.** No runtime dependencies. This is a deliberate constraint —
   `pip install maidbook` should pull nothing.
2. **Read-only when in doubt.** The health check never modifies files.
3. **Profile data is sacred.** Browser cleaners only touch `Cache/`, `Code Cache/`,
   `GPUCache/`. Never `Cookies`, `History`, `Bookmarks`, `Login Data`, etc.
4. **Auto-discovered = suspicious.** Anything Maidbook found but doesn't
   recognize gets the `review` tag. Users opt in, never the other way around.
5. **Claude Code UI lineage.** Rounded cards (`╭╮╰╯`), amber accent (color 208),
   braille spinner (`⠋⠙⠹…`), bullet markers (`●○❯`). Stay in that palette.

## The TUI state machine

```
             ┌──────────────────┐
   launch ──▶│       menu       │── q ──▶ exit
             └─────┬────────────┘
                   │ ↵ pick "cache" | "health" | "both" | "agents" | "stats" | "schedule"
       ┌───────────┼────────────────┬──────────────┬──────────┬───────────┐
       ▼           ▼                ▼              ▼          ▼           ▼
  ┌────────┐  ┌──────────┐   ┌──────────────┐ ┌────────┐ ┌─────────┐ ┌──────────┐
  │ scan   │  │ scan     │   │ health_scan  │ │agents_ │ │  stats  │ │ schedule │
  │ (cache)│  │ (cache)  │   │              │ │  scan  │ │ (leaf)  │ │  (leaf,  │
  └───┬────┘  └───┬──────┘   └──────┬───────┘ └────┬───┘ │  m·q)   │ │ Manage)  │
      │           │                 │ done         │ done└─────────┘ └──────────┘
      │ done      │ done            ▼              ▼
      ▼           ▼          ┌────────────────┐ ┌──────────────┐
  ┌────────────────┐         │ health_results │ │agents_browse │
  │    select      │         │  ↵ rescan / m  │ │ x/r/m        │
  │  space toggle  │         └────────────────┘ └──────────────┘
  │  ↵ confirm     │
  └────────┬───────┘
           │ ↵
           ▼
   ┌────────────────┐
   │ action_choice  │  ── n/Esc ──▶ select  ── q ──▶ exit
   │  Clean now     │
   │  Schedule …    │
   └──┬─────────┬───┘
   Clean now │  Schedule clean (overlay sub-states inside action_choice):
      │      │     • schedule_picking      — pick "daily" / "weekly"
      │      │     • schedule_time_picking — pick HH / MM
      │      │     ↵ install → calls schedule_cron(), back to "menu"
      ▼      │
   ┌────────────┐
   │  confirm   │ ── n/Esc ──▶ select   ── q ──▶ exit
   └─────┬──────┘
         │ y
         ▼
   ┌────────────┐  q/Esc ──▶ stop_requested.set()
   │   clean    │──── worker finishes ──▶ done
   └────────────┘                            │
                                             │ plan=="both" → health_scan
                                             │ else        → select (via ↵)
                                             ▼
                                         (loops)
```

Mode lives in `TUI.mode`. Every keypress handler checks `self.mode` first and
`continue`s out so draw logic stays isolated. The `schedule_picking` and
`schedule_time_picking` flags are overlay flags read while `mode ==
"action_choice"` — not separate top-level modes.

## Adding a new cache category

1. Write a `scan()` returning `(bytes, file_count, dir_count)` and a
   `clean(dry)` returning `(bytes_freed, errors, message)`. Use
   `path_stats()` / `rm_path()` from `common`.
2. Append a `Category(...)` to `build_categories()` in `cache.py` with a
   sensible `safety` label. "safe" means it rebuilds automatically.
3. That's it — TUI, CLI, and health scan discovery all pick it up.

## Adding a new health module

1. Write `scan_<thing>() -> list[Finding]` in `health.py`. Each finding has a
   severity (`ok | info | review | caution | risk`), a title, optional detail,
   optional remediation, optional path.
2. Add a `HealthModule(key, name, description, scan_<thing>)` to the
   `HEALTH_MODULES` list.
3. The TUI scans modules concurrently (min(5, len(HEALTH_MODULES)) workers —
   currently 5 of 7 modules run in parallel). Keep your scanner
   **read-only**. If you need to shell out, use `_run_quiet` and handle
   `rc == 127` (command not found) gracefully.
4. Findings sort automatically by severity. Missing tools should be `info`,
   not `caution`.

## Agent skill + MCP management

`agents.py` discovers AI agent infrastructure across Claude Code, Codex,
and Gemini. It provides:

- **`discover_skills()`** — walks `~/.claude/skills/`, `~/.codex/skills/`,
  `~/.gemini/` and returns `SkillEntry` objects with status (ok, broken_symlink,
  orphan, suspicious).
- **`discover_mcp_servers()`** — parses MCP config files (`~/.claude/mcp.json`,
  Claude Desktop config, `~/.gemini/settings.json`) and returns `McpServerEntry`
  objects with command validation.
- **`remove_skill()` / `remove_mcp_server()`** — management actions, only called
  from TUI after explicit `x` + `y` confirmation. `remove_mcp_server` edits the
  JSON config to remove the entry.

The TUI "Agent tools" menu item enters `agents_scan → agents_browse`.
Health modules `scan_skills` and `scan_mcp_configs` delegate to `agents.py`
for discovery and convert results to `Finding` objects.

## Curses gotchas (don't re-learn these)

- **Never write to the bottom-right cell.** It triggers an auto-scroll and
  throws `_curses.error: addwstr() returned ERR`. Use `safe_addstr` /
  `safe_fill` — they subtract 1 from the width on the last row.
- **Set locale BEFORE importing curses.** `common.py` runs
  `locale.setlocale(locale.LC_ALL, "")` at import time so UTF-8 box drawing
  renders. Do not remove that.
- **`os.listxattr` is Linux-only.** On macOS, shell out to `xattr` — that's
  what `_has_quarantine_xattr` does.
- **`curses.napms` raises `KeyboardInterrupt`.** Wrap it in try/except or
  Ctrl-C spits a traceback.
- **Colors need `use_default_colors()`.** Otherwise 256-color terminals get a
  black background override.

## Performance notes

- `path_stats` uses `os.scandir` — DirEntry caches lstat, so it's 3–5× faster
  than `os.walk` on deep cache trees. Don't switch back.
- The scan worker uses `ThreadPoolExecutor(max_workers=16)`. I/O-bound, so
  more workers ≈ more wall-clock speedup until you saturate the SSD.
- Composite scans (`scan_safe_caches`, `make_browser_cleaner.scan`) fan out
  internally so one huge subdirectory doesn't stall the parent task.
- Findings severity sort uses a dict-based rank so order is stable.

### Async deletion (mv-then-rmtree)

Cache deletion uses `common.rm_path_async()` for directories. The trick:

1. `os.rename(cache_dir, ~/.maidbook/trash/<unique>/)` — instant on APFS
   (single inode update), regardless of how many files are inside.
2. A daemon thread `shutil.rmtree`'s the trash subdir in the background.
3. The caller returns `(bytes_moved, 0)` immediately — from the user's
   perspective the cache is *gone*, even though disk reclamation lags
   behind by seconds-to-minutes for huge trees.

`rm_path_async` falls back to synchronous `rm_path` if the rename fails
(cross-filesystem, permission denied), so the honesty contract holds in
either path. Files and symlinks always go through `rm_path` — there's no
perceived-speed gain from deferring a single `unlink`.

**Lifecycle hooks** in `__main__.py`:
- `reap_pending_trash_async()` runs at startup in a daemon thread to
  clean up any orphans left by a previous session crash / force-quit.
  **Must NOT be made synchronous** — a 5 GB orphan tree would freeze
  the UI for tens of seconds before any rendering, defeating the whole
  point of async deletion.
- `wait_for_pending_reaps(timeout=2.0)` runs at exit so small cleans
  finish in-session; bigger ones are left for the next-startup reap.

**Honesty contract for the post-clean summary:** the TUI / CLI clean
summary calls `wait_for_pending_reaps(timeout=5.0)` which returns
`(threads_alive, bytes_pending_in_trash)`. The summary distinguishes:
- `Freed: X` — pending == 0, reapers finished within the wait
- `Freed: X (Y still finalizing in background)` — pending > 0, mv'd to
  trash but rmtree hasn't caught up yet

Never claim space as freed when it's only mv'd to trash. The bytes are
still on disk; they just have a different name.

**Don't switch the cleaners back to `rm_path`** for browser / `~/.cache` /
DerivedData paths — they're the exact case async was built for.

## Testing

Pytest suite under `tests/`, organised by surface:

- `test_common.py` — utility helpers (`human`, `fmt_path`, `path_size`,
  `rm_path`, `is_app_running`), whitelist, stats
- `test_cache.py` — Category scanners + cleaners (pip, brew, browsers,
  discovery, build artifacts)
- `test_health.py` — Finding scanners (xprotect, malware, quarantine,
  vulnerabilities, skills, MCP configs)
- `test_agents.py` — skill discovery, MCP server discovery, removal actions
- `test_cli.py` — argparse plumbing, TUI scan-worker exception isolation,
  cron mode, history, stats
- `test_integration.py` — argparse mode dispatch
- `test_security.py` — hardening regressions (path-injection, symlink
  semantics, partial-deletion honesty, redaction, severity classes)

Filesystem coupling is handled with `tmp_path` + `monkeypatch` — patch
`HOME` / `health.HOME` to point at a fixture directory and build the
expected layout under it. Subprocess-coupled tests use `unittest.mock.patch`
on `_run_quiet` / `subprocess.run`. Don't add real-filesystem dependencies.

CI: `.github/workflows/ci.yml` runs the suite on `macos-latest` across
Python 3.9 / 3.11 / 3.13 in a matrix on every push and PR. Three-version
matrix × parallel ≈ 2 minutes wall-clock. Treat green CI as the
non-negotiable gate before tagging a release.

Run locally:

```bash
pip install ".[test]"
python -m pytest tests/ -v
```

Currently 110/110 passing as of v0.3.1. When you fix a bug, add a test that
locks in the regression in the same commit — the v0.1.1 / v0.1.2 patches
each shipped with named regression tests, and that's the standard.

## Things that have been considered and deliberately left out

- **Trash integration.** `osascript -e 'tell app "Finder" to move …'` is
  possible but slow (~200ms per call) and breaks for root-owned paths. Users
  asking for undo can `Time Machine` or skip the category.
- **Config file.** Every knob currently fits on one keypress. Adding a config
  file means adding parsing, validation, migration — too much for a utility
  this size.
- **Multi-user deployment.** Every path is `~`-relative. No `/etc/…` or
  privileged cleanup. Keep it that way.
- **Cross-platform.** Linux/Windows cache layouts are very different and the
  health checks lean hard on macOS tools (`codesign`, `xattr`, `XProtect`,
  `pbcopy`). Forking is fine; shoehorning would ruin the code.

## When in doubt

Favor: **read-only**, **confirm before destructive**, **honest labels over
clever heuristics**. If a change could surprise a user or delete something
unexpected, surface it — don't hide it behind a `safe` tag.


## Recent Changes

### [Move] 2026-05-16 — repo relocated

Project moved from `~/Desktop/Learn-Level-UP/Project/Maidbook`
to `~/Desktop/Builder/Maidbook`. Git remote unchanged
(`https://github.com/Learn57130/Maidbook.git`).

---

### [Release] v0.3.1 — 2026-05-15

**Current state of the repo.** Everything below is shipped and on PyPI.

- **v0.3.0** — full feature release (build artifacts, agent tools, scheduled
  clean, cron mode, analytics, ASCII mascot, whitelist/pin, Vim navigation,
  async deletion, 7 health modules). Tagged `v0.3.0`, on PyPI.
- **v0.3.1** — docs-only patch: README rewritten to cover all v0.3 features
  (6-item menu, keybindings, new CLI flags, new sections for agent tools /
  scheduled clean / analytics). Needed because PyPI disallows re-uploading
  the same version. Tagged `v0.3.1`, on PyPI.
- **Tests:** 110/110 green (macOS, Python 3.9 / 3.11 / 3.13).
- **Next milestone:** v0.4 — see Roadmap in README.

Files modified in this patch:
- `README.md` (full v0.3 rewrite)
- `maidbook/__init__.py` (0.3.0 → 0.3.1)
- `pyproject.toml` (0.3.0 → 0.3.1)

---

### [Minor Change] 2026-05-15 23:07

Files modified:
- `CHANGELOG.md`

Diff:  1 file changed, 80 insertions(+)

---

### [Minor Change] 2026-05-15 22:29

Files modified:
- `maidbook/tui.py`

Diff:  14 files changed, 2387 insertions(+), 95 deletions(-)

---

### [Minor Change] 2026-05-15 22:28

Files modified:
- `maidbook/tui.py`

Diff:  14 files changed, 2392 insertions(+), 95 deletions(-)

---

### [Minor Change] 2026-05-15 22:25

Files modified:
- `maidbook/cli.py`
- `maidbook/tui.py`

Diff:  14 files changed, 2396 insertions(+), 95 deletions(-)

---

### [Minor Change] 2026-05-15 22:22

Files modified:
- `maidbook/cli.py`
- `maidbook/tui.py`

Diff:  14 files changed, 2369 insertions(+), 95 deletions(-)

---

### [Minor Change] 2026-05-15 22:11

Files modified:
- `maidbook/tui.py`

Diff:  14 files changed, 2349 insertions(+), 95 deletions(-)

---

### [Minor Change] 2026-05-15 15:18

Files modified:
- `maidbook/tui.py`

Diff:  14 files changed, 2348 insertions(+), 94 deletions(-)

---

### [Major Change] 2026-05-15 14:04

Files modified:
- `maidbook/cli.py`
- `maidbook/common.py`
- `maidbook/tui.py`
- `tests/test_cli.py`
- `tests/test_common.py`

Diff:  14 files changed, 2472 insertions(+), 94 deletions(-)

---

