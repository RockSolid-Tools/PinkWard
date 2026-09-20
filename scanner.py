"""Scan engine: walks the directory tree and adds up sizes.

It tells two measurements apart, because on Windows they drift a lot:

  * LOGICAL size  - what the file claims to be (os.stat().st_size)
  * SIZE ON DISK  - what it really takes up on the volume

A sparse file (virtual machine images, emulators, databases) can claim 512 GB
and take up 4 GB. An NTFS-compressed file takes up less than it claims. And
any tiny file still eats a whole cluster. By default we report the size ON
DISK: that is what you get back by deleting it.
"""

from __future__ import annotations

import ctypes
import heapq
import itertools
import os
import stat
import sys
import time
from array import array
from ctypes import wintypes

from classify import EXT_CATEGORY, N_CATEGORIES, OTHER

_BS = chr(92)
_UNC_MARK = _BS + _BS
_PREFIX_LOCAL = _BS + _BS + "?" + _BS
_PREFIX_UNC = _PREFIX_LOCAL + "UNC" + _BS

# Windows file attributes we care about.
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_SPARSE_FILE = 0x200
FILE_ATTRIBUTE_COMPRESSED = 0x800
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000  # OneDrive "online only"

_NEEDS_EXACT = FILE_ATTRIBUTE_SPARSE_FILE | FILE_ATTRIBUTE_COMPRESSED

# NTFS stores very small files inside the MFT record itself, so they do not
# use a cluster of their own. The exact limit depends on the record; measured
# over ~6,000 real files, 700 B is the cut-off that gets it right most often
# (98.4% exact sizes, 0.1% total error).
NTFS_RESIDENT_LIMIT = 700

# Largest files kept per content type and per extension.
TYPE_TOP = 100
EXT_TOP = 20

# When was it last touched. The bands are ordered, newest first, and the
# dashboard colors and sorts by them, so the order is part of the format.
AGE_BANDS = ("This month", "1 to 6 months", "6 to 12 months", "1 to 2 years",
             "Over 2 years")
N_AGES = len(AGE_BANDS)
_AGE_CUTS = (30, 182, 365, 730)   # days, matching the bands above

_GetCompressedFileSizeW = ctypes.windll.kernel32.GetCompressedFileSizeW
_GetCompressedFileSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
_GetCompressedFileSizeW.restype = wintypes.DWORD
_INVALID = 0xFFFFFFFF


class DirNode:
    """A directory in the tree. Totals are filled in during the second pass."""

    __slots__ = ("name", "parent", "children", "own_size", "own_logical",
                 "own_files", "total", "total_logical", "total_files",
                 "big_files", "denied", "cats", "ages", "is_root")

    def __init__(self, name: str, parent: "DirNode | None") -> None:
        self.name = name
        self.parent = parent
        # A scanned root: its name is a full path, and nothing above it is
        # part of that path. Several of them can hang off one holder node
        # when more than one drive was scanned (see combine).
        self.is_root = False
        self.children: list[DirNode] = []
        self.own_size = 0        # bytes on disk of the files directly inside
        self.own_logical = 0     # logical bytes of the files directly inside
        self.own_files = 0
        self.total = 0           # bytes on disk, recursive
        self.total_logical = 0   # logical bytes, recursive
        self.total_files = 0
        # bounded heap of (size, name, age band)
        self.big_files: list[tuple[int, str, int]] = []
        self.denied = False
        # Bytes per content category (classify.CATEGORIES) and per age band.
        # After the second pass both are recursive, like `total`. None when
        # there are no files.
        self.cats: array | None = None
        self.ages: array | None = None

    def path(self) -> str:
        parts = [self.name]
        node = None if self.is_root else self.parent
        while node is not None:
            parts.append(node.name)
            if node.is_root:
                break
            node = node.parent
        parts.reverse()
        return os.path.join(parts[0], *parts[1:]) if len(parts) > 1 else parts[0]


