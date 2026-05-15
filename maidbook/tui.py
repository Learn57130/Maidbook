"""Curses TUI: menu → cache scan/select/clean → health scan/results.

All state machine transitions happen through the mode attribute:
``menu → scan → select → confirm → clean → done`` for the cache flow,
``menu → health_scan → health_results`` for the health flow,
``menu → agents_scan → agents_browse`` for the agent tools flow.
The ``both`` plan chains ``done → health_scan``.
"""

from __future__ import annotations

import curses
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from .common import (
    APP_NAME, APP_TAGLINE, BOX_BL, BOX_BR, BOX_H, BOX_TL, BOX_TR, BOX_V,
    BULLET, MARK_CURSOR, MARK_SELECTED, MARK_UNSELECTED, SPINNER,
    fmt_path, human, is_app_running, load_stats, load_whitelist, redact_home,
    record_bloat_snapshot, record_session, save_whitelist,
)
from .agents import (
    McpServerEntry, SkillEntry,
    discover_mcp_servers, discover_skills,
    remove_mcp_server, remove_skill,
)
from .cache import Category, build_categories
from .health import Finding, HealthModule, HEALTH_MODULES


# ---------------------------------------------------------------------------
# TUI helpers — curses-safe string writers and a progress bar.
# ---------------------------------------------------------------------------


def safe_addstr(win, y: int, x: int, text: str, attr: int = 0) -> None:
    """Safe writer — never hits the bottom-right cell (avoids scroll ERR)."""
    try:
        h, w = win.getmaxyx()
    except curses.error:
        return
    if y < 0 or y >= h or x < 0 or x >= w:
        return
    max_len = w - x - (1 if y == h - 1 else 0)
    if max_len <= 0:
        return
    try:
        win.addnstr(y, x, text, max_len, attr)
    except curses.error:
        pass


def safe_fill(win, y: int, text: str, attr: int = 0) -> None:
    try:
        h, w = win.getmaxyx()
    except curses.error:
        return
    if y < 0 or y >= h:
        return
    width = w - (1 if y == h - 1 else 0)
    if width <= 0:
        return
    padded = text[:width].ljust(width)
    try:
        win.addnstr(y, 0, padded, width, attr)
    except curses.error:
        pass


def bar(frac: float, width: int, filled: str = "#", empty: str = ".") -> str:
    width = max(0, width)
    frac = max(0.0, min(1.0, frac))
    n = int(round(frac * width))
    return filled * n + empty * (width - n)


# ---------------------------------------------------------------------------
# Main TUI class
# ---------------------------------------------------------------------------


