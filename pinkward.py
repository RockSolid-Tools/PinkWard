#!/usr/bin/env python3
"""PinkWard - see what is taking up space on your disk, and where.

When it finishes it prints the report in the console plus a link to an HTML
dashboard with charts, folder navigation and what can be cleaned. In an
interactive terminal the dashboard is served locally (http://127.0.0.1) while
PinkWard stays open, so you can delete the safe things from it.

Examples:
  python pinkward.py                      scan the current drive
  python pinkward.py C:/Users/me          scan one folder
  python pinkward.py D:/ --open           and open the dashboard in the browser
  python pinkward.py . --depth 2 --top 30
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sys
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import classify
import dashboard
import report as rep
import scanner
import server

DEFAULT_EXCLUDES = ["$Recycle.Bin", "System Volume Information"]
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
    parser.add_argument("path", nargs="?", default=None,
                        help="folder or drive to scan (the current drive by default)")
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
    parser.add_argument("--no-color", action="store_true", help="output without color")
    parser.add_argument("--quiet", action="store_true", help="no progress line")
    return parser


def resolve_path(raw: str | None) -> str:
    if raw:
        return os.path.abspath(os.path.expanduser(raw))
    return os.path.abspath(os.sep)  # root of the current drive


def default_dashboard_path(target: str) -> str:
    """reports/C.html, reports/C_Users_me.html..."""
    drive, rest = os.path.splitdrive(target)
    slug = "".join(ch if ch.isalnum() or ch in "-." else "_"
                   for ch in drive.replace(":", "") + rest).strip("_")
    return os.path.join(REPORTS_DIR, (slug or "root") + ".html")


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

    target = resolve_path(args.path)
    if not os.path.isdir(target):
        print(f"Not a valid folder: {target}", file=sys.stderr)
        return 2

    style = rep.Style(not args.no_color and rep.enable_ansi())

    try:
        usage = shutil.disk_usage(target)
    except OSError:
        usage = None

    print(f"\nScanning {target} ...", file=sys.stderr)
    try:
        result = scanner.scan(
            target,
            exclude=DEFAULT_EXCLUDES + args.exclude,
            min_listed_file=args.min_file,
            follow_links=args.follow_links,
            count_cloud=args.count_cloud,
            logical=args.logical,
            progress=not args.quiet and sys.stderr.isatty(),
        )
    except KeyboardInterrupt:
        print("\nScan cancelled.", file=sys.stderr)
        return 130

    cleanup = classify.classify(result.root)
    rep.print_report(result, top=args.top, depth=args.depth, style=style,
                     cleanup=cleanup)

    if usage:
        used_pct = usage.used / usage.total * 100 if usage.total else 0
        print(style.bold("  DRIVE"))
        print(f"  {rep.human(usage.used)} used of {rep.human(usage.total)} "
              f"({used_pct:.0f}%)  |  {rep.human(usage.free)} free")
        covered = result.root.total / usage.used * 100 if usage.used else 0
        print(style.dim(f"  What was scanned covers {covered:.0f}% of the space in "
                        f"use on this drive.\n"))

    if args.no_dashboard:
        return 0

    dest = os.path.abspath(args.dashboard or default_dashboard_path(target))
    try:
        dash = dashboard.write_dashboard(result, dest, cleanup=cleanup, usage=usage)
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
            webbrowser.open(file_url)
        return 0

    srv = server.DashboardServer(dest, dash.actions, target,
                                 os.path.join(REPORTS_DIR, "cleanup.log"))
    try:
        url = srv.start()
    except OSError as exc:
        print(f"  Could not serve the dashboard ({exc}); open the file instead:",
              file=sys.stderr)
        print("  " + style.cyan(style.link(file_url)) + "\n")
        return 0
    print("  " + style.cyan(style.link(url)))
    print(style.dim("  Ctrl+click to open it. While PinkWard stays open you can delete "
                    "from there\n  whatever is marked as safe."))
    print(style.dim("  Copy to look at later (no deleting): ") +
          style.link(file_url, dest))
    if args.open:
        webbrowser.open(url)
    print("\n  " + style.bold("Press Enter to close the dashboard and quit."))
    srv.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
