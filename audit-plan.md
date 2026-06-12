# Pre-Announcement Audit Plan

A self-review checklist for Maidbook before public announcement. Designed for
the author (and future contributors) to verify that the project lives up to its
own claims without needing deep Python expertise.

## What we're auditing for

Not code style. Not Python idioms. Five real things:

1. **Truth** — the app does what the README says it does
2. **Safety** — it cannot harm a user who trusts it
3. **Honesty** — no hidden or unexpected behavior
4. **Exposure** — no personal information leaks in the repo
5. **Polish** — it looks like a serious project to a stranger

You can verify all five without reading a line of curses code.

## Estimated time

~2.5 hours total, best split across two sessions:

- **Session 1 (1 hr):** Blocks 1 + 2 — easiest, builds confidence
- **Session 2 (1.5 hr):** Blocks 3 + 4 + 5 + 6 — deeper checks

Do not try to finish it in a single sitting. Audit fatigue makes you miss things.

---

## 📘 Block 1 — Truth audit (30 min)

*"Everything the README claims is real and works."*

Set up a clean environment to simulate a stranger's experience:

```bash
cd /tmp
rm -rf maidbook-audit 2>/dev/null
git clone https://github.com/Learn57130/Maidbook.git maidbook-audit
cd maidbook-audit
```

### Checklist

- [ ] `pip install --user .` works as documented
- [ ] `pipx install .` works as documented
- [ ] `maidbook --version` prints the current version (currently `0.3.1`)
- [ ] `maidbook --cli --dry-run` runs without crashing
- [ ] `maidbook` (TUI) launches cleanly on first try

Every keybinding in the README table actually does what it says:

- [ ] `↑/↓/j/k` moves cursor
- [ ] `Space` toggles selection
- [ ] `A` selects all, `N` deselects all
- [ ] `s / b / o` tag filters work
- [ ] `d` toggles dry-run mode
- [ ] `r` rescans
- [ ] `q` quits cleanly from every screen
- [ ] `↵` proceeds to confirm

Health check:

- [ ] All 7 modules run and produce findings (xprotect, malware, codesign, quarantine, vulns, skills, mcp)
- [ ] `[C]` copies the report to clipboard — paste it somewhere and verify it matches what's on screen

**Red flag:** any keybinding that silently does nothing, or any CLI mode that
produces a traceback. Fix before announcing.

---

## 🔒 Block 2 — Personal exposure audit (15 min)

*"Nothing in the repo identifies me beyond what I intend."*

```bash
cd /tmp/maidbook-audit

# Search for real name / username / specific account handles
grep -rn "natthaphongsaruasawan" . --include='*.py' --include='*.md' --include='*.toml' --include='*.yml'

# Hardcoded absolute paths
grep -rn "/Users/" maidbook/ tests/

# Anything resembling a secret
grep -rniE "(api[_-]?key|token|secret|password)" . \
    --include='*.py' --include='*.md' --include='*.toml' --include='*.yml' \
  | grep -v test_

# Git committer info (public in every commit)
git log --format='%an <%ae>' | sort -u
```

### Checklist

- [ ] Real name / private username doesn't appear inside code (OK in GitHub URLs in README)
- [ ] No email addresses in source files (except optional `authors` in `pyproject.toml`)
- [ ] No `/Users/yourname/...` hardcoded paths — all should be `HOME` or `Path.home()`
- [ ] No API keys, tokens, passwords
- [ ] Git log committer name / email is what you want public
- [ ] `.gitignore` covers `.DS_Store`, `__pycache__`, `.venv`, `.env`

If git log reveals an email you don't want public: that's a bigger conversation,
because it's baked into every commit and hard to rewrite without losing history.

---

## 🕵️ Block 3 — Behavior audit (45 min)

*"The app does nothing sneaky. Every action is what it claims."*

### Dry-run first

```bash
cd /tmp/maidbook-audit
maidbook --cli --dry-run
```

Read the output carefully:

- [ ] Every category listed is something you recognize as a cache
- [ ] Every path shown is actually a cache directory, not something important
- [ ] No category claims to touch `~/Library/Application Support`, `~/Documents`, `~/Desktop`
- [ ] No category claims to touch browser `Cookies`, `History`, `Login Data`, or `Bookmarks`

### Verify the "no network" claim

```bash
# In one terminal:
maidbook

# In another terminal while TUI is running:
lsof -i -P | grep -i python | grep -i maidbook
# Expected output: empty
```

Confirm the codebase itself has no network code:

```bash
grep -rn "import requests\|import urllib\|import http\|urlopen\|import socket" maidbook/
# Expected output: empty
```

- [ ] No network activity during scan or clean
- [ ] No networking imports in the source

### Verify no persistent daemons

```bash
# Run maidbook, quit with q, then check nothing is lingering:
pgrep -fl maidbook
# Expected: empty
```

- [ ] No background processes survive the TUI exit

