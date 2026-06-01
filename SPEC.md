# Spec — Maidbook

> Mirror of the Spec sub-page (push-only). Maidbook is a shipped product — canonical product docs are `README.md` + `CHANGELOG.md`; this is the agent-facing summary.

## What it is

A tidy **cache cleaner + health check for macOS**. Single-file Python TUI, stdlib-only, no install bloat. On PyPI (`maidbook`), MIT-licensed, CI green.

## Capabilities

- **Safe cache cleaner** — browsers keep cookies/history/logins; only touches `Cache/`, `Code Cache/`, `GPUCache/`.
- **Build-artifact scanner** — finds `node_modules/`, `target/`, `venv/`, `__pycache__/`, etc. across project roots; respects a `.maidbook-keep` sentinel.
- **Vendor-aware purge** — `pip cache purge`, `npm cache clean`, `brew cleanup`; `shutil.rmtree` fallback. Async mv-then-rmtree on APFS.
- **Read-only health check** — XProtect, LaunchAgent heuristics, `codesign --verify` across `/Applications`, Gatekeeper quarantine, outdated pkgs (pip-audit/brew/npm), + AI agent skill & MCP config audit.
- **Auto-discovery** of `~/Library/Caches/` with safe/caution/review classification.
- **Scheduled auto-clean** (cron) + lifetime analytics/stats.

## Acceptance (v0.3.1)

Shipped to PyPI with green CI (110/110 tests, macOS · Python 3.9/3.11/3.13); safe-by-default (read-only health check, browsers keep their data, no kernel tricks); stdlib-only single binary.
