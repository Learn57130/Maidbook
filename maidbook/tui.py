"""Curses TUI: menu → cache scan/select/clean → health scan/results.

All state machine transitions happen through the mode attribute:
``menu → scan → select → confirm → clean → done`` for the cache flow, and
``menu → health_scan → health_results`` for the health flow. The ``both``
plan chains ``done → health_scan``.
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
    human, is_app_running, short_count,
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
        ("cache",  "Cache cleaner",
         "free up disk space · safe for cookies, history, logins"),
        ("health", "Health check",
         "malware heuristics · code-sign audit · XProtect · CVE scan"),
        ("both",   "Both",
         "clean caches first, then run health check"),
    ]

    def __init__(self, stdscr, cats: list[Category]):
        self.stdscr = stdscr
        self.cats = cats
        self.sizes: dict[str, int] = {}
        self.counts: dict[str, tuple[int, int]] = {}  # key -> (files, dirs)
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
            except OSError:
                return cat, (0, 0, 0)

        done = 0
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = {pool.submit(_one, c): c for c in self.cats}
            for fut in as_completed(futures):
                cat, (sz, files_n, dirs_n) = fut.result()
                done += 1
                with self.scan_lock:
                    self.sizes[cat.key] = sz
                    self.counts[cat.key] = (files_n, dirs_n)
                    self.scan_progress = done
                    self.scan_current = cat.name
        with self.scan_lock:
            self.scan_done = True
            self.scan_current = ""
            self.status = "Ready. Up/Down to move, SPACE to toggle, ENTER to clean."
        if self.mode == "scan":
            self.mode = "select"

    def clean_worker(self):
        rows = self.sorted_cats()
        selected = [c for c in rows if c.key in self.selected]
        self.clean_total = len(selected)
        self.clean_progress = 0
        self.log = [(f"Cleaning {len(selected)} categories (dry-run={self.dry_run})", 1),
                    ("", 0)]

        total_freed, total_errs = 0, 0
        for c in selected:
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
            self.clean_progress += 1

        self.active_item = ""
        self.log.append(("", 0))
        label = "Would free" if self.dry_run else "Freed"
        self.log.append((f"{label}: {human(total_freed)}    Errors: {total_errs}", 2))
        self.mode = "done"

    def start_cache_scan(self):
        self.mode = "scan"
        with self.scan_lock:
            self.sizes.clear()
            self.counts.clear()
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
            lines.append(f"  {glyph} [{f.severity}] {f.title}")
            if f.detail:
                lines.append(f"      {f.detail}")
            if f.remediation:
                lines.append(f"      → {f.remediation}")
            if f.path:
                lines.append(f"      {f.path}")
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
        else:  # "done"
            self.draw_log(h, w, title="DONE", color=self.C_OK)
            if self.plan == "both":
                hint = "  ↵ continue to health check · q quit · r rescan caches"
            else:
                hint = "  q quit · r rescan · ↵ back to menu"
            safe_addstr(self.stdscr, h - 1, 0, hint.ljust(w - 1),
                        curses.color_pair(self.C_DIM) | curses.A_DIM)

        self.stdscr.refresh()

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
            partial_files = sum(f for f, _d in self.counts.values())
            partial_dirs = sum(d for _f, d in self.counts.values())

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

        line_total = (f"  total so far    {human(partial_bytes):>12}   "
                      f"{partial_files:,} files, {partial_dirs:,} folders")
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
                lines.append(("detail", f.detail))
            if f.remediation:
                lines.append(("remediation", f.remediation))
            if f.path:
                lines.append(("path", f.path))

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
            counts = dict(self.counts)
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
        COL_SIZE = 30
        COL_ITEMS = 42
        COL_SAFETY = 55
        COL_DESC = 65

        def safety_style(level: str):
            if level == "safe":
                return "safe", self.C_OK, 0
            if level == "caution":
                return "caution", self.C_WARN, 0
            return "review", self.C_DIM, curses.A_DIM

        header_attr = curses.color_pair(self.C_DIM) | curses.A_DIM
        safe_addstr(self.stdscr, header_row, COL_NAME, "name", header_attr)
        safe_addstr(self.stdscr, header_row, COL_SIZE, f"{'size':>11}", header_attr)
        safe_addstr(self.stdscr, header_row, COL_ITEMS, f"{'files/dirs':>12}", header_attr)
        safe_addstr(self.stdscr, header_row, COL_SAFETY, "safety", header_attr)
        safe_addstr(self.stdscr, header_row, COL_DESC, "notes", header_attr)

        for i, c in enumerate(visible):
            real_idx = view_top + i
            y = start_row + i
            is_cursor = (real_idx == self.cursor)
            is_selected = (c.key in self.selected)

            cursor_glyph = MARK_CURSOR if is_cursor else " "
            bullet_glyph = MARK_SELECTED if is_selected else MARK_UNSELECTED

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

            fn, dn = counts.get(c.key, (0, 0))
            if sz is None:
                items_str = "…"
            elif fn == 0 and dn == 0:
                items_str = "—"
            else:
                items_str = f"{short_count(fn)}/{short_count(dn)}"
            safe_addstr(self.stdscr, y, COL_ITEMS, f"{items_str:>12}",
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
        other_sz = group_size("other")
        safe_sz = group_size("safe")

        groups = (f"  groups:  dev {human(dev_sz)} · safe {human(safe_sz)} · "
                  f"browsers {human(browser_sz)} · other {human(other_sz)}")
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
                fn, dn = counts.get(hovered.key, (0, 0))
                count_bits = []
                if fn:
                    count_bits.append(f"{fn:,} files")
                if dn:
                    count_bits.append(f"{dn:,} folders")
                count_str = ", ".join(count_bits) if count_bits else "empty"
                note_text = hovered.safety_note or self.status
                note = f"  {label}: {note_text}  ·  {count_str}"
                safe_addstr(self.stdscr, h - 3, 0, note[: w - 1],
                            curses.color_pair(color_pair))
            else:
                safe_addstr(self.stdscr, h - 3, 0, f"  {self.status}"[: w - 1],
                            curses.color_pair(self.C_DIM))

        primary = "  [A] select all   [N] deselect all   [SPACE] toggle   [↵] clean"
        safe_addstr(self.stdscr, h - 2, 0, primary[: w - 1],
                    curses.color_pair(self.C_ACCENT))

        hint = "  s safe · b browsers · o other · d dry-run · r rescan · q quit"
        safe_addstr(self.stdscr, h - 1, 0, hint[: w - 1].ljust(w - 1),
                    curses.color_pair(self.C_DIM) | curses.A_DIM)

    def draw_confirm(self, h: int, w: int):
        total_sel = sum(self.sizes.get(c.key, 0) for c in self.cats
                        if c.key in self.selected)
        box_w = min(60, w - 6)
        mode = "dry-run — nothing deleted" if self.dry_run else "DELETE FILES"
        body = [
            f"{BULLET} Confirm cleanup",
            f"  mode       {mode}",
            f"  categories {len(self.selected)}",
            f"  estimate   {human(total_sel)}",
            "",
            "  ❯ [y] proceed    [n] cancel",
        ]
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

    def draw_progress_bar(self, h: int, w: int):
        spin = SPINNER[self.spin_idx % len(SPINNER)]
        frac = (self.clean_progress / self.clean_total) if self.clean_total else 0
        bar_w = min(30, max(10, w - 40))
        bar_str = bar(frac, bar_w, "█", "░")
        if self.active_item:
            line = (f"  {spin} {self.active_item:<22} {bar_str} "
                    f"{self.clean_progress}/{self.clean_total}")
        else:
            line = (f"  {spin} finishing…              {bar_str} "
                    f"{self.clean_progress}/{self.clean_total}")
        safe_addstr(self.stdscr, h - 2, 0, line,
                    curses.color_pair(self.C_ACCENT))
        safe_addstr(self.stdscr, h - 1, 0, "  cleaning in progress…",
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
            if tag in c.tags:
                self.selected.add(c.key)

    def start_rescan(self):
        with self.scan_lock:
            self.sizes.clear()
            self.counts.clear()
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
                return
            if ch == -1:
                try:
                    curses.napms(50)
                except KeyboardInterrupt:
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
                if ch in (curses.KEY_UP, ord("k")):
                    self.health_cursor = max(0, self.health_cursor - 1)
                elif ch in (curses.KEY_DOWN, ord("j")):
                    self.health_cursor = min(max(0, total - 1),
                                             self.health_cursor + 1)
                elif ch == curses.KEY_PPAGE:
                    self.health_cursor = max(0, self.health_cursor - 10)
                elif ch == curses.KEY_NPAGE:
                    self.health_cursor = min(max(0, total - 1),
                                             self.health_cursor + 10)
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
                if ch in (curses.KEY_UP, ord("k")):
                    self.cursor = max(0, self.cursor - 1)
                elif ch in (curses.KEY_DOWN, ord("j")):
                    self.cursor = min(max(0, len(rows) - 1), self.cursor + 1)
                elif ch == curses.KEY_HOME:
                    self.cursor = 0
                elif ch == curses.KEY_END:
                    self.cursor = max(0, len(rows) - 1)
                elif ch == curses.KEY_PPAGE:
                    self.cursor = max(0, self.cursor - 5)
                elif ch == curses.KEY_NPAGE:
                    self.cursor = min(max(0, len(rows) - 1), self.cursor + 5)
                elif ch == ord(" "):
                    self.toggle_current()
                elif ch in (ord("a"), ord("A")):
                    self.selected = {c.key for c in self.cats}
                elif ch in (ord("n"), ord("N")):
                    self.selected.clear()
                elif ch == ord("s"):
                    self.selected.clear()
                    self.select_by_tag("safe")
                elif ch == ord("b"):
                    self.select_by_tag("browser")
                elif ch == ord("o"):
                    self.select_by_tag("other")
                elif ch == ord("d"):
                    self.dry_run = not self.dry_run
                elif ch == ord("r"):
                    self.start_rescan()
                elif ch in (curses.KEY_ENTER, 10, 13):
                    if self.selected:
                        self.mode = "confirm"
            elif self.mode == "confirm":
                if ch in (ord("y"), ord("Y")):
                    self.mode = "clean"
                    threading.Thread(target=self.clean_worker,
                                     daemon=True).start()
                elif ch in (ord("n"), ord("N"), 27):
                    self.mode = "select"
            elif self.mode == "clean":
                pass
            elif self.mode == "done":
                if ch in (ord("q"), 27):
                    return
                elif ch == ord("r"):
                    self.start_rescan()
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
