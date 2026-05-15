"""AI agent skill and MCP server discovery + management.

Scans skill directories for Claude Code, Codex, and Gemini, and parses
MCP server configuration files across all three agents.  Read-only by
default — removal helpers exist but are only called through the TUI
after explicit user confirmation.

Dependency: only ``common``.  Sits alongside ``cache`` and ``health``
in the import graph::

    common  ←  agents  ←  tui
            ←  cache   ←
            ←  health  ←
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .common import HOME, path_size

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SkillEntry:
    agent: str          # "claude", "codex", "gemini"
    name: str           # directory name
    path: Path
    size: int = 0
    status: str = "ok"  # ok | broken_symlink | orphan | suspicious
    detail: str = ""


@dataclass
class McpServerEntry:
    name: str
    source: str         # "claude-desktop" | "claude-code" | "gemini" | "project: <path>"
    config_path: Path
    command: str = ""
    args: list[str] = field(default_factory=list)
    transport: str = "stdio"   # stdio | http | sse
    status: str = "ok"         # ok | command_not_found | config_error
    detail: str = ""


# ---------------------------------------------------------------------------
# Well-known paths
# ---------------------------------------------------------------------------

_SKILL_LOCATIONS: list[tuple[str, Path]] = [
    # ~/.agents/skills is the canonical skill store managed by the agents CLI.
    # ~/.claude/skills is a symlink to the same directory — deduplicated by inode
    # in discover_skills() so it's never listed twice.
    ("agents",        HOME / ".agents" / "skills"),
    ("claude",        HOME / ".claude" / "skills"),   # fallback if .agents absent
    ("claude-agents", HOME / ".claude" / "agents"),   # sub-agent .md files
    ("codex",         HOME / ".codex" / "skills"),
    ("gemini",        HOME / ".gemini"),
]

_SUSPICIOUS_PATTERNS = ("rm ", "curl ", "wget ", "eval ", "exec ",
                        "| sh", "| bash")

# ~/.agents/.skill-lock.json: per-skill install metadata (source GitHub repo,
# install date).  Read once and cached in discover_skills().
_AGENTS_ROOT      = HOME / ".agents"
_SKILL_LOCK_PATH  = _AGENTS_ROOT / ".skill-lock.json"

_MCP_CONFIG_FILES: list[tuple[str, Path, str]] = [
    # ~/.agents/mcp.json — agents CLI canonical MCP registry (key: "servers")
    ("agents",
     _AGENTS_ROOT / "mcp.json",
     "servers"),
    # ~/.claude/mcp.json — explicit global file (older Claude Code versions)
    ("claude-code",
     HOME / ".claude" / "mcp.json",
     "mcpServers"),
    ("claude-desktop",
     HOME / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
     "mcpServers"),
    ("gemini",
     HOME / ".gemini" / "settings.json",
     "mcpServers"),
]

# ~/.claude.json stores per-project MCP servers under projects[path].mcpServers.
# This is Claude Code's primary MCP config — handled separately by
# _parse_claude_json_mcp() because the structure is nested, not flat.
_CLAUDE_JSON_PATH = HOME / ".claude.json"

# Gemini skills live at ~/.gemini/ alongside config files.  Only
# directories that look like skill repos count — skip known config
# files and dotfiles.
_GEMINI_SKIP = {
    "bin", "history", "tmp",
    "google_accounts.json", "oauth_creds.json", "projects.json",
    "settings.json", "state.json", "trustedFolders.json",
    "installation_id", "mcp-server-enablement.json",
    "GEMINI.md", "antigravity",
}

# ---------------------------------------------------------------------------
# Skill discovery
# ---------------------------------------------------------------------------


def _load_skill_lock(lock_path: Path) -> dict[str, dict]:
    """Return skill-name → metadata dict from .skill-lock.json, or {}."""
    try:
        with open(lock_path) as f:
            data = json.load(f)
        return data.get("skills", {}) if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def discover_skills(
    locations: list[tuple[str, Path]] | None = None,
    skill_lock: Path | None = None,
) -> list[SkillEntry]:
    """Walk agent skill directories and return an entry per skill.

    Directories that share an inode (e.g. ``~/.claude/skills`` symlinked to
    ``~/.agents/skills``) are scanned only once — the first location that
    resolves to a given inode wins, so the canonical ``agents`` label is used
    when both are listed.

    ``skill_lock`` defaults to ``~/.agents/.skill-lock.json``; pass an
    explicit path (or non-existent path) in tests for isolation.
    """
    locs = locations if locations is not None else _SKILL_LOCATIONS
    lock = _load_skill_lock(
        skill_lock if skill_lock is not None else _SKILL_LOCK_PATH
    )
    out: list[SkillEntry] = []
    seen_inodes: set[int] = set()   # (st_dev, st_ino) pairs already scanned

    for agent, base in locs:
        if not base.exists():
            continue
        # Resolve symlinks to detect duplicate directories
        try:
            st = base.stat()
            inode_key = (st.st_dev, st.st_ino)
        except OSError:
            continue
        if inode_key in seen_inodes:
            continue   # already scanned this physical directory under another name
        seen_inodes.add(inode_key)

        try:
            entries = list(base.iterdir())
        except OSError:
            continue

        for entry in entries:
            if entry.name.startswith("."):
                continue

            # Gemini: skip known config files
            if agent == "gemini" and entry.name in _GEMINI_SKIP:
                continue
            # Gemini: skip files that aren't directories (config backups etc.)
            if agent == "gemini" and not entry.is_dir() and not entry.is_symlink():
                continue

            skill = SkillEntry(agent=agent, name=entry.name, path=entry)

            if entry.is_symlink() and not entry.resolve().exists():
                skill.status = "broken_symlink"
                try:
                    skill.detail = f"target: {os.readlink(entry)}"
                except OSError:
                    skill.detail = "unreadable symlink target"
                out.append(skill)
                continue

            if entry.is_file() and entry.suffix == ".md":
                if agent == "claude-agents":
                    # ~/.claude/agents/*.md files are valid sub-agent definitions
                    skill.size = entry.stat().st_size
                    skill.status = "ok"
                    skill.detail = "sub-agent definition"
                else:
                    skill.status = "orphan"
                    skill.detail = "standalone .md with no matching skill directory"
                out.append(skill)
                continue

            if entry.is_dir():
                skill.size = path_size(entry)
                skill_md = entry / "SKILL.md"
                if skill_md.exists():
                    try:
                        text = skill_md.read_text(errors="replace")[:4096]
                        for pat in _SUSPICIOUS_PATTERNS:
                            if pat in text:
                                skill.status = "suspicious"
                                skill.detail = f"SKILL.md contains '{pat.strip()}'"
                                break
                    except OSError:
                        pass

            # Enrich from .skill-lock.json when available
            if skill.status == "ok" and entry.name in lock:
                meta = lock[entry.name]
                src = meta.get("source", "")
                installed = meta.get("installedAt", "")[:10]  # date only
                if src:
                    skill.detail = f"{src}  (installed {installed})" if installed else src

            out.append(skill)

    out.sort(key=lambda s: (s.agent, s.name))
    return out


# ---------------------------------------------------------------------------
# MCP server discovery
# ---------------------------------------------------------------------------


def _detect_transport(server_cfg: dict[str, Any]) -> str:
    if "url" in server_cfg:
        return "http"
    args = server_cfg.get("args", [])
    for a in args:
        if isinstance(a, str) and a.startswith("http"):
            return "http"
    return "stdio"


def _check_command(cmd: str) -> tuple[str, str]:
    """Return (status, detail) for a command string."""
    if not cmd:
        return "config_error", "no command specified"
    if cmd.startswith(("http://", "https://")):
        return "ok", "remote URL"
    resolved = shutil.which(cmd)
    if resolved:
        return "ok", resolved
    if os.path.isabs(cmd):
        if os.path.isfile(cmd):
            return "ok", cmd
        return "command_not_found", f"{cmd} does not exist"
    return "command_not_found", f"'{cmd}' not on PATH"


def _parse_claude_json_mcp(path: Path) -> list[McpServerEntry]:
    """Parse ~/.claude.json: projects[project_path].mcpServers (nested).

    Claude Code stores one mcpServers dict per project directory.  We
    deduplicate by server name — the first project that defines a name wins
    — and surface where each server was first seen via the ``source`` field.
    """
    if not path.exists():
        return []
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return [McpServerEntry(
            name="<config error>",
            source="claude-code (claude.json)",
            config_path=path,
            status="config_error",
            detail=f"failed to parse {path.name}",
        )]

    projects = data.get("projects", {})
    if not isinstance(projects, dict):
        return []

    seen: dict[str, McpServerEntry] = {}
    for project_path, project_cfg in projects.items():
        if not isinstance(project_cfg, dict):
            continue
        servers = project_cfg.get("mcpServers", {})
        if not isinstance(servers, dict) or not servers:
            continue
        project_label = Path(project_path).name
        for name, cfg in servers.items():
            if name in seen or not isinstance(cfg, dict) or not cfg:
                continue
            cmd = cfg.get("command", cfg.get("url", ""))
            args = cfg.get("args", [])
            if not isinstance(args, list):
                args = []
            transport = _detect_transport(cfg)
            status, detail = _check_command(cmd)
            seen[name] = McpServerEntry(
                name=name,
                source=f"claude-code · {project_label}",
                config_path=path,
                command=cmd,
                args=[str(a) for a in args],
                transport=transport,
                status=status,
                detail=detail,
            )
    return list(seen.values())


def discover_mcp_servers(
    config_files: list[tuple[str, Path, str]] | None = None,
    claude_json: Path | None = None,
) -> list[McpServerEntry]:
    """Parse MCP config files and return an entry per server.

    ``claude_json`` defaults to ``_CLAUDE_JSON_PATH`` (``~/.claude.json``).
    Pass an explicit path (or a non-existent path) in tests to avoid touching
    the real file.
    """
    cfgs = config_files if config_files is not None else _MCP_CONFIG_FILES
    out: list[McpServerEntry] = []

    for source, path, key in cfgs:
        if not path.exists():
            continue
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, ValueError):
            out.append(McpServerEntry(
                name="<config error>",
                source=source,
                config_path=path,
                status="config_error",
                detail=f"failed to parse {path.name}",
            ))
            continue

        servers = data.get(key, {})
        if not isinstance(servers, dict):
            continue

        for name, cfg in servers.items():
            if not isinstance(cfg, dict):
                continue
            cmd = cfg.get("command", cfg.get("url", ""))
            args = cfg.get("args", [])
            if not isinstance(args, list):
                args = []
            transport = _detect_transport(cfg)
            status, detail = _check_command(cmd)
            out.append(McpServerEntry(
                name=name,
                source=source,
                config_path=path,
                command=cmd,
                args=[str(a) for a in args],
                transport=transport,
                status=status,
                detail=detail,
            ))

    # Claude Code's primary MCP config: ~/.claude.json (nested per-project)
    _cj = claude_json if claude_json is not None else _CLAUDE_JSON_PATH
    out.extend(_parse_claude_json_mcp(_cj))

    out.sort(key=lambda s: (s.source, s.name))
    return out


# ---------------------------------------------------------------------------
# Management actions (called only with TUI confirmation)
# ---------------------------------------------------------------------------


def remove_skill(skill: SkillEntry) -> bool:
    """Remove a broken or orphan skill entry. Returns True on success."""
    try:
        if skill.path.is_symlink() or skill.path.is_file():
            skill.path.unlink()
        elif skill.path.is_dir():
            shutil.rmtree(skill.path)
        else:
            return False
        return True
    except OSError:
        return False


def remove_mcp_server(server: McpServerEntry) -> bool:
    """Remove a single MCP server entry from its config file.

    Reads the config, deletes the key, writes it back.  Returns True on
    success.
    """
    try:
        with open(server.config_path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False

    # Find the servers dict — try known keys
    for key in ("mcpServers",):
        servers = data.get(key)
        if isinstance(servers, dict) and server.name in servers:
            del servers[server.name]
            try:
                with open(server.config_path, "w") as f:
                    json.dump(data, f, indent=2)
                    f.write("\n")
                return True
            except OSError:
                return False
    return False
