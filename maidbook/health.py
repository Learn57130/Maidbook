"""Health check — defensive read-only scanners.

This is **NOT** antivirus. It surfaces obvious issues using macOS's own tools
(XProtect, codesign, xattr) plus optional pip-audit / brew / npm. Every scanner
is read-only: findings are reported, files are never modified.

Each scanner returns a list of :class:`Finding`. The TUI aggregates them and
sorts by severity (``risk`` → ``caution`` → ``review`` → ``info`` → ``ok``).
"""

from __future__ import annotations

import glob as _glob
import json as _json
import os
import plistlib
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .common import HOME, human


@dataclass
class Finding:
    module: str
    # severity: "ok" | "info" | "review" | "caution" | "risk"
    severity: str
    title: str
    detail: str = ""
    remediation: str = ""
    path: str = ""


@dataclass
class HealthModule:
    key: str
    name: str
    description: str
    scan: Callable[[], list[Finding]]


# Known-bad patterns. Conservative list — false positives are worse than
# misses here because the user will treat anything we flag seriously.
KNOWN_ADWARE_SIGNS = [
    ("MacKeeper", [
        "/Applications/MacKeeper.app",
        "~/Library/Application Support/MacKeeper",
        "~/Library/Application Support/com.mackeeper.MacKeeper",
    ]),
    ("Genieo", [
        "~/Library/LaunchAgents/com.genieo.*",
        "/Library/LaunchAgents/com.genieo.*",
    ]),
    ("Pirrit", [
        "~/Library/LaunchAgents/com.pirrit.*",
        "/Library/LaunchAgents/com.pirrit.*",
    ]),
    ("Shlayer", [
        "~/Library/LaunchAgents/com.shlayer.*",
    ]),
    ("Silver Sparrow", [
        "~/Library/._insu",
        "/tmp/agent.sh",
        "/tmp/version.json",
        "/tmp/version.plist",
    ]),
]


_APPLE_PREFIXES = ("com.apple.",)
_WELL_KNOWN_VENDOR_PREFIXES = (
    "com.google.", "com.microsoft.", "com.docker.", "com.adobe.",
    "com.jetbrains.", "com.anthropic.", "com.openai.", "com.github.",
    "com.1password.", "com.brave.", "org.mozilla.", "org.videolan.",
    "com.tinyspeck.", "com.spotify.", "com.valvesoftware.",
    "com.teamviewer.", "com.logmein.", "com.dropbox.", "com.crashplan.",
    "homebrew.",
)


def _expand_glob(pattern: str) -> list[Path]:
    """Expand ~ and globs in a pattern, return matching existing paths."""
    expanded = os.path.expanduser(pattern)
    if "*" not in expanded and "?" not in expanded:
        p = Path(expanded)
        return [p] if p.exists() else []
    return [Path(x) for x in _glob.glob(expanded)]


# ---------------------------------------------------------------------------
# Scanners
# ---------------------------------------------------------------------------


def scan_xprotect() -> list[Finding]:
    """Read XProtect.bundle plist, report version + last update age."""
    out: list[Finding] = []
    plist_path = Path(
        "/Library/Apple/System/Library/CoreServices/"
        "XProtect.bundle/Contents/Info.plist"
    )
    if not plist_path.exists():
        out.append(Finding(
            "xprotect", "caution", "XProtect plist not found",
            "the file at the expected path is missing",
            remediation="Check System Settings → Software Update",
            path=str(plist_path),
        ))
        return out
    try:
        with open(plist_path, "rb") as f:
            data = plistlib.load(f)
        version = str(data.get("CFBundleShortVersionString") or "?")
        mtime = plist_path.stat().st_mtime
        days = int((time.time() - mtime) / 86400)
    except (OSError, plistlib.InvalidFileException, ValueError) as e:
        out.append(Finding("xprotect", "info", "XProtect read error", str(e)))
        return out

    if days > 45:
        out.append(Finding(
            "xprotect", "caution",
            f"XProtect is stale ({days}d old)",
            f"version {version}; Apple normally pushes updates every few weeks",
            remediation="System Settings → Software Update",
        ))
        return out

    out.append(Finding(
        "xprotect", "ok",
        f"XProtect up to date ({days}d old)",
        f"version {version}",
    ))
    return out


