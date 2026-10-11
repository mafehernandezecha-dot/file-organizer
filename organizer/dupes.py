"""Plan trashing the extra copies of exact duplicates, always keeping one.

Reads the index only. The keeper of each set is chosen by location (organized
folders first), then a clean name over "copy"/"(1)", then the older file.
At apply time trash.py re-verifies the keeper before touching an extra.
"""

from __future__ import annotations

import html
import os
import time
from pathlib import Path
from typing import Dict, List

from organizer.analysis import human
from organizer.config import Config
from organizer.duplicates import duplicate_groups
from organizer.kinds import has_version_marker
from organizer.report import CSS, date, short


def _under(path: str, folder: Path) -> bool:
    return (path + os.sep).startswith(str(folder) + os.sep)


def location_rank(path: str, cfg: Config) -> int:
    """Lower is better: organized places first, intake folders last."""
    if _under(path, cfg.business_root):
        return 0
    if _under(path, cfg.screenshots_folder):
        return 1
    for i, intake in enumerate(cfg.intake_folders):
        if _under(path, intake):
            return 3 + i
    return 2


def choose_keeper(rows, cfg: Config):
    return min(rows, key=lambda r: (
        location_rank(r["path"], cfg),
        has_version_marker(r["name"]),
        len(r["name"]),
        r["birthtime"] or r["mtime"],
        r["path"],
    ))


def build_duplicates_plan(conn, scan_id: int, cfg: Config, scan_finished: float) -> Dict:
    sets = []
    items = []
    n = 0
    for group in duplicate_groups(conn, scan_id):
        group = [r for r in group if r["item_type"] == "file" and not r["sensitive"]]
        if len(group) < 2:
            continue
        keep = choose_keeper(group, cfg)
        extras = [r for r in group if r["path"] != keep["path"]]
        set_no = len(sets) + 1
        sets.append({"set": set_no, "keep": keep["path"], "size": keep["size"],
                     "keep_mtime": keep["mtime"]})
        for r in extras:
            n += 1
            items.append({"n": n, "set": set_no, "path": r["path"], "size": r["size"],
                          "mtime": r["mtime"], "dataless": bool(r["dataless"]),
                          "keep": keep["path"], "keep_size": keep["size"]})
    return {
        "version": 1,
        "action": "trash",
        "reason": "extra copies of duplicate files",
        "created_at": time.time(),
        "scan_finished_at": scan_finished,
        "items": items,
        "sets": sets,
    }


def render_duplicates_preview(plan: Dict, plan_path: Path) -> str:
    e = html.escape
    items, sets = plan["items"], plan["sets"]
    total = sum(i["size"] for i in items)
    by_set: Dict[int, List[Dict]] = {}
    for it in items:
        by_set.setdefault(it["set"], []).append(it)
    rows = []
    for s in sets:
        rows.append(
            f"<tr class=keep><td></td><td>✓ keep</td><td>{e(os.path.basename(s['keep']))}</td>"
            f"<td>{e(short(os.path.dirname(s['keep'])))}</td><td>{e(human(s['size']))}</td>"
            f"<td>{e(date(s['keep_mtime']))}</td></tr>")
        for it in by_set.get(s["set"], []):
            rows.append(
                f"<tr><td>{it['n']}</td><td>Trash</td><td>{e(os.path.basename(it['path']))}</td>"
                f"<td>{e(short(os.path.dirname(it['path'])))}</td><td>{e(human(it['size']))}</td>"
                f"<td>{e(date(it['mtime']))}</td></tr>")
        rows.append("<tr class=gap><td colspan=6></td></tr>")
    style = CSS + "tr.keep td{font-weight:600}tr.gap td{padding:2px;border:0}"
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Preview: Duplicates</title><style>{style}</style></head><body><main>"
        "<h1>Preview: trash extra copies of duplicates</h1>"
        f"<p class=sub>Based on the scan from {e(date(plan['scan_finished_at']))}</p>"
        "<div class=safe><b>Nothing has happened yet.</b> One copy of every set is kept "
        "(marked ✓). Only the extra copies go to the Trash, after you approve, and only if "
        "the kept copy is still there and still identical. Finder's “Put Back” or the "
        "<code>undo</code> command restores them.</div>"
        "<div class=tiles>"
        f"<div class=tile><b>{len(sets):,}</b><span>duplicate sets</span></div>"
        f"<div class=tile><b>{len(items):,}</b><span>extra copies</span></div>"
        f"<div class=tile><b>{e(human(total))}</b><span>freed when the Trash is emptied</span></div>"
        "</div>"
        "<h2>How to review</h2><ul class=obs>"
        "<li>Copies are byte-for-byte identical (checked with SHA-256), so keeping one loses nothing.</li>"
        "<li>To keep an extra copy too, note its number (#) and add <code>--skip 3,17</code>.</li>"
        f"<li>Approve with: <code>python3 -m organizer apply {e(str(plan_path))}</code></li>"
        "</ul>"
        "<h2>Sets</h2><div class=wrap><table><thead><tr><th>#</th><th></th><th>File</th>"
        "<th>Folder</th><th>Size</th><th>Modified</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table></div></main></body></html>"
    )
