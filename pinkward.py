#!/usr/bin/env python3
"""PinkWard - see what is taking up space on your disk, and where.

When it finishes it prints the report in the console plus a link to an HTML
dashboard with charts, folder navigation and what can be cleaned. In an
interactive terminal the dashboard is served locally (http://127.0.0.1) while
PinkWard stays open, so you can delete the safe things from it.

Examples:
  python pinkward.py                      scan the current drive
  python pinkward.py C:/Users/me          scan one folder
  python pinkward.py C:/ D:/              scan two drives in one go
  python pinkward.py --all-drives         scan every fixed drive on this PC
  python pinkward.py D:/ --open           and open the dashboard in the browser
  python pinkward.py . --depth 2 --top 30
"""

from __future__ import annotations

import argparse
import ctypes
import os
import pathlib
import shutil
import string
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import classify
import dashboard
import health as health_check
import report as rep
import scanner
import server
from version import VERSION

DEFAULT_EXCLUDES = ["$Recycle.Bin", "System Volume Information"]
DRIVE_FIXED = 3       # GetDriveTypeW: a real disk, not a stick or a share
ALL_DRIVES_NAME = "This PC"
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


def parse_size(text: str) -> int:
    """Takes '500', '10MB', '1.5 GB'."""
    text = text.strip().upper().replace(" ", "")
    factors = {"B": 1, "K": 1 << 10, "KB": 1 << 10, "M": 1 << 20, "MB": 1 << 20,
               "G": 1 << 30, "GB": 1 << 30, "T": 1 << 40, "TB": 1 << 40}
    for suffix in sorted(factors, key=len, reverse=True):
        if text.endswith(suffix):
            number = text[: -len(suffix)] or "0"
            return int(float(number) * factors[suffix])
    return int(float(text))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="PinkWard",
        description="See what is taking up space on your disk, and where.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1] if "Examples:" in __doc__ else None,
    )
    parser.add_argument("--version", action="version", version=f"PinkWard {VERSION}")
    parser.add_argument("path", nargs="*", default=None, metavar="PATH",
                        help="folder or drive to scan (the current drive by default). "
                             "Several can be given: they are scanned one after "
                             "another and the dashboard walks all of them")
    parser.add_argument("-a", "--all-drives", action="store_true",
                        help="scan every fixed drive on this PC")
    parser.add_argument("-t", "--top", type=int, default=20,
                        help="how many entries to show in each ranking (20)")
    parser.add_argument("-d", "--depth", type=int, default=1,
                        help="folder levels in the first breakdown (1)")
    parser.add_argument("--dashboard", "--html", dest="dashboard", nargs="?",
                        default=None, metavar="FILE",
                        help="where to save the HTML dashboard "
                             "(reports/<scanned path>.html by default)")
    parser.add_argument("--no-dashboard", action="store_true",
                        help="console report only, no dashboard")
    parser.add_argument("--open", action="store_true",
                        help="open the dashboard in your browser when done")
    parser.add_argument("--no-serve", action="store_true",
                        help="do not serve the dashboard locally: just the file, "
                             "with no way to delete from it")
    parser.add_argument("-x", "--exclude", action="append", default=[],
                        metavar="NAME",
                        help="folder name to skip (can be repeated)")
    parser.add_argument("--min-file", type=parse_size, default="1MB",
                        metavar="SIZE",
                        help="smallest single file worth listing (1MB)")
    parser.add_argument("--follow-links", action="store_true",
                        help="follow symlinks and junctions (may count twice)")
    parser.add_argument("--logical", action="store_true",
                        help="use the logical size instead of the real size on disk")
    parser.add_argument("--count-cloud", action="store_true",
                        help="count online-only files (OneDrive) as taking up space")
    parser.add_argument("--no-report", action="store_true",
                        help="skip the console report: only the dashboard link")
    parser.add_argument("--no-health", action="store_true",
                        help="do not ask Windows about the health of the drives")
    parser.add_argument("--no-clipboard", action="store_true",
                        help="do not put the dashboard link on the clipboard")
    parser.add_argument("--no-color", action="store_true", help="output without color")
    parser.add_argument("--quiet", action="store_true", help="no progress line")
    return parser


def fixed_drives() -> list[str]:
    """Every fixed drive on this PC, in letter order."""
    out = []
    try:
        drive_type = ctypes.windll.kernel32.GetDriveTypeW
    except (AttributeError, OSError):
        return out
    for letter in string.ascii_uppercase:
        root = letter + ":" + os.sep
        try:
            if drive_type(root) == DRIVE_FIXED and os.path.isdir(root):
                out.append(root)
        except OSError:
            continue
    return out


def _within(path: str, other: str) -> bool:
    """Whether `path` sits inside `other` (or is the same folder)."""
    path = os.path.normcase(path.rstrip(os.sep)) + os.sep
    other = os.path.normcase(other.rstrip(os.sep)) + os.sep
    return path.startswith(other)


def resolve_paths(raw: list[str] | None, all_drives: bool = False) -> list[str]:
    """What to scan: what was asked for, without anything counted twice."""
    chosen = [os.path.abspath(os.path.expanduser(p)) for p in (raw or [])]
    if all_drives:
        chosen += fixed_drives()
    if not chosen:
        chosen = [os.path.abspath(os.sep)]   # root of the current drive
    out: list[str] = []
    # A folder inside another one that is also being scanned would have its
    # size counted twice, so the outer one wins.
    for target in sorted(chosen, key=lambda p: len(p)):
        if not any(_within(target, kept) for kept in out):
            out.append(target)
    return sorted(out, key=lambda p: chosen.index(p))


def _slug(target: str) -> str:
    drive, rest = os.path.splitdrive(target)
    return "".join(ch if ch.isalnum() or ch in "-." else "_"
                   for ch in drive.replace(":", "") + rest).strip("_")


