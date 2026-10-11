"""Read-only filesystem walk of approved folders.

Safety properties:
  * Only paths inside approved roots are visited.
  * Symlinks are recorded, never followed.
  * Hidden entries, system/app-data folders, and credential-looking names
    are skipped.
  * macOS packages and software projects are recorded as single units and
    never descended into.
  * Each root stays on its own volume (no crossing into mounted drives).
  * Files are only ever stat()-ed here; reading happens in duplicates.py.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import time
from pathlib import Path
from typing import Callable, Iterator, Optional, Tuple

from organizer.config import (
    ALWAYS_EXCLUDED_NAMES,
    PACKAGE_EXTENSIONS,
    PROJECT_MARKERS,
    Config,
    is_sensitive_name,
)
from organizer.kinds import base_name, is_screenshot, kind_for

Progress = Optional[Callable[[int], None]]

# st_flags bit for files whose contents live only in iCloud ("Optimize Mac
# Storage"). Reading them would trigger a download, so they are never opened.
SF_DATALESS = 0x40000000


def is_dataless(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_flags", 0) & SF_DATALESS)


def _dir_size(path: str, device: int) -> int:
    """Total bytes under a package/project, without following symlinks."""
    total = 0
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for fn in filenames:
            try:
                st = os.lstat(os.path.join(dirpath, fn))
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and st.st_dev == device:
                total += st.st_size
    return total


def _is_project(path: str) -> bool:
    return any(os.path.lexists(os.path.join(path, m)) for m in PROJECT_MARKERS)


def _walk(cfg: Config, root: Path) -> Iterator[Tuple[str, str, os.stat_result, str]]:
    """Yield (path, item_type, stat, skip_reason). skip_reason '' means indexed."""
    try:
        root_st = os.stat(root)
    except OSError as e:
        yield str(root), "skipped", None, f"unreadable root: {e.strerror}"  # type: ignore[misc]
        return
    excluded = {Path(os.path.expanduser(x)).resolve() for x in cfg.extra_excluded}
    stack = [str(root)]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except PermissionError:
            yield current, "skipped", None, "permission denied (macOS privacy control)"  # type: ignore[misc]
            continue
        except OSError as e:
            yield current, "skipped", None, f"unreadable: {e.strerror}"  # type: ignore[misc]
            continue
        for entry in entries:
            name = entry.name
            if name.startswith(".") or name == "Icon\r":
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode):
                yield entry.path, "symlink", st, ""
                continue
            if entry.is_dir(follow_symlinks=False):
                if name in ALWAYS_EXCLUDED_NAMES or Path(entry.path) in excluded:
                    yield entry.path, "skipped", st, "excluded folder"
                    continue
                if is_sensitive_name(name):
                    yield entry.path, "skipped", st, "looks like credentials/secrets"
                    continue
                if st.st_dev != root_st.st_dev:
                    yield entry.path, "skipped", st, "different volume (not crossing drives)"
                    continue
                if Path(name).suffix.lower() in PACKAGE_EXTENSIONS:
                    yield entry.path, "package", st, ""
                    continue
                if _is_project(entry.path):
                    yield entry.path, "project", st, ""
                    continue
                stack.append(entry.path)
            elif stat.S_ISREG(st.st_mode):
                yield entry.path, "file", st, ""


def scan(conn: sqlite3.Connection, cfg: Config, progress: Progress = None) -> int:
    started = time.time()
    cur = conn.execute(
        "INSERT INTO scans (started_at, roots) VALUES (?, ?)",
        (started, "\n".join(str(r) for r in cfg.roots)),
    )
    scan_id = int(cur.lastrowid)
    count = 0
    keywords = [k.lower() for k in cfg.business_keywords]

    for root in cfg.roots:
        for path, item_type, st, reason in _walk(cfg, root):
            if reason:
                conn.execute(
                    "INSERT INTO skipped (scan_id, path, reason) VALUES (?, ?, ?)",
                    (scan_id, path, reason),
                )
                continue
            name = os.path.basename(path)
            size = st.st_size
            if item_type in ("package", "project"):
                size = _dir_size(path, st.st_dev)
            elif item_type == "symlink":
                size = 0
            rel = os.path.relpath(path, root).lower()
            kind = "project" if item_type == "project" else kind_for(name)
            business = any(k in rel for k in keywords) or (
                cfg.loose_photos_are_business and item_type == "file"
                and kind == "image" and not is_screenshot(name)
            )
            conn.execute(
                """INSERT OR IGNORE INTO files
                   (scan_id, root, path, name, ext, kind, size, mtime, atime, birthtime,
                    device, inode, item_type, sensitive, dataless, business, base_name)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    scan_id, str(root), path, name, Path(name).suffix.lower(),
                    kind,
                    size, st.st_mtime, st.st_atime, getattr(st, "st_birthtime", None),
                    st.st_dev, st.st_ino, item_type, int(is_sensitive_name(name)), int(is_dataless(st)),
                    int(business), base_name(name),
                ),
            )
            count += 1
            if progress and count % 1000 == 0:
                progress(count)
                conn.commit()

    conn.execute("UPDATE scans SET finished_at=? WHERE id=?", (time.time(), scan_id))
    conn.commit()
    return scan_id
