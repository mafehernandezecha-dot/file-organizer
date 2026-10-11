"""Turn the index into audit findings. Pure reads from SQLite."""

from __future__ import annotations

import os
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List

from organizer.config import Config
from organizer.duplicates import duplicate_groups
from organizer.kinds import has_version_marker, is_screenshot

DAY = 86400


@dataclass
class Audit:
    scan_id: int
    started_at: float
    finished_at: float
    roots: List[str]
    total_items: int = 0
    total_bytes: int = 0
    by_root: Dict[str, Dict[str, int]] = field(default_factory=dict)
    by_kind: List[Dict] = field(default_factory=list)
    largest_folders: List[Dict] = field(default_factory=list)
    largest_files: List[sqlite3.Row] = field(default_factory=list)
    stale_count: int = 0
    stale_bytes: int = 0
    stale_examples: List[sqlite3.Row] = field(default_factory=list)
    duplicate_groups: List[List[sqlite3.Row]] = field(default_factory=list)
    duplicate_reclaimable: int = 0
    version_families: List[List[sqlite3.Row]] = field(default_factory=list)
    business_by_root: Dict[str, Counter] = field(default_factory=dict)
    business_total: int = 0
    projects: List[sqlite3.Row] = field(default_factory=list)
    packages: List[sqlite3.Row] = field(default_factory=list)
    symlinks: int = 0
    sensitive_count: int = 0
    dataless_count: int = 0
    skipped: Counter = field(default_factory=Counter)
    observations: List[str] = field(default_factory=list)


def _top_folder(root: str, path: str) -> str:
    rel = os.path.relpath(path, root)
    parts = rel.split(os.sep)
    return os.path.join(root, parts[0]) if len(parts) > 1 else root


