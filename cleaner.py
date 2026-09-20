"""Deleting, from the dashboard, what the scan marked as safe.

Good practices it follows on purpose:

  * It only cleans "safe" rules that carry `clean` in classify.py: temp files,
    dumps and caches, where deleting by hand is the usual thing to do. What
    has an official tool (npm, pip, the browser itself, Windows Update...)
    keeps the note instead of a button.
  * Only inside your user folder, and never that folder itself or its direct
    children: nothing from the system, nothing needing administrator rights.
  * Right before deleting it checks again that the path exists, that it is not
    a link and that it is still what the scan said it was.
  * It never follows links, junctions or symlinks: it neither walks into them
    nor deletes them.
  * Anything in use is skipped. For "contents" folders it deletes what is
    inside and keeps the folder, because programs expect to find it.
  * Every cleanup is written to reports/cleanup.log.
  * No PowerShell and no external processes: only the standard library.

Deletion is permanent, with no trip through the Recycle Bin: this is content
that comes back on its own, and the Recycle Bin would free no space.
"""

from __future__ import annotations

import os
import stat
import time

from classify import match_path
from scanner import is_link, long_path

FILE_ATTRIBUTE_READONLY = 0x1


class Action:
    """One concrete thing the dashboard is allowed to clean."""

    __slots__ = ("path", "is_dir", "mode", "rule")

    def __init__(self, path: str, is_dir: bool, mode: str, rule) -> None:
        self.path = path        # full path
        self.is_dir = is_dir
        self.mode = mode        # "contents", "folder" or "file"
        self.rule = rule


def plan(finding, home: str | None = None) -> Action | None:
    """The cleanup action for a finding, or None when the app does not offer it."""
    rule = finding.rule
    if rule.level != "safe" or not rule.clean:
        return None
    path = finding.node.path()
    if finding.file:
        path = os.path.join(path, finding.file)
    if not in_home(path, home):
        return None
    return Action(path, finding.file is None, rule.clean, rule)


def in_home(path: str, home: str | None = None) -> bool:
    """Inside the user folder and at least two levels below it."""
    home = os.path.normcase(os.path.abspath(home or os.path.expanduser("~")))
    target = os.path.normcase(os.path.abspath(path))
    try:
        if os.path.commonpath([home, target]) != home:
            return False
    except ValueError:   # another drive
        return False
    depth = [p for p in os.path.relpath(target, home).split(os.sep) if p not in ("", ".")]
    return len(depth) >= 2


def run(action: Action, home: str | None = None) -> dict:
    """Clean one action. Returns what went, what was skipped and a message."""
    out = {"ok": False, "freed": 0, "files": 0, "skipped": 0, "links": 0, "message": ""}

    if not in_home(action.path, home):
        out["message"] = "Refused: it is outside your user folder."
        return out
    try:
        st = os.lstat(long_path(action.path))
    except FileNotFoundError:
        out.update(ok=True, message="Already gone.")
        return out
    except OSError as exc:
        out["message"] = f"Could not open it: {exc.strerror or exc}"
        return out
    if is_link(st):
        out["message"] = "Refused: it is a link, left alone."
        return out
    if stat.S_ISDIR(st.st_mode) != action.is_dir:
        out["message"] = "Refused: it changed since the scan."
        return out
    rule = match_path(action.path, action.is_dir)
    if rule is None or rule.id != action.rule.id:
        out["message"] = "Refused: it no longer matches what was scanned."
        return out

    if action.mode == "file":
        if _remove_file(action.path, st):
            out.update(files=1, freed=st.st_size)
        else:
            out["skipped"] = 1
    else:
        _wipe(action.path, keep_root=action.mode == "contents", out=out)

    out["ok"] = True
    if out["skipped"]:
        out["message"] = f"Partly deleted: {out['skipped']} in use or not allowed."
    else:
        out["message"] = "Deleted."
    return out


def _remove_file(path: str, st: os.stat_result) -> bool:
    target = long_path(path)
    try:
        os.unlink(target)
        return True
    except PermissionError:
        # Windows will not delete read-only files: drop the attribute first.
        if getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_READONLY:
            try:
                os.chmod(target, stat.S_IWRITE)
                os.unlink(target)
                return True
            except OSError:
                return False
        return False
    except OSError:
        return False


def _wipe(root: str, *, keep_root: bool, out: dict) -> None:
    """Delete what is inside `root` without following links."""
    dirs: list[str] = []
    stack = [long_path(root)]
    while stack:
        current = stack.pop()
        try:
            it = os.scandir(current)
        except OSError:
            out["skipped"] += 1
            continue
        with it:
            for entry in it:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    out["skipped"] += 1
                    continue
                if is_link(st):
                    out["links"] += 1   # neither followed nor deleted
                    continue
                if stat.S_ISDIR(st.st_mode):
                    stack.append(entry.path)
                    dirs.append(entry.path)
                elif _remove_file(entry.path, st):
                    out["files"] += 1
                    out["freed"] += st.st_size
                else:
                    out["skipped"] += 1
    # Folders go deepest first; the ones left non-empty (something in use, a
    # link) simply stay.
    for folder in reversed(dirs):
        try:
            os.rmdir(folder)
        except OSError:
            pass
    if not keep_root:
        try:
            os.rmdir(long_path(root))
        except OSError:
            pass


def log(log_path: str, action: Action, out: dict) -> None:
    """Write down one cleanup (a single tab-separated line)."""
    line = "\t".join([time.strftime("%Y-%m-%d %H:%M:%S"), action.rule.label, action.path,
                      f"{out['files']} files", f"{out['freed']} bytes",
                      f"{out['skipped']} skipped", out["message"]])
    try:
        folder = os.path.dirname(log_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
