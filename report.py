"""Scan output: the terminal report."""

from __future__ import annotations

import os
import sys
import unicodedata

from classify import ACTIONABLE, LEVELS, Classification
from scanner import DirNode, ScanResult, display_path, walk_nodes

_ESC = chr(27)
_BACKSLASH = chr(92)

UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def enable_ansi() -> bool:
    """Turn on ANSI sequences in the Windows console. Returns whether we have color."""
    if not sys.stdout.isatty():
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


class Style:
    def __init__(self, enabled: bool) -> None:
        self.on = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"{_ESC}[{code}m{text}{_ESC}[0m" if self.on else text

    def bold(self, t): return self._wrap("1", t)
    def dim(self, t): return self._wrap("2", t)
    def cyan(self, t): return self._wrap("36", t)
    def yellow(self, t): return self._wrap("33", t)
    def red(self, t): return self._wrap("31", t)
    def green(self, t): return self._wrap("32", t)

    def link(self, url: str, text: str | None = None) -> str:
        """OSC 8 hyperlink in the terminals that understand it.

        Everywhere else the plain URL goes out: almost every terminal spots it.
        """
        text = url if text is None else text
        if not (self.on and supports_hyperlinks()):
            return text
        st = _ESC + _BACKSLASH
        return f"{_ESC}]8;;{url}{st}{text}{_ESC}]8;;{st}"


def supports_hyperlinks() -> bool:
    """Known terminals with OSC 8 hyperlinks."""
    env = os.environ
    return bool(env.get("WT_SESSION") or env.get("VTE_VERSION")
                or env.get("KONSOLE_VERSION") or env.get("KITTY_WINDOW_ID")
                or env.get("TERM_PROGRAM") in ("vscode", "iTerm.app", "WezTerm",
                                               "ghostty", "Hyper"))