def default_dashboard_path(targets: str | list[str]) -> str:
    """reports/C.html, reports/C_Users_me.html, reports/C+D.html..."""
    if isinstance(targets, str):
        targets = [targets]
    slug = "+".join(_slug(t) for t in targets)[:80].strip("_+")
    return os.path.join(REPORTS_DIR, (slug or "root") + ".html")


def print_drives(targets: list[str], usage: dict, scanned: int, style) -> None:
    """How full each scanned drive is, each drive counted once."""
    per_drive: dict[str, object] = {}
    for target in targets:
        answer = usage.get(target)
        if answer is not None:
            per_drive.setdefault(os.path.splitdrive(target)[0].upper() or target, answer)
    if not per_drive:
        return
    print(style.bold("  DRIVES" if len(per_drive) > 1 else "  DRIVE"))
    for drive, answer in per_drive.items():
        used_pct = answer.used / answer.total * 100 if answer.total else 0
        name = (drive + "  ") if len(per_drive) > 1 else ""
        print(f"  {name}{rep.human(answer.used)} used of {rep.human(answer.total)} "
              f"({used_pct:.0f}%)  |  {rep.human(answer.free)} free")
    used = sum(a.used for a in per_drive.values())
    covered = scanned / used * 100 if used else 0
    where = "these drives" if len(per_drive) > 1 else "this drive"
    print(style.dim(f"  What was scanned covers {covered:.0f}% of the space in use on "
                    f"{where}.\n"))


def main(argv: list[str] | None = None) -> int:
    if os.name != "nt":
        print("PinkWard only runs on Windows.", file=sys.stderr)
        return 2

    args = build_parser().parse_args(argv)
    if isinstance(args.min_file, str):
        args.min_file = parse_size(args.min_file)

    # With the output redirected to a file, a name with characters the console
    # encoding cannot take must not bring the report down.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    targets = resolve_paths(args.path, args.all_drives)
    missing = [t for t in targets if not os.path.isdir(t)]
    if missing:
        print("Not a valid folder: " + ", ".join(missing), file=sys.stderr)
        return 2

    style = rep.Style(not args.no_color and rep.enable_ansi())

    usage = {}
    for target in targets:
        try:
            usage[target] = shutil.disk_usage(target)
        except OSError:
            pass

    # Windows is asked about the drives on another thread, so its answer is
    # ready by the time the scan finishes and costs no extra wait.
    probe = health_check.Probe(enabled=not args.no_health and not args.no_dashboard).start()

    scans = []
    try:
        for target in targets:
            print(f"\n  Scanning {target} ...", file=sys.stderr)
            scans.append(scanner.scan(
                target,
                exclude=DEFAULT_EXCLUDES + args.exclude,
                min_listed_file=args.min_file,
                follow_links=args.follow_links,
                count_cloud=args.count_cloud,
                logical=args.logical,
                progress=not args.quiet and sys.stderr.isatty(),
            ))
    except KeyboardInterrupt:
        print("\nScan cancelled.", file=sys.stderr)
        return 130
    result = scanner.combine(scans, ALL_DRIVES_NAME)

    cleanup = classify.classify(result.root)

    # The dashboard says all of this far better; with --no-report the console
    # keeps only the link to it.
    if not args.no_report:
        rep.print_report(result, top=args.top, depth=args.depth, style=style,
                         cleanup=cleanup)

        print_drives(targets, usage, result.root.total, style)

    if args.no_dashboard:
        return 0

    dest = os.path.abspath(args.dashboard or default_dashboard_path(targets))
    try:
        dash = dashboard.write_dashboard(result, dest, cleanup=cleanup, usage=usage,
                                         health=probe.result(targets),
                                         elevated=rep.is_elevated())
    except OSError as exc:
        print(f"  Could not save the dashboard: {exc}\n", file=sys.stderr)
        return 1

    file_url = pathlib.Path(dest).as_uri()
    print(style.bold("  DASHBOARD") +
          style.dim(f"  charts, folders you can walk through and what to clean "
                    f"({rep.human(os.path.getsize(dest))})"))

    # Serving it only makes sense when somebody is there to use it and close
    # it; with the output redirected or inside a script, just the file.
    if args.no_serve or not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("  " + style.cyan(style.link(file_url)))
        print(style.dim("  Ctrl+click the link to open it (or use --open).\n"))
        if args.open:
            rep.open_url(file_url)
        return 0

    srv = server.DashboardServer(dest, dash.actions, targets,
                                 os.path.join(REPORTS_DIR, "cleanup.log"))
    try:
        url = srv.start()
    except OSError as exc:
        print(f"  Could not serve the dashboard ({exc}); open the file instead:",
              file=sys.stderr)
        print("  " + style.cyan(style.link(file_url)) + "\n")
        return 0
    # In an elevated console Ctrl+click usually does nothing and Ctrl+C with
    # no selection stops PinkWard instead of copying, so the link goes on the
    # clipboard by itself.
    copied = rep.to_clipboard(url) if not args.no_clipboard else False
    print("  " + style.cyan(style.link(url)))
    print(style.dim("  " + ("Already copied: just paste it in your browser. "
                            if copied else "Ctrl+click to open it. ") +
                    "While PinkWard stays open you can\n  delete from there whatever "
                    "is marked as safe."))
    # The saved copy is only worth mentioning when it outlives the run: with
    # --no-report PinkWard is being run from a folder that gets wiped.
    if not args.no_report:
        print(style.dim("  Copy to look at later (no deleting): ") +
              style.link(file_url, dest))
    if args.open:
        rep.open_url(url)
    print("\n  " + style.bold("Press Enter to close the dashboard and quit."))
    srv.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