def build_audit(conn: sqlite3.Connection, scan_id: int, cfg: Config) -> Audit:
    scan = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    a = Audit(scan_id, scan["started_at"], scan["finished_at"], scan["roots"].split("\n"))
    rows = conn.execute("SELECT * FROM files WHERE scan_id=?", (scan_id,)).fetchall()
    now = scan["finished_at"] or time.time()
    stale_cutoff = now - cfg.stale_days * DAY

    folder_sizes: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
    kind_stats: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
    stale: List[sqlite3.Row] = []
    for r in rows:
        a.total_items += 1
        a.total_bytes += r["size"]
        rs = a.by_root.setdefault(r["root"], {"items": 0, "bytes": 0})
        rs["items"] += 1
        rs["bytes"] += r["size"]
        kind_stats[r["kind"]][0] += 1
        kind_stats[r["kind"]][1] += r["size"]
        fs = folder_sizes[_top_folder(r["root"], r["path"])]
        fs[0] += 1
        fs[1] += r["size"]
        if r["item_type"] == "symlink":
            a.symlinks += 1
        elif r["item_type"] == "project":
            a.projects.append(r)
        elif r["item_type"] == "package":
            a.packages.append(r)
        a.sensitive_count += r["sensitive"]
        a.dataless_count += r["dataless"]
        last_touch = max(r["mtime"], r["atime"] or 0)
        if r["item_type"] == "file" and last_touch < stale_cutoff:
            stale.append(r)
        if r["business"]:
            a.business_total += 1
            a.business_by_root.setdefault(r["root"], Counter())[r["kind"]] += 1

    a.by_kind = sorted(
        ({"kind": k, "items": v[0], "bytes": v[1]} for k, v in kind_stats.items()),
        key=lambda d: d["bytes"], reverse=True,
    )
    a.largest_folders = sorted(
        ({"path": p, "items": v[0], "bytes": v[1]} for p, v in folder_sizes.items()),
        key=lambda d: d["bytes"], reverse=True,
    )[:15]
    a.largest_files = sorted(
        (r for r in rows if r["item_type"] in ("file", "package")),
        key=lambda r: r["size"], reverse=True,
    )[:25]
    a.stale_count = len(stale)
    a.stale_bytes = sum(r["size"] for r in stale)
    a.stale_examples = sorted(stale, key=lambda r: r["size"], reverse=True)[:15]
    a.projects.sort(key=lambda r: r["size"], reverse=True)
    a.packages.sort(key=lambda r: r["size"], reverse=True)

    a.duplicate_groups = duplicate_groups(conn, scan_id)
    a.duplicate_reclaimable = sum(g[0]["size"] * (len(g) - 1) for g in a.duplicate_groups)

    families: Dict[tuple, List[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        if r["item_type"] == "file" and r["kind"] not in ("code", "other"):
            families[(r["base_name"], r["kind"])].append(r)
    a.version_families = sorted(
        (sorted(g, key=lambda r: r["mtime"]) for g in families.values()
         if len(g) > 1 and any(has_version_marker(r["name"]) for r in g)),
        key=len, reverse=True,
    )

    for row in conn.execute(
        "SELECT reason, COUNT(*) AS n FROM skipped WHERE scan_id=? GROUP BY reason", (scan_id,)
    ):
        a.skipped[row["reason"]] = row["n"]

    a.observations = _observations(rows, a)
    return a


def _observations(rows: List[sqlite3.Row], a: Audit) -> List[str]:
    """Plain-language organization problems. Each is a fact, not an action."""
    obs = []
    by_root_name = defaultdict(list)
    for r in rows:
        by_root_name[os.path.basename(r["root"]).lower()].append(r)

    downloads = by_root_name.get("downloads", [])
    if downloads:
        installers = [r for r in downloads if r["kind"] == "installer"]
        archives = [r for r in downloads if r["kind"] == "archive"]
        if installers:
            obs.append(f"Downloads holds {len(installers)} installer(s) (.dmg/.pkg) "
                       f"using {human(sum(r['size'] for r in installers))}.")
        if archives:
            obs.append(f"Downloads holds {len(archives)} compressed archive(s) "
                       f"using {human(sum(r['size'] for r in archives))}; some may already be extracted.")
        biz = sum(r["business"] for r in downloads)
        if biz:
            obs.append(f"{biz} Memobrick-related item(s) are sitting in Downloads.")

    desktop = by_root_name.get("desktop", [])
    if desktop:
        loose = [r for r in desktop if os.path.dirname(r["path"]) == r["root"]]
        shots = [r for r in desktop if is_screenshot(r["name"])]
        if len(loose) > 20:
            obs.append(f"The Desktop has {len(loose)} loose items at the top level.")
        if shots:
            obs.append(f"{len(shots)} screenshot(s) are on the Desktop.")

    media_homes = {"pictures", "movies"}
    stray_media = [r for r in rows if r["kind"] in ("image", "video")
                   and os.path.basename(r["root"]).lower() not in media_homes
                   and not r["business"] and r["item_type"] == "file"]
    if stray_media:
        obs.append(f"{len(stray_media)} photo/video file(s) live outside Pictures and Movies "
                   f"and are not recognized as Memobrick.")
    if a.packages and any(r["name"].endswith(".photoslibrary") for r in a.packages):
        obs.append("An Apple Photos library was found; it is kept intact and never "
                   "reorganized — manage personal photos inside the Photos app.")

    biz_roots = [root for root, c in a.business_by_root.items() if sum(c.values())]
    if len(biz_roots) > 1:
        obs.append(f"Memobrick files are spread across {len(biz_roots)} top-level locations.")
    if a.duplicate_groups:
        obs.append(f"{len(a.duplicate_groups)} set(s) of exact duplicates; "
                   f"{human(a.duplicate_reclaimable)} is held by the extra copies.")
    if a.version_families:
        obs.append(f"{len(a.version_families)} document families have multiple versions "
                   f"(final / v2 / copy …) that may need a clear 'current' version.")
    if a.dataless_count:
        obs.append(f"{a.dataless_count} file(s) are stored only in iCloud; they were indexed "
                   f"but not opened, so duplicate checks skip them.")
    return obs


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
