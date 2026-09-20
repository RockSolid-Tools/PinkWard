"""The version number, kept in the VERSION file so bumping it touches one line.

Everything that shows a version reads it from here: the console (--version),
the dashboard footer and the launcher, which reads the file straight off disk.
"""

from __future__ import annotations

import os

VERSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")


def read() -> str:
    try:
        with open(VERSION_FILE, encoding="utf-8") as fh:
            return fh.read().strip() or "unknown"
    except OSError:
        return "unknown"


VERSION = read()
