"""Self-contained HTML dashboard: charts, folder navigation and cleanup.

Builds a single .html file with no external resources out of
`dashboard_template.html`. It carries the scanned tree inside (the largest
items, up to MAX_ITEMS; small things are grouped), what each known folder is,
the biggest files of each type and what can be cleaned from the app itself
when server.py is serving it.
"""

from __future__ import annotations

import heapq
import itertools
import json
import os

import classify
import cleaner
import scanner
from classify import Classification
from scanner import DirNode, ScanResult, display_path
from version import VERSION

_BS = chr(92)
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "dashboard_template.html")

MAX_ITEMS = 120_000   # folders + files included one by one
MIN_ITEM = 64 << 10   # below this, things are always grouped
MAX_DENIED = 2_000    # unreadable folders shown
MAX_LISTED = 400      # locations listed per cleanup group
EXTS_SHOWN = 40       # extensions in the overall table...
EXTS_PER_TYPE = 12    # ...plus the main ones of each type

KIND_DIR, KIND_FILE, KIND_MORE_FILES, KIND_MORE_DIRS = 0, 1, 2, 3


class Dashboard:
    """The generated file and what can be cleaned from it (id -> Action)."""

    __slots__ = ("path", "actions")

    def __init__(self, path: str, actions: dict) -> None:
        self.path = path
        self.actions = actions


def write_dashboard(result: ScanResult, dest: str, *, cleanup: Classification,
                    usage=None, health=None, max_items: int = MAX_ITEMS) -> Dashboard:
    payload, actions = build_payload(result, cleanup, usage, health=health,
                                     max_items=max_items)
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Keeps a file name containing "</script>" from closing the tag early.
    data = data.replace("<", _BS + "u003c")

    with open(TEMPLATE, encoding="utf-8") as fh:
        template = fh.read()
    title = _escape(result.root.name)
    before, after = template.split("__PAYLOAD__", 1)
    html = before.replace("__ROOT__", title) + data + after.replace("__ROOT__", title)

    folder = os.path.dirname(dest)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(html)
    return Dashboard(dest, actions)