class TUI:
    MENU_ITEMS = [
        ("cache",    "Cache cleaner",
         "free up disk space · safe for cookies, history, logins"),
        ("health",   "Health check",
         "malware heuristics · code-sign audit · XProtect · CVE scan"),
        ("both",     "Both",
         "clean caches first, then run health check"),
        ("agents",   "Agent tools",
         "browse + manage AI skills · audit MCP server configs"),
        ("stats",    "Stats",
         "lifetime space freed · bloat velocity · session history"),
        ("schedule", "Manage schedule",
         "view or remove the automatic scheduled cron clean"),
    ]

    def __init__(self, stdscr, cats: list[Category]):
        self.stdscr = stdscr
        self.cats = cats
        self.sizes: dict[str, int] = {}
        self.selected: set[str] = set()
        self.cursor = 0
        self.dry_run = False
        self.status = "Scanning..."
        self.log: list[tuple[str, int]] = []
        self.scan_done = False
        self.scan_current = ""
        self.scan_progress = 0
        self.scan_total = len(cats)
        self.scan_lock = threading.Lock()
        # Modes:
        #   menu           → landing screen
        #   scan           → cache scan in progress
        #   select/confirm/clean/done → cache flow
        #   health_scan    → running the 5 health modules
        #   health_results → showing findings list
        self.mode = "menu"
        # Schedule setup: entered from cache select screen via [S]
        #   select → (S) → picking(interval) → time_picking → back to select
        self.schedule_picking = False       # choosing interval (w/d)
        self.schedule_time_picking = False  # choosing hour/minute
        self.sched_pending_keys: set = set()  # selection copied from select screen
        self.sched_interval = "weekly"
        self.sched_hour = 3
        self.sched_minute = 0
        self.sched_time_field = 0           # 0 = hour focused, 1 = minute focused
        self.schedule_msg = ""              # feedback line after action
        self.manage_confirm = False         # True while awaiting y/n for remove
        self.action_choice = 0             # 0 = Clean now, 1 = Schedule clean
        self.plan = "cache"  # "cache" | "health" | "both"
        self.menu_cursor = 0
        # Health-check state
        self.findings: list[Finding] = []
        self.health_progress = 0
        self.health_total = len(HEALTH_MODULES)
        self.health_current = ""
        self.health_cursor = 0
        self.flash_text = ""
        self.flash_until = 0.0
        self.spin_idx = 0
        self.active_item = ""
        self.clean_progress = 0
        self.clean_total = 0
        self.pending_g = False
        self.stop_requested = threading.Event()
        self.whitelist: set[str] = load_whitelist()
        # Agent tools state
        self.agent_skills: list[SkillEntry] = []
        self.agent_mcp: list[McpServerEntry] = []
        self.agents_cursor = 0
        self.agents_confirm: str | None = None  # key of item pending removal
        self.agents_progress = 0
        self.agents_total = 0
        self.agents_current = ""

    def setup_colors(self):
        curses.start_color()
        curses.use_default_colors()
        # Claude Code style: amber/orange accent, otherwise restrained palette.
        orange = 208 if curses.COLORS >= 256 else curses.COLOR_YELLOW
        dim_gray = 244 if curses.COLORS >= 256 else curses.COLOR_WHITE
        self.C_ACCENT = 1   # orange
        self.C_OK = 2       # green
        self.C_WARN = 3     # yellow
        self.C_ERR = 4      # red
        self.C_INFO = 5     # blue
        self.C_DIM = 6      # dim gray
        self.C_INV = 7      # inverted
        curses.init_pair(self.C_ACCENT, orange, -1)
        curses.init_pair(self.C_OK, curses.COLOR_GREEN, -1)
        curses.init_pair(self.C_WARN, curses.COLOR_YELLOW, -1)
        curses.init_pair(self.C_ERR, curses.COLOR_RED, -1)
        curses.init_pair(self.C_INFO, curses.COLOR_BLUE, -1)
        curses.init_pair(self.C_DIM, dim_gray, -1)
        curses.init_pair(self.C_INV, -1, -1)

    # ---------------------- workers ----------------------
    def scan_worker(self):
        def _one(cat: Category):
            try:
                return cat, cat.scan()
            except (OSError, subprocess.SubprocessError, RuntimeError,
                    ValueError):
                return cat, 0

        done = 0
        # ``du`` subprocess is already fast; too many workers just trade
        # I/O parallelism for process-spawn overhead. 8 is the sweet spot.
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_one, c): c for c in self.cats}
            for fut in as_completed(futures):
                cat, sz = fut.result()
                done += 1
                with self.scan_lock:
                    self.sizes[cat.key] = sz
                    self.scan_progress = done
                    self.scan_current = cat.name
        with self.scan_lock:
            self.scan_done = True
            self.scan_current = ""
            self.status = "Ready. Up/Down to move, SPACE to toggle, ENTER to clean."
            total_cache = sum(self.sizes.values())

        # Record a bloat-velocity snapshot so the Stats screen has a trend.
        try:
            record_bloat_snapshot(total_cache)
        except OSError:
            pass

        if self.mode == "scan":
            self.mode = "select"

    def clean_worker(self):
        rows = self.sorted_cats()
        selected = [c for c in rows if c.key in self.selected]
        self.clean_total = len(selected)
        self.clean_progress = 0
        self.stop_requested.clear()
        self.log = [(f"Cleaning {len(selected)} categories (dry-run={self.dry_run})", 1),
                    ("", 0)]

        total_freed, total_errs = 0, 0
        actually_freed = 0  # updated to post-pending value inside the with block
        stopped_early = False
        cleaned_names: list[str] = []
        t0 = time.monotonic()
        from .common import async_batch, wait_for_pending_reaps
        with async_batch() as batch_pending_bytes:
            for c in selected:
                if self.stop_requested.is_set():
                    stopped_early = True
                    remaining = self.clean_total - self.clean_progress
                    self.log.append((f"⏹  Stopped — {remaining} categories skipped", 3))
                    break
                self.active_item = c.name
                running = [a for a in c.requires_apps_closed if is_app_running(a)]
                if running and not self.dry_run:
                    self.log.append((f"⏭  {c.name:<22} skipped — {', '.join(running)} running", 3))
                    self.clean_progress += 1
                    continue
                self.log.append((f"→  {c.name:<22} working…", 5))
                try:
                    freed, errs, msg = c.clean(self.dry_run)
                except (OSError, subprocess.SubprocessError, RuntimeError) as e:
                    self.log[-1] = (f"✗  {c.name:<22} error: {e}", 4)
                    total_errs += 1
                    self.clean_progress += 1
                    continue
                icon = "✓" if errs == 0 else "!"
                color = 2 if errs == 0 else 3
                size_str = human(freed) if freed else "—"
                self.log[-1] = (f"{icon}  {c.name:<22} {size_str:>11}  {msg}", color)
                total_freed += freed
                total_errs += errs
                if freed > 0 and errs == 0:
                    cleaned_names.append(c.name)
                self.clean_progress += 1

            self.active_item = ""
            self.log.append(("", 0))
            if self.dry_run:
                self.log.append((f"Would free: {human(total_freed)}    Errors: {total_errs}", 2))
            else:
                wait_for_pending_reaps(timeout=5.0)
                pending = batch_pending_bytes()
                actually_freed = max(0, total_freed - pending)
                if pending > 0:
                    self.log.append((
                        f"Freed: {human(actually_freed)}    "
                        f"({human(pending)} still finalizing in background)    "
                        f"Errors: {total_errs}",
                        2,
                    ))
                else:
                    self.log.append((
                        f"Freed: {human(actually_freed)}    Errors: {total_errs}",
                        2,
                    ))
            if stopped_early:
                self.log.append(("(interrupted by user — partial clean)", 3))

        # Persist stats — only count what was actually freed (non-dry-run,
        # non-zero, no errors).  Use ``actually_freed`` (post-pending) to
        # match the on-screen "Freed: X" line.
        if not self.dry_run and actually_freed > 0:
            duration = time.monotonic() - t0
            try:
                record_session(actually_freed, cleaned_names, duration)
            except OSError:
                pass  # stats file unwritable — don't crash the TUI

        self.mode = "done"

    def start_cache_scan(self):
        self.mode = "scan"
        with self.scan_lock:
            self.sizes.clear()
            self.scan_done = False
            self.scan_progress = 0
            self.scan_current = ""
        threading.Thread(target=self.scan_worker, daemon=True).start()

    def start_health_scan(self):
        self.mode = "health_scan"
        self.findings = []
        self.health_progress = 0
        self.health_current = ""
        threading.Thread(target=self.health_scan_worker, daemon=True).start()

    def start_agents_scan(self):
        self.mode = "agents_scan"
        self.agent_skills = []
        self.agent_mcp = []
        self.agents_cursor = 0
        self.agents_confirm = None
        self.agents_progress = 0
        self.agents_total = 0
        self.agents_current = ""
        threading.Thread(target=self.agents_scan_worker, daemon=True).start()

    def agents_scan_worker(self):
        from .agents import _SKILL_LOCATIONS, _MCP_CONFIG_FILES
        self.agents_total = len(_SKILL_LOCATIONS) + len(_MCP_CONFIG_FILES)

        skills_acc: list[SkillEntry] = []
        for agent, base in _SKILL_LOCATIONS:
            self.agents_current = f"skills · {agent}"
            skills_acc.extend(discover_skills([(agent, base)]))
            self.agents_progress += 1
        self.agent_skills = skills_acc

        mcp_acc: list[McpServerEntry] = []
        for source, path, key in _MCP_CONFIG_FILES:
            self.agents_current = f"mcp · {source}"
            mcp_acc.extend(discover_mcp_servers([(source, path, key)]))
            self.agents_progress += 1
        self.agent_mcp = mcp_acc

        self.mode = "agents_browse"

    def health_scan_worker(self):
        def _one(mod: HealthModule):
            try:
                return mod, mod.scan()
            except (OSError, subprocess.SubprocessError, RuntimeError,
                    ValueError) as e:
                return mod, [Finding(mod.key, "info",
                                     f"{mod.name} error", str(e))]

        with ThreadPoolExecutor(max_workers=min(5, len(HEALTH_MODULES))) as pool:
            futures = {pool.submit(_one, m): m for m in HEALTH_MODULES}
            done = 0
            for fut in as_completed(futures):
                mod, fds = fut.result()
                done += 1
                self.health_progress = done
                self.health_current = mod.name
                self.findings.extend(fds)

        rank = {"risk": 0, "caution": 1, "review": 2, "info": 3, "ok": 4}
        self.findings.sort(key=lambda f: (rank.get(f.severity, 5),
                                          f.module, f.title))
        self.mode = "health_results"
        self.health_cursor = 0

    # ---------------------- clipboard export ----------------------
    def format_findings(self) -> str:
        """Plain-text report suitable for pasting anywhere."""
        lines = [
            "Maidbook — Health Check Report",
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            "",
        ]
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        lines.append(
            f"Summary: risks={counts.get('risk', 0)}  "
            f"caution={counts.get('caution', 0)}  "
            f"review={counts.get('review', 0)}  "
            f"info={counts.get('info', 0)}  "
            f"ok={counts.get('ok', 0)}"
        )
        lines.append("")

        sev_glyph = {"risk": "■", "caution": "▲", "review": "◆",
                     "ok": "✓", "info": "·"}
        current_mod: str | None = None
        for f in self.findings:
            if f.module != current_mod:
                if current_mod is not None:
                    lines.append("")
                current_mod = f.module
                mod_name = next((m.name for m in HEALTH_MODULES
                                 if m.key == f.module), f.module)
                lines.append(f"# {mod_name}")
            glyph = sev_glyph.get(f.severity, "·")
            lines.append(f"  {glyph} [{f.severity}] {redact_home(f.title)}")
            if f.detail:
                # Redact $HOME → ~ inside free-form text so pasted reports
                # don't leak the username via codesign / launchctl strings.
                lines.append(f"      {redact_home(f.detail)}")
            if f.remediation:
                lines.append(f"      → {redact_home(f.remediation)}")
            if f.path:
                lines.append(f"      {fmt_path(f.path)}")
        return "\n".join(lines) + "\n"

    def copy_findings(self) -> bool:
        text = self.format_findings()
        try:
            r = subprocess.run(
                ["pbcopy"], input=text, text=True, timeout=5,
            )
            return r.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            return False

    def flash(self, text: str, seconds: float = 1.8) -> None:
        self.flash_text = text
        self.flash_until = time.time() + seconds

    # ---------------------- drawing ----------------------
    def draw(self):
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()
        if h < 18 or w < 80:
            safe_addstr(self.stdscr, 0, 0,
                        "Terminal too small — need 80 wide × 18 tall")
            self.stdscr.refresh()
            return

        self.draw_banner(h, w)

        if self.mode == "menu":
            self.draw_menu(h, w)
        elif self.mode == "scan":
            self.draw_scan(h, w)
        elif self.mode == "select":
            self.draw_select(h, w)
        elif self.mode == "action_choice":
            self.draw_action_choice(h, w)
        elif self.mode == "confirm":
            self.draw_select(h, w)
            self.draw_confirm(h, w)
        elif self.mode == "clean":
            self.draw_log(h, w, title="CLEANING", color=1)
            self.draw_progress_bar(h, w)
        elif self.mode == "health_scan":
            self.draw_health_scan(h, w)
        elif self.mode == "health_results":
            self.draw_health_results(h, w)
        elif self.mode == "stats":
            self.draw_stats(h, w)
        elif self.mode == "schedule":
            self.draw_schedule(h, w)
        elif self.mode == "agents_scan":
            self.draw_agents_scan(h, w)
        elif self.mode == "agents_browse":
            self.draw_agents_browse(h, w)
        else:  # "done"
            self.draw_log(h, w, title="DONE", color=self.C_OK)
            if self.plan == "both":
                hint = "  ↵ continue to health check · m menu · r rescan · q quit"
            else:
                hint = "  ↵ back to select · m menu · r rescan · q quit"
            safe_addstr(self.stdscr, h - 1, 0, hint.ljust(w - 1),
                        curses.color_pair(self.C_DIM) | curses.A_DIM)

        self.stdscr.refresh()

    MASCOT_TIDY = [" (•‿•) ", " /|  |\\ ", "  d  b  "]
    MASCOT_MESSY = [" (•_•;)", " /| |\\ ", "  d  b  "]
    MASCOT_CHAOS = [" (×_×) ", " /|##|\\ ", "  d  b  "]

    def _mascot_state(self) -> list[str]:
        total = sum(self.sizes.values()) if self.sizes else 0
        if total > 2_000_000_000:
            return self.MASCOT_CHAOS
        if total > 500_000_000:
            return self.MASCOT_MESSY
        return self.MASCOT_TIDY

    def draw_banner(self, h: int, w: int):
        inner_w = min(w - 4, 72)
        x0 = 2
        label = f" {BULLET} {APP_NAME} "
        top = (BOX_TL + BOX_H * 2 + label +
               BOX_H * max(0, inner_w - len(label) - 3) + BOX_TR)
        tagline = f"  {APP_TAGLINE}"
        mid = BOX_V + tagline.ljust(inner_w - 2) + BOX_V
        bot = BOX_BL + BOX_H * (inner_w - 2) + BOX_BR
        safe_addstr(self.stdscr, 0, x0, top, curses.color_pair(self.C_ACCENT))
        safe_addstr(self.stdscr, 1, x0, mid, curses.color_pair(self.C_DIM))
        safe_addstr(self.stdscr, 2, x0, bot, curses.color_pair(self.C_ACCENT))

        if w > 100:
            mascot = self._mascot_state()
            mx = x0 + inner_w + 3
            for i, line in enumerate(mascot):
                safe_addstr(self.stdscr, i, mx, line,
                            curses.color_pair(self.C_DIM))

    def draw_menu(self, h: int, w: int):
        top = 5
        safe_addstr(self.stdscr, top, 2,
                    f"{BULLET} What would you like to do?",
                    curses.color_pair(self.C_ACCENT) | curses.A_BOLD)

        for i, (_key, name, desc) in enumerate(self.MENU_ITEMS):
            y = top + 2 + i * 2
            is_cursor = (i == self.menu_cursor)
            cursor_glyph = MARK_CURSOR if is_cursor else " "
            bullet_glyph = MARK_SELECTED if is_cursor else MARK_UNSELECTED
            safe_addstr(self.stdscr, y, 2, cursor_glyph,
                        curses.color_pair(self.C_ACCENT) | curses.A_BOLD)
            safe_addstr(self.stdscr, y, 4, bullet_glyph,
                        curses.color_pair(
                            self.C_ACCENT if is_cursor else self.C_DIM))
            name_attr = curses.A_BOLD if is_cursor else 0
            safe_addstr(self.stdscr, y, 6, name.ljust(18), name_attr)
            safe_addstr(self.stdscr, y, 26, desc,
                        curses.color_pair(self.C_DIM))

        hint = "  ↑/↓ move · ↵ select · q quit"
        safe_addstr(self.stdscr, h - 1, 0, hint.ljust(w - 1),
                    curses.color_pair(self.C_DIM) | curses.A_DIM)

    def draw_scan(self, h: int, w: int):
        with self.scan_lock:
            progress = self.scan_progress
            total = self.scan_total
            current = self.scan_current
            partial_bytes = sum(self.sizes.values())

        top = 5
        spin = SPINNER[self.spin_idx % len(SPINNER)]
        safe_addstr(self.stdscr, top, 2,
                    f"{spin} Scanning caches",
                    curses.color_pair(self.C_ACCENT) | curses.A_BOLD)
        safe_addstr(self.stdscr, top, 22,
                    "— walking ~/Library/Caches, ~/.cache, and browser profiles",
                    curses.color_pair(self.C_DIM))

        bar_w = min(40, max(10, w - 30))
        frac = (progress / total) if total else 0
        bar_str = bar(frac, bar_w, "█", "░")
        safe_addstr(self.stdscr, top + 2, 4,
                    f"{bar_str}  {progress:>3}/{total}",
                    curses.color_pair(self.C_ACCENT))

        line_total = f"  total so far    {human(partial_bytes):>12}"
        safe_addstr(self.stdscr, top + 4, 2, line_total,
                    curses.color_pair(self.C_ACCENT) | curses.A_BOLD)

        cur_label = current if current else "finishing…"
        if len(cur_label) > w - 14:
            cur_label = cur_label[: w - 15] + "…"
        safe_addstr(self.stdscr, top + 5, 2,
                    f"  scanning     {cur_label}",
                    curses.color_pair(self.C_DIM))

        safe_addstr(self.stdscr, h - 1, 0,
                    "  please wait · q quit".ljust(w - 1),
                    curses.color_pair(self.C_DIM) | curses.A_DIM)

    def draw_health_scan(self, h: int, w: int):
        progress = self.health_progress
        total = self.health_total
        current = self.health_current
        top = 5
        spin = SPINNER[self.spin_idx % len(SPINNER)]

        safe_addstr(self.stdscr, top, 2,
                    f"{spin} Running health check",
                    curses.color_pair(self.C_ACCENT) | curses.A_BOLD)
        safe_addstr(self.stdscr, top, 30,
                    "— read-only scans · nothing is modified",
                    curses.color_pair(self.C_DIM))

        bar_w = min(40, max(10, w - 30))
        frac = (progress / total) if total else 0
        bar_str = bar(frac, bar_w, "█", "░")
        safe_addstr(self.stdscr, top + 2, 4,
                    f"{bar_str}  {progress}/{total}",
                    curses.color_pair(self.C_ACCENT))

        for i, mod in enumerate(HEALTH_MODULES):
            y = top + 4 + i
            done = any(f.module == mod.key for f in self.findings)
            if done:
                marker = "✓"
                color = curses.color_pair(self.C_OK)
            elif mod.name == current:
                marker = spin
                color = curses.color_pair(self.C_ACCENT)
            else:
                marker = "·"
                color = curses.color_pair(self.C_DIM) | curses.A_DIM
            line = f"  {marker}  {mod.name:<22} {mod.description}"
            safe_addstr(self.stdscr, y, 2, line[: w - 2], color)

        safe_addstr(self.stdscr, h - 1, 0,
                    "  please wait · q quit".ljust(w - 1),
                    curses.color_pair(self.C_DIM) | curses.A_DIM)

    def _severity_style(self, sev: str):
        if sev == "risk":
            return "■", self.C_ERR, curses.A_BOLD
        if sev == "caution":
            return "▲", self.C_WARN, 0
        if sev == "review":
            return "◆", self.C_ACCENT, 0
        if sev == "ok":
            return "✓", self.C_OK, 0
        return "·", self.C_DIM, curses.A_DIM

    def draw_health_results(self, h: int, w: int):
        top = 4
        counts = {"risk": 0, "caution": 0, "review": 0, "info": 0, "ok": 0}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        summary = (
            f"{BULLET} Health check results   "
            f"risks {counts['risk']}  caution {counts['caution']}  "
            f"review {counts['review']}  info {counts['info']}  "
            f"ok {counts['ok']}"
        )
        safe_addstr(self.stdscr, top, 2, summary[: w - 2],
                    curses.color_pair(self.C_ACCENT) | curses.A_BOLD)

        start_row = top + 2
        footer_rows = 2
        max_rows = h - start_row - footer_rows

        # Flatten to displayable lines
        lines: list[tuple[str, object]] = []
        current_mod: str | None = None
        for f in self.findings:
            if f.module != current_mod:
                if current_mod is not None:
                    lines.append(("blank", None))
                current_mod = f.module
                mod_name = next((m.name for m in HEALTH_MODULES
                                 if m.key == f.module), f.module)
                lines.append(("section", mod_name))
            lines.append(("finding", f))
            if f.detail:
                lines.append(("detail", redact_home(f.detail)))
            if f.remediation:
                lines.append(("remediation", redact_home(f.remediation)))
            if f.path:
                # Redact $HOME → ~ in the on-screen display too, so it stays
                # consistent with the clipboard export.
                lines.append(("path", fmt_path(f.path)))

        if self.health_cursor >= len(lines):
            self.health_cursor = max(0, len(lines) - 1)
        if self.health_cursor < 0:
            self.health_cursor = 0

        view_top = 0
        if self.health_cursor >= max_rows:
            view_top = self.health_cursor - max_rows + 1
        visible = lines[view_top: view_top + max_rows]

        for i, (kind, payload) in enumerate(visible):
            y = start_row + i
            if kind == "section":
                safe_addstr(self.stdscr, y, 2,
                            f"● {payload}"[: w - 2],
                            curses.color_pair(self.C_ACCENT) | curses.A_BOLD)
            elif kind == "finding":
                f: Finding = payload  # type: ignore
                glyph, color, attr = self._severity_style(f.severity)
                line = f"  {glyph}  {f.title}"
                safe_addstr(self.stdscr, y, 2, line[: w - 2],
                            curses.color_pair(color) | attr)
            elif kind == "detail":
                safe_addstr(self.stdscr, y, 2, f"       {payload}"[: w - 2],
                            curses.color_pair(self.C_DIM))
            elif kind == "remediation":
                safe_addstr(self.stdscr, y, 2, f"     → {payload}"[: w - 2],
                            curses.color_pair(self.C_INFO))
            elif kind == "path":
                safe_addstr(self.stdscr, y, 2, f"       {payload}"[: w - 2],
                            curses.color_pair(self.C_DIM) | curses.A_DIM)

        above = view_top
        below = len(lines) - (view_top + len(visible))
        if above or below:
            bits = []
            if above:
                bits.append(f"↑ {above} above")
            if below:
                bits.append(f"↓ {below} below")
            safe_addstr(self.stdscr, h - 2, 0,
                        ("  " + " · ".join(bits))[: w - 1],
                        curses.color_pair(self.C_DIM) | curses.A_DIM)

        if self.flash_text and time.time() < self.flash_until:
            flash_color = (self.C_OK if self.flash_text.startswith("✓")
                           else self.C_ERR)
            safe_addstr(self.stdscr, h - 1, 0,
                        f"  {self.flash_text}".ljust(w - 1),
                        curses.color_pair(flash_color) | curses.A_BOLD)
        else:
            hint = "  ↑/↓ scroll · [C] copy report · r rescan · m menu · q quit"
            safe_addstr(self.stdscr, h - 1, 0, hint.ljust(w - 1),
                        curses.color_pair(self.C_DIM) | curses.A_DIM)

    def draw_select(self, h: int, w: int):
        top = 4
        safe_addstr(self.stdscr, top, 2, f"{BULLET} Cache categories",
                    curses.color_pair(self.C_ACCENT) | curses.A_BOLD)
        safe_addstr(self.stdscr, top, 22,
                    "— select with space, clean with enter",
                    curses.color_pair(self.C_DIM))

        header_row = top + 2
        start_row = top + 3
        max_rows = min(15, h - start_row - 7)
        with self.scan_lock:
            sizes = dict(self.sizes)
            scanning = not self.scan_done
            scan_now = self.scan_current
            progress = self.scan_progress

        rows = sorted(self.cats, key=lambda c: -sizes.get(c.key, 0))
        if self.cursor >= len(rows):
            self.cursor = max(0, len(rows) - 1)

        view_top = 0
        if self.cursor >= max_rows:
            view_top = self.cursor - max_rows + 1
        visible = rows[view_top: view_top + max_rows]

        COL_CURSOR = 2
        COL_BULLET = 4
        COL_NAME = 6
        COL_SIZE = 30   # right-aligned, 11 wide
        COL_DIR = 43    # directory path (with ~ prefix)
        COL_SAFETY = 72 # 8 wide
        COL_DESC = 82   # notes, trimmed to fit

        def safety_style(level: str):
            if level == "safe":
                return "safe", self.C_OK, 0
            if level == "caution":
                return "caution", self.C_WARN, 0
            return "review", self.C_DIM, curses.A_DIM

        header_attr = curses.color_pair(self.C_DIM) | curses.A_DIM
        safe_addstr(self.stdscr, header_row, COL_NAME, "name", header_attr)
        safe_addstr(self.stdscr, header_row, COL_SIZE, f"{'size':>11}", header_attr)
        safe_addstr(self.stdscr, header_row, COL_DIR, "directory", header_attr)
        safe_addstr(self.stdscr, header_row, COL_SAFETY, "safety", header_attr)
        safe_addstr(self.stdscr, header_row, COL_DESC, "notes", header_attr)

        for i, c in enumerate(visible):
            real_idx = view_top + i
            y = start_row + i
            is_cursor = (real_idx == self.cursor)
            is_selected = (c.key in self.selected)

            is_pinned = c.key in self.whitelist
            cursor_glyph = MARK_CURSOR if is_cursor else " "
            bullet_glyph = ("⊘" if is_pinned else
                            MARK_SELECTED if is_selected else MARK_UNSELECTED)

            sz = sizes.get(c.key)
            if sz is None:
                size_str = "…"
            elif sz == 0:
                size_str = "—"
            else:
                size_str = human(sz)

            safe_addstr(self.stdscr, y, COL_CURSOR, cursor_glyph,
                        curses.color_pair(self.C_ACCENT) | curses.A_BOLD)
            bullet_color = self.C_ACCENT if is_selected else self.C_DIM
            safe_addstr(self.stdscr, y, COL_BULLET, bullet_glyph,
                        curses.color_pair(bullet_color))

            name = c.name
            name_max = COL_SIZE - COL_NAME - 1
            if len(name) > name_max:
                name = name[: name_max - 1] + "…"
            name_attr = curses.A_BOLD if is_cursor else 0
            safe_addstr(self.stdscr, y, COL_NAME, name.ljust(name_max),
                        name_attr)

            size_color = self.C_OK if is_selected else self.C_DIM
            safe_addstr(self.stdscr, y, COL_SIZE, f"{size_str:>11}",
                        curses.color_pair(size_color))

            dir_budget = max(0, COL_SAFETY - COL_DIR - 1)
            dir_text = c.path_hint or ""
            if len(dir_text) > dir_budget:
                dir_text = "…" + dir_text[-(dir_budget - 1):]
            safe_addstr(self.stdscr, y, COL_DIR, dir_text.ljust(dir_budget),
                        curses.color_pair(self.C_DIM))

            label, color_pair, extra_attr = safety_style(c.safety)
            safe_addstr(self.stdscr, y, COL_SAFETY, label.ljust(8),
                        curses.color_pair(color_pair) | extra_attr)

            desc_budget = max(0, w - COL_DESC - 2)
            desc = c.description
            if len(desc) > desc_budget:
                desc = desc[: max(0, desc_budget - 1)] + "…" if desc_budget > 0 else ""
            safe_addstr(self.stdscr, y, COL_DESC, desc,
                        curses.color_pair(self.C_DIM))

        # Scroll hint
        guide_y = start_row + len(visible)
        above = view_top
        below = len(rows) - (view_top + len(visible))
        parts = []
        if above > 0:
            parts.append(f"↑ {above} more above")
        if below > 0:
            parts.append(f"↓ {below} more below (use ↓ / PgDn)")
        if parts:
            guide = "  " + "   ".join(parts)
            safe_addstr(self.stdscr, guide_y, 0, guide[: w - 1],
                        curses.color_pair(self.C_DIM) | curses.A_DIM)
        elif len(rows) > 0:
            safe_addstr(self.stdscr, guide_y, 2, f"— {len(rows)} items —",
                        curses.color_pair(self.C_DIM) | curses.A_DIM)

        # Footer
        total_all = sum(sizes.values())
        total_sel = sum(sizes.get(c.key, 0) for c in self.cats
                        if c.key in self.selected)
        sel_count = len(self.selected)
        total_count = len(self.cats)

        def group_size(tag: str) -> int:
            return sum(sizes.get(c.key, 0) for c in self.cats
                       if tag in c.tags)

        dev_sz = group_size("dev")
        browser_sz = group_size("browser")
        artifact_sz = group_size("dev-artifacts")
        other_sz = group_size("other")
        safe_sz = group_size("safe")

        groups = (f"  groups:  dev {human(dev_sz)} · safe {human(safe_sz)} · "
                  f"browsers {human(browser_sz)} · artifacts {human(artifact_sz)} · "
                  f"other {human(other_sz)}")
        safe_addstr(self.stdscr, h - 5, 0, groups[: w - 1],
                    curses.color_pair(self.C_DIM))

        mode_tag = "  [DRY-RUN ON]" if self.dry_run else ""
        totals = (f"  {BULLET} total {human(total_all)} ({total_count})   "
                  f"selected {human(total_sel)} "
                  f"({sel_count}/{total_count}){mode_tag}")
        safe_addstr(self.stdscr, h - 4, 0, totals[: w - 1],
                    curses.color_pair(self.C_ACCENT) | curses.A_BOLD)

        if scanning:
            spin = SPINNER[self.spin_idx % len(SPINNER)]
            status = f"  {spin} scanning {progress}/{self.scan_total} — {scan_now}"
            safe_addstr(self.stdscr, h - 3, 0, status[: w - 1],
                        curses.color_pair(self.C_ACCENT))
        else:
            hovered = rows[self.cursor] if 0 <= self.cursor < len(rows) else None
            if hovered:
                label, color_pair, _ = (
                    ("safe",    self.C_OK,   0) if hovered.safety == "safe" else
                    ("caution", self.C_WARN, 0) if hovered.safety == "caution" else
                    ("review",  self.C_DIM,  curses.A_DIM)
                )
                note_text = hovered.safety_note or self.status
                note = f"  {label}: {note_text}"
                if hovered.path_hint:
                    note += f"  ·  {hovered.path_hint}"
                safe_addstr(self.stdscr, h - 3, 0, note[: w - 1],
                            curses.color_pair(color_pair))
            else:
                safe_addstr(self.stdscr, h - 3, 0, f"  {self.status}"[: w - 1],
                            curses.color_pair(self.C_DIM))

        primary = "  [A] select all   [N] deselect all   [SPACE] toggle   [↵] clean"
        safe_addstr(self.stdscr, h - 2, 0, primary[: w - 1],
                    curses.color_pair(self.C_ACCENT))

        hint = "  s safe · b browsers · v artifacts · o other · w pin · d dry-run · r rescan · q quit"
        safe_addstr(self.stdscr, h - 1, 0, hint[: w - 1].ljust(w - 1),
                    curses.color_pair(self.C_DIM) | curses.A_DIM)

    def _draw_sched_overlay(self, h: int, w: int) -> None:
        """Inline interval / time picker shown over the select screen."""
        n = len(self.sched_pending_keys)
        oy = h // 2 - 4
        box_w = 52
        ox = max(2, (w - box_w) // 2)

        def _ol(row, text, attr=curses.A_NORMAL):
            safe_addstr(self.stdscr, oy + row, ox, text[:box_w], attr)

        _ol(0, f"  Schedule {n} categor{'ies' if n != 1 else 'y'}",
            curses.color_pair(self.C_ACCENT) | curses.A_BOLD)
        _ol(1, "  " + "─" * (box_w - 4), curses.color_pair(self.C_DIM))

        if self.schedule_picking:
            _ol(2, "  Choose interval:", curses.color_pair(self.C_DIM))
            _ol(3, "    [w]  weekly — every Sunday",
                curses.color_pair(self.C_INFO))
            _ol(4, "    [d]  daily  — every day",
                curses.color_pair(self.C_INFO))
            _ol(5, "    [esc] cancel", curses.color_pair(self.C_DIM))

        elif self.schedule_time_picking:
            lbl = "weekly (Sunday)" if self.sched_interval == "weekly" else "daily"
            _ol(2, f"  Interval: {lbl}   set time:",
                curses.color_pair(self.C_DIM))
            h_attr = (curses.color_pair(self.C_ACCENT) | curses.A_BOLD
                      if self.sched_time_field == 0
                      else curses.color_pair(self.C_DIM))
            m_attr = (curses.color_pair(self.C_ACCENT) | curses.A_BOLD
                      if self.sched_time_field == 1
                      else curses.color_pair(self.C_DIM))
            _ol(3, f"    Hour   [ {self.sched_hour:02d} ]  ←/→ adjust", h_attr)
            _ol(4, f"    Minute [ {self.sched_minute:02d} ]  ←/→ adjust", m_attr)
            _ol(5, "    Tab switch field   ↵ confirm   esc cancel",
                curses.color_pair(self.C_DIM))

    def draw_action_choice(self, h: int, w: int):
        """Choose: Clean now vs Schedule clean for the current selection."""
        top = 4
        n = len(self.selected)
        safe_addstr(self.stdscr, top, 2,
                    f"{BULLET} {n} categor{'ies' if n != 1 else 'y'} selected — what do you want to do?",
                    curses.color_pair(self.C_ACCENT) | curses.A_BOLD)

        _items = [
            ("Clean now",      "delete cache files immediately (asks for confirmation)"),
            ("Schedule clean", "install as a recurring automatic cron job"),
        ]
        y = top + 2
        for i, (label, desc) in enumerate(_items):
            is_cur = (i == self.action_choice)
            marker = MARK_CURSOR if is_cur else " "
            label_attr = (curses.color_pair(self.C_ACCENT) | curses.A_BOLD
                          if is_cur else curses.A_BOLD)
            safe_addstr(self.stdscr, y, 4, f"  {marker} {label}", label_attr)
            safe_addstr(self.stdscr, y, 4 + 4 + len(label) + 2,
                        desc, curses.color_pair(self.C_DIM))
            y += 1

        hint = "  ↑/↓ move · ↵ confirm · n/Esc back · q quit"
        safe_addstr(self.stdscr, h - 1, 0, hint.ljust(w - 1),
                    curses.color_pair(self.C_DIM) | curses.A_DIM)

        # Schedule interval/time picker overlaid on top when active
        if self.schedule_picking or self.schedule_time_picking:
            self._draw_sched_overlay(h, w)

    def draw_confirm(self, h: int, w: int):
        total_sel = sum(self.sizes.get(c.key, 0) for c in self.cats
                        if c.key in self.selected)
        box_w = min(60, w - 6)
        mode = "dry-run — nothing deleted" if self.dry_run else "DELETE FILES"

        # N5: itemise the top selected categories so the user sees exactly
        # what's about to be cleaned. Selection drift between dry-run and
        # real-run is a common foot-gun without this.
        selected_cats = sorted(
            (c for c in self.cats if c.key in self.selected),
            key=lambda c: -self.sizes.get(c.key, 0),
        )
        N_PREVIEW = 5

        body = [
            f"{BULLET} Confirm cleanup",
            f"  mode       {mode}",
            f"  categories {len(self.selected)}",
            f"  estimate   {human(total_sel)}",
            "",
            "  about to clean:",
        ]
        for c in selected_cats[:N_PREVIEW]:
            sz = human(self.sizes.get(c.key, 0))
            name = c.name if len(c.name) <= 28 else c.name[:27] + "…"
            body.append(f"    • {name:<28} {sz:>10}")
        if len(selected_cats) > N_PREVIEW:
            body.append(f"    + {len(selected_cats) - N_PREVIEW} more …")
        body.extend(["", "  ❯ [y] proceed    [n] cancel"])
        box_h = len(body) + 2
        y0 = h - box_h - 4
        x0 = 2
        safe_addstr(self.stdscr, y0, x0,
                    BOX_TL + BOX_H * (box_w - 2) + BOX_TR,
                    curses.color_pair(self.C_ACCENT))
        safe_addstr(self.stdscr, y0 + box_h - 1, x0,
                    BOX_BL + BOX_H * (box_w - 2) + BOX_BR,
                    curses.color_pair(self.C_ACCENT))
        for i, ln in enumerate(body):
            row = y0 + 1 + i
            safe_addstr(self.stdscr, row, x0, BOX_V,
                        curses.color_pair(self.C_ACCENT))
            safe_addstr(self.stdscr, row, x0 + box_w - 1, BOX_V,
                        curses.color_pair(self.C_ACCENT))
            content = f" {ln}".ljust(box_w - 2)[: box_w - 2]
            if i == 0:
                safe_addstr(self.stdscr, row, x0 + 1, content,
                            curses.color_pair(self.C_ACCENT) | curses.A_BOLD)
            elif ln.startswith("  ❯"):
                safe_addstr(self.stdscr, row, x0 + 1, content,
                            curses.color_pair(self.C_ACCENT))
            else:
                safe_addstr(self.stdscr, row, x0 + 1, content,
                            curses.color_pair(self.C_DIM))

    def draw_log(self, h: int, w: int, title: str, color: int):
        top = 4
        safe_addstr(self.stdscr, top, 2, f"{BULLET} {title}",
                    curses.color_pair(color) | curses.A_BOLD)
        start = top + 2
        usable = h - start - 3
        visible = self.log[-usable:] if len(self.log) > usable else self.log
        for i, (line, c) in enumerate(visible):
            y = start + i
            if c:
                safe_addstr(self.stdscr, y, 2, line, curses.color_pair(c))
            else:
                safe_addstr(self.stdscr, y, 2, line,
                            curses.color_pair(self.C_DIM))

    def draw_stats(self, h: int, w: int):
        stats = load_stats()
        total = stats.get("total_freed_all_time", 0)
        sessions = stats.get("sessions", [])
        velocity = stats.get("bloat_velocity", [])

        top = 4
        safe_addstr(self.stdscr, top, 2,
                    f"{BULLET} Lifetime statistics",
                    curses.color_pair(self.C_ACCENT) | curses.A_BOLD)

        y = top + 2

        # Summary card
        card_w = min(w - 6, 60)
        safe_addstr(self.stdscr, y, 4,
                    BOX_TL + BOX_H * (card_w - 2) + BOX_TR,
                    curses.color_pair(self.C_DIM))
        y += 1
        lines = [
            ("Total freed (all time)", human(total)),
            ("Sessions recorded", str(len(sessions))),
        ]
        if sessions:
            avg_freed = sum(s.get("freed", 0) for s in sessions) // max(len(sessions), 1)
            lines.append(("Avg freed / session", human(avg_freed)))
            last = sessions[-1]
            lines.append(("Last session", f"{last.get('date', '?')[:10]}  {human(last.get('freed', 0))}"))

        for label, val in lines:
            row = f"{BOX_V}  {label:<26} {val:>20}  {BOX_V}"
            safe_addstr(self.stdscr, y, 4, row[:card_w],
                        curses.color_pair(self.C_DIM))
            safe_addstr(self.stdscr, y, 6, label,
                        curses.color_pair(self.C_INFO))
            safe_addstr(self.stdscr, y, 32, val,
                        curses.A_BOLD)
            y += 1

        safe_addstr(self.stdscr, y, 4,
                    BOX_BL + BOX_H * (card_w - 2) + BOX_BR,
                    curses.color_pair(self.C_DIM))
        y += 2


        # Bloat velocity
        if velocity:
            safe_addstr(self.stdscr, y, 4, "Cache footprint trend",
                        curses.color_pair(self.C_ACCENT) | curses.A_BOLD)
            y += 1
            recent = velocity[-8:]
            max_sz = max((v.get("total_cache_size", 0) for v in recent), default=1) or 1
            spark_w = min(20, w - 40)
            for v in recent:
                sz = v.get("total_cache_size", 0)
                date = v.get("date", "?")[:10]
                frac = sz / max_sz
                spark = bar(frac, spark_w, "▓", "░")
                line = f"    {date}  {human(sz):>10}  {spark}"
                safe_addstr(self.stdscr, y, 4, line[:w - 6],
                            curses.color_pair(self.C_DIM))
                y += 1
                if y >= h - 3:
                    break

            if len(velocity) >= 2:
                delta = velocity[-1].get("total_cache_size", 0) - velocity[-2].get("total_cache_size", 0)
                arrow = "↑" if delta > 0 else "↓" if delta < 0 else "→"
                safe_addstr(self.stdscr, y, 4,
                            f"    trend: {arrow} {human(abs(delta))} since last scan",
                            curses.color_pair(self.C_OK if delta <= 0 else self.C_WARN))
                y += 1

        elif not sessions:
            safe_addstr(self.stdscr, y, 4,
                        "No data yet. Run a cache clean to start tracking.",
                        curses.color_pair(self.C_DIM))

        # Recent sessions
        if sessions and y < h - 4:
            y += 1
            safe_addstr(self.stdscr, y, 4, "Recent sessions",
                        curses.color_pair(self.C_ACCENT) | curses.A_BOLD)
            y += 1
            for s in reversed(sessions[-5:]):
                date = s.get("date", "?")[:10]
                freed = human(s.get("freed", 0))
                dur = s.get("duration", 0)
                n_cats = len(s.get("categories", []))
                line = f"    {date}  freed {freed:>10}  ({n_cats} categories, {dur}s)"
                safe_addstr(self.stdscr, y, 4, line[:w - 6],
                            curses.color_pair(self.C_DIM))
                y += 1
                if y >= h - 2:
                    break

        hint = "  m menu · q quit"
        safe_addstr(self.stdscr, h - 1, 0, hint.ljust(w - 1),
                    curses.color_pair(self.C_DIM) | curses.A_DIM)

    # ---------------------- schedule cleaning -------------------------
    def draw_schedule(self, h: int, w: int):
        from .cli import schedule_status
        sched = schedule_status()

        safe_addstr(self.stdscr, 4, 2,
                    f"{BULLET} Manage schedule",
                    curses.color_pair(self.C_ACCENT) | curses.A_BOLD)

        y = 6
        if sched:
            safe_addstr(self.stdscr, y, 4, f"  active: {sched}",
                        curses.color_pair(self.C_OK))
            from .common import load_schedule_config
            cfg = load_schedule_config()
            keys = cfg.get("selected_keys", [])
            cats_label = f"{len(keys)} categories selected" if keys else "all non-whitelisted"
            safe_addstr(self.stdscr, y + 1, 4, f"  categories: {cats_label}",
                        curses.color_pair(self.C_DIM))
            y += 1
            y += 2
            if self.manage_confirm:
                safe_addstr(self.stdscr, y, 4,
                            f"  Current:  {sched}",
                            curses.color_pair(self.C_INFO))
                y += 1
                safe_addstr(self.stdscr, y, 4,
                            "  Remove this schedule?  [y] yes   [n] cancel",
                            curses.color_pair(self.C_WARN))
            else:
                safe_addstr(self.stdscr, y, 4,
                            "  ↵ remove schedule",
                            curses.color_pair(self.C_ACCENT))
        else:
            safe_addstr(self.stdscr, y, 4, "  no schedule active",
                        curses.color_pair(self.C_DIM))
            y += 2
            safe_addstr(self.stdscr, y, 4,
                        "  To set one: Cache cleaner → select categories → ↵ → Schedule clean",
                        curses.color_pair(self.C_DIM))

        if self.schedule_msg:
            y += 2
            if y < h - 3:
                safe_addstr(self.stdscr, y, 4, self.schedule_msg,
                            curses.color_pair(self.C_OK) | curses.A_BOLD)

        hint = "  m menu · q quit"
        safe_addstr(self.stdscr, h - 1, 0, hint.ljust(w - 1),
                    curses.color_pair(self.C_DIM) | curses.A_DIM)

    # ---------------------- agent tools drawing ----------------------
    def draw_agents_scan(self, h: int, w: int):
        top = 5
        spin = SPINNER[self.spin_idx % len(SPINNER)]
        safe_addstr(self.stdscr, top, 2,
                    f"{spin} Scanning agent skills and MCP servers",
                    curses.color_pair(self.C_ACCENT) | curses.A_BOLD)
        safe_addstr(self.stdscr, top, 44,
                    "— walking ~/.claude, ~/.codex, ~/.gemini",
                    curses.color_pair(self.C_DIM))

        bar_w = min(40, max(10, w - 30))
        frac = (self.agents_progress / self.agents_total) if self.agents_total else 0
        bar_str = bar(frac, bar_w, "█", "░")
        safe_addstr(self.stdscr, top + 2, 4,
                    f"{bar_str}  {self.agents_progress}/{self.agents_total}",
                    curses.color_pair(self.C_ACCENT))
        if self.agents_current:
            safe_addstr(self.stdscr, top + 4, 4,
                        f"→ {self.agents_current}",
                        curses.color_pair(self.C_DIM))

    _AGENT_LABELS = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini"}

    def _agents_lines(self) -> list[tuple[str, object]]:
        """Build a flat list of display lines for agents_browse, grouped by
        agent (skills) and by source (MCP servers).
        """
        lines: list[tuple[str, object]] = []

        # Skills — group by agent
        if self.agent_skills:
            by_agent: dict[str, list[SkillEntry]] = {}
            for sk in self.agent_skills:
                by_agent.setdefault(sk.agent, []).append(sk)
            for agent in sorted(by_agent):
                label = self._AGENT_LABELS.get(agent, agent.title())
                lines.append(("section", f"Skills · {label} ({len(by_agent[agent])})"))
                for sk in by_agent[agent]:
                    lines.append(("skill", sk))
                lines.append(("blank", None))

        # MCP servers — group by source
        if self.agent_mcp:
            by_source: dict[str, list[McpServerEntry]] = {}
            for srv in self.agent_mcp:
                by_source.setdefault(srv.source, []).append(srv)
            for source in sorted(by_source):
                lines.append(("section",
                              f"MCP Servers · {source} ({len(by_source[source])})"))
                for srv in by_source[source]:
                    lines.append(("mcp", srv))
                lines.append(("blank", None))

        # Trim trailing blank
        while lines and lines[-1][0] == "blank":
            lines.pop()
        return lines

    def _agents_advance(self, idx: int, direction: int,
                        lines: list[tuple[str, object]]) -> int:
        """Move cursor `direction` steps (±1) through `lines`, skipping
        blank rows.  Wraps around at the ends.
        """
        n = len(lines)
        if n == 0:
            return 0
        new = idx
        for _ in range(n):
            new = (new + direction) % n
            kind, _payload = lines[new]
            if kind != "blank":
                return new
        return idx

    def _skill_status_style(self, status: str):
        styles = {
            "ok":             ("✓", self.C_OK, 0),
            "broken_symlink": ("✗", self.C_ERR, curses.A_BOLD),
            "orphan":         ("?", self.C_WARN, 0),
            "suspicious":     ("!", self.C_WARN, curses.A_BOLD),
        }
        return styles.get(status, ("·", self.C_DIM, 0))

    def _mcp_status_style(self, status: str):
        styles = {
            "ok":                ("✓", self.C_OK, 0),
            "command_not_found": ("✗", self.C_ERR, curses.A_BOLD),
            "config_error":      ("!", self.C_WARN, curses.A_BOLD),
        }
        return styles.get(status, ("·", self.C_DIM, 0))

    def _draw_agents_legend(self, y: int, w: int):
        """Render a colored legend row.  Each glyph is drawn in the
        same color used in the list, so meaning is unambiguous.
        """
        parts = [
            ("✓", "ok",         self.C_OK),
            ("✗", "broken",     self.C_ERR),
            ("?", "orphan",     self.C_WARN),
            ("!", "suspicious", self.C_WARN),
        ]
        safe_addstr(self.stdscr, y, 2, "legend:",
                    curses.color_pair(self.C_DIM))
        x = 10
        for glyph, label, color in parts:
            if x + 2 + len(label) + 4 > w - 1:
                break
            safe_addstr(self.stdscr, y, x, glyph,
                        curses.color_pair(color) | curses.A_BOLD)
            safe_addstr(self.stdscr, y, x + 2, label,
                        curses.color_pair(self.C_DIM))
            x += 2 + len(label) + 4

    def draw_agents_browse(self, h: int, w: int):
        top = 4
        n_skills = len(self.agent_skills)
        n_mcp = len(self.agent_mcp)
        safe_addstr(self.stdscr, top, 2,
                    f"{BULLET} Agent tools   "
                    f"{n_skills} skills · {n_mcp} MCP servers",
                    curses.color_pair(self.C_ACCENT) | curses.A_BOLD)
        self._draw_agents_legend(top + 1, w)

        lines = self._agents_lines()
        start_row = top + 3
        footer_rows = 3
        max_rows = h - start_row - footer_rows

        if self.agents_cursor >= len(lines):
            self.agents_cursor = max(0, len(lines) - 1)

        view_top = 0
        if self.agents_cursor >= max_rows:
            view_top = self.agents_cursor - max_rows + 1
        visible = lines[view_top: view_top + max_rows]

        for i, (kind, payload) in enumerate(visible):
            y = start_row + i
            row_idx = view_top + i
            is_cursor = (row_idx == self.agents_cursor)
            cursor_ch = MARK_CURSOR if is_cursor else " "

            if kind == "section":
                safe_addstr(self.stdscr, y, 2,
                            f"  {BULLET} {payload}",
                            curses.color_pair(self.C_ACCENT) | curses.A_BOLD)
            elif kind == "skill":
                sk: SkillEntry = payload  # type: ignore
                glyph, color, attr = self._skill_status_style(sk.status)
                size_str = human(sk.size) if sk.size else ""
                inline = ""
                if sk.status != "ok":
                    inline = f"  {sk.detail}"
                line = (f"{cursor_ch} {glyph}    {sk.name:<32} "
                        f"{size_str:>10}{inline}")
                safe_addstr(self.stdscr, y, 4, line[:w - 5],
                            curses.color_pair(color) | attr)
            elif kind == "mcp":
                srv: McpServerEntry = payload  # type: ignore
                glyph, color, attr = self._mcp_status_style(srv.status)
                transport_tag = f"[{srv.transport}]"
                cmd_short = srv.command or "—"
                if len(cmd_short) > 36:
                    cmd_short = "…" + cmd_short[-35:]
                line = (f"{cursor_ch} {glyph}    {srv.name:<22} "
                        f"{cmd_short:<38} {transport_tag}")
                safe_addstr(self.stdscr, y, 4, line[:w - 5],
                            curses.color_pair(color) | attr)
            elif kind == "blank":
                pass

        # Detail line for cursor item
        detail_y = h - footer_rows
        if 0 <= self.agents_cursor < len(lines):
            kind, payload = lines[self.agents_cursor]
            detail = ""
            if kind == "skill":
                sk = payload  # type: ignore
                detail = f"  {sk.path}"
                if sk.detail:
                    detail += f"  — {sk.detail}"
            elif kind == "mcp":
                srv = payload  # type: ignore
                detail = f"  {srv.config_path}"
                if srv.detail:
                    detail += f"  — {srv.detail}"
            if detail:
                safe_addstr(self.stdscr, detail_y, 0, detail[:w - 1],
                            curses.color_pair(self.C_DIM) | curses.A_DIM)

        # Confirm bar
        if self.agents_confirm:
            safe_addstr(self.stdscr, h - 2, 0,
                        f"  Remove this entry? [y/n]".ljust(w - 1),
                        curses.color_pair(self.C_WARN) | curses.A_BOLD)
        elif self.flash_text and time.time() < self.flash_until:
            flash_color = (self.C_OK if self.flash_text.startswith("✓")
                           else self.C_ERR)
            safe_addstr(self.stdscr, h - 2, 0,
                        f"  {self.flash_text}".ljust(w - 1),
                        curses.color_pair(flash_color) | curses.A_BOLD)

        hint = "  ↑/↓ scroll · x remove broken/stale · r rescan · m menu · q quit"
        safe_addstr(self.stdscr, h - 1, 0, hint[:w - 1].ljust(w - 1),
                    curses.color_pair(self.C_DIM) | curses.A_DIM)

    def draw_progress_bar(self, h: int, w: int):
        spin = SPINNER[self.spin_idx % len(SPINNER)]
        frac = (self.clean_progress / self.clean_total) if self.clean_total else 0
        bar_w = min(30, max(10, w - 50))
        bar_str = bar(frac, bar_w, "█", "░")
        step = self.clean_progress + 1
        if self.active_item:
            label = f"cleaning {step}/{self.clean_total}: {self.active_item}"
            if len(label) > 36:
                label = label[:35] + "…"
            line = f"  {spin} {label:<38} {bar_str}"
        else:
            line = f"  {spin} {'finishing…':<38} {bar_str}"
        safe_addstr(self.stdscr, h - 2, 0, line,
                    curses.color_pair(self.C_ACCENT))
        hint = "  cleaning in progress… press Ctrl+C to stop after current item"
        safe_addstr(self.stdscr, h - 1, 0, hint[: w - 1],
                    curses.color_pair(self.C_DIM) | curses.A_DIM)

    # ---------------------- actions ----------------------
    def sorted_cats(self) -> list[Category]:
        with self.scan_lock:
            sizes = dict(self.sizes)
        return sorted(self.cats, key=lambda c: -sizes.get(c.key, 0))

    def toggle_current(self):
        rows = self.sorted_cats()
        if 0 <= self.cursor < len(rows):
            key = rows[self.cursor].key
            if key in self.selected:
                self.selected.discard(key)
            else:
                self.selected.add(key)

    def select_by_tag(self, tag: str):
        for c in self.cats:
            if tag in c.tags and c.key not in self.whitelist:
                self.selected.add(c.key)

    def start_rescan(self):
        with self.scan_lock:
            self.sizes.clear()
            self.scan_done = False
            self.scan_progress = 0
        self.selected.clear()
        self.cursor = 0
        self.log = []
        self.mode = "scan"
        self.status = "Scanning..."
        threading.Thread(target=self.scan_worker, daemon=True).start()

    # ---------------------- main loop ----------------------
    def run(self):
        self.setup_colors()
        curses.curs_set(0)
        self.stdscr.nodelay(True)
        self.stdscr.keypad(True)
        try:
            self._loop()
        except KeyboardInterrupt:
            return

    def _loop(self):
        last_spin = time.time()
        while True:
            now = time.time()
            if now - last_spin > 0.12:
                self.spin_idx += 1
                last_spin = now

            self.draw()

            try:
                ch = self.stdscr.getch()
            except KeyboardInterrupt:
                if self.mode == "clean":
                    self.stop_requested.set()
                    continue
                return
            if ch == -1:
                try:
                    curses.napms(50)
                except KeyboardInterrupt:
                    if self.mode == "clean":
                        self.stop_requested.set()
                        continue
                    return
                continue

            if self.mode == "menu":
                if ch in (ord("q"), 27):
                    return
                if ch in (curses.KEY_UP, ord("k")):
                    self.menu_cursor = max(0, self.menu_cursor - 1)
                elif ch in (curses.KEY_DOWN, ord("j")):
                    self.menu_cursor = min(len(self.MENU_ITEMS) - 1,
                                           self.menu_cursor + 1)
                elif ch in (curses.KEY_ENTER, 10, 13, ord(" ")):
                    self.plan = self.MENU_ITEMS[self.menu_cursor][0]
                    if self.plan in ("cache", "both"):
                        self.start_cache_scan()
                    elif self.plan == "stats":
                        self.mode = "stats"
                    elif self.plan == "agents":
                        self.start_agents_scan()
                    elif self.plan == "schedule":
                        self.manage_confirm = False
                        self.schedule_msg = ""
                        self.mode = "schedule"
                    else:
                        self.start_health_scan()
                continue

            if self.mode == "scan":
                if ch in (ord("q"), 27):
                    return
                continue

            if self.mode == "health_scan":
                if ch in (ord("q"), 27):
                    return
                continue

            if self.mode == "stats":
                if ch in (ord("q"), 27):
                    return
                if ch == ord("m"):
                    self.mode = "menu"
                continue

            if self.mode == "schedule":
                # ── manage confirm ──
                if self.manage_confirm:
                    if ch in (ord("y"), ord("Y")):
                        from .cli import schedule_status as _ss, unschedule_cron as _uc
                        if _ss():
                            _uc()
                            self.schedule_msg = "✓ Scheduled clean removed"
                        self.manage_confirm = False
                    elif ch in (ord("n"), ord("N"), 27):
                        self.manage_confirm = False
                        self.schedule_msg = ""
                    continue

                if ch in (ord("q"), 27):
                    return
                if ch == ord("m"):
                    self.manage_confirm = False
                    self.schedule_msg = ""
                    self.mode = "menu"
                elif ch in (curses.KEY_ENTER, 10, 13):
                    self.schedule_msg = ""
                    from .cli import schedule_status as _ss
                    if _ss():
                        self.manage_confirm = True
                    # else: nothing active to manage
                continue

            if self.mode == "agents_scan":
                if ch in (ord("q"), 27):
                    return
                continue

            if self.mode == "agents_browse":
                if ch in (ord("q"), 27):
                    return
                lines = self._agents_lines()
                last = max(0, len(lines) - 1)

                # Confirm dialog takes priority
                if self.agents_confirm is not None:
                    if ch in (ord("y"), ord("Y")):
                        kind, payload = lines[self.agents_cursor]
                        ok = False
                        if kind == "skill":
                            ok = remove_skill(payload)
                        elif kind == "mcp":
                            ok = remove_mcp_server(payload)
                        if ok:
                            self.flash("✓ Removed")
                            self.start_agents_scan()
                        else:
                            self.flash("✗ Removal failed")
                        self.agents_confirm = None
                    else:
                        self.agents_confirm = None
                    continue

                if ch == ord("g"):
                    if self.pending_g:
                        self.agents_cursor = 0
                        self.pending_g = False
                    else:
                        self.pending_g = True
                    continue
                if self.pending_g:
                    self.pending_g = False
                if ch == ord("G"):
                    self.agents_cursor = last
                    # snap back to the previous non-blank row if last is blank
                    if 0 <= self.agents_cursor < len(lines) and lines[self.agents_cursor][0] == "blank":
                        self.agents_cursor = self._agents_advance(
                            self.agents_cursor, -1, lines)
                elif ch in (curses.KEY_UP, ord("k")):
                    self.agents_cursor = self._agents_advance(
                        self.agents_cursor, -1, lines)
                elif ch in (curses.KEY_DOWN, ord("j")):
                    self.agents_cursor = self._agents_advance(
                        self.agents_cursor, 1, lines)
                elif ch == curses.KEY_PPAGE:
                    for _ in range(10):
                        self.agents_cursor = self._agents_advance(
                            self.agents_cursor, -1, lines)
                elif ch == curses.KEY_NPAGE:
                    for _ in range(10):
                        self.agents_cursor = self._agents_advance(
                            self.agents_cursor, 1, lines)
                elif ch == ord("x"):
                    if 0 <= self.agents_cursor < len(lines):
                        kind, payload = lines[self.agents_cursor]
                        removable = False
                        if kind == "skill" and payload.status in ("broken_symlink", "orphan"):
                            removable = True
                        elif kind == "mcp" and payload.status in ("command_not_found", "config_error"):
                            removable = True
                        if removable:
                            self.agents_confirm = "pending"
                        else:
                            self.flash("✗ Only broken/stale entries can be removed")
                elif ch == ord("r"):
                    self.start_agents_scan()
                elif ch == ord("m"):
                    self.mode = "menu"
                continue

            if self.mode == "health_results":
                if ch in (ord("q"), 27):
                    return
                total = 0
                for f in self.findings:
                    total += 1
                    if f.detail:      total += 1
                    if f.remediation: total += 1
                    if f.path:        total += 1
                total += len(HEALTH_MODULES) * 2
                last = max(0, total - 1)
                if ch == ord("g"):
                    if self.pending_g:
                        self.health_cursor = 0
                        self.pending_g = False
                    else:
                        self.pending_g = True
                    continue
                if self.pending_g:
                    self.pending_g = False
                if ch == ord("G"):
                    self.health_cursor = last
                elif ch in (curses.KEY_UP, ord("k")):
                    self.health_cursor = last if self.health_cursor == 0 else self.health_cursor - 1
                elif ch in (curses.KEY_DOWN, ord("j")):
                    self.health_cursor = 0 if self.health_cursor >= last else self.health_cursor + 1
                elif ch == curses.KEY_PPAGE:
                    self.health_cursor = max(0, self.health_cursor - 10)
                elif ch == curses.KEY_NPAGE:
                    self.health_cursor = min(last, self.health_cursor + 10)
                elif ch == curses.KEY_HOME:
                    self.health_cursor = 0
                elif ch == ord("r"):
                    self.start_health_scan()
                elif ch == ord("m"):
                    self.mode = "menu"
                elif ch in (ord("c"), ord("C")):
                    if self.copy_findings():
                        self.flash(f"✓ Copied {len(self.findings)} findings to clipboard")
                    else:
                        self.flash("✗ pbcopy failed (clipboard unavailable)")
                continue

            if self.mode == "select":
                if ch in (ord("q"), 27):
                    return
                rows = self.sorted_cats()
                last = max(0, len(rows) - 1)
                if ch == ord("g"):
                    if self.pending_g:
                        self.cursor = 0
                        self.pending_g = False
                    else:
                        self.pending_g = True
                    continue
                if self.pending_g:
                    self.pending_g = False
                if ch == ord("G"):
                    self.cursor = last
                elif ch in (curses.KEY_UP, ord("k")):
                    self.cursor = last if self.cursor == 0 else self.cursor - 1
                elif ch in (curses.KEY_DOWN, ord("j")):
                    self.cursor = 0 if self.cursor >= last else self.cursor + 1
                elif ch == curses.KEY_HOME:
                    self.cursor = 0
                elif ch == curses.KEY_END:
                    self.cursor = last
                elif ch == curses.KEY_PPAGE:
                    self.cursor = max(0, self.cursor - 5)
                elif ch == curses.KEY_NPAGE:
                    self.cursor = min(last, self.cursor + 5)
                elif ch == ord(" "):
                    self.toggle_current()
                elif ch in (ord("a"), ord("A")):
                    self.selected = {c.key for c in self.cats
                                     if c.key not in self.whitelist}
                elif ch in (ord("n"), ord("N")):
                    self.selected.clear()
                elif ch == ord("w"):
                    if 0 <= self.cursor < len(rows):
                        key = rows[self.cursor].key
                        if key in self.whitelist:
                            self.whitelist.discard(key)
                        else:
                            self.whitelist.add(key)
                            self.selected.discard(key)
                        save_whitelist(self.whitelist)
                elif ch == ord("s"):
                    self.selected = {
                        c.key for c in self.cats
                        if c.safety == "safe" and c.key not in self.whitelist
                    }
                elif ch == ord("b"):
                    self.selected.clear()
                    self.select_by_tag("browser")
                elif ch == ord("o"):
                    self.selected.clear()
                    self.select_by_tag("other")
                elif ch == ord("v"):
                    self.selected.clear()
                    self.select_by_tag("dev-artifacts")
                elif ch == ord("d"):
                    self.dry_run = not self.dry_run
                elif ch == ord("r"):
                    self.start_rescan()
                elif ch in (curses.KEY_ENTER, 10, 13):
                    if self.selected:
                        self.action_choice = 0   # default: Clean now
                        self.mode = "action_choice"
            elif self.mode == "action_choice":
                # ── schedule picker overlays (highest priority) ──
                if self.schedule_picking:
                    if ch == 27:
                        self.schedule_picking = False
                    elif ch in (ord("w"), ord("d")):
                        self.sched_interval = "weekly" if ch == ord("w") else "daily"
                        self.sched_time_field = 0
                        self.schedule_picking = False
                        self.schedule_time_picking = True
                    continue
                if self.schedule_time_picking:
                    if ch == 27:
                        self.schedule_time_picking = False
                    elif ch == ord("\t"):
                        self.sched_time_field = 1 - self.sched_time_field
                    elif ch in (curses.KEY_RIGHT, ord("l")):
                        if self.sched_time_field == 0:
                            self.sched_hour = (self.sched_hour + 1) % 24
                        else:
                            self.sched_minute = (self.sched_minute + 5) % 60
                    elif ch in (curses.KEY_LEFT, ord("h")):
                        if self.sched_time_field == 0:
                            self.sched_hour = (self.sched_hour - 1) % 24
                        else:
                            self.sched_minute = (self.sched_minute - 5) % 60
                    elif ch in (curses.KEY_ENTER, 10, 13):
                        from .cli import schedule_cron as _sc, print_schedule_summary
                        _sc(self.sched_interval, self.sched_hour,
                            self.sched_minute, list(self.sched_pending_keys),
                            quiet=True)
                        self.schedule_time_picking = False
                        n = len(self.sched_pending_keys)
                        # Temporarily leave curses, print the summary box,
                        # then restore the TUI display.
                        curses.endwin()
                        print_schedule_summary(
                            self.sched_interval, self.sched_hour,
                            self.sched_minute, n,
                        )
                        input("  Press Enter to return to Maidbook…")
                        self.stdscr.touchwin()
                        self.stdscr.refresh()
                        self.mode = "menu"      # back to main menu after scheduling
                    continue

                if ch in (ord("q"),):
                    return
                elif ch in (ord("n"), ord("N"), 27):
                    self.mode = "select"        # cancel → back to select
                elif ch in (curses.KEY_UP, ord("k")):
                    self.action_choice = max(0, self.action_choice - 1)
                elif ch in (curses.KEY_DOWN, ord("j")):
                    self.action_choice = min(1, self.action_choice + 1)
                elif ch in (curses.KEY_ENTER, 10, 13):
                    if self.action_choice == 0:
                        self.mode = "confirm"   # Clean now → confirm screen
                    else:
                        # Schedule clean → copy selection, enter interval picker
                        self.sched_pending_keys = set(self.selected)
                        self.sched_interval = "weekly"
                        self.sched_hour = 3
                        self.sched_minute = 0
                        self.sched_time_field = 0
                        self.schedule_picking = True
            elif self.mode == "confirm":
                if ch in (ord("y"), ord("Y")):
                    self.mode = "clean"
                    threading.Thread(target=self.clean_worker,
                                     daemon=True).start()
                elif ch in (ord("q"),):
                    return
                elif ch in (ord("n"), ord("N"), 27):
                    self.mode = "select"
            elif self.mode == "clean":
                # Ctrl+C is handled by KeyboardInterrupt above; q/Esc requests a stop
                if ch in (ord("q"), 27):
                    self.stop_requested.set()
            elif self.mode == "done":
                if ch in (ord("q"), 27):
                    return
                elif ch == ord("r"):
                    self.start_rescan()
                elif ch == ord("m"):
                    self.log = []
                    self.selected.clear()
                    self.mode = "menu"
                elif ch in (curses.KEY_ENTER, 10, 13):
                    self.log = []
                    if self.plan == "both":
                        self.plan = "health"
                        self.start_health_scan()
                    else:
                        self.mode = "select"


def run_tui():
    cats = build_categories()

    def _main(stdscr):
        tui = TUI(stdscr, cats)
        tui.run()

    curses.wrapper(_main)