def scan_malware_heuristics(
    agent_dirs: list[Path] | None = None,
) -> list[Finding]:
    """Look for known-bad paths + LaunchAgents from unknown vendors.

    ``agent_dirs`` defaults to the three standard macOS plist dirs.
    Override it in tests to keep scans confined to a fixture dir.
    """
    out: list[Finding] = []

    # 1) Known adware signatures
    for family, patterns in KNOWN_ADWARE_SIGNS:
        for pat in patterns:
            for match in _expand_glob(pat):
                out.append(Finding(
                    "malware", "risk",
                    f"Known adware match: {family}",
                    f"path matches {family} signature",
                    remediation="Remove this manually; consider running Malwarebytes",
                    path=str(match),
                ))

    # 2) LaunchAgents / LaunchDaemons from unknown vendors
    if agent_dirs is None:
        agent_dirs = [
            HOME / "Library/LaunchAgents",
            Path("/Library/LaunchAgents"),
            Path("/Library/LaunchDaemons"),
        ]
    for d in agent_dirs:
        if not d.exists():
            continue
        try:
            entries = list(d.glob("*.plist"))
        except OSError:
            continue
        for plist in entries:
            name = plist.stem
            if name.startswith(_APPLE_PREFIXES):
                continue
            if name.startswith(_WELL_KNOWN_VENDOR_PREFIXES):
                continue
            out.append(Finding(
                "malware", "review",
                f"Unknown LaunchAgent: {name}",
                f"in {d}",
                remediation=(
                    f"If unexpected: launchctl unload {plist}; "
                    f"then delete the plist"
                ),
                path=str(plist),
            ))

    if not out:
        out.append(Finding(
            "malware", "ok",
            "No known adware signatures found",
            "checked MacKeeper / Genieo / Pirrit / Shlayer / Silver Sparrow patterns",
        ))
    return out


def scan_codesign() -> list[Finding]:
    """Run codesign --verify on every .app in /Applications and ~/Applications."""
    out: list[Finding] = []
    apps: list[Path] = []
    for root in (Path("/Applications"), HOME / "Applications"):
        if not root.exists():
            continue
        try:
            for child in root.iterdir():
                if child.name.endswith(".app"):
                    apps.append(child)
        except OSError:
            pass

    if not apps:
        out.append(Finding("codesign", "info", "No applications to check", ""))
        return out

    def _verify(app: Path):
        try:
            r = subprocess.run(
                ["codesign", "--verify", "--strict", str(app)],
                capture_output=True, timeout=20,
            )
            if r.returncode != 0:
                err = r.stderr.decode("utf-8", "replace").strip().splitlines()
                first = err[0] if err else "verification failed"
                return app.name, first[:100]
        except (subprocess.SubprocessError, OSError):
            return app.name, "codesign check timed out"
        return None

    bad: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for result in pool.map(_verify, apps):
            if result:
                bad.append(result)

    for name, err in bad:
        out.append(Finding(
            "codesign", "caution",
            f"Signature issue: {name}",
            err,
            remediation="If unexpected, reinstall from the official source",
        ))
    if not bad:
        out.append(Finding(
            "codesign", "ok",
            f"All {len(apps)} applications signed correctly", "",
        ))
    return out


def _has_quarantine_xattr(p: Path) -> bool:
    """macOS-specific — python's os.listxattr is Linux-only, so shell out."""
    try:
        r = subprocess.run(
            ["xattr", str(p)], capture_output=True, text=True, timeout=5,
        )
        return "com.apple.quarantine" in r.stdout
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return False


def scan_quarantine() -> list[Finding]:
    """List files in ~/Downloads / ~/Desktop still carrying com.apple.quarantine."""
    out: list[Finding] = []
    dirs = [HOME / "Downloads", HOME / "Desktop"]
    candidates: list[Path] = []
    for d in dirs:
        if not d.exists():
            continue
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for p in entries:
            if not p.is_dir():
                candidates.append(p)

    found: list[Path] = []
    if candidates:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = pool.map(_has_quarantine_xattr, candidates)
            for p, flagged in zip(candidates, results):
                if flagged:
                    found.append(p)

    if not found:
        out.append(Finding(
            "quarantine", "ok",
            "No quarantined files in Downloads / Desktop", "",
        ))
        return out

    for p in found[:25]:
        try:
            sz = human(p.lstat().st_size)
        except OSError:
            sz = "?"
        out.append(Finding(
            "quarantine", "info",
            f"Quarantined: {p.name}",
            f"{sz} · Gatekeeper flagged on download",
            remediation="Run it once to clear the flag, or delete if unwanted",
            path=str(p),
        ))
    if len(found) > 25:
        out.append(Finding(
            "quarantine", "info",
            f"+ {len(found) - 25} more quarantined files",
            "truncated for display",
        ))
    return out