def build_payload(result: ScanResult, cleanup: Classification, usage=None, *,
                  health=None, max_items: int = MAX_ITEMS) -> tuple[dict, dict]:
    """The dashboard JSON plus the cleanup actions (node id -> Action)."""
    root = result.root
    dirs, files, cutoff = _select(root, max_items, MIN_ITEM)
    clean_groups = cleanup.groups()

    # Everything the dashboard links to has to be in the tree, even when it is
    # smaller than the cut-off.
    def force(node: DirNode, name: str | None = None, size: int = 0) -> None:
        _include(node, dirs)
        if name is not None:
            # -1: this one was pulled in by a link, so its age is not at hand.
            files.setdefault(node, {}).setdefault(name, (size, -1))

    for _rule, _size, items in clean_groups:
        for finding in items[:MAX_LISTED]:
            force(finding.node, finding.file, finding.size)
    top_files = []
    for size, path in sorted(result.top_files, reverse=True):
        shown = display_path(path)
        parent = _locate(root, os.path.dirname(shown))
        if parent is not None:
            force(parent, os.path.basename(shown), size)
        top_files.append((size, shown, parent))
    type_tops = [sorted(heap, reverse=True) for heap in result.top_by_cat]
    ext_names = _pick_exts(result)
    ext_tops = {ext: sorted(result.top_by_ext.get(ext, ()), reverse=True)
                for ext in ext_names}
    for entries in type_tops + list(ext_tops.values()):
        for size, _tick, name, node in entries:
            force(node, name, size)

    tree = _emit(root, dirs, files)
    index, file_index = tree["index"], tree["file_index"]

    rules: list[dict] = []
    rule_pos: dict[int, int] = {}

    def rid(rule) -> int:
        pos = rule_pos.get(rule.id)
        if pos is None:
            pos = rule_pos[rule.id] = len(rules)
            rules.append({"label": rule.label, "level": rule.level, "desc": rule.desc,
                          "how": rule.how, "clean": rule.clean, "close": rule.close})
        return pos

    cls: dict[int, int] = {}
    for node, rule in cleanup.dirs.items():
        pos = index.get(node)
        if pos is not None:
            cls[pos] = rid(rule)
    for key, rule in cleanup.files.items():
        pos = file_index.get(key)
        if pos is not None:
            cls[pos] = rid(rule)

    # Every location goes as [id, bytes, 1 if the app can clean it].
    actions: dict[int, cleaner.Action] = {}
    groups = []
    for rule, size, items in clean_groups:
        listed = []
        for finding in items[:MAX_LISTED]:
            pos = (file_index.get((finding.node, finding.file)) if finding.file
                   else index.get(finding.node))
            if pos is None:
                continue
            action = cleaner.plan(finding)
            if action is not None:
                actions[pos] = action
            listed.append([pos, finding.size, 1 if action else 0])
        groups.append({"r": rid(rule), "s": size, "n": len(items), "items": listed})

    def file_ids(entries) -> list[int]:
        return [file_index[(node, name)] for _size, _tick, name, node in entries
                if (node, name) in file_index]

    cat_totals = [[0, 0] for _ in classify.CATEGORIES]   # [bytes, files]
    for ext, (count, size) in result.by_ext.items():
        slot = cat_totals[classify.category_of(ext)]
        slot[0] += size
        slot[1] += count

    meta = {
        "root": root.name,
        "version": VERSION,
        "sep": os.sep,
        "date": round(result.started_at),
        "elapsed": round(result.elapsed, 1),
        "measure": result.measure,
        "total": root.total,
        "files": root.total_files,
        "logicalTotal": root.total_logical,
        "errors": len(result.errors),
        "cloudFiles": result.cloud_only_files,
        "cloudBytes": result.cloud_only_bytes,
        "links": result.skipped_links,
        "sparseFiles": result.sparse_files,
        "sparseSaved": result.sparse_saved,
        "disk": ({"total": usage.total, "used": usage.used, "free": usage.free}
                 if usage else None),
        "cats": [label for _key, label in classify.CATEGORIES],
        "ages": list(scanner.AGE_BANDS),
        "levels": {key: {"label": label, "hint": hint}
                   for key, (label, hint) in classify.LEVELS.items()},
        "items": len(tree["n"]),
        "cutoff": cutoff,
    }

    payload = {
        "meta": meta,
        "health": health or {},
        "nodes": {key: tree[key] for key in ("n", "s", "f", "p", "k", "c", "a")},
        "denied": tree["denied"],
        "rules": rules,
        "cls": cls,
        "clean": {
            "totals": {level: {"s": s, "n": n}
                       for level, (s, n) in cleanup.totals().items()},
            "groups": groups,
        },
        "topFiles": [[size, shown,
                      index.get(parent, -1) if parent is not None else -1,
                      file_index.get((parent, os.path.basename(shown)), -1),
                      classify.category_of(os.path.splitext(shown)[1].lower())]
                     for size, shown, parent in top_files],
        "catTotals": cat_totals,
        "typeTops": [file_ids(entries) for entries in type_tops],
        # [extension, files, bytes, category, ids of its largest files]
        "exts": [[ext, result.by_ext[ext][0], result.by_ext[ext][1],
                  classify.category_of(ext), file_ids(ext_tops[ext])]
                 for ext in ext_names],
    }
    return payload, actions


def _pick_exts(result: ScanResult) -> list[str]:
    """The extensions taking up the most space, plus the top ones per type."""
    ranked = sorted(result.by_ext, key=lambda ext: result.by_ext[ext][1], reverse=True)
    chosen = set(ranked[:EXTS_SHOWN])
    per_type: dict[int, int] = {}
    for ext in ranked:
        cat = classify.category_of(ext)
        if per_type.get(cat, 0) < EXTS_PER_TYPE:
            per_type[cat] = per_type.get(cat, 0) + 1
            chosen.add(ext)
    return [ext for ext in ranked if ext in chosen]