class ScanResult:
    def __init__(self, root: DirNode) -> None:
        self.root = root
        self.by_ext: dict[str, list[int]] = {}      # ext -> [count, bytes on disk]
        self.top_files: list[tuple[int, str]] = []  # bounded heap
        # Bounded heaps of (size, tick, name, DirNode) per category and extension.
        self.top_by_cat: list[list] = [[] for _ in range(N_CATEGORIES)]
        self.top_by_ext: dict[str, list] = {}
        self.errors: list[str] = []
        self.skipped_links = 0
        self.cloud_only_files = 0
        self.cloud_only_bytes = 0
        self.sparse_files = 0        # sparse/compressed files found
        self.sparse_saved = 0        # bytes they claim but do not use
        self.cluster_size = 4096
        self.measure = "disk"        # or "logical"
        self.started_at = time.time()
        self.elapsed = 0.0


def long_path(path: str) -> str:
    """Windows long-path prefix: gets past the 260 character limit."""
    if path.startswith(_PREFIX_LOCAL):
        return path
    abs_path = os.path.abspath(path)
    if abs_path.startswith(_UNC_MARK):
        return _PREFIX_UNC + abs_path[2:]
    return _PREFIX_LOCAL + abs_path


def display_path(path: str) -> str:
    """Drop the long-path prefix before showing a path to the user."""
    if path.startswith(_PREFIX_UNC):
        return _UNC_MARK + path[len(_PREFIX_UNC):]
    if path.startswith(_PREFIX_LOCAL):
        return path[len(_PREFIX_LOCAL):]
    return path


def cluster_size_for(path: str) -> int:
    """Cluster size of the volume: files take up multiples of this."""
    try:
        drive = os.path.splitdrive(os.path.abspath(path))[0]
        root = (drive + os.sep) if drive else None
        sectors = wintypes.DWORD()
        bytes_per_sector = wintypes.DWORD()
        free_clusters = wintypes.DWORD()
        total_clusters = wintypes.DWORD()
        ok = ctypes.windll.kernel32.GetDiskFreeSpaceW(
            root, ctypes.byref(sectors), ctypes.byref(bytes_per_sector),
            ctypes.byref(free_clusters), ctypes.byref(total_clusters))
        if ok and sectors.value and bytes_per_sector.value:
            return sectors.value * bytes_per_sector.value
    except Exception:
        pass
    return 4096


def _exact_size_on_disk(path: str) -> int | None:
    """Space actually allocated (resolves sparse files and NTFS compression)."""
    high = wintypes.DWORD(0)
    low = _GetCompressedFileSizeW(path, ctypes.byref(high))
    if low == _INVALID and ctypes.GetLastError() != 0:
        return None
    return (high.value << 32) | low


