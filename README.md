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

## Install

### With pipx (recommended)

[`pipx`](https://pipx.pypa.io/) installs each Python tool in its own isolated
venv and puts a symlink in `~/.local/bin/`. No conflicts with system Python
or other packages, and uninstalling is one command.

```bash
# one-time: install pipx if you don't have it
brew install pipx
pipx ensurepath          # adds ~/.local/bin to PATH, restart terminal

# then install Maidbook
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
| `Space` | Toggle selection |
| `A` | Select all |
| `N` | Deselect all |
| `s` | Select safe only |
| `b` | Select browsers |
| `o` | Select auto-discovered (other) |
| `d` | Toggle dry-run mode |
| `r` | Rescan |
| `↵` | Clean (requires confirmation) |
| `q` | Quit |

**Health check results**

| Key | Action |
|---|---|
| `↑` `↓` `PgUp` `PgDn` | Scroll findings |
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

- **macOS** (Monterey 12+, tested on Sequoia 15)
- **Python 3.9+**
- Optional: `pip-audit` (unlocks Python CVE checks)

Linux is not a target — several checks (XProtect, `codesign`, `xattr`, `pbcopy`) are macOS-specific.

## Contributing

PRs welcome. Issues especially welcome — tell us when a cache shouldn't have been cleaned, or when a health finding was wrong. See `CLAUDE.md` for project-layout notes.

## License

MIT. See [LICENSE](./LICENSE).

## Acknowledgments

UI aesthetic borrowed from Claude Code — the rounded cards, braille spinner, and restrained amber palette.