### Real destructive test (careful, small scope)

Pick one low-risk category to actually delete (e.g. `pip`):

- [ ] Before deletion, note the directory size:
      `du -sh ~/Library/Caches/pip`
- [ ] Run `maidbook`, select only `pip`, clean
- [ ] The UI-reported freed size roughly matches what was there
- [ ] After deletion: `du -sh ~/Library/Caches/pip` shows ~0

If you clean a browser category, verify profile data is intact:

```bash
# Before:
ls ~/Library/Application\ Support/BraveSoftware/Brave-Browser/*/Cookies* 2>/dev/null
# Clean browser-brave via maidbook…
# After:
ls ~/Library/Application\ Support/BraveSoftware/Brave-Browser/*/Cookies* 2>/dev/null
# Both outputs should be identical — Cookies file still exists
```

- [ ] Browser cookies / history / logins are untouched after clean

**Red flag:** anything that deletes files outside `~/Library/Caches/`, `~/.cache/`,
or `~/Library/Developer/Xcode/DerivedData/`. This is the single thing that would
permanently burn user trust.

---

## 🎨 Block 4 — UX stress test (30 min)

*"A real human would have a good experience, not a frustrating one."*

Put yourself in the shoes of a first-time user who hasn't read the code.

### Terminal variations

- [ ] Run `maidbook` on a narrow terminal (~80 cols). Layout still works?
- [ ] Run on a very wide terminal (200+ cols). Doesn't look absurd?
- [ ] Try running when the working directory is `~/Library/Caches`. Still works?

### Exit paths

- [ ] `Ctrl+C` during a scan exits cleanly (no traceback)
- [ ] `q` quits cleanly from every screen (menu, scan, select, confirm, done, health_scan, health_results)

### Human test

- [ ] Watch a non-technical friend or family member use it for 2 minutes. Note what confused them.
- [ ] Read the hover text on a `review`-tagged row. Is the reason understandable in plain English?
- [ ] Copy a health report (`C`). Paste somewhere. Does it read naturally to someone without context?

**Red flag:** anything that makes a new user think *"what do I do now?"* with
no obvious answer.

---

## 🧰 Block 5 — GitHub repo hygiene (15 min)

*"The project page itself gives a good first impression."*

Open https://github.com/Learn57130/Maidbook in a browser as if you've never
seen it.

### Repo landing page

- [ ] README banner image loads (no broken-image icon)
- [ ] README logo loads
- [ ] All markdown renders correctly (tables, code blocks, lists)
- [ ] Install commands in README can be copy-pasted without errors

### Settings

- [ ] **About** panel (top right) has a description — click the ⚙ gear if empty
- [ ] **Topics** tags added (suggested: `macos`, `cli`, `tui`, `python`, `cache`, `cleanup`)
- [ ] **Website** field set (optional — repo URL or landing page)
- [ ] **Issues** tab is enabled (default; check Settings → General)
- [ ] **Discussions** tab is enabled (Settings → General → Features)
- [ ] **Social preview image** uploaded (Settings → Social preview → banner.png)

### Quality signals

- [ ] CI is green on `main` (Actions tab shows a green check)
- [ ] LICENSE file renders as "MIT" on the repo landing page
- [ ] No large unintended files (> 1 MB) in the repo:
      ```bash
      git ls-files -s | sort -k3 -h | tail -5
      ```

---

## 🚀 Block 6 — Final go/no-go (10 min)

Three hard questions. If the answer to any is **no**, don't announce yet.

1. **Would I install this tool on my mother's Mac?**
   If no → something about trust or safety is off. Find it.

2. **If a stranger reads my code, will current-me feel proud or embarrassed?**
   If embarrassed → fix the embarrassing parts first. Your work is a reflection of you.

3. **If a stranger uses this tool and files a bug, can I realistically respond within a week?**
   If no → wait a few days until you have the bandwidth. Announcing and ignoring the response is worse than not announcing.

If all three are **yes** → **announce.**

---

## Tools that help during the audit

- **`git grep <pattern>`** — search anywhere in tracked files (faster than `grep -rn`)
- **`git log --stat`** — see every commit's file changes at a glance
- **`git show HEAD`** — full diff of the latest commit
- **`git diff v0.3.1..HEAD`** — everything that's changed since your latest release tag
- **Octotree browser extension** — makes GitHub's file tree easier to navigate
- **`grip`** (`pip install grip`) — renders your README locally exactly as GitHub would

## What to do when something looks wrong

Don't try to fix it mid-audit. Write it down in a TODO list and finish the
block. Fix afterwards. Half-finished audits miss things.

If a check makes you go *"what does this actually do?"*, paste the 10–20 lines
of code somewhere and ask a more experienced developer — or just walk away and
come back to it later. Understanding beats rushing.

---

**Remember:** this audit isn't about making the code perfect. It's about
making sure what you *ship* matches what you *claim*. That's the only thing
that matters on announcement day.
