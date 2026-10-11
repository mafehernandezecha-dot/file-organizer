"""Exact-duplicate detection: size -> quick hash -> full SHA-256.

Files are opened read-only. Skipped: credential-looking names, iCloud
cloud-only placeholders (reading would download them), empty files, and
hard links to the same inode (those are one file, not duplicates).
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict
from typing import Callable, Dict, List, Optional

CHUNK = 1024 * 1024
EDGE = 64 * 1024


def quick_hash(path: str, size: int) -> str:
    h = hashlib.sha256(str(size).encode())
    with open(path, "rb") as f:
        if size <= 2 * EDGE:
            h.update(f.read())  # small file: the quick hash covers all of it
        else:
            h.update(f.read(EDGE))
            f.seek(-EDGE, 2)
            h.update(f.read(EDGE))
    return h.hexdigest()


def full_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _candidates(conn: sqlite3.Connection, scan_id: int) -> Dict[int, List[sqlite3.Row]]:
    rows = conn.execute(
        """SELECT id, path, size, device, inode FROM files
           WHERE scan_id=? AND item_type='file' AND sensitive=0 AND dataless=0 AND size>0
             AND size IN (SELECT size FROM files
                          WHERE scan_id=? AND item_type='file' AND sensitive=0
                            AND dataless=0 AND size>0
                          GROUP BY size HAVING COUNT(*)>1)""",
        (scan_id, scan_id),
    ).fetchall()
    by_size: Dict[int, List[sqlite3.Row]] = defaultdict(list)
    seen_inodes = set()
    for r in rows:
        key = (r["device"], r["inode"])
        if key in seen_inodes:
            continue
        seen_inodes.add(key)
        by_size[r["size"]].append(r)
    return {s: rs for s, rs in by_size.items() if len(rs) > 1}


def find_duplicates(
    conn: sqlite3.Connection, scan_id: int, progress: Optional[Callable[[str], None]] = None
) -> int:
    """Fill quick_hash/full_hash for candidates. Returns number of files hashed."""
    hashed = 0
    for size, rows in _candidates(conn, scan_id).items():
        by_quick: Dict[str, List[sqlite3.Row]] = defaultdict(list)
        qh_for: Dict[int, str] = {}
        for r in rows:
            try:
                qh = quick_hash(r["path"], size)
            except OSError:
                continue
            conn.execute("UPDATE files SET quick_hash=? WHERE id=?", (qh, r["id"]))
            by_quick[qh].append(r)
            qh_for[r["id"]] = qh
        for group in by_quick.values():
            if len(group) < 2:
                continue
            for r in group:
                if size <= 2 * EDGE:
                    fh = qh_for[r["id"]]  # small file: quick hash already covers it
                else:
                    try:
                        fh = full_hash(r["path"])
                    except OSError:
                        continue
                conn.execute("UPDATE files SET full_hash=? WHERE id=?", (fh, r["id"]))
                hashed += 1
                if progress and hashed % 200 == 0:
                    progress(r["path"])
        conn.commit()
    return hashed


def duplicate_groups(conn: sqlite3.Connection, scan_id: int) -> List[List[sqlite3.Row]]:
    rows = conn.execute(
        """SELECT * FROM files WHERE scan_id=? AND full_hash IN (
               SELECT full_hash FROM files WHERE scan_id=? AND full_hash IS NOT NULL
               GROUP BY full_hash HAVING COUNT(*)>1)
           ORDER BY size DESC, full_hash, mtime""",
        (scan_id, scan_id),
    ).fetchall()
    groups: Dict[str, List[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        groups[r["full_hash"]].append(r)
    return sorted(groups.values(), key=lambda g: g[0]["size"] * (len(g) - 1), reverse=True)