def _run_quiet(cmd: list[str], timeout: int = 60) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return 127, "", "not found"
    except subprocess.SubprocessError as e:
        return 1, "", str(e)


def scan_vulnerabilities() -> list[Finding]:
    """Wrap pip-audit / brew outdated / npm outdated -g where available.

    Honest labelling: only ``pip-audit`` produces real CVE matches and earns
    a ``caution`` severity. ``brew outdated`` and ``npm outdated -g`` only
    show update drift — packages that are behind their latest release with
    no known vulnerability — so they emit ``info`` findings with neutral
    "update available" wording instead of security-tinged "outdated".
    """
    out: list[Finding] = []

    # pip-audit (optional) — real CVE scan, this one IS a security finding.
    rc, stdout, _stderr = _run_quiet(["pip-audit", "--format=json"], timeout=90)
    if rc == 127:
        out.append(Finding(
            "vulns", "info", "pip-audit not installed",
            "no Python CVE scan performed",
            remediation="pip install pip-audit",
        ))
    elif rc == 0:
        try:
            data = _json.loads(stdout or "[]")
        except _json.JSONDecodeError:
            out.append(Finding("vulns", "info", "pip-audit: parse error", ""))
        else:
            vulns = (data.get("vulnerabilities", data)
                     if isinstance(data, dict) else data)
            vuln_count = len(vulns) if isinstance(vulns, list) else 0
            if vuln_count:
                out.append(Finding(
                    "vulns", "caution",
                    f"pip-audit: {vuln_count} known vulnerabilities",
                    "review with `pip-audit` then upgrade affected packages",
                    remediation="pip install --upgrade <package>",
                ))
            else:
                out.append(Finding("vulns", "ok", "pip-audit: clean", ""))

    # brew outdated — package version drift, not vulnerability data. ``info``
    # severity, neutral "update available" wording.
    rc, stdout, _ = _run_quiet(["brew", "outdated", "--quiet"], timeout=30)
    if rc == 127:
        pass  # no brew on this system
    elif rc == 0:
        outdated = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
        if outdated:
            preview = ", ".join(outdated[:5]) + (
                f", … (+{len(outdated) - 5})" if len(outdated) > 5 else ""
            )
            out.append(Finding(
                "vulns", "info",
                f"brew: {len(outdated)} package updates available",
                f"{preview} — version drift only, not CVE data",
                remediation="brew upgrade",
            ))
        else:
            out.append(Finding("vulns", "ok", "brew: all up to date", ""))

    # npm outdated -g — same caveat as brew. ``info``, not ``caution``.
    rc, stdout, _ = _run_quiet(["npm", "outdated", "-g", "--json"], timeout=30)
    if rc != 127:
        try:
            data = _json.loads(stdout) if stdout.strip() else {}
        except _json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and data:
            pkgs = list(data.keys())
            preview = ", ".join(pkgs[:5]) + (
                f", … (+{len(pkgs) - 5})" if len(pkgs) > 5 else ""
            )
            out.append(Finding(
                "vulns", "info",
                f"npm (global): {len(pkgs)} package updates available",
                f"{preview} — version drift only, not CVE data",
                remediation="npm update -g",
            ))
        elif data == {}:
            out.append(Finding("vulns", "ok", "npm global: up to date", ""))

    if not out:
        out.append(Finding(
            "vulns", "info", "No package managers found",
            "install pip-audit / brew / npm to enable vulnerability checks",
        ))
    return out


HEALTH_MODULES: list[HealthModule] = [
    HealthModule("xprotect", "XProtect status",
                 "Apple built-in malware signatures version + age",
                 scan_xprotect),
    HealthModule("malware", "Malware heuristics",
                 "Known adware paths + unsigned LaunchAgents",
                 scan_malware_heuristics),
    HealthModule("codesign", "Code-sign audit",
                 "codesign --verify across /Applications",
                 scan_codesign),
    HealthModule("quarantine", "Quarantine review",
                 "Files flagged by Gatekeeper in Downloads / Desktop",
                 scan_quarantine),
    HealthModule("vulns", "Vulnerability check",
                 "Outdated pip-audit / brew / npm packages",
                 scan_vulnerabilities),
]