def ascii_text(text: str) -> str:
    """Drop accents for the console, which does not always take UTF-8."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def human(size: float) -> str:
    """Format bytes for humans (base 1024)."""
    idx = 0
    value = float(size)
    while value >= 1024 and idx < len(UNITS) - 1:
        value /= 1024
        idx += 1
    if idx == 0:
        return f"{int(value)} B"
    precision = 1 if value >= 10 else 2
    return f"{value:.{precision}f} {UNITS[idx]}"


def plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def bar(fraction: float, width: int = 22) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = int(round(fraction * width))
    return "#" * filled + "." * (width - filled)


def find_hotspots(root: DirNode, min_bytes: int, dominance: float = 0.55) -> list[DirNode]:
    """Folders where the space *actually* piles up.

    A parent like C:/Users always looks huge even when it is only a container.
    We keep the big folders in which no single child holds most of the size:
    that is where the weight really sits.
    """
    spots: list[DirNode] = []
    for node in walk_nodes(root):
        if node.total < min_bytes:
            continue
        biggest_child = max((c.total for c in node.children), default=0)
        if biggest_child < node.total * dominance:
            spots.append(node)
    spots.sort(key=lambda n: n.total, reverse=True)
    return spots


def _children_breakdown(node: DirNode) -> list[tuple[str, int, bool]]:
    """Children (folders + loose files, added up) sorted by size."""
    rows = [(c.name, c.total, True) for c in node.children]
    if node.own_size:
        rows.append((f"({plural(node.own_files, 'loose file', 'loose files')})",
                     node.own_size, False))
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows


def print_report(result: ScanResult, *, top: int = 20, depth: int = 1,
                 style: Style | None = None, out=None,
                 cleanup: Classification | None = None) -> None:
    out = out if out is not None else sys.stdout
    st = style or Style(False)
    root = result.root
    total = root.total or 1

    def line(text: str = "") -> None:
        print(text, file=out)

    line()
    line(st.bold(f"  DISK ANALYSIS  {root.name}"))
    measure = "on disk" if result.measure == "disk" else "logical"
    line(st.dim(f"  {human(root.total)} {measure} in "
                f"{plural(root.total_files, 'file', 'files')} "
                f"| {result.elapsed:.1f}s"))
    line()

    # --- First level breakdown ---------------------------------------------
    line(st.bold(f"  SPACE BY FOLDER (level {depth})"))
    line()
    _print_level(root, depth, total, st, out)
    line()

    # --- Space hotspots -----------------------------------------------------
    min_spot = max(50 << 20, int(root.total * 0.005))
    spots = find_hotspots(root, min_spot)[:top]
    if spots:
        line(st.bold("  SPACE HOTSPOTS  ") +
             st.dim("(where the weight piles up, without repeating parent folders)"))
        line()
        base = len(root.name.rstrip(os.sep)) + 1
        for node in spots:
            path = node.path()
            shown = path[base:] if len(path) > base else path
            pct = node.total / total
            line(f"  {human(node.total):>10}  {st.dim(bar(pct))} {pct*100:5.1f}%  "
                 f"{st.cyan(shown)}")
            line(st.dim("              " + plural(node.total_files, "file", "files")))
        line()

    # --- Largest files ------------------------------------------------------
    files = sorted(result.top_files, reverse=True)[:top]
    if files:
        line(st.bold("  LARGEST FILES"))
        line()
        for size, path in files:
            line(f"  {human(size):>10}  {display_path(path)}")
        line()

    # --- By file type -------------------------------------------------------
    exts = sorted(result.by_ext.items(), key=lambda kv: kv[1][1], reverse=True)[:15]
    if exts:
        line(st.bold("  BY FILE TYPE"))
        line()
        for ext, (count, size) in exts:
            pct = size / total
            line(f"  {human(size):>10}  {st.dim(bar(pct))} {pct*100:5.1f}%  "
                 f"{ext:<16} {st.dim(plural(count, 'file', 'files'))}")
        line()

    # --- Cleanup ------------------------------------------------------------
    if cleanup is not None and cleanup.findings:
        _print_cleanup(cleanup, root, top, st, out)

    # --- Notes --------------------------------------------------------------
    notes = []
    if result.sparse_files and result.sparse_saved > (10 << 20):
        notes.append(
            plural(result.sparse_files, "sparse or compressed file",
                   "sparse or compressed files") +
            f" claim {human(result.sparse_saved)} more than they use; "
            "the real size is what counts here")
    if result.cloud_only_files:
        notes.append(plural(result.cloud_only_files, "online-only file",
                            "online-only files") +
                     f" ({human(result.cloud_only_bytes)}) left out: they use no disk")
    if result.skipped_links:
        notes.append(plural(result.skipped_links, "link/junction skipped",
                            "links/junctions skipped") +
                     " (keeps the same data from being counted twice)")
    if result.errors:
        notes.append(plural(len(result.errors), "unreadable folder",
                            "unreadable folders") +
                     " (permissions); run as administrator to include them")
    if notes:
        line(st.bold("  NOTES"))
        for note in notes:
            line(st.yellow("  ! ") + st.dim(note))
        line()


_LEVEL_COLOR = {"safe": "green", "review": "yellow", "tool": "red"}


def _print_cleanup(cleanup: Classification, root: DirNode, top: int, st: Style,
                   out) -> None:
    """Cleanup candidates grouped by level and, inside it, by kind of thing."""
    totals = cleanup.totals()
    groups = cleanup.groups()
    per_level = max(3, top // 4)
    base = len(root.name.rstrip(os.sep)) + 1

    print(st.bold("  CLEANUP  ") +
          st.dim("(what each one is and how to clean it: in the dashboard)"), file=out)
    print(file=out)
    for level in ACTIONABLE:
        size, count = totals[level]
        if not count:
            continue
        paint = getattr(st, _LEVEL_COLOR[level])
        print(f"  {paint(st.bold(f'{LEVELS[level][0]:<18}'))}{human(size):>10}  "
              f"{st.dim('in ' + plural(count, 'place', 'places'))}", file=out)
        shown = [g for g in groups if g[0].level == level][:per_level]
        for rule, group_size, items in shown:
            if len(items) == 1:
                finding = items[0]
                path = finding.node.path()
                if finding.file:
                    path = os.path.join(path, finding.file)
                where = path[base:] if len(path) > base else path
            else:
                where = plural(len(items), "place", "places")
            print(f"    {human(group_size):>10}  {ascii_text(rule.label):<36} "
                  f"{st.dim(where)}", file=out)
        print(file=out)


def _print_level(node: DirNode, depth: int, total: int, st: Style, out,
                 indent: str = "  ") -> None:
    rows = _children_breakdown(node)
    node_map = {c.name: c for c in node.children}
    for name, size, is_dir in rows:
        if size == 0:
            continue
        pct = size / total
        label = st.cyan(name) if is_dir else st.dim(name)
        print(f"{indent}{human(size):>10}  {st.dim(bar(pct))} {pct*100:5.1f}%  {label}",
              file=out)
        if is_dir and depth > 1:
            _print_level(node_map[name], depth - 1, total, st, out, indent + "    ")
