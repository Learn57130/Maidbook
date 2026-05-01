<p align="center">
  <img src="assets/banner.png" alt="Maidbook — the tidy Mac keeper" width="100%" />
</p>

# Maidbook <img src="assets/logo.png" align="right" width="120" />

A tidy cache cleaner + health check for macOS. Single-binary TUI, stdlib-only, no install bloat.

```
╭─ ● Maidbook ────────────────────────────────────────────────────────╮
│  the tidy Mac keeper                                                │
╰─────────────────────────────────────────────────────────────────────╯

● What would you like to do?

  ❯ ● Cache cleaner     free up disk space · safe for cookies, history, logins
    ○ Health check      malware heuristics · code-sign audit · XProtect · CVE scan
    ○ Both              clean caches first, then run health check

  ↑/↓ move · ↵ select · q quit
```

## Why?

Most Mac cleaners either want $40 a year or do scary kernel things. Maidbook is
a single-file Python tool that:

- **Cleans caches safely** — browsers keep their cookies, history, logins, and bookmarks. It only touches `Cache/`, `Code Cache/`, and `GPUCache/` subfolders.
- **Uses vendor tools when possible** — `pip cache purge`, `npm cache clean`, `brew cleanup -s --prune=all`. Falls back to `shutil.rmtree` otherwise.
- **Runs a read-only health check** — XProtect status, LaunchAgent heuristics, `codesign --verify` across `/Applications`, Gatekeeper quarantine review, outdated packages via `pip-audit` / `brew outdated` / `npm outdated`.
- **Auto-discovers** every folder under `~/Library/Caches/` and classifies it (`safe` / `caution` / `review`) so nothing goes missing.

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

```bash
# coming later
pip install maidbook
```

## Usage

```bash
maidbook              # launch the TUI
maidbook --cli        # plain-text CLI (no curses)
maidbook --cli --all  # clean every category, no prompts (DANGER)
maidbook --dry-run    # scan only, nothing deleted
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
| `Home` | Jump to first row |
| `End` (`Fn`+`→` on Macbook) | Jump to last row |
| `PgUp` / `PgDn` | Move 5 rows at a time |
| `Space` | Toggle selection |
| `A` | Select all |
| `N` | Deselect all |
| `s` | Select everything with safety = `safe` |
| `b` | Select browsers (replaces current selection) |
| `o` | Select auto-discovered (replaces current selection) |
| `d` | Toggle dry-run mode |
| `r` | Rescan |
| `↵` | Clean (requires confirmation) |
| `q` | Quit |

**Health check results**

| Key | Action |
|---|---|
| `↑` `↓` / `j` `k` | Scroll one line |
| `Home` | Jump to first finding |
| `End` (`Fn`+`→` on Macbook) | Jump to last finding |
| `PgUp` / `PgDn` | Scroll 10 findings at a time |
| `C` | Copy full report to clipboard |
| `r` | Rescan |
| `m` | Back to menu |
| `q` | Quit |

## Safety

- **No Trash integration.** Deletions are permanent. Dry-run first if you're unsure.
- **Confirmation required** before any deletion in the TUI.
- **Browser caches only**, never browser profile data.
- **Running browsers are skipped** automatically via `pgrep`.
- **Auto-discovered rows default to `review`** — the user decides, we don't.
- The **Health Check is read-only** — it reports, never modifies.

## What's in the Health Check

| Module | What it does |
|---|---|
| **XProtect status** | Reads Apple's malware signature plist, flags >45 d old |
| **Malware heuristics** | Known adware path signatures (MacKeeper / Genieo / Pirrit / Shlayer / Silver Sparrow) + LaunchAgents from unknown vendors |
| **Code-sign audit** | `codesign --verify --strict` across `/Applications` + `~/Applications` |
| **Quarantine review** | Files in `~/Downloads` / `~/Desktop` still flagged by Gatekeeper |
| **Vulnerability check** | Wraps `pip-audit`, `brew outdated`, `npm outdated -g` when available |

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

## Roadmap — v0.2 planned

A lean list of what's next. Order is priority-ish, not strict.

- **Intelligent cache discovery** — signature-based scanning for dev-heavy
  artifacts (`node_modules`, `target/`, `docker`, `__pycache__`) with
  automated path detection across your projects dir.
- **Risk grading** — finer tiering of deletable items by "risk level"
  (low / medium / high) to distinguish safe log cleanup from time-costly
  build re-compilation.
- **Headless cron mode** — a `--cron` flag for automated background purges
  based on user-defined TTL (time-to-live) rules.
- **Smart whitelisting** — toggle-based "pinning" in the TUI to protect
  specific active projects from automated cleanup.
- **Quantitative analytics** — persistent tracking (JSON / SQLite) of
  cumulative space saved and "bloat velocity" (GB growth over time).
- **Post-action reporting** — summary logs after each cron execution for
  full transparency on what was reclaimed.
- **ASCII mascot integration** — a reactive minimalist mascot inside the
  TUI that changes state based on how tidy the system is.
- **AI-agent skill audit** — a 6th health module that scans `~/.claude/`,
  `~/.codex/`, `~/.gemini/` skill directories for broken symlinks, orphan
  `SKILL.md` files, and suspicious shell hooks in frontmatter. Read-only
  audit only; full skill management (sync, install, uninstall) stays out
  of scope and lives in dedicated tooling.

Open an issue if you want to discuss scope on any of these, or want to grab
one to contribute.

## Contributing

PRs welcome. Issues especially welcome — tell us when a cache shouldn't have been cleaned, or when a health finding was wrong. See `CLAUDE.md` for project-layout notes.

## License

MIT. See [LICENSE](./LICENSE).

## Acknowledgments

UI aesthetic borrowed from Claude Code — the rounded cards, braille spinner, and restrained amber palette.
