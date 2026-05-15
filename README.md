<p align="center">
  <img src="https://raw.githubusercontent.com/Learn57130/Maidbook/main/assets/banner.png" alt="Maidbook — the tidy Mac keeper" width="100%" />
</p>

# Maidbook <img src="https://raw.githubusercontent.com/Learn57130/Maidbook/main/assets/logo.png" align="right" width="120" />

[![PyPI version](https://img.shields.io/pypi/v/maidbook.svg)](https://pypi.org/project/maidbook/)
[![Python versions](https://img.shields.io/pypi/pyversions/maidbook.svg)](https://pypi.org/project/maidbook/)
[![License: MIT](https://img.shields.io/pypi/l/maidbook.svg)](https://github.com/Learn57130/Maidbook/blob/main/LICENSE)
[![CI](https://github.com/Learn57130/Maidbook/actions/workflows/ci.yml/badge.svg)](https://github.com/Learn57130/Maidbook/actions/workflows/ci.yml)

A tidy cache cleaner + health check for macOS. Single-binary TUI, stdlib-only, no install bloat.

```
╭─ ● Maidbook ────────────────────────────────────────────────────────╮
│  the tidy Mac keeper                                                │
╰─────────────────────────────────────────────────────────────────────╯

● What would you like to do?

  ❯ ● Cache cleaner     free up disk space · safe for cookies, history, logins
    ○ Health check      malware heuristics · code-sign audit · XProtect · CVE scan
    ○ Both              clean caches first, then run health check
    ○ Agent tools       browse Claude / Codex / Gemini skills + MCP server configs
    ○ Stats             lifetime freed · session history · bloat velocity trend
    ○ Manage schedule   view or remove the scheduled automatic clean

  ↑/↓ move · ↵ select · q quit
```

## Why?

Most Mac cleaners either want $40 a year or do scary kernel things. Maidbook is
a single-file Python tool that:

- **Cleans caches safely** — browsers keep their cookies, history, logins, and bookmarks. It only touches `Cache/`, `Code Cache/`, and `GPUCache/` subfolders.
- **Finds build artifacts** — recursively discovers `node_modules/`, `target/`, `venv/`, `__pycache__`, and similar dev-artifact directories across your project roots. Each artifact dir becomes its own selectable row.
- **Uses vendor tools when possible** — `pip cache purge`, `npm cache clean`, `brew cleanup -s --prune=all`. Falls back to `shutil.rmtree` otherwise.
- **Runs a read-only health check** — XProtect status, LaunchAgent heuristics, `codesign --verify` across `/Applications`, Gatekeeper quarantine review, outdated packages via `pip-audit` / `brew outdated` / `npm outdated`, plus AI agent skill and MCP config audits.
- **Auto-discovers** every folder under `~/Library/Caches/` and classifies it (`safe` / `caution` / `review`) so nothing goes missing.
- **Schedules automatic cleans** — installs a launchd job that runs headless cron mode on your chosen interval, logging results to `~/.maidbook/logs/`.
- **Tracks lifetime stats** — persistent analytics across all sessions (total freed, bloat velocity, average per session).

## ⚠️ Honest scope note

The **Health Check** is **not antivirus**. It wraps built-in macOS tools to surface obvious issues. For real signature-based malware scanning use Malwarebytes, Sophos, or Bitdefender. Maidbook will never claim to replace them.

**Network use.** Maidbook itself never makes network connections — no analytics, no telemetry, no auto-update. The optional vulnerability scanner it wraps (`pip-audit`) connects to the PyPA Advisory Database over HTTPS to fetch CVE data. If you want fully offline operation, skip the Health Check or don't install `pip-audit`. `brew outdated` and `npm outdated -g` are local-only by default but may trigger their own update fetches depending on configuration.

## Install

### Quick check — do you have what you need?

Open Terminal and run:

```bash
python3 --version    # need 3.9 or newer (already on every modern Mac)
which pipx           # if it prints a path, you're set
```

If `pipx` prints a path, skip ahead to **With pipx (recommended)**. If
`pipx` says "not found", do the **First-time setup** below — it takes
about two minutes and is one-time only.

### First-time setup (only if you don't have `pipx`)

Maidbook needs `pipx` to install cleanly. The easiest way to get `pipx`
on macOS is via Homebrew. If you've never installed developer tools on
this Mac, work through the steps below in order.

**1. Install Homebrew** — the macOS package manager.

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

When the installer finishes, it prints **two `eval` lines** at the
bottom telling you to add `brew` to your shell. Copy and paste both
into Terminal — without that step, `brew` won't be on your `PATH`.

Verify:

```bash
brew --version
```

**2. Install `pipx`.**

```bash
brew install pipx
pipx ensurepath
```

**Close and reopen Terminal** so the new `PATH` takes effect, then
verify:

```bash
pipx --version
```

You're ready for the install step below.

> **Don't want Homebrew?** You can skip Homebrew entirely and use the
> **With pip (alternative)** section further down — it installs Maidbook
> straight into your Python user-scripts dir. Slightly more `PATH`
> finicky, but pure Python and no extra package manager.

### With pipx (recommended)

[`pipx`](https://pipx.pypa.io/) installs each Python tool in its own isolated
venv and puts a symlink in `~/.local/bin/`. No conflicts with system Python
or other packages, and uninstalling is one command.

```bash
git clone https://github.com/Learn57130/Maidbook.git
cd Maidbook
pipx install .
```

Now `maidbook` works from any directory.

To update later:

```bash
cd Maidbook && git pull && pipx install --force .
```

To uninstall:

```bash
pipx uninstall maidbook
```

> **Heads-up:** if you run `pipx uninstall maidbook` while you're `cd`'d into
> the cloned `Maidbook/` folder (or any parent of it), pipx errors out with
> `'maidbook' looks like a path`. macOS's filesystem is case-insensitive, so
> `maidbook` resolves to the `Maidbook/` directory and pipx refuses to treat
> it as a package name. Run the command from a neutral directory — e.g.
> `(cd /tmp && pipx uninstall maidbook)` — or just `cd ~` first.

### With pip (alternative)

```bash
git clone https://github.com/Learn57130/Maidbook.git
cd Maidbook
pip install --user .
```

The binary lands in your Python user scripts dir — often
`~/Library/Python/3.x/bin/maidbook` on macOS. If that's not on your `PATH`,
either add it or use `pip install -e .` for an editable dev install.

### From PyPI

The fastest path — one line, no `git clone`:

```bash
pipx install maidbook
```

Or, if you'd rather use plain pip:

```bash
pip install --user maidbook
```

To upgrade later: `pipx upgrade maidbook` (or `pip install -U --user maidbook`).

## Usage

```bash
maidbook              # launch the TUI
maidbook --cli        # plain-text CLI (no curses)
maidbook --cli --all  # clean every category, no prompts (DANGER)
maidbook --dry-run    # scan only, nothing deleted
maidbook --cron       # headless mode: clean persisted selection, JSON output
maidbook --schedule   # install a weekly launchd job (runs --cron automatically)
maidbook --schedule daily   # daily instead of weekly
maidbook --unschedule # remove the launchd scheduled clean
maidbook --history    # print the last 10 cron-session log entries
maidbook --stats      # print lifetime cleaning statistics
maidbook --version
```

### TUI keybindings

**Menu**

| Key | Action |
|---|---|
| `↑` `↓` / `j` `k` | Move selection |
| `↵` `Space` | Confirm |
| `q` | Quit |

**Cache selector**

| Key | Action |
|---|---|
| `↑` `↓` / `j` `k` | Move cursor |
| `G` | Jump to last row |
| `gg` | Jump to first row |
| `Home` | Jump to first row |
| `End` (`Fn`+`→` on Macbook) | Jump to last row |
| `PgUp` / `PgDn` | Move 5 rows at a time |
| `Space` | Toggle selection |
| `A` | Select all (skips pinned rows) |
| `N` | Deselect all |
| `s` | Select everything with safety = `safe` |
| `b` | Select browsers (replaces current selection) |
| `o` | Select auto-discovered (replaces current selection) |
| `v` | Select dev-artifacts (replaces current selection) |
| `w` | Pin / unpin current row (pinned rows are skipped by `A` and cron) |
| `d` | Toggle dry-run mode |
| `r` | Rescan |
| `↵` | Open action-choice screen (Clean now / Schedule clean) |
| `q` | Quit |

**Action-choice screen** (shown after `↵` in the cache selector)

| Key | Action |
|---|---|
| `↑` `↓` / `j` `k` | Move between "Clean now" / "Schedule clean" |
| `↵` | Confirm choice |
| `n` `Esc` | Back to cache selector |
| `q` | Quit |

**Health check results**

| Key | Action |
|---|---|
| `↑` `↓` / `j` `k` | Scroll one line |
| `G` | Jump to last finding |
| `gg` | Jump to first finding |
| `Home` | Jump to first finding |
| `End` (`Fn`+`→` on Macbook) | Jump to last finding |
| `PgUp` / `PgDn` | Scroll 10 findings at a time |
| `C` | Copy full report to clipboard |
| `r` | Rescan |
| `m` | Back to menu |
| `q` | Quit |

**Agent tools browser**

| Key | Action |
|---|---|
| `↑` `↓` / `j` `k` | Move cursor |
| `x` | Mark entry for removal |
| `y` | Confirm removal of marked entry |
| `r` | Rescan |
| `m` | Back to menu |
| `q` | Quit |

## Safety

- **No Trash integration.** Deletions are permanent. Dry-run first if you're unsure.
- **Confirmation required** before any deletion in the TUI.
- **Browser caches only**, never browser profile data.
- **Running browsers are skipped** automatically via `pgrep`.
- **Auto-discovered rows default to `review`** — the user decides, we don't.
- **Pinned rows** (`⊘`) are excluded from `A` select-all, tag filters, and cron runs.
- **Build artifact rows default to `caution`** — they'll cost a rebuild, so they're never auto-selected.
- **The Health Check is read-only** — it reports, never modifies.
- **Agent tools removal requires `x` + `y` double-confirm** — the browser never removes without explicit confirmation.
- **Async deletion** — large trees are renamed into a trash staging area instantly, then deleted in the background. The summary always distinguishes "Freed: X" from "Freed: X (Y still finalizing in background)."

## What's in the Health Check

| Module | What it does |
|---|---|
| **XProtect status** | Reads Apple's malware signature plist, flags >45 d old |
| **Malware heuristics** | Known adware path signatures (MacKeeper / Genieo / Pirrit / Shlayer / Silver Sparrow) + LaunchAgents from unknown vendors |
| **Code-sign audit** | `codesign --verify --strict` across `/Applications` + `~/Applications` |
| **Quarantine review** | Files in `~/Downloads` / `~/Desktop` still flagged by Gatekeeper |
| **Vulnerability check** | Wraps `pip-audit`, `brew outdated`, `npm outdated -g` when available |
| **Agent skill audit** | Broken symlinks, orphan SKILL.md files, suspicious shell hooks in `~/.claude/skills/`, `~/.codex/skills/`, `~/.gemini/` |
| **MCP config check** | Validates command existence for each MCP server in Claude / Gemini configs; flags missing executables |

## Build artifact scanner

Maidbook v0.3 recursively discovers dev-build artifacts across common project
roots (`~/Developer`, `~/Projects`, `~/repos`, `~/code`, `~/Desktop`,
`~/Documents`):

| Artifact | Why it's flagged |
|---|---|
| `node_modules/` | npm/yarn/pnpm install cache |
| `target/` | Rust / Java / Scala build output |
| `.build/` | Swift Package Manager |
| `build/` `dist/` | Python/JS generic build (only when a project file sibling is found) |
| `venv/` `.venv/` | Python virtual environments |
| `__pycache__/` | Python bytecode caches |

Each artifact dir becomes its own selectable row tagged `dev-artifacts`,
safety `caution`. Filter them in the TUI with `[v]`.

**`.maidbook-keep` sentinel** — drop a `.maidbook-keep` file in any project
root and Maidbook will skip that project entirely during artifact scanning.

## Scheduled automatic clean

Set up a recurring clean from inside the TUI (cache selector → `↵` → "Schedule clean")
or from the command line:

```bash
maidbook --schedule          # weekly, runs every Sunday at 03:00
maidbook --schedule daily    # daily at 03:00
maidbook --unschedule        # remove
```

The schedule installs a launchd job under
`~/Library/LaunchAgents/com.maidbook.cron.plist`. It runs
`maidbook --cron` automatically and logs results to `~/.maidbook/logs/`.

When you schedule from inside the TUI, the **exact categories you had
selected** are persisted — cron will clean those and only those.

```bash
maidbook --history    # print the last 10 cron-session summaries
maidbook --cron       # run once immediately (JSON output to stdout)
maidbook --cron --dry-run   # dry run, nothing deleted
```

The "Manage schedule" TUI menu item shows the current status and lets
you remove the schedule with `↵`.

## Analytics & stats

Maidbook tracks every session in `~/.maidbook/stats.json`:

- **Lifetime freed** — cumulative bytes reclaimed across all runs.
- **Session history** — date, amount freed, categories cleaned, duration (capped at 500 entries).
- **Bloat velocity** — cache-size snapshot taken at every TUI scan, so you can see how fast your caches grow.

View the Stats screen inside the TUI (menu → "Stats") or from the CLI:

```bash
maidbook --stats
```

## Agent tools browser

The **Agent tools** TUI menu opens a browser that discovers and audits your
local AI infrastructure:

- **Claude Code / Codex / Gemini skills** — flags broken symlinks (`caution`),
  orphan `SKILL.md` files (`info`), and suspicious shell hooks in skill
  frontmatter (`review`).
- **MCP server configs** — parses `~/.claude/mcp.json`, Claude Desktop config,
  and `~/.gemini/settings.json`; validates that each server's command exists on
  disk; flags duplicates by inode.

The browser is read-only by default. Press `x` to mark an entry for removal,
then `y` to confirm. Only broken / stale entries can be removed; healthy entries
are protected.

## ASCII mascot

When your terminal is wider than 100 columns, Maidbook shows a small reactive
character in the banner area:

| State | Trigger |
|---|---|
| Tidy | Total cache < 500 MB |
| Messy | 500 MB – 2 GB |
| Chaos | > 2 GB |

## Requirements

**Required**

- **macOS** (Monterey 12+, tested on Sequoia 15)
- **Python 3.9+** (already on every modern Mac)

**Optional — Maidbook works without these, but they unlock more findings**

| Tool | What you get if it's installed |
|---|---|
| [`pipx`](https://pipx.pypa.io/) | The cleanest install path (see *Install* above) |
| [`pip-audit`](https://pypi.org/project/pip-audit/) | Python package CVE scan in the Health Check |
| [Homebrew](https://brew.sh/) (`brew`) | "Outdated formula" check in the Health Check |
| [Node.js](https://nodejs.org/) (`npm`) | npm cache cleaning + "outdated global package" check |

If none of the optional tools are installed, Maidbook still runs — the
relevant rows just appear as `info: not installed` instead of producing
findings. Nothing crashes; nothing pesters you to install anything.

Linux is not a target — several checks (XProtect, `codesign`, `xattr`, `pbcopy`) are macOS-specific.

## Roadmap — v0.4 ideas

A lean list of what might come next. Nothing committed — open an issue if you want to discuss scope or grab one to contribute.

- **Within-category progress** — show the current path / file count inside the clean screen so large trees feel less opaque.
- **Configurable scan roots** — let users add custom project root directories to the artifact scanner via a lightweight config file.
- **Scheduled clean editor** — edit the schedule interval and time from inside the TUI without running `--unschedule` + `--schedule` again.
- **Risk grading** — finer tiering (low / medium / high) displayed alongside the existing `safe / caution / review` safety column.
- **Post-clean diff** — a "what changed" summary after each session (categories, sizes before/after) exported to the stats log.
- **Multi-profile support** — handle macOS fast-user-switching by scoping `~/.maidbook/` to the current user UID.

Open an issue if you want to discuss scope on any of these, or want to grab one to contribute.

## Contributing

PRs welcome. Issues especially welcome — tell us when a cache shouldn't have been cleaned, or when a health finding was wrong. See `CLAUDE.md` for project-layout notes.

## License

MIT. See [LICENSE](./LICENSE).

## Acknowledgments

UI aesthetic borrowed from Claude Code — the rounded cards, braille spinner, and restrained amber palette.