def _select(root: DirNode, budget: int, min_size: int):
    """Pick what goes in one by one: the largest items first.

    Folders and files compete for the same budget, so the detail lands where
    the space is and not on whichever branch is walked first. Returns the
    folders, the files per folder and the size of the smallest one included.
    """
    tie = itertools.count()
    heap: list = []
    dirs = {root}
    files: dict[DirNode, dict[str, int]] = {}

    def expand(node: DirNode) -> None:
        for child in node.children:
            if child.total >= min_size:
                heapq.heappush(heap, (-child.total, next(tie), child, None, -1))
        for size, name, band in node.big_files:
            if size >= min_size:
                heapq.heappush(heap, (-size, next(tie), node, name, band))

    expand(root)
    cutoff = min_size
    while heap and budget > 0:
        neg, _, node, name, band = heapq.heappop(heap)
        budget -= 1
        if name is None:
            dirs.add(node)
            expand(node)
        else:
            files.setdefault(node, {})[name] = (-neg, band)
        if not budget and heap:
            cutoff = -heap[0][0]
    return dirs, files, cutoff


def _include(node: DirNode | None, dirs: set) -> None:
    while node is not None and node not in dirs:
        dirs.add(node)
        node = node.parent


def _locate(root: DirNode, folder: str) -> DirNode | None:
    """The DirNode for a path inside the scan, or None."""
    base = root.name.rstrip(os.sep)
    if not folder.lower().startswith(base.lower()):
        return None
    node = root
    for part in folder[len(base):].split(os.sep):
        if not part:
            continue
        node = next((c for c in node.children if c.name == part), None)
        if node is None:
            return None
    return node


def _emit(root: DirNode, dirs: set, files: dict) -> dict:
    """Flatten the chosen tree into parallel columns (compact JSON)."""
    names: list = []
    sizes: list = []
    counts: list = []
    parents: list = []
    kinds: list = []
    cats: list = []
    ages: list = []
    index: dict[DirNode, int] = {}
    file_index: dict[tuple[DirNode, str], int] = {}
    denied: list[int] = []

    def emit(name, size, count, parent, kind, cat, age) -> int:
        names.append(name)
        sizes.append(size)
        counts.append(count)
        parents.append(parent)
        kinds.append(kind)
        cats.append(cat)
        ages.append(age)
        return len(names) - 1

    stack: list[tuple[DirNode, int]] = [(root, -1)]
    while stack:
        node, parent = stack.pop()
        pos = index[node] = emit(node.name, node.total, node.total_files, parent,
                                 KIND_DIR, _pairs(node.cats), _pairs(node.ages))
        if node.denied:
            denied.append(pos)

        hidden = hidden_size = hidden_files = 0
        for child in node.children:
            if child in dirs:
                stack.append((child, pos))
            elif child.denied and not child.total and len(denied) < MAX_DENIED:
                cpos = index[child] = emit(child.name, 0, 0, pos, KIND_DIR, [], [])
                denied.append(cpos)
            else:
                hidden += 1
                hidden_size += child.total
                hidden_files += child.total_files

        listed = files.get(node, {})
        listed_size = 0
        for name, (size, band) in sorted(listed.items(), key=lambda kv: kv[1][0],
                                         reverse=True):
            ext = os.path.splitext(name)[1].lower()
            file_index[(node, name)] = emit(name, size, 1, pos, KIND_FILE,
                                            classify.category_of(ext), band)
            listed_size += size

        rest = node.own_size - listed_size
        rest_files = node.own_files - len(listed)
        if rest > 0 and rest_files > 0:
            emit(_more(rest_files, "file", "files"), rest, rest_files, pos,
                 KIND_MORE_FILES, None, None)
        if hidden and hidden_size > 0:
            emit(_more(hidden, "folder", "folders"), hidden_size, hidden_files, pos,
                 KIND_MORE_DIRS, None, None)

    return {"n": names, "s": sizes, "f": counts, "p": parents, "k": kinds, "c": cats,
            "a": ages, "index": index, "file_index": file_index, "denied": denied}


def _pairs(counters) -> list[int]:
    """A counter array as [slot, bytes, slot, bytes, ...], zeros dropped."""
    out: list[int] = []
    if counters is not None:
        for slot, size in enumerate(counters):
            if size:
                out += (slot, size)
    return out


def _more(n: int, one: str, many: str) -> str:
    return f"{n:,} more {one if n == 1 else many}"


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
