# Maidbook website — design spec

**Date:** 2026-07-05
**Status:** approved (approach), pending spec review
**Goal:** A static landing page + docs, hosted free on GitHub Pages, that looks
like the Maidbook TUI and keeps the README as the single source of truth for docs.

## Decisions (already made)

- **Scope:** landing page **and** docs (front door + reference).
- **Docs source:** single source of truth — docs pages render the existing
  `README.md` / `CHANGELOG.md`; those files are never duplicated or edited.
- **Approach B (static + client-side render):** no build step, no Jekyll.
  Docs pages `fetch()` the raw markdown from GitHub and render it with one
  vendored `marked.min.js`. README/CHANGELOG stay untouched at repo root.
- **Hosting:** GitHub Pages, deploy from `main` branch, `/docs` folder.
  Live URL: `https://learn57130.github.io/Maidbook/`.

## File layout

Everything the site needs lives under `/docs` (Pages publishing root):

```
docs/
├── index.html        landing page (bespoke, self-contained)
├── docs.html         renders README.md into site chrome
├── changelog.html    renders CHANGELOG.md into site chrome
├── site.css          shared terminal-aesthetic stylesheet
├── marked.min.js     vendored markdown renderer (one file, pinned version)
└── superpowers/      (dev-only specs — not linked from the site)
```

Reused, not copied: `assets/logo.png`, `assets/banner.png` (referenced by raw
GitHub URL or relative path — see Data flow).

## Aesthetic

Lean into the TUI identity so the site reads as the same product:

- Dark background, amber accent = curses color 208 ≈ `#ff8700`.
- Rounded box-drawing cards (`╭ ╮ ╰ ╯`), `● ○ ❯` bullet markers.
- Monospace type for chrome/code; the TUI menu screenshot reproduced as
  styled HTML (from the README's ASCII block), not a bitmap, so it stays crisp.
- Palette stays inside the Claude-Code lineage described in CLAUDE.md.

## Page contents

**`index.html` (landing):**
1. Hero — `logo.png`, one-line tagline ("the tidy Mac keeper"), buttons to
   PyPI and GitHub.
2. The TUI menu rendered as a styled rounded card (the `╭─ ● Maidbook ─╮` block).
3. 3–4 feature cards: safe cache clean · build-artifact discovery · read-only
   health check · agent (skill/MCP) tools.
4. `pip install maidbook` copy-to-clipboard block.
5. Footer: MIT, links to Docs / Changelog / GitHub / PyPI.

**`docs.html` / `changelog.html`:** shared header + nav, body = rendered
markdown fetched at runtime.

## Data flow (Approach B)

1. `docs.html` loads → `fetch(RAW_README_URL)` where
   `RAW_README_URL = https://raw.githubusercontent.com/Learn57130/Maidbook/main/README.md`.
2. `marked.parse(text)` → inject HTML into the content container.
3. `changelog.html` does the same for `CHANGELOG.md`.

- `raw.githubusercontent.com` returns `access-control-allow-origin: *`, so the
  cross-origin `fetch` is allowed. (Verified at build time — see Testing.)
- Images in the README use absolute `raw.githubusercontent.com` URLs already,
  so they resolve unchanged once rendered.

## Error handling

- If a `fetch` fails (offline / GitHub down / non-200): show a short inline
  message with a direct "view on GitHub" link to the same file. The docs page
  never renders blank.
- `marked.min.js` is vendored (not a CDN link) so the render layer has no
  external runtime dependency and no CSP surprises.

## Testing (the one check)

A single runnable check — `docs/verify_site.py` (stdlib only, matches the
project's testing style):

1. Assert `index.html`, `docs.html`, `changelog.html`, `site.css`,
   `marked.min.js` exist under `docs/`.
2. Assert each HTML file references `site.css` and (docs/changelog) `marked.min.js`.
3. Assert the raw README + CHANGELOG URLs return HTTP 200 **and** an
   `access-control-allow-origin` header (the CORS assumption B depends on).
   Skip network asserts gracefully if offline (`urllib` timeout → xfail note),
   so it never blocks a commit.

Run: `python docs/verify_site.py`.

## Out of scope (YAGNI)

- No custom domain, no analytics, no cookie banner.
- No framework, bundler, or CSS preprocessor.
- No CI workflow for the site — Pages serves the static files directly.
- No search, no versioned docs. Add only if asked.

## Enabling Pages (one-time, manual)

Repo → Settings → Pages → Source: "Deploy from a branch" → Branch: `main`,
Folder: `/docs` → Save. First publish takes ~1 min.