def _is_reparse(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def is_link(st: os.stat_result) -> bool:
    """Symlink, junction or any other reparse point: never follow it."""
    return stat.S_ISLNK(st.st_mode) or _is_reparse(st)


def _dir_key(path: str, st: os.stat_result):
    """Identity of a folder (device, inode) so loops can be spotted."""
    if not st.st_ino:
        # On Windows, DirEntry.stat() does not carry the file id: ask for it.
        try:
            st = os.stat(long_path(path))
        except OSError:
            return None
    return (st.st_dev, st.st_ino)


def _push_bounded(heap: list, item: tuple[int, str], limit: int) -> None:
    if len(heap) < limit:
        heapq.heappush(heap, item)
    elif item[0] > heap[0][0]:
        heapq.heapreplace(heap, item)


def scan(root_path: str, *, exclude: list[str] | None = None,
         files_per_dir: int = 20, top_files: int = 60,
         min_listed_file: int = 1 << 20, follow_links: bool = False,
         count_cloud: bool = False, logical: bool = False,
         progress: bool = True) -> ScanResult:
    """Walk `root_path` and return the tree with its totals filled in."""
    root_path = os.path.abspath(root_path)
    exclude_lower = {e.lower() for e in (exclude or [])}

    root = DirNode(root_path, None)
    root.is_root = True
    result = ScanResult(root)
    result.cluster_size = cluster = cluster_size_for(root_path)
    result.measure = "logical" if logical else "disk"
    start = time.monotonic()
    zero_cats = [0] * N_CATEGORIES
    zero_ages = [0] * N_AGES
    now = time.time()
    age_cuts = tuple(now - days * 86400 for days in _AGE_CUTS)
    ext_category = EXT_CATEGORY.get
    top_by_cat, top_by_ext = result.top_by_cat, result.top_by_ext
    tick = itertools.count()   # heap tie-breaker: nodes are never compared

    visited: set = set()       # folders already walked when following links
    if follow_links:
        try:
            key = _dir_key(root_path, os.stat(long_path(root_path)))
        except OSError:
            key = None
        if key:
            visited.add(key)

    # Pass 1: iterative walk (no recursion, copes with deep trees).
    stack: list[DirNode] = [root]
    seen_dirs = 0
    last_tick = 0.0

    while stack:
        node = stack.pop()
        dir_path = node.path()

        try:
            it = os.scandir(long_path(dir_path))
        except PermissionError:
            node.denied = True
            result.errors.append(f"Access denied: {dir_path}")
            continue
        except OSError as exc:
            result.errors.append(f"{type(exc).__name__}: {dir_path}")
            continue

        seen_dirs += 1
        if progress:
            now = time.monotonic()
            if now - last_tick > 0.15:
                last_tick = now
                sys.stderr.write("\r" + _ESC_CLEAR +
                                 f"  scanning... {seen_dirs:,} folders  "
                                 f"{dir_path[:70]}")
                sys.stderr.flush()

        with it:
            while True:
                try:
                    entry = next(it)
                except StopIteration:
                    break
                except OSError as exc:
                    result.errors.append(f"{type(exc).__name__}: {dir_path}")
                    break

                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    result.errors.append(f"Could not read: {entry.path}")
                    continue

                attrs = getattr(st, "st_file_attributes", 0)
                cloud = bool(attrs & FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)

                if attrs & FILE_ATTRIBUTE_REPARSE_POINT and not follow_links:
                    # Junctions and symlinks are not followed: avoids loops and
                    # double counting. Cloud placeholders carry the same bit but
                    # are real files, so they are not dropped here.
                    if not cloud:
                        result.skipped_links += 1
                        continue

                if stat.S_ISDIR(st.st_mode):
                    if entry.name.lower() in exclude_lower:
                        continue
                    if follow_links:
                        key = _dir_key(entry.path, st)
                        if key in visited:
                            result.skipped_links += 1   # loop or repeated folder
                            continue
                        if key:
                            visited.add(key)
                    child = DirNode(entry.name, node)
                    node.children.append(child)
                    stack.append(child)
                    continue

                if not stat.S_ISREG(st.st_mode):
                    continue

                logical_size = st.st_size

                if cloud:
                    result.cloud_only_files += 1
                    result.cloud_only_bytes += logical_size
                    if not count_cloud:
                        continue

                # Size on disk. We only pay for the system call on the files
                # that can lie: sparse or NTFS-compressed ones.
                if logical:
                    size = logical_size
                elif attrs & _NEEDS_EXACT:
                    exact = _exact_size_on_disk(long_path(entry.path))
                    if exact is None:
                        size = logical_size
                    else:
                        size = exact
                        result.sparse_files += 1
                        result.sparse_saved += max(0, logical_size - exact)
                elif logical_size == 0:
                    size = 0
                elif logical_size <= NTFS_RESIDENT_LIMIT:
                    # Resident in the MFT: no cluster of its own.
                    size = -(-logical_size // 8) * 8
                else:
                    # Everything else takes up a whole number of clusters.
                    size = -(-logical_size // cluster) * cluster

                node.own_size += size
                node.own_logical += logical_size
                node.own_files += 1

                ext = os.path.splitext(entry.name)[1].lower() or "(no extension)"
                slot = result.by_ext.get(ext)
                if slot is None:
                    result.by_ext[ext] = [1, size]
                else:
                    slot[0] += 1
                    slot[1] += size

                cat = ext_category(ext, OTHER)
                cats = node.cats
                if cats is None:
                    cats = node.cats = array("q", zero_cats)
                cats[cat] += size

                # When it was last written. A clock ahead of us lands in the
                # newest band rather than breaking the order.
                when = st.st_mtime
                if when >= age_cuts[0]:
                    band = 0
                elif when >= age_cuts[1]:
                    band = 1
                elif when >= age_cuts[2]:
                    band = 2
                elif when >= age_cuts[3]:
                    band = 3
                else:
                    band = 4
                ages = node.ages
                if ages is None:
                    ages = node.ages = array("q", zero_ages)
                ages[band] += size

                if size:
                    # Biggest ones per type and extension, for the dashboard.
                    heap = top_by_cat[cat]
                    if len(heap) < TYPE_TOP or size > heap[0][0]:
                        _push_bounded(heap, (size, next(tick), entry.name, node), TYPE_TOP)
                    heap = top_by_ext.get(ext)
                    if heap is None:
                        heap = top_by_ext[ext] = []
                    if len(heap) < EXT_TOP or size > heap[0][0]:
                        _push_bounded(heap, (size, next(tick), entry.name, node), EXT_TOP)

                if size >= min_listed_file:
                    _push_bounded(node.big_files, (size, entry.name, band), files_per_dir)
                    _push_bounded(result.top_files, (size, entry.path), top_files)

    # Pass 2: iterative post-order to push the totals upwards.
    order: list[DirNode] = []
    stack = [root]
    while stack:
        node = stack.pop()
        order.append(node)
        stack.extend(node.children)

    for node in reversed(order):
        total = node.own_size
        total_logical = node.own_logical
        total_files = node.own_files
        cats = node.cats
        ages = node.ages
        for child in node.children:
            total += child.total
            total_logical += child.total_logical
            total_files += child.total_files
            child_cats = child.cats
            if child_cats is not None:
                if cats is None:
                    cats = node.cats = array("q", child_cats)
                else:
                    for i, value in enumerate(child_cats):
                        if value:
                            cats[i] += value
            child_ages = child.ages
            if child_ages is not None:
                if ages is None:
                    ages = node.ages = array("q", child_ages)
                else:
                    for i, value in enumerate(child_ages):
                        if value:
                            ages[i] += value
        node.total = total
        node.total_logical = total_logical
        node.total_files = total_files

    if progress:
        sys.stderr.write("\r" + _ESC_CLEAR)
        sys.stderr.flush()

    result.elapsed = time.monotonic() - start
    return result


_ESC_CLEAR = chr(27) + "[K"


def walk_nodes(root: DirNode):
    """Iterate every node in the tree (pre-order)."""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node.children)


def _add_counter(node: DirNode, field: str, other, size: int) -> None:
    """Add one counter array (cats or ages) onto a node."""
    if other is None:
        return
    own = getattr(node, field)
    if own is None:
        setattr(node, field, array("q", other))
        return
    for i, value in enumerate(other):
        if value:
            own[i] += value


def combine(results: list[ScanResult], name: str = "This PC",
            top_files: int = 60) -> ScanResult:
    """Several scanned drives as a single tree, with one holder above them.

    The holder is not a real folder: it has no path of its own, and every
    root below it keeps carrying its own (is_root). Everything downstream -
    classification, dashboard, report - then works the same whether one drive
    was scanned or five.
    """
    if len(results) == 1:
        return results[0]

    holder = DirNode(name, None)
    merged = ScanResult(holder)
    merged.started_at = min(r.started_at for r in results)
    merged.elapsed = sum(r.elapsed for r in results)
    merged.measure = results[0].measure
    merged.cluster_size = results[0].cluster_size
    tick = itertools.count()

    for res in results:
        root = res.root
        root.parent = holder
        holder.children.append(root)
        holder.total += root.total
        holder.total_logical += root.total_logical
        holder.total_files += root.total_files
        _add_counter(holder, "cats", root.cats, N_CATEGORIES)
        _add_counter(holder, "ages", root.ages, N_AGES)

        for ext, (count, size) in res.by_ext.items():
            slot = merged.by_ext.get(ext)
            if slot is None:
                merged.by_ext[ext] = [count, size]
            else:
                slot[0] += count
                slot[1] += size
        for item in res.top_files:
            _push_bounded(merged.top_files, item, top_files)
        for cat, heap in enumerate(res.top_by_cat):
            for size, _old, fname, node in heap:
                _push_bounded(merged.top_by_cat[cat],
                              (size, next(tick), fname, node), TYPE_TOP)
        for ext, heap in res.top_by_ext.items():
            dest = merged.top_by_ext.setdefault(ext, [])
            for size, _old, fname, node in heap:
                _push_bounded(dest, (size, next(tick), fname, node), EXT_TOP)

        merged.errors.extend(res.errors)
        merged.skipped_links += res.skipped_links
        merged.cloud_only_files += res.cloud_only_files
        merged.cloud_only_bytes += res.cloud_only_bytes
        merged.sparse_files += res.sparse_files
        merged.sparse_saved += res.sparse_saved

    return merged


def roots_of(result: ScanResult) -> list[DirNode]:
    """The scanned roots: the tree itself, or the drives under the holder."""
    root = result.root
    return [root] if root.is_root else list(root.children)
