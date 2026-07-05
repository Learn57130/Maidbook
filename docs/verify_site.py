#!/usr/bin/env python3
"""Self-check for the Maidbook GitHub Pages site. Stdlib only.

Structural checks are hard requirements (exit 1 on failure).
Network checks (raw README/CHANGELOG reachable + CORS header) are
best-effort: skipped with a note when offline so they never block a commit.
"""
import sys
import urllib.error
import urllib.request
from pathlib import Path

DOCS = Path(__file__).resolve().parent
RAW = "https://raw.githubusercontent.com/Learn57130/Maidbook/main/{}"
REQUIRED = ["index.html", "docs.html", "changelog.html", "site.css", "marked.min.js"]

# each html file must link these local assets
LINKS = {
    "index.html": ["site.css"],
    "docs.html": ["site.css", "marked.min.js"],
    "changelog.html": ["site.css", "marked.min.js"],
}


def check_files():
    errors = []
    for name in REQUIRED:
        if not (DOCS / name).is_file():
            errors.append(f"missing file: docs/{name}")
    for html, needs in LINKS.items():
        p = DOCS / html
        if not p.is_file():
            continue  # already reported above
        text = p.read_text(encoding="utf-8")
        for asset in needs:
            if asset not in text:
                errors.append(f"docs/{html} does not reference {asset}")
    return errors


def check_network():
    """Best-effort: returns a list of note strings. Never raises."""
    notes = []
    for fname in ("README.md", "CHANGELOG.md"):
        url = RAW.format(fname)
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status != 200:
                    notes.append(f"FAIL {fname}: HTTP {resp.status}")
                    continue
                cors = resp.headers.get("access-control-allow-origin")
                if cors is None:
                    notes.append(f"FAIL {fname}: no access-control-allow-origin header")
                else:
                    notes.append(f"ok   {fname}: 200, CORS={cors}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            notes.append(f"skip {fname}: network unavailable ({e})")
    return notes


def main():
    errors = check_files()
    print("== structural ==")
    if errors:
        for e in errors:
            print("  FAIL", e)
    else:
        print("  ok   all files present and linked")

    print("== network (best-effort) ==")
    for n in check_network():
        print("  ", n)

    if errors:
        print("\nRESULT: FAIL")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
